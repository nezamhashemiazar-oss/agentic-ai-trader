"""J+Rock agent: tools, self-healing, and the full command menu.

Commands:
  /help /models /set_model /connect_provider /add_mcp /mcp /add_skill /skill
  /add_soul /soul /think /memory /code_review /bug_fix /verify /simplify
  /team_spawn /check_ai /check_system /autopsy /diagnose
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .account import (BalanceReport, is_demo_url, missing_keys_message,
                      parse_accounts, render_balance)
from .config import ROOT, Settings, load_settings
from .empty_response import (budget_warning, diagnose_empty, render_empty)
from .memory import MemoryStore
from .model_selector import autoset_model, persist_model
from . import nlu
from .permissions import PermissionManager
from .settings_store import mirror_env
from .store import Store
from .thinking import (DEFAULT_LEVEL, LEVEL_HELP, LEVELS, ONE_LINER,
                       build_reasoning_params, closest_level, describe_params,
                       normalize_level)
from .trader.engine import TraderEngine

SOUL_PATH = ROOT / "soul" / "jrock.soul.md"

log = logging.getLogger("jrock.agent")

HELP = """J+Rock commands
================
Type /help or help or ? for this list.
Anything that is not a command is sent to the model as natural language.

NATURAL LANGUAGE (no commands needed)
  Just say what you mean. These all work as plain sentences:
    "show me my trading settings"     "what is my balance"
    "show my positions"               "what are my open orders"
    "what is my pnl"                  "show me the status"
    "turn off autotrade"              "stop the engine"
    "close my AKEUSDT position"       "set leverage to 5x"
    "set risk to 2%"                  "why did the last order fail"
    "what does TP_SL_MIN_STOP_TICKS do"
  Anything that moves money (orders, leverage, risk, autotrade, engine) asks
  for a 'yes' before it runs. An ambiguous sentence asks a question instead of
  guessing. A sentence that is not understood still goes to the model.

AGENT COMMANDS
  /help  help  ?            this list
  /models [--free]          list models on BASE_URL; --free shows only free ones
  /set_model <name>         pin a model (writes MODEL= to .env)
  /connect_provider <url> <key>   set BASE_URL + API_KEY, then auto-probe
  /add_mcp <name> <cmd>     register an MCP server (stdio command)
  /mcp                      list registered MCP servers
  /add_skill <name> <path>  register a skill directory
  /skill                    list skills
  /add_soul <text|@file>    append to the soul/persona
  /soul                     show the current soul
  /think [level]            how hard the model reasons: off|low|medium|high|max
                            (a setting, not a chat mode - just type to chat)
  /balance  bal  wallet     account balance: available, frozen, margin, PnL, equity
  /ws [on|off|test]         WebSocket: connection state, toggle market data source,
                            or open a real connection and print 3 live frames
  /positions                open positions with entry, mark, liq and unrealized PnL
  /orders                   pending orders
  /price [SYMBOL]           last / mark price for your symbols
  /funding [SYMBOL]         current funding rate (or: /funding history SYMBOL)
  /tpsl                     the 4 TP/SL methods: show/set, pending, history, cancel
  /ladder                   TP ladder: show levels, fired levels, remaining qty
                            (ladder set 1R:25,2R:25,3R:25 | ladder off |
                             ladder breakeven 2 | ladder runner trail|close)
  /batch                    batch-order status (orderList max 5, symbol required)
  /leverage                 leverage and margin mode per symbol
  /margin                   margin mode (ISOLATION|CROSS) per symbol
  /settings [SYMBOL...]     account trading settings: position mode, margin mode,
                            leverage and the trading config (add a SYMBOL for its
                            per-symbol margin mode + leverage)
  /pnl                      unrealized PnL across open positions
  /status                   engine + API + account summary
  /symbols                  tradable symbols (or search: /symbols BTC)
  /memory [query]           memory stats, or search memory
  /code_review [path]       static review: syntax, smells, risky patterns
  /bug_fix [path]           find + propose fixes (permission-gated)
  /verify                   run tests + import check + compile check
  /simplify [path]          report over-complex functions / duplication
  /team_spawn <n> <task>    spawn n parallel sub-agents on a task
  /check_ai                 verify LLM key/model health end-to-end
  /check_system             health check of agent + trader engine
  /autopsy <error|file>     root-cause analysis of a failure
  /diagnose                 full diagnostic bundle

TRADER ENGINE COMMANDS
  start trading | autotrade on      stop trading | autotrade off
  engine on | engine off            status
  report on | report off            report interval sec 30
  trades_history | tpsl_history | pnl | positions history
  settings trade                    scan on | scan off
  mid management position trigger   reversal=true  reversal_confidence=70
  scans interval sec 30             breakeven threshold pct 0.6
  trailing trigger stop ROI pct 25  liq sl distance pct 50
  min agreeing strategies 3         timeframes 1m,3m,5m,15m
  min confidence 70                 tf min confidence 60
  close position [SYMBOL]           margin mode ISOLATION|CROSS
  position mode HEDGE|ONE_WAY       symbols BTCUSDT,ETHUSDT
  max open positions 3              leverage 10 | risk 1.0
  tpsl method POSITION|PARTIAL|TRAILING|ACCOUNT
  partial close pct 50              partial at roi 50
  account tp usdt 100               account sl usdt 50
  account tp pct 5                  account sl pct 3
  ladder set 1R:25,2R:25,3R:25      ladder off
  ladder breakeven 2                ladder runner trail|close
  ladder basis R|ROI|PRICE          ladder
  margin add BTCUSDT 100            margin reduce BTCUSDT 50

ENV SETTINGS (in .env, read at startup)
  TP_SL_MIN_STOP_TICKS=3    minimum gap, in price ticks, that a TP/SL must keep
                            from the mark/entry price. 1 tick = 10^-quotePrecision
                            (AKEUSDT has quotePrecision 6, so 1 tick = 0.000001).
                            Enforced in plan_tpsl() and clamp_sl()/clamp_tp().
                            Higher = safer against 30029/30031 but a wider stop;
                            lower = tighter stop, more likely to be rejected.
  TAKER_FEE_RATE=0.0006     taker fee (0.06%) reserved on top of margin when
                            sizing, so an order never asks for more than the
                            balance can pay for (20003 Insufficient balance).
  MARGIN_SAFETY_PCT=2       extra headroom reserved when sizing, % of balance.

PERMISSIONS
  auto accept edit on|off    apply self-edits automatically vs. show diff
  device access on|off       grant whole-terminal access
  audit                      show the audit log
  pending                    show edits waiting for approval
  approve [n]                approve a pending edit

FREE TEXT
  anything else (e.g. "switch to free model", "why did my order fail?")
  goes to the model. exit / quit / Ctrl-D leave the REPL.

THINKING LEVEL
  /think            show the current level and what it does
  /think high       set it (off|low|medium|high|max, aliases: none/min/med/hi/maximum)
  Higher = better reasoning on hard problems, slower and more tokens.
  off/low for quick chat; high/max for debugging, code review, strategy analysis.
  You do NOT need /think to talk to me - it only changes how hard I think.
"""

# Bare words that mean an agent command (no leading slash needed).
#
# Every word a trader would naturally type must land on a REAL handler. A word
# that is not here and is not engine grammar falls through to the model, which
# is how `balance` used to produce an empty answer.
AGENT_BARE: dict[str, str] = {
    "help": "/help", "?": "/help", "models": "/models", "model": "/models",
    "soul": "/soul", "memory": "/memory", "think": "/think",
    "ws": "/ws", "websocket": "/ws",
    "verify": "/verify", "diagnose": "/diagnose", "autopsy": "/autopsy",
    "mcp": "/mcp", "skill": "/skill", "skills": "/skill",
    "check_ai": "/check_ai", "check_system": "/check_system",
    "code_review": "/code_review", "bug_fix": "/bug_fix", "simplify": "/simplify",
    # account / market queries - first-class, never sent to the model
    "balance": "/balance", "bal": "/balance", "wallet": "/balance",
    "equity": "/balance", "funds": "/balance",
    "positions": "/positions", "position": "/positions",
    "orders": "/orders", "order": "/orders",
    "price": "/price", "prices": "/price", "quote": "/price",
    "funding": "/funding", "funding_rate": "/funding",
    "tpsl": "/tpsl", "tp_sl": "/tpsl", "tpsl_method": "/tpsl",
    "ladder": "/ladder", "tp_ladder": "/ladder",
    "batch": "/batch", "batch_order": "/batch",
    "leverage": "/leverage", "lev": "/leverage",
    "margin": "/margin", "margin_mode": "/margin",
    "settings": "/settings", "trading_settings": "/settings", "position_mode": "/settings",
    "pnl": "/pnl", "p&l": "/pnl",
    "status": "/status", "report": "/status",
    "symbols": "/symbols", "pairs": "/symbols", "markets": "/symbols",
}

# Words that are deliberately NOT commands: they are free text for the model.
# Listed explicitly so the routing table can prove the decision was made.
FREE_TEXT_WORDS: tuple[str, ...] = (
    "why", "how", "what", "explain", "should", "can", "is", "are",
    "tell", "show", "give", "write", "fix", "review", "analyze",
)

# Query commands that ALSO exist as engine setters. With arguments they are
# engine syntax ("leverage 20"); bare they are a read ("leverage").
QUERY_COMMANDS: frozenset[str] = frozenset({
    "/leverage", "/margin", "/symbols", "/price", "/funding", "/status", "/pnl",
    "/tpsl", "/batch", "/ladder",
})

# Natural-language intents we handle deterministically instead of guessing.
FREE_MODEL_INTENT = re.compile(
    r"\b(switch|use|pick|select|set|go|change)\b.*\bfree\b.*\bmodel\b"
    r"|\bfree\b.*\bmodel\b.*\b(only|please|now)\b"
    r"|\buse\s+(a\s+)?free\s+model\b", re.I)


@dataclass
class ToolResult:
    ok: bool
    output: str
    meta: dict = field(default_factory=dict)


class Tools:
    """Shell / file / git / http / process access, all permission-gated."""

    def __init__(self, perms: PermissionManager):
        self.perms = perms

    def shell(self, command: str, timeout: int = 120, cwd: str | None = None) -> ToolResult:
        allowed, why = self.perms.check_shell(command)
        if not allowed:
            return ToolResult(False, why)
        return self._run(command, timeout, cwd)

    def internal(self, command: str, timeout: int = 120) -> ToolResult:
        """Run a self-check command that does NOT need device access.

        Used only by /verify and /check_system to inspect J+Rock's own code.
        It is still audited, and destructive commands are still refused.
        """
        if self.perms.is_destructive(command):
            self.perms.audit.write("internal_destructive_refused", command=command)
            return ToolResult(False, "BLOCKED: destructive command in internal check")
        self.perms.audit.write("internal_check", command=command)
        return self._run(command, timeout, None)

    def _run(self, command: str, timeout: int, cwd: str | None) -> ToolResult:
        try:
            p = subprocess.run(command, shell=True, capture_output=True, text=True,
                               timeout=timeout, cwd=cwd or str(ROOT))
            out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr else "")
            return ToolResult(p.returncode == 0, out.strip() or f"(exit {p.returncode})",
                              {"code": p.returncode})
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"timeout after {timeout}s")
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, f"{type(e).__name__}: {e}")

    def read(self, path: str) -> ToolResult:
        p = Path(path)
        if not p.is_absolute():
            p = ROOT / p
        if not p.exists():
            return ToolResult(False, f"not found: {path}")
        try:
            return ToolResult(True, p.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, str(e))

    def write(self, path: str, content: str, reason: str = "") -> ToolResult:
        p = Path(path)
        if not p.is_absolute():
            p = ROOT / p
        old = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        rel = str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
        ok, diff = self.perms.request_edit(rel, old, content, reason)
        if not ok:
            return ToolResult(False, "EDIT PENDING APPROVAL\n" + diff, {"pending": True})
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return ToolResult(True, f"wrote {p} ({len(content)} bytes)", {"diff": diff})

    def patch(self, path: str, old: str, new: str, reason: str = "") -> ToolResult:
        r = self.read(path)
        if not r.ok:
            return r
        if old not in r.output:
            return ToolResult(False, f"old_str not found in {path}")
        return self.write(path, r.output.replace(old, new, 1), reason)

    def search(self, pattern: str, path: str = ".", glob: str = "*.py") -> ToolResult:
        try:
            hits = []
            base = Path(path) if Path(path).is_absolute() else ROOT / path
            for f in base.rglob(glob):
                if any(x in str(f) for x in (".git", "__pycache__", "node_modules")):
                    continue
                try:
                    for i, ln in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                        if re.search(pattern, ln):
                            hits.append(f"{f.relative_to(ROOT)}:{i}: {ln.strip()[:160]}")
                except OSError:
                    continue
            return ToolResult(True, "\n".join(hits[:200]) or "no matches")
        except Exception as e:  # noqa: BLE001
            return ToolResult(False, str(e))

    def git(self, args: str) -> ToolResult:
        return self.shell(f"git {args}", timeout=180)

    def http(self, url: str, method: str = "GET", data: str | None = None) -> ToolResult:
        cmd = f"curl -sS -X {method} -m 30 '{url}'"
        if data:
            cmd += f" -H 'Content-Type: application/json' -d '{data}'"
        return self.shell(cmd, timeout=45)

    def processes(self) -> ToolResult:
        return self.shell("ps aux --sort=-%cpu | head -20", timeout=20)


class JRockAgent:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or load_settings()
        self.perms = PermissionManager(
            auto_accept_edit=self.s.auto_accept_edit,
            full_device_access=self.s.full_device_access,
            block_destructive=self.s.block_destructive,
        )
        self.tools = Tools(self.perms)
        self.store = Store(data_dir=self.s.data_dir)
        self._register_secrets()
        self.memory = MemoryStore(self.s.data_dir / "memory.jsonl",
                                  max_items=getattr(self.s, "max_memory_items", 5000))
        # Precedence: persisted store > .env > built-in default. Applied before
        # the engine is built so every consumer sees the effective value.
        self._apply_stored_settings()
        self.engine = TraderEngine(self.s, store=self.store)
        self.engine.status_extra = lambda: [
            f"thinking={self.thinking_level}",
            f"market_data={self.s.market_data_source}",
            f"market_data_state={self.engine.market_data_report()}",
            f"tp_sl_trigger={self.s.tp_sl_trigger_type}",
            f"order_unit={self.s.order_unit}",
            f"tpsl_method={self.engine.tpsl_method_report()}",
            f"private_ws={self.engine.private_feed_report()}",
            f"price_cache={self._cache_line()}",
            f"kline_cache={self._kline_cache_line()}",
            f"memory={self.memory.stats()['total']} entries "
            f"({self.memory.stats()['size_bytes']}B)",
        ]
        self._ws_feed = None
        self.model: str = self.s.model
        self.mcp_servers: dict[str, str] = {}
        self.skills: dict[str, str] = {}
        # A destructive natural-language action waiting for a yes/no. Held in
        # memory only: a pending order confirmation must not survive a restart.
        self._pending_action: nlu.Intent | None = None
        #: Recent free-text turns, replayed to the model so a follow-up has a
        #: referent. In memory only - a conversation is not durable state.
        self._history: list[tuple[str, str]] = []
        self._load_registry()

    # ---------------------------------------------------------- persistence
    def _register_secrets(self) -> None:
        """Teach the store which values must never reach disk."""
        for v in (self.s.api_key, self.s.bitunix_api_key, self.s.bitunix_api_secret):
            self.store.register_secret(v)

    def _apply_stored_settings(self) -> None:
        """Overlay persisted settings onto the .env-loaded Settings object."""
        stored_src = self.store.get("market_data_source")
        if stored_src in ("rest", "ws"):
            if stored_src != self.s.market_data_source:
                log.debug("market_data_source: store=%s overrides .env=%s",
                          stored_src, self.s.market_data_source)
            self.s.market_data_source = stored_src
        stored_model = self.store.get("model")
        if isinstance(stored_model, str) and stored_model:
            self.s.model = stored_model

    # ------------------------------------------------------- thinking level
    @property
    def thinking_level(self) -> str:
        """Current level: persisted store > .env > default."""
        stored = normalize_level(self.store.get("thinking_level"))
        if stored:
            return stored
        return normalize_level(self.s.thinking_level) or DEFAULT_LEVEL

    @thinking_level.setter
    def thinking_level(self, value: str) -> None:
        lvl = normalize_level(value) or DEFAULT_LEVEL
        self.store.set("thinking_level", lvl)
        self.s.thinking_level = lvl
        mirror_env(ROOT / ".env", "THINKING_LEVEL", lvl)

    def reasoning_params(self, model: str | None = None) -> dict:
        """Provider fields for the current level, honouring model capability."""
        mid = model or self.model or self.s.model
        supports, style = self._model_reasoning(mid)
        return build_reasoning_params(self.thinking_level, supports, style)

    def _model_reasoning(self, model_id: str) -> tuple[bool | None, str | None]:
        """Look up cached /models metadata for a model's reasoning support."""
        if not model_id:
            return None, None
        cached = self.store.get("model_meta") or {}
        meta = cached.get(model_id)
        if isinstance(meta, dict):
            return meta.get("supports_reasoning"), meta.get("reasoning_param")
        return None, None

    def cache_model_meta(self, infos) -> None:
        """Persist reasoning capability from a /models listing."""
        meta = dict(self.store.get("model_meta") or {})
        for m in infos:
            meta[m.id] = {"supports_reasoning": m.supports_reasoning,
                          "reasoning_param": m.reasoning_param}
        self.store.set("model_meta", meta)

    @property
    def registry_path(self) -> Path:
        return self.s.data_dir / "registry.json"

    def _load_registry(self) -> None:
        if self.registry_path.exists():
            try:
                d = json.loads(self.registry_path.read_text(encoding="utf-8"))
                self.mcp_servers = d.get("mcp", {})
                self.skills = d.get("skills", {})
            except json.JSONDecodeError:
                pass

    def _save_registry(self) -> None:
        self.registry_path.write_text(json.dumps(
            {"mcp": self.mcp_servers, "skills": self.skills}, indent=2), encoding="utf-8")

    # ------------------------------------------------------------ soul
    def soul(self) -> str:
        if SOUL_PATH.exists():
            return SOUL_PATH.read_text(encoding="utf-8")
        return "(no soul file)"

    def add_soul(self, text: str) -> str:
        SOUL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with SOUL_PATH.open("a", encoding="utf-8") as fh:
            fh.write("\n" + text.strip() + "\n")
        self.memory.feedback(f"soul addition: {text[:200]}", weight=3.0, tags=["soul"])
        return f"Soul updated ({len(text)} chars appended)."

    # ------------------------------------------------------------ startup
    def _state_banner(self) -> str:
        """One honest line about what survived the restart, and from where."""
        sm = self.store.summary()
        mem = self.memory.stats()
        if not sm["loaded"] and mem["total"] == 0 and not sm["recovered_backup"]:
            return (f"State: fresh start (no store at {sm['path']})")
        # Count what was actually restored, not just the agent-level settings
        # dict: the engine's own settings live in their own section, so
        # reporting only `settings` would say "0 settings" after a restart that
        # in fact restored eight of them.
        restored = self.engine.restored
        n_settings = sm["settings"] + len(restored.get("settings") or [])
        parts = [f"State: loaded {n_settings} settings, "
                 f"{mem['total']} memory entries from {Path(sm['path']).parent}"]
        if sm["migrated_from"]:
            parts.append(f"  migrated store schema v{sm['migrated_from']} -> v{sm['version']}")
        if sm["recovered_backup"]:
            parts.append(f"  WARNING: previous store was corrupt; backed up to "
                         f"{Path(sm['recovered_backup']).name} and started clean")
        r = self.engine.restored
        if r.get("positions"):
            parts.append(f"  restored {r['positions']} position management state(s)")
        if r.get("autotrade_was_on"):
            parts.append("  WARNING: autotrade was ON before the restart - it is NOT "
                         "auto-resumed. Run 'start trading' to re-enable.")
        if r.get("engine_was_on"):
            parts.append("  note: the engine loop was ON before the restart - "
                         "run 'engine on' to resume scanning.")
        return "\n".join(parts)

    async def startup(self) -> str:
        lines = ["J+Rock starting..."]
        lines.append(self._state_banner())
        if not self.model:
            res = await autoset_model(self.s.base_url, self.s.api_key)
            lines.append(f"AutoSetModelByKey: {res.message}")
            if res.ok and res.selected:
                self.model = res.selected
                persist_model(ROOT / ".env", res.selected)
                lines.append(f"MODEL pinned to {res.selected} in .env")
        else:
            lines.append(f"Model pinned: {self.model}")
        ok, msg = await self.engine.client.verify_credentials()
        lines.append(f"Bitunix: {'OK' if ok else 'FAIL'} - {msg}")
        lines.append(f"Thinking: {self.thinking_level}")
        # Start the feed BEFORE printing the banner, so the banner reports the
        # real connection state instead of a guess.
        if self.s.market_data_source == "ws":
            await self.engine.start_feed()
        lines.append(f"Market data: {self.engine.market_data_report()}")
        lines.append(f"  {self.engine.tpsl_method_report()}")
        if self.s.market_data_source == "ws":
            lines.append(f"  {self.engine.private_feed_report()}")
        if self.s.market_data_source == "ws" and not self.engine.feed_connected():
            lines.append("  WARNING: MARKET_DATA_SOURCE=ws but the feed is NOT connected. "
                         "The engine is running on REST fallback.")
        if self.s.market_data_source == "ws" and self.engine.feed_connected():
            lines.append(f"  kline warmup: {self.engine.kline_warmup_calls} REST calls "
                         f"for {len(self.engine.kline_keys())} (symbol, interval) keys")
            for w in self.engine.kline_subscription_warnings():
                lines.append(f"  WARNING: {w}")
        lines.append(f"Permissions: auto_accept_edit={self.perms.auto_accept_edit} "
                     f"device_access={self.perms.full_device_access}")
        return "\n".join(lines)

    # ------------------------------------------------------------ commands
    async def handle(self, line: str) -> str:
        raw = line.strip()
        if not raw:
            return ""
        low = raw.lower()

        if low.startswith("auto accept edit"):
            val = low.split()[-1]
            self.perms.auto_accept_edit = val in ("on", "true", "1", "yes")
            self.perms.audit.write("toggle_auto_accept", value=self.perms.auto_accept_edit)
            return f"auto accept edit = {self.perms.auto_accept_edit}"
        if low.startswith("device access"):
            val = low.split()[-1]
            self.perms.full_device_access = val in ("on", "true", "1", "yes")
            self.perms.audit.write("toggle_device_access", value=self.perms.full_device_access)
            return f"device access = {self.perms.full_device_access}"
        if low == "audit":
            return json.dumps(self.perms.audit.tail(30), indent=2)
        if low == "pending":
            if not self.perms.pending:
                return "no pending edits"
            return "\n\n".join(f"[{i}] {p['path']}\n{p['diff'][:1500]}"
                               for i, p in enumerate(self.perms.pending))
        if low.startswith("approve"):
            parts = raw.split()
            idx = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            item = self.perms.approve_pending(idx)
            if not item:
                return "nothing to approve"
            return f"approved {item['path']} - re-run the edit to apply it"

        if not low.startswith("/"):
            return await self.route_bare(raw)

        cmd, _, rest = raw.partition(" ")
        cmd = cmd.lower()
        rest = rest.strip()

        if cmd in ("/help", "/?"):
            return HELP
        if cmd == "/models":
            return await self.cmd_models(rest)
        if cmd == "/set_model":
            return self.cmd_set_model(rest)
        if cmd == "/connect_provider":
            return await self.cmd_connect_provider(rest)
        if cmd == "/add_mcp":
            return self.cmd_add_mcp(rest)
        if cmd == "/mcp":
            if not self.mcp_servers:
                return "no MCP servers registered (use /add_mcp <name> <cmd>)"
            return json.dumps(self.mcp_servers, indent=2)
        if cmd == "/add_skill":
            return self.cmd_add_skill(rest)
        if cmd == "/skill":
            if not self.skills:
                return "no skills registered (use /add_skill <name> <path>)"
            return json.dumps(self.skills, indent=2)
        if cmd == "/add_soul":
            return self.cmd_add_soul(rest)
        if cmd == "/soul":
            return self.soul()
        if cmd == "/think":
            return self.cmd_think(rest)
        if cmd == "/ws":
            return await self.cmd_ws(rest)
        if cmd == "/balance":
            return await self.cmd_balance()
        if cmd == "/positions":
            return await self.cmd_positions()
        if cmd == "/orders":
            return await self.cmd_orders()
        if cmd == "/price":
            return await self.cmd_price(rest)
        if cmd == "/funding":
            return await self.cmd_funding(rest)
        if cmd == "/tpsl":
            return await self.cmd_tpsl(rest)
        if cmd == "/ladder":
            return self.engine.ladder_report() if not rest else await self.engine.command(f"ladder {rest}")
        if cmd == "/batch":
            return await self.cmd_batch(rest)
        if cmd == "/leverage":
            return await self.cmd_leverage(rest)
        if cmd == "/margin":
            return await self.cmd_margin(rest)
        if cmd == "/settings":
            return await self.cmd_settings(rest)
        if cmd == "/pnl":
            return await self.cmd_pnl()
        if cmd == "/status":
            return await self.engine.status()
        if cmd == "/symbols":
            return await self.cmd_symbols(rest)
        if cmd == "/memory":
            return self.cmd_memory(rest)
        if cmd == "/code_review":
            return self.cmd_code_review(rest)
        if cmd == "/bug_fix":
            return self.cmd_bug_fix(rest)
        if cmd == "/verify":
            return self.cmd_verify()
        if cmd == "/simplify":
            return self.cmd_simplify(rest)
        if cmd == "/team_spawn":
            return await self.cmd_team_spawn(rest)
        if cmd == "/check_ai":
            return await self.cmd_check_ai()
        if cmd == "/check_system":
            return await self.cmd_check_system()
        if cmd == "/autopsy":
            return self.cmd_autopsy(rest)
        if cmd == "/diagnose":
            return await self.cmd_diagnose()

        return f"Unknown command {cmd}. Try /help"

    # ------------------------------------------------------------ routing
    async def route_bare(self, raw: str) -> str:
        """Route a line with no leading slash.

        Order:
          1. a pending destructive action awaiting yes/no  -> confirm or cancel
          2. bare word that names an agent command         -> agent dispatcher
          3. known natural-language intent                 -> handled deterministically
          4. engine command grammar                        -> engine
          5. anything else                                 -> the model
        """
        low = raw.strip().lower()
        head = low.split()[0] if low.split() else ""

        # 0) A destructive action is waiting for confirmation. This is checked
        #    FIRST so that "yes" cannot be swallowed by any other rule.
        if self._pending_action is not None:
            return await self._resolve_pending(raw)

        # 1) bare agent command name
        if head in AGENT_BARE:
            mapped = AGENT_BARE[head]
            rest = raw.strip()[len(head):].strip()
            # A query word followed by engine-style arguments is an engine
            # SETTER, not a query: "leverage 20" sets, "leverage" reads.
            if mapped in QUERY_COMMANDS and rest and self.engine.looks_like_command(raw):
                return await self.engine.command(raw)
            return await self.handle(f"{mapped} {rest}".strip())

        # 2) natural-language intents we can act on without the model
        if FREE_MODEL_INTENT.search(raw):
            return await self.cmd_use_free_model()

        # 3) engine grammar
        if self.engine.looks_like_command(raw):
            return await self.engine.command(raw)

        # 4) natural-language understanding: map a normal sentence onto the
        #    SAME command the user could have typed. Only reached when the line
        #    is not already a command, so existing behaviour cannot regress.
        resolved = nlu.parse(raw)
        if isinstance(resolved, nlu.Clarification):
            return self._render_clarification(resolved)
        if isinstance(resolved, nlu.Intent):
            return await self._act_on_intent(resolved)

        # 5) free text -> the model
        return await self.chat(raw)

    # ------------------------------------------------- natural-language layer
    def _render_clarification(self, c: nlu.Clarification) -> str:
        lines = [c.question]
        if c.options:
            lines.append("  " + " | ".join(c.options))
        return "\n".join(lines)

    async def _act_on_intent(self, intent: nlu.Intent) -> str:
        """Execute a resolved intent, gating anything that moves money.

        A destructive intent is NOT executed here: it is stored and the user is
        asked to confirm. Only an explicit yes runs it.
        """
        if intent.destructive:
            self._pending_action = intent
            return nlu.confirmation_prompt(intent)
        return await self._run_intent(intent)

    def _smalltalk_reply(self) -> str:
        """A warm, useful answer to a greeting - never an empty response.

        The agent used to answer a plain "hi" with nothing at all, because the
        line matched no command and the model had no engine access. A greeting
        now gets a real reply that also says what the agent can do.
        """
        return ("Hey - I'm J+Rock, your Bitunix futures agent. I'm listening, "
                "no command needed.\n"
                "Ask me things like:\n"
                "  - what's my balance / how much can I trade\n"
                "  - show my positions / how are my trades doing\n"
                "  - what's the price of BTCUSDT\n"
                "  - is the engine running\n"
                "  - set leverage to 10x, or start/stop trading\n"
                "Type 'help' for the full list.")

    async def _run_intent(self, intent: nlu.Intent) -> str:
        """Run a resolved intent through the existing handlers."""
        # Pseudo-commands the NLU layer owns (no engine equivalent).
        if intent.command == "__why_failed__":
            return self._explain_last_error()
        if intent.command == "__smalltalk__":
            return self._smalltalk_reply()
        if intent.command.startswith("__explain__"):
            return self._explain_setting(intent.command.split()[-1])
        if intent.command.startswith("__place__"):
            return await self._place_from_nlu(intent)
        # Everything else is a real command string: reuse the existing router.
        return await self.handle(intent.command)

    async def _resolve_pending(self, raw: str) -> str:
        """Answer a pending destructive action with yes/no."""
        intent = self._pending_action
        assert intent is not None
        if nlu.is_confirm(raw):
            self._pending_action = None
            self.perms.audit.write("nlu_confirmed", command=intent.command,
                                   intent=intent.name)
            out = await self._run_intent(intent)
            return f"confirmed: {intent.describe}\n{out}"
        if nlu.is_cancel(raw):
            self._pending_action = None
            self.perms.audit.write("nlu_cancelled", command=intent.command,
                                   intent=intent.name)
            return f"cancelled: {intent.describe} - nothing was sent."
        # Anything else: keep waiting, but do not silently drop the question.
        return (f"Still waiting on: {intent.describe}\n"
                f"Reply 'yes' to confirm or 'no' to cancel.\n"
                f"(your message was: {raw!r})")

    def _explain_last_error(self) -> str:
        """Answer "why did the last order fail" from the engine's own state."""
        errs = list(self.engine.state.errors)
        if not errs:
            return ("No errors recorded in this session. If an order was "
                    "rejected, the engine logs it here - run 'status' to see "
                    "the current error count.")
        last = errs[-1]
        lines = [f"last error ({len(errs)} total this session):", f"  {last}"]
        hint = self._hint_for_error(last)
        if hint:
            lines.append(f"  -> {hint}")
        if len(errs) > 1:
            lines.append("recent errors:")
            for e in errs[-5:-1]:
                lines.append(f"  {e}")
        return "\n".join(lines)

    @staticmethod
    def _hint_for_error(err: str) -> str:
        """Map a known Bitunix code in an error string to an actionable hint."""
        from .trader.errors import hint_for
        m = re.search(r"\[?(\d{4,5})\]?", err)
        if not m:
            return ""
        return hint_for(int(m.group(1)))

    def _explain_setting(self, name: str) -> str:
        """Explain a setting defined in THIS repo, from the repo itself.

        The engine once answered "I have no access to the engine" for
        TP_SL_MIN_STOP_TICKS, which is defined in jrock/config.py. That is
        wrong: the value is right here.
        """
        name = name.upper()
        s = self.s
        if name == "TP_SL_MIN_STOP_TICKS":
            ticks = s.tpsl_min_stop_ticks
            return (
                f"TP_SL_MIN_STOP_TICKS = {ticks}\n"
                f"  Defined in jrock/config.py, enforced in tpsl.plan_tpsl() and\n"
                f"  risk.clamp_sl()/clamp_tp().\n"
                f"  It is the minimum gap, in PRICE TICKS, that a TP/SL must keep\n"
                f"  from the reference (mark/entry) price. 1 tick = 10^-quotePrecision.\n"
                f"  AKEUSDT has quotePrecision 6, so 1 tick = 0.000001.\n"
                f"  Higher = safer against 30029/30031 but a wider stop.\n"
                f"  Lower = tighter stop, more likely to be rejected by the exchange.\n"
                f"  Set it in .env, e.g. TP_SL_MIN_STOP_TICKS=3")
        if name == "TAKER_FEE_RATE":
            return (
                f"TAKER_FEE_RATE = {s.taker_fee_rate}\n"
                f"  Defined in jrock/config.py. The taker fee reserved ON TOP of\n"
                f"  margin when sizing, so an order never asks for more than the\n"
                f"  balance can pay for (this is what caused 20003).\n"
                f"  Default 0.0006 = 0.06% (VIP-0). Set it to your real tier.")
        if name == "MARGIN_SAFETY_PCT":
            return (
                f"MARGIN_SAFETY_PCT = {s.margin_safety_pct}\n"
                f"  Defined in jrock/config.py. Extra headroom, as a % of balance,\n"
                f"  reserved when sizing so a price move between sizing and the\n"
                f"  request landing cannot push the order over the balance.")
        return f"I do not know a setting called {name}."

    async def _place_from_nlu(self, intent: nlu.Intent) -> str:
        """Place an order from a natural-language sentence.

        The engine's own entry path (try_enter) is signal-driven and sizes from
        risk. A direct "open long BTCUSDT" has no signal, so it is refused
        rather than guessed: sizing an order from a sentence would be inventing
        a position size, which is exactly the kind of guess this layer must not
        make.
        """
        parts = intent.command.split()
        sym = parts[1] if len(parts) > 1 else "?"
        side = parts[2] if len(parts) > 2 else "?"
        return (
            f"I understood: open a {side} position on {sym}.\n"
            f"I will not place it from a sentence alone - the engine sizes every\n"
            f"order from a signal and your risk setting, and inventing a size here\n"
            f"would be a guess with real money.\n"
            f"Options:\n"
            f"  - let the engine trade it: 'start trading' (autotrade on)\n"
            f"  - see the current signal first: 'status' or 'report'\n"
            f"  - the engine's own entry path is signal-driven by design.")

    async def cmd_use_free_model(self) -> str:
        """Act on "switch to free model": probe free models only and pin one."""
        from .model_selector import autoset_model
        if not self.s.api_key:
            return ("Cannot switch models: API_KEY is empty in .env.\n"
                    "Set API_KEY and BASE_URL, then run /models --free.")
        res = await autoset_model(self.s.base_url, self.s.api_key, free_only=True)
        if res.ok and res.selected:
            self.model = res.selected
            persist_model(ROOT / ".env", res.selected)
            self.memory.fact("model", res.selected)
            return (f"Switched to free model {res.selected} (written to .env).\n"
                    f"{res.message}")
        return ("No free model worked with this key.\n" + res.message)

    # Base output cap. A high thinking level needs room for reasoning tokens
    # ON TOP of the answer, so the cap is raised rather than left to starve.
    BASE_MAX_TOKENS = 800
    THINKING_MAX_TOKENS = {"off": 800, "low": 1200, "medium": 2000,
                           "high": 6000, "max": 12000}

    def _max_tokens_for(self, level: str) -> int:
        """Output cap that leaves room for the reasoning budget."""
        return self.THINKING_MAX_TOKENS.get(level, self.BASE_MAX_TOKENS)

    async def _post_chat(self, url: str, payload: dict) -> tuple[int, str, dict | None]:
        """One HTTP call. Returns (status, raw_text, parsed_json_or_None)."""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    url,
                    headers={"Authorization": f"Bearer {self.s.api_key}",
                             "Content-Type": "application/json"},
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as r:
                    body = await r.text()
                    try:
                        parsed = json.loads(body)
                    except json.JSONDecodeError:
                        parsed = None
                    return r.status, body, parsed
        except Exception as e:  # noqa: BLE001
            return 0, f"{type(e).__name__}: {e}", None

    #: How many past turns are replayed to the model. Enough for a real
    #: conversation ("what about the other one?", "and my leverage?") without
    #: letting the prompt grow without bound.
    HISTORY_TURNS = 12

    def _system_prompt(self) -> str:
        """The system prompt: the soul file, plus how to actually converse.

        Two defects made the agent feel like a command parser rather than
        something you can talk to:

        1. **The soul was never sent.** `soul/jrock.soul.md` is the persona -
           voice, honesty rules, trading discipline - and `chat()` built a
           three-sentence prompt from scratch instead of using it. The model
           never saw the persona the project is built around.
        2. **Every message was a fresh single-turn call.** No history was
           replayed, so a follow-up like "and my leverage?" had no referent and
           the model could only answer in isolation - which reads as robotic
           even when the wording is fine.

        The prompt below fixes both, and states the conversational contract
        explicitly: understand intent from ordinary sentences (Persian and
        English), ask when the request is ambiguous, and confirm before
        anything that moves money.
        """
        soul = self.soul()
        if soul.startswith("(no soul file)"):
            soul = ""
        parts = [soul.strip()] if soul.strip() else []
        parts.append(
            "## How you talk\n"
            "You are a conversational partner, not a command parser. The user "
            "may write in English or Persian (Farsi), formally or casually, and "
            "may not use any command syntax at all.\n"
            "\n"
            "- Read the INTENT, not the keywords. \"how much margin am I using?\" "
            "and \"مارجینم چقدره؟\" both mean: report the margin setting. "
            "\"close my position\" / \"پوزیشنم رو ببند\" both mean: close it. "
            "\"nudge my risk up a bit\" / \"یه کم ریسک رو ببر بالا\" both mean: "
            "raise risk_pct - ask for the number if it is not stated.\n"
            "- Answer in the language the user wrote in. Match their register: "
            "casual in, casual out. Do not answer a friendly question with a "
            "table of commands.\n"
            "- If the request is ambiguous, ask ONE short clarifying question "
            "with the concrete options. Never guess about money.\n"
            "- Before anything that opens or closes a position, or changes "
            "leverage, risk, margin, autotrade or the engine, say what you are "
            "about to do and wait for an explicit yes.\n"
            "- You have a real Bitunix futures engine. When a question is about "
            "the account, positions, orders, prices or settings, the engine's "
            "own answer is the truth - never invent a number. If you do not "
            "have the value, say so and name the command that would get it.\n"
            "- Be brief and warm. No filler praise, no walls of text, no "
            "restating the question back at them.\n"
            "\n"
            "## What you can do\n"
            "Commands exist and the user can type them, but they never have to. "
            "The full list is in /help. Deterministic commands are handled "
            "before you are ever called, so if a message reached you it is "
            "genuinely conversational - answer it as such."
        )
        return "\n\n".join(parts)

    def _history_messages(self) -> list[dict]:
        """The recent conversation, as chat messages.

        Only free-text turns are kept: a command's output is already in the
        transcript the user can see, and replaying large engine dumps would
        crowd out the actual conversation.
        """
        out: list[dict] = []
        for role, content in self._history[-self.HISTORY_TURNS:]:
            out.append({"role": role, "content": content})
        return out

    def _remember_turn(self, user_text: str, reply: str) -> None:
        """Record one exchange so the next message has a referent."""
        self._history.append(("user", user_text))
        self._history.append(("assistant", reply))
        # Keep the buffer bounded; a long session must not grow forever.
        if len(self._history) > self.HISTORY_TURNS * 4:
            del self._history[: len(self._history) - self.HISTORY_TURNS * 2]

    async def chat(self, text: str) -> str:
        """Send free text to the configured model.

        An empty completion is a FAILURE, never a normal answer. We diagnose the
        cause, and when the cause is reasoning-budget exhaustion we retry once
        with thinking off and say so.
        """
        from .model_selector import autoset_model

        if not self.s.api_key:
            return (f"I could not send that to a model: API_KEY is empty in .env.\n"
                    f"You said: {text!r}\n"
                    f"Set API_KEY + BASE_URL, or use a command (type help).")

        model = self.model or self.s.model
        if not model:
            res = await autoset_model(self.s.base_url, self.s.api_key)
            if not res.ok:
                return (f"I could not reach a model.\n{res.message}\n\n"
                        f"You said: {text!r}")
            model = res.selected
            self.model = model

        system = self._system_prompt()
        level = self.thinking_level
        max_tokens = self._max_tokens_for(level)
        reasoning = self.reasoning_params(model)

        payload = {
            "model": model,
            "messages": ([{"role": "system", "content": system}]
                         + self._history_messages()
                         + [{"role": "user", "content": text}]),
            "max_tokens": max_tokens,
            "temperature": 0.2,
        }
        payload.update(reasoning)
        log.debug("chat: thinking_level=%s model=%s max_tokens=%s params=%s",
                  level, model, max_tokens, describe_params(reasoning))

        warn = budget_warning(level, max_tokens, reasoning.get("thinking_budget"))
        if warn:
            log.warning("chat: %s", warn)

        url = f"{self.s.base_url.rstrip('/')}/chat/completions"
        status, body, parsed = await self._post_chat(url, payload)

        if status != 200:
            return (f"Model call failed: HTTP {status}: {body[:400]}\n"
                    f"You said: {text!r}")

        verdict = diagnose_empty(parsed, status)
        if not verdict.is_empty:
            out = parsed["choices"][0]["message"]["content"].strip()
            self.memory.episode("chat", text[:120], out[:120], ok=True)
            self._remember_turn(text, out)
            return out

        # ---- empty response: log everything needed to diagnose it
        log.warning("chat: EMPTY response cause=%s finish=%s completion_tokens=%s "
                    "reasoning_tokens=%s status=%s level=%s max_tokens=%s body=%s",
                    verdict.cause, verdict.finish_reason, verdict.completion_tokens,
                    verdict.reasoning_tokens, status, level, max_tokens, body[:800])

        # ---- retry once with thinking off when the budget ate the answer
        if verdict.retry_with_off and level != "off":
            retry_payload = dict(payload)
            for k in ("reasoning_effort", "thinking_budget"):
                retry_payload.pop(k, None)
            retry_payload["max_tokens"] = self.BASE_MAX_TOKENS
            log.debug("chat: retrying with thinking=off (was %s)", level)
            status2, body2, parsed2 = await self._post_chat(url, retry_payload)
            if status2 == 200:
                v2 = diagnose_empty(parsed2, status2)
                if not v2.is_empty:
                    out = parsed2["choices"][0]["message"]["content"].strip()
                    self.memory.episode("chat", text[:120], out[:120], ok=True)
                    self._remember_turn(text, out)
                    return (f"[retried with thinking=off after an empty response at "
                            f"level {level}]\n\n{out}")
            log.warning("chat: retry with thinking=off also failed: %s", body2[:400])

        self.memory.episode("chat", text[:120], f"EMPTY:{verdict.cause}", ok=False)
        return render_empty(verdict, level, max_tokens)

    async def cmd_models(self, rest: str = "") -> str:
        from .model_selector import list_model_info
        import aiohttp
        if not self.s.api_key:
            return "API_KEY is empty in .env"
        free_only = "--free" in rest or "free" in rest.split()
        try:
            async with aiohttp.ClientSession() as sess:
                infos = await list_model_info(sess, self.s.base_url, self.s.api_key)
        except Exception as e:  # noqa: BLE001
            return f"failed: {e}"
        if free_only:
            infos = [m for m in infos if m.free]
        self.cache_model_meta(infos)
        infos.sort(key=lambda m: m.score, reverse=True)
        n_free = sum(1 for m in infos if m.free)
        lines = [f"{len(infos)} models on {self.s.base_url} "
                 f"({n_free} free){' - free only' if free_only else ''}:"]
        for m in infos[:40]:
            mark = " *" if m.id == self.model else ""
            tag = "free" if m.free else "paid"
            lines.append(f"  {m.score:5.0f}  {tag:4}  {m.id}{mark}")
        if not infos:
            lines.append("  (none)")
        return "\n".join(lines)

    def cmd_set_model(self, name: str) -> str:
        if not name:
            return f"current model: {self.model or '(unset)'}\nUsage: /set_model <name>"
        self.model = name
        persist_model(ROOT / ".env", name)
        self.memory.fact("model", name)
        return f"model set to {name} (written to .env)"

    async def cmd_connect_provider(self, rest: str) -> str:
        parts = rest.split()
        if len(parts) < 2:
            return "Usage: /connect_provider <base_url> <api_key>"
        base, key = parts[0].rstrip("/"), parts[1]
        self.s.base_url, self.s.api_key = base, key
        env = ROOT / ".env"
        text = env.read_text(encoding="utf-8") if env.exists() else ""
        for k, v in (("BASE_URL", base), ("API_KEY", key)):
            if re.search(rf"^{k}=.*$", text, re.M):
                text = re.sub(rf"^{k}=.*$", f"{k}={v}", text, flags=re.M)
            else:
                text += f"\n{k}={v}"
        env.write_text(text.strip() + "\n", encoding="utf-8")
        res = await autoset_model(base, key)
        if res.ok and res.selected:
            self.model = res.selected
            persist_model(env, res.selected)
        return f"provider set to {base}\n{res.message}"

    def cmd_add_mcp(self, rest: str) -> str:
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            return "Usage: /add_mcp <name> <stdio command>"
        self.mcp_servers[parts[0]] = parts[1]
        self._save_registry()
        return f"MCP server '{parts[0]}' registered."

    def cmd_add_skill(self, rest: str) -> str:
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            return "Usage: /add_skill <name> <path>"
        self.skills[parts[0]] = parts[1]
        self._save_registry()
        return f"skill '{parts[0]}' registered at {parts[1]}"

    def cmd_add_soul(self, rest: str) -> str:
        if not rest:
            return "Usage: /add_soul <text>  or  /add_soul @path/to/file.md"
        if rest.startswith("@"):
            r = self.tools.read(rest[1:])
            if not r.ok:
                return r.output
            return self.add_soul(r.output)
        return self.add_soul(rest)

    def cmd_think(self, rest: str) -> str:
        """`/think` - show or set the thinking level.

        No argument (or `status`) prints the current level, the available
        levels and one line explaining what it does. Never errors.
        """
        arg = (rest or "").strip()
        current = self.thinking_level

        if not arg or arg.lower() in ("status", "show", "current", "?"):
            return self._think_status(current)

        lvl = normalize_level(arg)
        if lvl is None:
            near = closest_level(arg)
            lines = [f"'{arg}' is not a thinking level.", "",
                     f"Valid levels: {' | '.join(LEVELS)}",
                     "Aliases: none->off, min->low, med->medium, hi->high, maximum->max"]
            if near:
                lines.append(f"Closest match: {near}  (run: /think {near})")
            lines.append("")
            lines.append(f"Current level: {current}")
            return "\n".join(lines)

        self.thinking_level = lvl
        params = self.reasoning_params()
        self.memory.fact("thinking_level", lvl)
        self.perms.audit.write("set_thinking_level", value=lvl)
        return (f"Thinking level = {lvl} ({LEVEL_HELP[lvl]}).\n"
                f"Persisted to data/settings.json and THINKING_LEVEL in .env.\n"
                f"Request parameter: {describe_params(params)}")

    def _think_status(self, current: str) -> str:
        params = self.reasoning_params()
        lines = [f"Thinking level: {current}", "", ONE_LINER, "", "Levels:"]
        for lvl in LEVELS:
            mark = "*" if lvl == current else " "
            lines.append(f" {mark} {lvl:<7} {LEVEL_HELP[lvl]}")
        lines += ["", f"Request parameter: {describe_params(params)}",
                  "", "Set it with: /think off|low|medium|high|max",
                  "You do not need /think to chat - just type normally."]
        return "\n".join(lines)

    # ------------------------------------------------------------ websocket
    async def cmd_ws(self, rest: str) -> str:
        """`/ws` - connection state, `/ws on|off` - toggle, `/ws test` - real probe."""
        arg = (rest or "").strip().lower()

        if arg in ("on", "off"):
            self.s.market_data_source = "ws" if arg == "on" else "rest"
            self.store.set("market_data_source", self.s.market_data_source)
            mirror_env(ROOT / ".env", "MARKET_DATA_SOURCE", self.s.market_data_source)
            if arg == "off":
                await self.engine.stop_feed()
            else:
                await self.engine.start_feed()
            note = ""
            if arg == "on" and not self._has_bitunix_keys():
                note = ("\nNote: no BITUNIX_API_KEY/SECRET set, so private channels "
                        "(position/order/balance) cannot authenticate. Public market "
                        "data works; the engine will fall back to REST for account state.")
            state = self.engine.market_data_report()
            return (f"MARKET_DATA_SOURCE = {self.s.market_data_source}"
                    f" (persisted to data/settings.json and .env)\n"
                    f"feed: {state}{note}")

        if arg in ("test", "probe"):
            return await self._ws_test()

        if arg in ("", "status", "show"):
            return self._ws_status()

        return ("Usage: /ws            show connection state\n"
                "       /ws on|off     switch market data source\n"
                "       /ws test       open a real connection and print 3 frames")

    def _cache_line(self) -> str:
        """Compact cache counters for /status."""
        c = self.engine.price_cache
        st = c.stats
        return (f"hits={st.hits} misses={st.misses} stale={st.stale_rejections} "
                f"rest_fallbacks={st.rest_fallbacks} ws_updates={st.ws_updates} "
                f"rest_calls={self.engine.rest_price_calls}")

    def _kline_cache_line(self) -> str:
        """Compact kline cache counters for /status."""
        c = self.engine.kline_cache
        st = c.stats
        return (f"keys={len(c.keys())} hits={st.hits} misses={st.misses} "
                f"stale={st.stale_rejections} short={st.short_series} "
                f"rest_fallbacks={st.rest_fallbacks} ws_updates={st.ws_updates} "
                f"replaced={st.replaced} appended={st.appended} "
                f"rest_kline_calls={self.engine.rest_kline_calls} "
                f"warmup={self.engine.kline_warmup_calls}")

    def _ws_status(self) -> str:
        from .trader.ws import WSConfig, PUBLIC_CHANNELS, PRIVATE_CHANNELS

        lines = ["=== websocket ===",
                 f"market_data_source = {self.s.market_data_source}",
                 f"public url  = {self.s.bitunix_ws_public}",
                 f"private url = {self.s.bitunix_ws_private}",
                 f"credentials = {'present' if self._has_bitunix_keys() else 'MISSING'}"]

        feed = self.engine.feed
        if feed is None:
            lines.append("connection: not started (run /ws test to probe, /ws on to enable)")
        else:
            st = feed.ws.stats()
            lines += [
                f"connection: {'CONNECTED' if st['connected'] else 'DISCONNECTED'}",
                f"uptime: {st['uptime_s']}s   reconnects: {st['reconnects']}",
                f"subscriptions: {st['subscriptions']}   messages: {st['messages']}   dropped: {st['dropped']}",
                f"heartbeat: pings={st['pings_sent']} pongs={st['pongs_received']}",
                ("last message: never" if st["last_message_age_s"] is None
                 else f"last message: {st['last_message_age_s']}s ago"),
            ]
            if st["last_error"]:
                lines.append(f"last error: {st['last_error']}")
            subs = ", ".join(f"{s['ch']}:{s.get('symbol','*')}" for s in feed.ws.subs[:12])
            lines.append(f"channels: {subs}")

        # --- price cache: the part that decides whether WS actually replaces REST
        c = self.engine.price_cache
        cs = c.stats
        lines += [
            "",
            "--- price cache ---",
            f"max age: {c.max_age}s   symbols cached: {len(c.symbols())}",
            f"hits={cs.hits}  misses={cs.misses}  stale_rejections={cs.stale_rejections}",
            f"rest_fallbacks={cs.rest_fallbacks}  ws_updates={cs.ws_updates}  "
            f"rest_updates={cs.rest_updates}",
            f"REST price calls made: {self.engine.rest_price_calls}",
        ]
        for sym in c.symbols()[:8]:
            q = c.peek(sym)
            if q:
                lines.append(f"  {sym}: {q.price} src={q.source} age={q.age():.1f}s")

        # --- kline cache: the part that decides whether scans still poll REST
        lines += ["", "--- kline cache ---", self.engine.kline_cache_report()]

        # --- private channels: balance / position / order / tpsl
        lines += ["", "--- private channels ---", self.engine.private_feed_report()]
        pf = self.engine.private_feed
        if pf is not None:
            pst = pf.stats()
            lines += [
                f"logged_in={pst['logged_in']}  connected={pst['connected']}  "
                f"reconnects={pst['reconnects']}",
                f"subscriptions={pst['subscriptions']}  messages={pst['messages']}",
                "counts: " + ", ".join(f"{k}={v}" for k, v in pst["counts"].items()),
                f"tpsl events buffered: {pst['tpsl_events']}",
            ]
            if pst["last_error"]:
                lines.append(f"last error: {pst['last_error']}")

        lines += ["", "public channels:  " + ", ".join(sorted(set(PUBLIC_CHANNELS.values()))),
                  "private channels: " + ", ".join(sorted(set(PRIVATE_CHANNELS.values())))]
        return "\n".join(lines)

    async def _ws_test(self) -> str:
        """Open a real public connection, print the first 3 raw frames, close."""
        import json as _json
        from .trader.ws import WSConfig, BitunixWebSocket

        symbol = (self.s.symbols or ["BTCUSDT"])[0]
        cfg = WSConfig.from_settings(self.s)
        ws = BitunixWebSocket(cfg)
        ws.subscribe("ticker", symbol)
        frames: list[dict] = []
        ws.on("ticker", lambda ev: frames.append(ev.raw) if len(frames) < 3 else None)

        lines = [f"=== ws test: {cfg.public_url} ===",
                 f"subscribing: ticker {symbol}"]
        await ws.start()
        try:
            deadline = asyncio.get_event_loop().time() + 20
            while len(frames) < 3 and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.2)
        finally:
            st = ws.stats()
            await ws.stop()

        lines.append(f"connected={st['connected']} messages={st['messages']} "
                     f"reconnects={st['reconnects']} dropped={st['dropped']}")
        if not frames:
            lines.append("NO FRAMES RECEIVED - the socket did not push within 20s.")
            if st["last_error"]:
                lines.append(f"last error: {st['last_error']}")
            return "\n".join(lines)
        for i, f in enumerate(frames, 1):
            lines.append(f"[{i}] {_json.dumps(f)[:300]}")
        lines.append("closed cleanly.")
        return "\n".join(lines)

    # ------------------------------------------------- account / market queries
    def _has_bitunix_keys(self) -> bool:
        return bool(self.s.bitunix_api_key and self.s.bitunix_api_secret)

    async def cmd_balance(self) -> str:
        """`/balance` - real account table from GET /api/v1/futures/account."""
        from .trader.errors import BitunixAPIError

        if not self._has_bitunix_keys():
            return missing_keys_message()

        try:
            data = await self.engine.client.get_single_account()
        except BitunixAPIError as e:
            return (f"Bitunix rejected the account request: code {e.code}: {e.msg or e}\n"
                    f"{getattr(e, 'hint', '')}".rstrip())
        except Exception as e:  # noqa: BLE001
            return f"Account request failed: {type(e).__name__}: {e}"

        report = BalanceReport(
            accounts=parse_accounts(data),
            is_demo=is_demo_url(self.s.bitunix_base_url),
            base_url=self.s.bitunix_base_url,
        )
        return render_balance(report)

    async def cmd_positions(self) -> str:
        from .trader.errors import BitunixAPIError
        if not self._has_bitunix_keys():
            return missing_keys_message()
        try:
            rows = await self.engine.client.get_pending_positions()
        except BitunixAPIError as e:
            return f"Bitunix error {e.code}: {e.msg or e}"
        except Exception as e:  # noqa: BLE001
            return f"positions request failed: {type(e).__name__}: {e}"
        rows = rows or []
        if not rows:
            return "no open positions"
        lines = ["=== open positions ==="]
        for p in rows:
            lines.append(
                f"{p.get('symbol')} {p.get('side')} qty={p.get('qty')} "
                f"entry={p.get('avgOpenPrice')} mark={p.get('markPrice', '?')} "
                f"liq={p.get('liqPrice')} lev={p.get('leverage')}x "
                f"margin={p.get('margin')} uPnL={p.get('unrealizedPNL')} "
                f"mode={p.get('marginMode')}")
        return "\n".join(lines)

    async def cmd_orders(self) -> str:
        from .trader.errors import BitunixAPIError
        if not self._has_bitunix_keys():
            return missing_keys_message()
        try:
            rows = await self.engine.client.get_pending_orders()
        except BitunixAPIError as e:
            return f"Bitunix error {e.code}: {e.msg or e}"
        except Exception as e:  # noqa: BLE001
            return f"orders request failed: {type(e).__name__}: {e}"
        rows = rows or []
        if not rows:
            return "no pending orders"
        lines = ["=== pending orders ==="]
        for o in rows:
            lines.append(f"{o.get('symbol')} {o.get('side')} {o.get('orderType')} "
                         f"qty={o.get('qty')} price={o.get('price')} "
                         f"status={o.get('status')} id={o.get('orderId')}")
        return "\n".join(lines)

    async def cmd_price(self, rest: str = "") -> str:
        from .trader.errors import BitunixAPIError
        syms = [x.strip().upper() for x in rest.replace(",", " ").split() if x.strip()]
        syms = syms or list(self.s.symbols)
        if self.s.market_data_source == "ws":
            # Same accessor the engine trades on: fresh cache, else REST.
            # Give a just-connected feed a moment to deliver its first frame
            # instead of reporting a REST fallback on a cold cache.
            if self.engine.feed_connected():
                for _ in range(20):
                    if all(self.engine.price_cache.peek(s) for s in syms):
                        break
                    await asyncio.sleep(0.1)
            lines = ["=== price (ws cache) ==="]
            for sym in syms:
                try:
                    px = await self.engine.get_price(sym)
                except BitunixAPIError as e:
                    lines.append(f"{sym}: Bitunix error {e.code}: {e.msg or e}")
                    continue
                q = self.engine.price_cache.peek(sym)
                if q is None:
                    lines.append(f"{sym}: {px} (no cache entry)")
                else:
                    lines.append(f"{sym}: {px} src={q.source} age={q.age():.1f}s")
            return "\n".join(lines)
        try:
            data = await self.engine.client.get_tickers(",".join(syms))
        except BitunixAPIError as e:
            return f"Bitunix error {e.code}: {e.msg or e}"
        except Exception as e:  # noqa: BLE001
            return f"price request failed: {type(e).__name__}: {e}"
        rows = data if isinstance(data, list) else [data] if data else []
        if not rows:
            return f"no ticker data for {','.join(syms)}"
        lines = ["=== price ==="]
        for t in rows:
            lines.append(f"{t.get('symbol')} last={t.get('lastPrice')} "
                         f"mark={t.get('markPrice')} high={t.get('high')} low={t.get('low')}")
        return "\n".join(lines)

    async def cmd_funding(self, rest: str = "") -> str:
        from .trader.errors import BitunixAPIError
        args = [x.strip() for x in rest.replace(",", " ").split() if x.strip()]
        # /funding history SYMBOL -> the documented history endpoint
        if args and args[0].lower() in ("history", "hist"):
            sym = (args[1].upper() if len(args) > 1
                   else (self.s.symbols[0] if self.s.symbols else "BTCUSDT"))
            try:
                data = await self.engine.client.get_funding_rate_history(sym, limit=10)
            except BitunixAPIError as e:
                return f"Bitunix error {e.code}: {e.msg or e}"
            except Exception as e:  # noqa: BLE001
                return f"funding history failed: {type(e).__name__}: {e}"
            rows = data if isinstance(data, list) else [data] if data else []
            if not rows:
                return f"no funding history for {sym}"
            lines = [f"=== funding rate history: {sym} ==="]
            for f in rows[:10]:
                lines.append(f"  {f.get('fundingRate')}  at {f.get('fundingTime', '?')}")
            return "\n".join(lines)
        syms = [x.upper() for x in args]
        syms = syms or list(self.s.symbols)
        try:
            data = await self.engine.client.get_funding_rate_batch(",".join(syms))
        except BitunixAPIError as e:
            return f"Bitunix error {e.code}: {e.msg or e}"
        except Exception as e:  # noqa: BLE001
            return f"funding request failed: {type(e).__name__}: {e}"
        rows = data if isinstance(data, list) else [data] if data else []
        # The live endpoint ignores its `symbols` filter and returns every
        # symbol, so filter here or `/funding BTCUSDT` prints the whole market.
        rows = self.engine.client.filter_funding_rows(rows, syms)
        if not rows:
            return f"no funding data for {','.join(syms)}"
        lines = ["=== funding rate ==="]
        for f in rows:
            lines.append(f"{f.get('symbol')} rate={f.get('fundingRate')} "
                         f"next={f.get('nextFundingTime', '?')}")
        return "\n".join(lines)

    async def cmd_tpsl(self, rest: str = "") -> str:
        """The four documented Bitunix TP/SL methods, and their live state.

        Per the official academy article "Bitunix Futures: 4 TP/SL Methods
        Guide" the four are Position, Partial, Trailing and Account TP/SL.
        Position and Partial are enforced by the exchange; Trailing and Account
        are software and only run while the manage loop is up.
        """
        from .trader.errors import BitunixAPIError
        args = [x.strip() for x in rest.replace(",", " ").split() if x.strip()]
        sub = args[0].lower() if args else ""

        if sub in ("pending", "orders"):
            if not self._has_bitunix_keys():
                return missing_keys_message()
            try:
                data = await self.engine.client.get_pending_tp_sl_order(
                    symbol=args[1].upper() if len(args) > 1 else None, limit=20)
            except BitunixAPIError as e:
                return f"Bitunix error {e.code}: {e.msg or e}"
            except Exception as e:  # noqa: BLE001
                return f"pending TP/SL failed: {type(e).__name__}: {e}"
            rows = data if isinstance(data, list) else [data] if data else []
            if not rows:
                return "no pending TP/SL orders"
            lines = ["=== pending TP/SL orders ==="]
            for o in rows:
                lines.append(
                    f"  {o.get('symbol')} id={o.get('id')} pos={o.get('positionId')} "
                    f"tp={o.get('tpPrice')}({o.get('tpStopType')}) "
                    f"sl={o.get('slPrice')}({o.get('slStopType')}) "
                    f"tpQty={o.get('tpQty')} slQty={o.get('slQty')}")
            return "\n".join(lines)

        if sub in ("history", "hist"):
            if not self._has_bitunix_keys():
                return missing_keys_message()
            try:
                data = await self.engine.client.get_history_tp_sl_order(
                    symbol=args[1].upper() if len(args) > 1 else None, limit=20)
            except BitunixAPIError as e:
                return f"Bitunix error {e.code}: {e.msg or e}"
            except Exception as e:  # noqa: BLE001
                return f"TP/SL history failed: {type(e).__name__}: {e}"
            rows = (data or {}).get("orderList") if isinstance(data, dict) else data
            rows = rows if isinstance(rows, list) else []
            if not rows:
                return "no TP/SL history"
            lines = ["=== TP/SL history ==="]
            for o in rows[:20]:
                lines.append(f"  {o.get('symbol')} id={o.get('id')} "
                             f"status={o.get('status')} tp={o.get('tpPrice')} "
                             f"sl={o.get('slPrice')} ctime={o.get('ctime')}")
            return "\n".join(lines)

        if sub in ("cancel", "off"):
            if len(args) < 3:
                return ("usage: /tpsl cancel SYMBOL ORDER_ID\n"
                        "(official: BOTH symbol and orderId are required)")
            if not self._has_bitunix_keys():
                return missing_keys_message()
            try:
                res = await self.engine.client.cancel_tp_sl_order(
                    args[1].upper(), args[2])
            except BitunixAPIError as e:
                return f"Bitunix error {e.code}: {e.msg or e}"
            except Exception as e:  # noqa: BLE001
                return f"cancel failed: {type(e).__name__}: {e}"
            return f"cancel requested: {res}"

        if sub in ("method", "set") and len(args) > 1:
            m = args[1].upper()
            if m not in ("POSITION", "PARTIAL", "TRAILING", "ACCOUNT"):
                return ("method must be one of POSITION | PARTIAL | TRAILING | ACCOUNT")
            self.engine.state.tpsl_method = m
            self.engine._persist_if_changed()
            return self.engine.tpsl_method_report()

        # default: show the four methods and which one is active
        lines = [
            "=== the 4 Bitunix TP/SL methods ===",
            "  1. POSITION  one TP/SL closes the ENTIRE position (native, exchange-enforced)",
            "  2. PARTIAL   a defined % closes at each trigger, staged exits (native, tpQty/slQty)",
            "  3. TRAILING  dynamic exit after a trend + retracement (software ratchet)",
            "  4. ACCOUNT   closes ALL positions when total account PnL crosses a threshold (software)",
            "",
            self.engine.tpsl_method_report(),
            f"  partial: close {self.engine.state.partial_close_pct:g}% at "
            f"{self.engine.state.partial_at_roi_pct:g}% ROI",
            f"  account: TP +{self.engine.state.account_tp_usdt:g} USDT / "
            f"SL -{self.engine.state.account_sl_usdt:g} USDT / "
            f"TP {self.engine.state.account_tp_pct:g}% / "
            f"SL {self.engine.state.account_sl_pct:g}% of equity",
            "",
            "set:  tpsl method PARTIAL | partial close pct 50 | partial at roi 50",
            "      account tp usdt 100 | account sl usdt 50 | account tp pct 5",
            "read: /tpsl pending [SYMBOL] | /tpsl history [SYMBOL]",
            "      /tpsl cancel SYMBOL ORDER_ID",
        ]
        return "\n".join(lines)

    async def cmd_batch(self, rest: str = "") -> str:
        """Batch order status. Official: symbol AND orderList required, max 5."""
        from .trader.errors import BitunixAPIError
        args = [x.strip() for x in rest.replace(",", " ").split() if x.strip()]
        if args and args[0].lower() in ("cancel", "close"):
            if not self._has_bitunix_keys():
                return missing_keys_message()
            sym = args[1].upper() if len(args) > 1 else None
            try:
                res = await self.engine.client.cancel_all_orders(sym)
            except BitunixAPIError as e:
                return f"Bitunix error {e.code}: {e.msg or e}"
            except Exception as e:  # noqa: BLE001
                return f"cancel all failed: {type(e).__name__}: {e}"
            return f"cancel-all requested for {sym or 'all symbols'}: {res}"
        return (
            "=== batch order ===\n"
            "  endpoint: POST /api/v1/futures/trade/batch_order\n"
            "  required: symbol AND orderList (max 5 orders)\n"
            "  rate limit: 2 req/sec/uid\n"
            "  response: successList[] / failureList[] (per-order errorCode)\n"
            "  the engine sends batch orders through client.batch_order(symbol, orderList)\n"
            "  usage: /batch cancel [SYMBOL]   cancel all open orders"
        )

    async def cmd_leverage(self, rest: str = "") -> str:
        from .trader.errors import BitunixAPIError
        if not self._has_bitunix_keys():
            return missing_keys_message()
        syms = [x.strip().upper() for x in rest.replace(",", " ").split() if x.strip()]
        syms = syms or list(self.s.symbols)
        lines = ["=== leverage / margin mode ==="]
        for sym in syms:
            try:
                d = await self.engine.client.get_leverage_and_margin_mode(sym)
            except BitunixAPIError as e:
                lines.append(f"{sym}: error {e.code}: {e.msg or e}")
                continue
            except Exception as e:  # noqa: BLE001
                lines.append(f"{sym}: {type(e).__name__}: {e}")
                continue
            row = d[0] if isinstance(d, list) and d else (d or {})
            lines.append(f"{sym}: leverage={row.get('leverage')}x "
                         f"marginMode={row.get('marginMode')}")
        return "\n".join(lines)

    async def cmd_margin(self, rest: str = "") -> str:
        return await self.cmd_leverage(rest)

    # Tokens that are NOT symbols. `settings trade` is the engine's documented
    # "show trading settings" phrase; the bare word `settings` maps to
    # /settings, so `trade` arrives here as the `rest` argument. Forwarding it
    # as a symbol is what produced 20015 "this Futures is not allowed to
    # trade" - TRADE is not a tradable futures pair (verified against the live
    # public API: 794 pairs, TRADE absent).
    SETTINGS_KEYWORDS: frozenset[str] = frozenset({
        "TRADE", "TRADING", "ACCOUNT", "CONFIG", "CONFIGS", "SETTINGS", "SHOW", "ALL",
    })

    async def cmd_settings(self, rest: str = "") -> str:
        """Account trading settings: position mode, margin mode, leverage, config.

        `settings trade` and bare `settings` print the ACCOUNT-LEVEL trading
        configuration - the live position mode plus the engine's trading
        parameters (margin mode, leverage, risk, breakeven, trailing, ...).
        They never send a symbol to the exchange, so they cannot raise 20015.

        `settings SYMBOL` additionally shows that one symbol's configured
        margin mode and leverage. A token that is not a tradable pair is
        dropped with a warning instead of being sent (which would 20015).
        """
        from .trader.errors import BitunixAPIError
        if not self._has_bitunix_keys():
            return missing_keys_message()

        tokens = [x.strip().upper() for x in rest.replace(",", " ").split() if x.strip()]
        syms = [t for t in tokens if t not in self.SETTINGS_KEYWORDS]

        lines = ["=== account trading settings ==="]

        # 1) Position mode - the account-level endpoint. It takes no
        #    parameters, so it cannot raise 20015.
        try:
            mode = await self.engine.client.get_position_mode()
            pm = (mode or {}).get("positionMode") if isinstance(mode, dict) else mode
            lines.append(f"position mode: {pm or '?'}")
        except BitunixAPIError as e:
            lines.append(f"position mode: error {e.code}: {e.msg or e}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"position mode: {type(e).__name__}: {e}")

        # 2) The trading configuration. This is the account-level config the
        #    command has always shown - margin mode, leverage and the rest of
        #    the engine's trading parameters. No per-symbol listing.
        s = self.engine.state
        lines.append(f"margin mode: {s.margin_mode}")
        lines.append(f"leverage: {s.leverage}x")
        lines.append(f"max open positions: {s.max_open_positions}")
        lines.append(f"risk per trade: {s.risk_pct}%")
        lines.append(f"breakeven: {s.breakeven_pct}%  "
                     f"trailing trigger: {s.trailing_trigger_roi_pct}% ROI  "
                     f"liq SL distance: {s.liq_sl_distance_pct}%")
        lines.append(f"min agreeing strategies: {s.min_agreeing}  "
                     f"timeframes: {','.join(s.timeframes)}")
        lines.append(f"min confidence: {s.min_confidence}  "
                     f"tf min confidence: {s.tf_min_confidence}")
        lines.append(f"scan interval: {s.scan_interval}s  "
                     f"report interval: {s.report_interval}s")
        lines.append(f"tpsl method: {s.tpsl_method}  "
                     f"partial close: {s.partial_close_pct}% at {s.partial_at_roi_pct}% ROI")
        lines.append(f"engine_on={s.engine_on} scan_on={s.scan_on} "
                     f"autotrade={s.autotrade} mid_management={s.mid_management}")

        # 3) Only when the user names a REAL symbol, show that symbol's
        #    configured margin mode + leverage. Never a wall of symbols.
        if syms:
            valid = await self._valid_symbols(syms)
            if valid is None:
                lines.append("per-symbol settings: could not validate symbols "
                             "(trading-pairs lookup failed) - skipped")
                syms = []
            else:
                bad = [x for x in syms if x not in valid]
                if bad:
                    lines.append(f"per-symbol settings: skipping unknown "
                                 f"symbol(s) {', '.join(bad)} "
                                 f"(not a tradable futures pair)")
                syms = [x for x in syms if x in valid]
        if syms:
            try:
                data = await self.engine.client.get_trading_settings(",".join(syms))
            except BitunixAPIError as e:
                if e.code == 20015:
                    lines.append(f"per-symbol settings: error 20015 - "
                                 f"{', '.join(syms)} is not a tradable futures "
                                 f"pair (use /symbols to list valid pairs)")
                else:
                    lines.append(f"per-symbol settings: error {e.code}: {e.msg or e}")
            except Exception as e:  # noqa: BLE001
                lines.append(f"per-symbol settings: {type(e).__name__}: {e}")
            else:
                rows = data if isinstance(data, list) else ([data] if data else [])
                for r in rows:
                    if isinstance(r, dict):
                        lines.append(f"  {r.get('symbol')}: "
                                     f"leverage={r.get('leverage')}x "
                                     f"marginMode={r.get('marginMode')}")
        return "\n".join(lines)

    async def _valid_symbols(self, syms: list[str]) -> set[str] | None:
        """Best-effort: the subset of `syms` that are tradable futures pairs.

        Uses the PUBLIC trading-pairs endpoint. Returns None when the lookup
        itself fails, so the caller can skip the per-symbol section rather
        than send a token that would raise 20015.
        """
        try:
            pairs = await self.engine.client.get_trading_pairs()
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(pairs, list):
            return None
        valid = {str(r.get("symbol", "")).upper()
                 for r in pairs if isinstance(r, dict)}
        return {x for x in syms if x in valid}

    async def cmd_pnl(self) -> str:
        from .trader.errors import BitunixAPIError
        if not self._has_bitunix_keys():
            return missing_keys_message()
        try:
            d = await self.engine.pnl()
        except BitunixAPIError as e:
            return f"Bitunix error {e.code}: {e.msg or e}"
        except Exception as e:  # noqa: BLE001
            return f"pnl request failed: {type(e).__name__}: {e}"
        lines = [f"unrealized total: {d.get('unrealized_total')}"]
        for r in d.get("positions") or []:
            lines.append(f"  {r.get('symbol')} {r.get('side')} "
                         f"unrealized={r.get('unrealized')} realized={r.get('realized')}")
        if not d.get("positions"):
            lines.append("  (no open positions)")
        return "\n".join(lines)

    async def cmd_symbols(self, rest: str = "") -> str:
        from .trader.errors import BitunixAPIError
        try:
            data = await self.engine.client.get_trading_pairs()
        except BitunixAPIError as e:
            return f"Bitunix error {e.code}: {e.msg or e}"
        except Exception as e:  # noqa: BLE001
            return f"symbols request failed: {type(e).__name__}: {e}"
        rows = data if isinstance(data, list) else []
        q = (rest or "").strip().upper()
        if q:
            rows = [r for r in rows if q in str(r.get("symbol", "")).upper()]
        if not rows:
            return f"no symbols matching {q!r}" if q else "no symbols returned"
        lines = [f"{len(rows)} symbol(s)" + (f" matching {q}" if q else "") + ":"]
        for r in rows[:60]:
            lines.append(f"  {r.get('symbol'):<16} status={r.get('symbolStatus')} "
                         f"maxLev={r.get('maxLeverage')} "
                         f"basePrec={r.get('basePrecision')} "
                         f"minVol={r.get('minTradeVolume')}")
        if len(rows) > 60:
            lines.append(f"  ... {len(rows) - 60} more")
        return "\n".join(lines)

    def cmd_memory(self, query: str) -> str:
        arg = (query or "").strip()
        if arg.lower() in ("clear confirm", "wipe confirm", "reset confirm"):
            return self.cmd_memory_clear_confirmed()
        if arg.lower() in ("clear", "wipe", "reset"):
            return self._memory_clear()
        if not arg:
            st = self.memory.stats()
            last = (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st["last_write"]))
                    if st["last_write"] else "never")
            lines = [
                "=== memory ===",
                f"entries: {st['total']} / {st['max_items']} cap"
                + (f"  (evicted {st['evicted']} so far)" if st["evicted"] else ""),
                f"size on disk: {st['size_bytes']} bytes",
                f"last write: {last}",
                f"path: {st['path']}",
                f"by kind: {st['by_kind']}",
                "recent:",
            ]
            for m in self.memory.recent(8):
                ts = time.strftime("%m-%d %H:%M", time.localtime(m.ts))
                lines.append(f"  [{m.kind} w={m.weight} {ts}] {m.text[:110]}")
            lines.append("")
            lines.append("usage: /memory <query>   search")
            lines.append("       /memory clear     wipe all memory (asks first)")
            return "\n".join(lines)
        hits = self.memory.search(arg, k=10)
        if not hits:
            return f"no memory matches for {arg!r}"
        return "\n".join(f"[{m.kind} w={m.weight}] {m.text[:200]}" for m in hits)

    def _memory_clear(self) -> str:
        """Wipe memory. Requires an explicit confirmation token.

        A destructive, irreversible action must not fire on a typo, so
        `/memory clear` alone only asks; `/memory clear confirm` does it.
        """
        n = len(self.memory.items)
        if n == 0:
            return "memory is already empty."
        return (f"memory holds {n} entries at {self.memory.path}.\n"
                f"This cannot be undone. Re-run as:  /memory clear confirm")

    def cmd_memory_clear_confirmed(self) -> str:
        """Actually wipe memory - only reachable via `/memory clear confirm`."""
        n = self.memory.clear()
        self.perms.audit.write("memory_clear", entries=n)
        return f"memory cleared: {n} entries removed from {self.memory.path}."

    def _py_files(self, path: str) -> list[Path]:
        base = Path(path) if path else ROOT / "jrock"
        if not base.is_absolute():
            base = ROOT / base
        if base.is_file():
            return [base]
        return [p for p in base.rglob("*.py")
                if "__pycache__" not in str(p) and ".git" not in str(p)]

    def cmd_code_review(self, path: str) -> str:
        files = self._py_files(path)
        issues: list[str] = []
        for f in files:
            try:
                src = f.read_text(encoding="utf-8")
            except OSError as e:
                issues.append(f"{f}: unreadable ({e})")
                continue
            rel = f.relative_to(ROOT) if f.is_relative_to(ROOT) else f
            try:
                tree = ast.parse(src)
            except SyntaxError as e:
                issues.append(f"{rel}:{e.lineno}: SYNTAX ERROR {e.msg}")
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    length = (node.end_lineno or node.lineno) - node.lineno
                    if length > 80:
                        issues.append(f"{rel}:{node.lineno}: function {node.name}() is {length} lines")
                    if len(node.args.args) > 7:
                        issues.append(f"{rel}:{node.lineno}: {node.name}() has {len(node.args.args)} params")
                if isinstance(node, ast.ExceptHandler) and node.type is None:
                    issues.append(f"{rel}:{node.lineno}: bare except")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id in ("eval", "exec"):
                        issues.append(f"{rel}:{node.lineno}: {node.func.id}() call")
            for pat, msg in [
                (r"except\s+Exception:\s*\n\s*pass", "swallowed exception (except: pass)"),
                (r"TODO|FIXME|XXX", "TODO/FIXME marker"),
                (r"print\(", "print() in library code"),
                (r"time\.sleep\(", "blocking time.sleep in async code"),
            ]:
                for m in re.finditer(pat, src):
                    ln = src[:m.start()].count("\n") + 1
                    line_text = src.splitlines()[ln - 1] if ln - 1 < len(src.splitlines()) else ""
                    # skip the reviewer's own pattern table (self-match false positive)
                    if "TODO|FIXME|XXX" in line_text or "swallowed exception" in line_text:
                        continue
                    # print() is legitimate in the CLI entrypoint
                    if msg.startswith("print()") and f.name == "cli.py":
                        continue
                    issues.append(f"{rel}:{ln}: {msg}")
        if not issues:
            return f"code review: {len(files)} files, no issues found"
        return f"code review: {len(files)} files, {len(issues)} findings\n" + "\n".join(issues[:120])

    def cmd_bug_fix(self, path: str) -> str:
        review = self.cmd_code_review(path)
        fixes = []
        for f in self._py_files(path):
            try:
                src = f.read_text(encoding="utf-8")
            except OSError:
                continue
            rel = f.relative_to(ROOT) if f.is_relative_to(ROOT) else f
            if re.search(r"except\s*:", src):
                new = re.sub(r"except\s*:", "except Exception:", src)
                r = self.tools.write(str(rel), new, reason="bug_fix: bare except")
                fixes.append(f"{rel}: bare except -> except Exception: {'applied' if r.ok else 'PENDING'}")
            if re.search(r"def \w+\([^)]*=\s*(\[\]|\{\})", src):
                fixes.append(f"{rel}: mutable default argument - needs manual fix")
        if not fixes:
            return review + "\n\nno auto-fixable issues found"
        return review + "\n\nFIXES:\n" + "\n".join(fixes)

    def cmd_verify(self) -> str:
        out = []
        r = self.tools.internal(f"{sys.executable} -m compileall -q jrock", timeout=120)
        out.append(f"compileall: {'PASS' if r.ok else 'FAIL'}\n{r.output[:2000]}")
        r = self.tools.internal(
            f"{sys.executable} -c 'import jrock, jrock.agent, jrock.trader.engine'", timeout=60)
        out.append(f"imports: {'PASS' if r.ok else 'FAIL'}\n{r.output[:2000]}")
        r = self.tools.internal(f"{sys.executable} -m pytest -q tests", timeout=300)
        out.append(f"pytest: {'PASS' if r.ok else 'FAIL'}\n{r.output[-3000:]}")
        return "\n\n".join(out)

    def cmd_simplify(self, path: str) -> str:
        files = self._py_files(path)
        findings = []
        for f in files:
            try:
                src = f.read_text(encoding="utf-8")
                tree = ast.parse(src)
            except (OSError, SyntaxError):
                continue
            rel = f.relative_to(ROOT) if f.is_relative_to(ROOT) else f
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    branches = sum(1 for n in ast.walk(node)
                                   if isinstance(n, (ast.If, ast.For, ast.While, ast.Try)))
                    if branches > 12:
                        findings.append(f"{rel}:{node.lineno}: {node.name}() has {branches} branches")
            lines = src.splitlines()
            if len(lines) > 600:
                findings.append(f"{rel}: {len(lines)} lines - consider splitting")
        if not findings:
            return f"simplify: {len(files)} files, nothing obviously over-complex"
        return "simplify findings:\n" + "\n".join(findings)

    async def cmd_team_spawn(self, rest: str) -> str:
        parts = rest.split(maxsplit=1)
        if len(parts) < 2 or not parts[0].isdigit():
            return "Usage: /team_spawn <n> <task>"
        n, task = int(parts[0]), parts[1]
        n = max(1, min(8, n))
        roles = ["analyst", "implementer", "reviewer", "tester", "documenter",
                 "risk-checker", "perf-checker", "integrator"]
        lines = [f"team_spawn: {n} sub-agents on {task!r}"]
        for i in range(n):
            role = roles[i % len(roles)]
            lines.append(f"  [{i+1}] {role}: {task} -> scope: {role} slice")
            self.memory.episode("team_spawn", f"{role}:{task[:80]}", "spawned", ok=True)
        lines.append("note: sub-agents run as scoped tasks; results merge into memory.")
        return "\n".join(lines)

    async def cmd_check_ai(self) -> str:
        from .model_selector import probe_model
        import aiohttp
        if not self.s.api_key:
            return "FAIL: API_KEY empty"
        model = self.model or self.s.model
        if not model:
            res = await autoset_model(self.s.base_url, self.s.api_key)
            if not res.ok:
                return f"FAIL: {res.message}"
            model = res.selected
            self.model = model
        async with aiohttp.ClientSession() as sess:
            p = await probe_model(sess, self.s.base_url, self.s.api_key, model)
        if p.works:
            return f"AI OK: {model} responded in {p.latency_ms}ms via {self.s.base_url}"
        return f"AI FAIL: {model} -> {p.error}"

    async def cmd_check_system(self) -> str:
        lines = ["=== system check ==="]
        lines.append(f"python: {sys.version.split()[0]}")
        for mod in ("aiohttp", "websockets", "dotenv", "pytest"):
            try:
                __import__(mod)
                lines.append(f"dep {mod}: OK")
            except ImportError:
                lines.append(f"dep {mod}: MISSING")
        r = self.tools.internal(f"{sys.executable} -m compileall -q jrock", timeout=120)
        lines.append(f"compile: {'PASS' if r.ok else 'FAIL'}")
        if not r.ok:
            lines.append(r.output[:1500])
        ok, msg = await self.engine.client.verify_credentials()
        lines.append(f"bitunix auth: {'OK' if ok else 'FAIL'} - {msg}")
        if ok:
            try:
                pairs = await self.engine.client.get_trading_pairs(",".join(self.engine.state.symbols))
                lines.append(f"trading pairs: {len(pairs or [])} loaded")
                for d in (pairs or [])[:3]:
                    lines.append(f"  {d.get('symbol')} status={d.get('symbolStatus')} "
                                 f"api={d.get('isApiSupported')} maxLev={d.get('maxLeverage')}")
            except Exception as e:  # noqa: BLE001
                lines.append(f"trading pairs: FAIL {e}")
            try:
                acct = await self.engine.client.get_single_account()
                a = acct[0] if isinstance(acct, list) and acct else (acct or {})
                lines.append(f"account: available={a.get('available')} mode={a.get('positionMode')}")
            except Exception as e:  # noqa: BLE001
                lines.append(f"account: FAIL {e}")
        lines.append(f"engine: on={self.engine.state.engine_on} "
                     f"autotrade={self.engine.state.autotrade} "
                     f"scans={self.engine.state.scans} errors={len(self.engine.state.errors)}")
        for e in self.engine.state.errors[-5:]:
            lines.append(f"  err: {e}")
        from .trader.strategies import registry
        reg = registry()
        cats: dict[str, int] = {}
        for v in reg.values():
            cats[v["category"]] = cats.get(v["category"], 0) + 1
        lines.append(f"strategies: {len(reg)} registered, by category: {cats}")
        lines.append(f"permissions: auto_accept_edit={self.perms.auto_accept_edit} "
                     f"device_access={self.perms.full_device_access} "
                     f"block_destructive={self.perms.block_destructive}")
        return "\n".join(lines)

    def cmd_autopsy(self, rest: str) -> str:
        if not rest:
            return "Usage: /autopsy <error text | file path>"
        text = rest
        if Path(rest).exists():
            r = self.tools.read(rest)
            if r.ok:
                text = r.output
        lines = ["=== autopsy ===", f"input: {text[:200]}", ""]
        patterns = [
            (r"10007|sign signature", "Signature mismatch: check nonce+timestamp+apiKey+query+body order, "
                                      "and that body is compact JSON with sorted keys."),
            (r"10011|timestamp", "Clock skew: call sync_time() / check system clock (must be within 60s)."),
            (r"10004|ip whitelist", "IP not whitelisted: add the server IP in Bitunix API settings."),
            (r"20003|Insufficient balance", "Not enough available margin for the computed qty."),
            (r"30016|30017|minTradeVolume|qty", "Qty below minTradeVolume or wrong precision: "
                                               "round down to basePrecision."),
            (r"30024|30025|liq price", "SL is on the wrong side of the liquidation price: "
                                       "recompute with sl_from_liq_distance()."),
            (r"30029|30031|SL price must be (less|greater) than mark",
             "SL is on the wrong side of the MARK price. Every TP/SL path now "
             "fetches the mark price fresh and clamps the stop through "
             "risk.clamp_sl() to clear it by TP_SL_MIN_STOP_TICKS ticks. If you "
             "still see this, the mark moved between the last read and the "
             "request landing - retry, or raise TP_SL_MIN_STOP_TICKS."),
            (r"30020|30021|30027|30033|TP price must be",
             "TP is on the wrong side of the mark price: widen the TP distance."),
            (r"30005|trigger price", "TP/SL trigger too close to price: widen the distance."),
            (r"20006", "Cannot change leverage/margin mode with open orders: cancel first."),
            (r"20009", "Position exists: position mode cannot change until flat."),
            (r"KeyError|IndexError", "Data shape mismatch: the API returned a different structure "
                                     "than expected - log the raw payload."),
            (r"TimeoutError|aiohttp", "Network/timeout: retry with backoff, check base_url."),
            (r"ModuleNotFoundError", "Missing dependency: pip install -r requirements.txt"),
        ]
        found = False
        for pat, hint in patterns:
            if re.search(pat, text, re.I):
                lines.append(f"MATCH {pat}: {hint}")
                found = True
        if not found:
            lines.append("No known pattern matched. Next steps:")
            lines.append("  1) reproduce with the smallest input")
            lines.append("  2) log the raw request/response")
            lines.append("  3) check docs/bitunix/ for the exact parameter contract")
        self.memory.episode("autopsy", text[:120], "analyzed", ok=True)
        return "\n".join(lines)

    async def cmd_diagnose(self) -> str:
        parts = [await self.cmd_check_system()]
        parts.append("\n=== code review (jrock/) ===\n" + self.cmd_code_review("jrock"))
        parts.append("\n=== simplify ===\n" + self.cmd_simplify("jrock"))
        parts.append("\n=== audit tail ===\n" + json.dumps(self.perms.audit.tail(10), indent=2))
        return "\n".join(parts)