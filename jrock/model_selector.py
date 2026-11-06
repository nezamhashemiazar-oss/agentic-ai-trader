"""AutoSetModelByKey.

Reads BASE_URL + API_KEY from .env, calls GET {BASE_URL}/models, then probes each
candidate with a tiny chat completion to find which models ACTUALLY work with that
key. Picks the best working one by a capability/quality heuristic.

Never fabricates: if nothing works, it says so and returns the raw errors.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass
class ModelProbe:
    id: str
    score: float = 0.0
    works: bool = False
    latency_ms: int | None = None
    error: str | None = None


@dataclass
class AutoSetResult:
    base_url: str
    ok: bool
    selected: str | None = None
    probes: list[ModelProbe] = field(default_factory=list)
    message: str = ""
    listed: int = 0


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


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


async def list_models(session: aiohttp.ClientSession, base_url: str, api_key: str) -> list[str]:
    url = f"{base_url.rstrip('/')}/models"
    async with session.get(url, headers=_headers(api_key), timeout=aiohttp.ClientTimeout(total=20)) as r:
        text = await r.text()
        if r.status != 200:
            raise RuntimeError(f"GET /models -> HTTP {r.status}: {text[:300]}")
        data = json.loads(text)
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise RuntimeError(f"Unexpected /models payload: {text[:300]}")
    out = []
    for it in items:
        mid = it.get("id") if isinstance(it, dict) else str(it)
        if mid:
            out.append(mid)
    return out


async def probe_model(session: aiohttp.ClientSession, base_url: str, api_key: str,
                      model: str, timeout: float = 25.0) -> ModelProbe:
    p = ModelProbe(id=model, score=score_model(model))
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
                d = json.loads(text)
                if d.get("choices"):
                    p.works = True
                else:
                    p.error = f"200 but no choices: {text[:200]}"
            else:
                p.error = f"HTTP {r.status}: {text[:200]}"
    except asyncio.TimeoutError:
        p.error = f"timeout after {timeout}s"
    except Exception as e:  # noqa: BLE001
        p.error = f"{type(e).__name__}: {e}"
    return p


async def autoset_model(base_url: str, api_key: str, max_probe: int = 8,
                        concurrency: int = 4) -> AutoSetResult:
    if not api_key:
        return AutoSetResult(base_url, False, message="API_KEY is empty in .env - cannot probe models.")
    if not base_url:
        return AutoSetResult(base_url, False, message="BASE_URL is empty in .env.")

    async with aiohttp.ClientSession() as session:
        try:
            models = await list_models(session, base_url, api_key)
        except Exception as e:  # noqa: BLE001
            return AutoSetResult(base_url, False, message=f"Could not list models: {e}")

        if not models:
            return AutoSetResult(base_url, False, message="Provider returned an empty model list.")

        ranked = sorted(models, key=score_model, reverse=True)
        candidates = [m for m in ranked if score_model(m) > 0][:max_probe]
        if not candidates:
            candidates = ranked[:max_probe]

        sem = asyncio.Semaphore(concurrency)

        async def run(m: str) -> ModelProbe:
            async with sem:
                return await probe_model(session, base_url, api_key, m)

        probes = await asyncio.gather(*(run(m) for m in candidates))

    working = [p for p in probes if p.works]
    if not working:
        errs = "; ".join(f"{p.id}: {p.error}" for p in probes[:5])
        return AutoSetResult(base_url, False, probes=list(probes), listed=len(models),
                             message=f"No model responded successfully with this key. Tried {len(probes)}. {errs}")

    working.sort(key=lambda p: (p.score, -(p.latency_ms or 99999)), reverse=True)
    best = working[0]
    return AutoSetResult(
        base_url, True, selected=best.id, probes=list(probes), listed=len(models),
        message=(f"Selected {best.id} (score {best.score:.0f}, {best.latency_ms}ms). "
                 f"{len(working)}/{len(probes)} probed models work."),
    )


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
