"""AutoSetModelByKey.

Reads BASE_URL + API_KEY from .env, calls GET {BASE_URL}/models, then probes
candidates with a tiny chat completion to find which models ACTUALLY work with
that key.

Candidate selection is driven by the provider's OWN metadata, not a hardcoded
premium list. Providers expose "is this free" in several different ways and we
read all of them:

  * OpenRouter / Kilo style:  {"pricing": {"prompt": "0", "completion": "0"}}
  * a boolean flag:           {"free": true}
  * an id suffix:             "vendor/model:free"
  * a zero cost field:        {"cost": 0} / {"price": 0}

Probe order: free models first, then paid. When the account balance is known to
be <= 0 (a 402 told us), paid models are skipped entirely instead of being
probed and failing one by one.

Never fabricates: if nothing works, it prints a table of what was tried and why
each one failed, plus the exact next action.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

QUALITY_HINTS: list[tuple[str, float]] = [
    (r"gpt-5", 100), (r"o3", 98), (r"o4", 97), (r"gpt-4\.1", 95), (r"gpt-4o(?!-mini)", 90),
    (r"claude.*(opus|sonnet-4|4\.5)", 96), (r"claude.*sonnet", 88), (r"claude.*haiku", 70),
    (r"gemini.*(2\.5|3).*pro", 94), (r"gemini.*flash", 80),
    (r"deepseek.*(v3|r1|chat)", 85), (r"qwen.*(max|72b|235b)", 82), (r"llama.*(70b|405b)", 78),
    (r"mistral.*large", 76), (r"grok.*(4|3)", 88),
]
PENALTY = [(r"mini", -12), (r"nano", -20), (r"embed", -100), (r"whisper", -100),
           (r"tts", -100), (r"dall-e", -100), (r"image", -100), (r"audio", -100),
           (r"moderation", -100), (r"rerank", -100), (r"vision-preview", -5)]

# Model ids that are obviously not chat models.
NON_CHAT = re.compile(r"(embed|whisper|tts|dall-e|moderation|rerank|image|audio|"
                      r"stable-diffusion|flux|sdxl)", re.I)


@dataclass
class ModelInfo:
    id: str
    free: bool = False
    prompt_price: float | None = None
    completion_price: float | None = None
    raw: dict = field(default_factory=dict)

    @property
    def score(self) -> float:
        return score_model(self.id)


@dataclass
class ModelProbe:
    id: str
    score: float = 0.0
    works: bool = False
    free: bool = False
    latency_ms: int | None = None
    status: str = "not probed"
    error: str | None = None
    balance: float | None = None


@dataclass
class AutoSetResult:
    base_url: str
    ok: bool
    selected: str | None = None
    probes: list[ModelProbe] = field(default_factory=list)
    message: str = ""
    listed: int = 0
    free_count: int = 0
    balance: float | None = None

    def table(self) -> str:
        """Compact model | status | reason table."""
        if not self.probes:
            return ""
        w = max(len(p.id) for p in self.probes)
        w = min(max(w, 12), 48)
        rows = [f"{'model'.ljust(w)}  {'status':<10}  reason"]
        rows.append(f"{'-' * w}  {'-' * 10}  {'-' * 40}")
        for p in self.probes:
            reason = (p.error or "").replace("\n", " ")[:110]
            rows.append(f"{p.id[:w].ljust(w)}  {p.status:<10}  {reason}")
        return "\n".join(rows)


def score_model(model_id: str) -> float:
    s = 50.0
    low = model_id.lower()
    for pat, pts in QUALITY_HINTS:
        if re.search(pat, low):
            s = max(s, pts)
    for pat, pts in PENALTY:
        if re.search(pat, low):
            s += pts
    return s


def _as_float(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_model(item: Any) -> ModelInfo | None:
    """Turn one /models entry into a ModelInfo, reading every free/pricing hint."""
    if isinstance(item, str):
        mid = item
        raw: dict = {}
    elif isinstance(item, dict):
        mid = item.get("id") or item.get("name") or item.get("model")
        raw = item
    else:
        return None
    if not mid:
        return None

    info = ModelInfo(id=str(mid), raw=raw)
    low = info.id.lower()

    # 1) explicit boolean flags
    for key in ("free", "is_free", "isFree", "free_model"):
        if isinstance(raw.get(key), bool):
            info.free = bool(raw[key])
            break

    # 2) id suffix ":free"
    if low.endswith(":free"):
        info.free = True

    # 3) pricing block (OpenRouter / Kilo)
    pricing = raw.get("pricing")
    if isinstance(pricing, dict):
        info.prompt_price = _as_float(pricing.get("prompt"))
        info.completion_price = _as_float(pricing.get("completion"))
        if info.prompt_price is not None and info.completion_price is not None:
            if info.prompt_price == 0 and info.completion_price == 0:
                info.free = True
            elif info.prompt_price > 0 or info.completion_price > 0:
                info.free = False

    # 4) flat cost / price fields
    for key in ("cost", "price", "input_price", "prompt_price"):
        val = _as_float(raw.get(key))
        if val is not None:
            if val == 0:
                info.free = True
            elif val > 0:
                info.free = False
            break

    return info


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


async def list_model_info(session: aiohttp.ClientSession, base_url: str,
                          api_key: str) -> list[ModelInfo]:
    url = f"{base_url.rstrip('/')}/models"
    async with session.get(url, headers=_headers(api_key),
                           timeout=aiohttp.ClientTimeout(total=20)) as r:
        text = await r.text()
        if r.status != 200:
            raise RuntimeError(f"GET /models -> HTTP {r.status}: {text[:300]}")
        data = json.loads(text)
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise RuntimeError(f"Unexpected /models payload: {text[:300]}")
    out = []
    for it in items:
        info = parse_model(it)
        if info:
            out.append(info)
    return out


async def list_models(session: aiohttp.ClientSession, base_url: str,
                      api_key: str) -> list[str]:
    """Back-compat: just the ids."""
    return [m.id for m in await list_model_info(session, base_url, api_key)]


def _extract_balance(text: str) -> float | None:
    """Pull a balance out of a 402 body, e.g. {"balance":-0.025288}."""
    try:
        d = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r'"balance"\s*:\s*(-?\d+(?:\.\d+)?)', text or "")
        return float(m.group(1)) if m else None
    stack = [d]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k.lower() == "balance":
                    f = _as_float(v)
                    if f is not None:
                        return f
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


async def probe_model(session: aiohttp.ClientSession, base_url: str, api_key: str,
                      model: str, timeout: float = 25.0,
                      free: bool = False) -> ModelProbe:
    p = ModelProbe(id=model, score=score_model(model), free=free)
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "max_tokens": 5,
        "temperature": 0,
    }
    t0 = time.perf_counter()
    try:
        async with session.post(url, headers=_headers(api_key), json=payload,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as r:
            text = await r.text()
            p.latency_ms = int((time.perf_counter() - t0) * 1000)
            if r.status == 200:
                try:
                    d = json.loads(text)
                except json.JSONDecodeError:
                    p.status = "bad-json"
                    p.error = f"200 but non-JSON: {text[:160]}"
                    return p
                if d.get("choices"):
                    p.works = True
                    p.status = "ok"
                else:
                    p.status = "no-choices"
                    p.error = f"200 but no choices: {text[:160]}"
            elif r.status == 402:
                p.status = "402"
                p.balance = _extract_balance(text)
                p.error = "paid model - credits required"
                if p.balance is not None:
                    p.error += f" (balance {p.balance})"
            elif r.status == 401:
                p.status = "401"
                p.error = "unauthorized - API_KEY rejected"
            elif r.status == 404:
                p.status = "404"
                p.error = "model not found on this provider"
            elif r.status == 429:
                p.status = "429"
                p.error = "rate limited"
            else:
                p.status = str(r.status)
                p.error = f"HTTP {r.status}: {text[:160]}"
    except asyncio.TimeoutError:
        p.status = "timeout"
        p.error = f"timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001
        p.status = "error"
        p.error = f"{type(e).__name__}: {e}"
    return p


def _free_only() -> bool:
    return os.getenv("FREE_ONLY", "").strip().lower() in ("1", "true", "yes", "on")


async def autoset_model(base_url: str, api_key: str, max_probe: int = 8,
                        concurrency: int = 4, free_only: bool | None = None,
                        max_paid_probe: int = 3) -> AutoSetResult:
    """Probe models and pick the best working one.

    Order: free models first (by quality score), then paid models - and paid
    models are skipped entirely when the balance is known to be <= 0.
    """
    if free_only is None:
        free_only = _free_only()
    if not api_key:
        return AutoSetResult(base_url, False,
                             message="API_KEY is empty in .env - cannot probe models.")
    if not base_url:
        return AutoSetResult(base_url, False, message="BASE_URL is empty in .env.")

    async with aiohttp.ClientSession() as session:
        try:
            infos = await list_model_info(session, base_url, api_key)
        except Exception as e:  # noqa: BLE001
            return AutoSetResult(base_url, False, message=f"Could not list models: {e}")

        if not infos:
            return AutoSetResult(base_url, False,
                                 message="Provider returned an empty model list.")

        chat = [m for m in infos if not NON_CHAT.search(m.id)]
        free = sorted([m for m in chat if m.free], key=lambda m: m.score, reverse=True)
        paid = sorted([m for m in chat if not m.free], key=lambda m: m.score, reverse=True)

        if free_only:
            candidates = free[:max_probe]
            if not candidates:
                return AutoSetResult(
                    base_url, False, listed=len(infos), free_count=0,
                    message=(f"FREE_ONLY is set but none of the {len(infos)} models on "
                             f"{base_url} advertise free pricing. Unset FREE_ONLY or add credits."))
        else:
            candidates = free[:max_probe]
            if len(candidates) < max_probe:
                candidates += paid[:min(max_paid_probe, max_probe - len(candidates))]

        if not candidates:
            return AutoSetResult(base_url, False, listed=len(infos),
                                 message="No chat-capable models found in the /models list.")

        sem = asyncio.Semaphore(concurrency)
        balance: float | None = None
        probes: list[ModelProbe] = []

        async def run(m: ModelInfo) -> ModelProbe:
            async with sem:
                return await probe_model(session, base_url, api_key, m.id, free=m.free)

        # Free first, sequentially in small batches so a 402 on the first paid
        # model can stop us from wasting probes on the rest.
        free_c = [m for m in candidates if m.free]
        paid_c = [m for m in candidates if not m.free]

        if free_c:
            probes.extend(await asyncio.gather(*(run(m) for m in free_c)))
            working = [p for p in probes if p.works]
            if working:
                return _pick(base_url, working, probes, len(infos), len(free), None)

        for p in probes:
            if p.balance is not None:
                balance = p.balance
                break

        if paid_c:
            if balance is not None and balance <= 0:
                for m in paid_c:
                    probes.append(ModelProbe(
                        id=m.id, score=m.score, free=False, status="skipped",
                        error=f"skipped - account balance is {balance}, add credits or use a free model"))
            else:
                for m in paid_c:
                    p = await run(m)
                    probes.append(p)
                    if p.balance is not None:
                        balance = p.balance
                        if balance <= 0:
                            rest = paid_c[paid_c.index(m) + 1:]
                            for r in rest:
                                probes.append(ModelProbe(
                                    id=r.id, score=r.score, free=False, status="skipped",
                                    error=f"skipped - account balance is {balance}"))
                            break
                    if p.works:
                        break

    working = [p for p in probes if p.works]
    if working:
        return _pick(base_url, working, probes, len(infos), len(free), balance)

    return _failure(base_url, probes, len(infos), len(free), balance, free_only)


def _pick(base_url: str, working: list[ModelProbe], probes: list[ModelProbe],
          listed: int, free_count: int, balance: float | None) -> AutoSetResult:
    # Prefer free models (they cost nothing and already work), then quality.
    working.sort(key=lambda p: (p.free, p.score, -(p.latency_ms or 99999)), reverse=True)
    best = working[0]
    kind = "free" if best.free else "paid"
    return AutoSetResult(
        base_url, True, selected=best.id, probes=list(probes), listed=listed,
        free_count=free_count, balance=balance,
        message=(f"Selected {best.id} ({kind}, score {best.score:.0f}, "
                 f"{best.latency_ms}ms). {len(working)}/{len(probes)} probed models work."),
    )


def _failure(base_url: str, probes: list[ModelProbe], listed: int, free_count: int,
             balance: float | None, free_only: bool) -> AutoSetResult:
    res = AutoSetResult(base_url, False, probes=list(probes), listed=listed,
                        free_count=free_count, balance=balance)
    lines = [f"No model responded successfully with this key. Tried {len(probes)} "
             f"of {listed} listed ({free_count} free)."]
    if balance is not None:
        lines.append(f"ACCOUNT BALANCE IS {balance} - add credits or use a free model.")
    lines.append("")
    lines.append(res.table())
    lines.append("")
    if balance is not None and balance <= 0:
        lines.append("Next action: top up at your provider's billing page, or run with "
                     "FREE_ONLY=true to restrict probing to free models.")
    elif free_only:
        lines.append("Next action: no free models worked. Unset FREE_ONLY and add credits.")
    else:
        lines.append("Next action: check API_KEY validity and BASE_URL, then retry. "
                     "Run with BITUNIX_DEBUG_SIGN-style verbose logging if the provider "
                     "returns an unexpected body.")
    res.message = "\n".join(lines)
    return res


def persist_model(env_path: Path | str, model: str) -> None:
    """Write MODEL=... into .env, preserving everything else."""
    p = Path(env_path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    found = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith("MODEL="):
            lines[i] = f"MODEL={model}"
            found = True
            break
    if not found:
        lines.append(f"MODEL={model}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
