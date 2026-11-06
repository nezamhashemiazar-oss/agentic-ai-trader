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
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import ROOT, Settings, load_settings
from .memory import MemoryStore
from .model_selector import autoset_model, persist_model
from .permissions import PermissionManager
from .trader.engine import TraderEngine

SOUL_PATH = ROOT / "soul" / "jrock.soul.md"

HELP = """J+Rock commands
================
Type /help or help or ? for this list.
Anything that is not a command is sent to the model as natural language.

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
  /think <question>         structured reasoning pass (plan, risks, next step)
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

PERMISSIONS
  auto accept edit on|off    apply self-edits automatically vs. show diff
  device access on|off       grant whole-terminal access
  audit                      show the audit log
  pending                    show edits waiting for approval
  approve [n]                approve a pending edit

FREE TEXT
  anything else (e.g. "switch to free model", "why did my order fail?")
  goes to the model. exit / quit / Ctrl-D leave the REPL.
"""

# Bare words that mean an agent command (no leading slash needed).
AGENT_BARE: dict[str, str] = {
    "help": "/help", "?": "/help", "models": "/models", "model": "/models",
    "soul": "/soul", "memory": "/memory", "think": "/think",
    "verify": "/verify", "diagnose": "/diagnose", "autopsy": "/autopsy",
    "mcp": "/mcp", "skill": "/skill", "skills": "/skill",
    "check_ai": "/check_ai", "check_system": "/check_system",
    "code_review": "/code_review", "bug_fix": "/bug_fix", "simplify": "/simplify",
}

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
        self.memory = MemoryStore(self.s.data_dir / "memory.jsonl")
        self.engine = TraderEngine(self.s)
        self.model: str = self.s.model
        self.mcp_servers: dict[str, str] = {}
        self.skills: dict[str, str] = {}
        self._load_registry()

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
    async def startup(self) -> str:
        lines = ["J+Rock starting..."]
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
          1. bare word that names an agent command  -> agent dispatcher
          2. known natural-language intent          -> handled deterministically
          3. engine command grammar                 -> engine
          4. anything else                          -> the model
        """
        low = raw.strip().lower()
        head = low.split()[0] if low.split() else ""

        # 1) bare agent command name
        if head in AGENT_BARE:
            mapped = AGENT_BARE[head]
            rest = raw.strip()[len(head):].strip()
            return await self.handle(f"{mapped} {rest}".strip())

        # 2) natural-language intents we can act on without the model
        if FREE_MODEL_INTENT.search(raw):
            return await self.cmd_use_free_model()

        # 3) engine grammar
        if self.engine.looks_like_command(raw):
            return await self.engine.command(raw)

        # 4) free text -> the model
        return await self.chat(raw)

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

    async def chat(self, text: str) -> str:
        """Send free text to the configured model."""
        from .model_selector import autoset_model
        import aiohttp

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

        system = ("You are J+Rock, an agentic coding and trading-systems engineer. "
                  "Be direct, never fabricate results, say 'I don't know' when unsure. "
                  "You have a Bitunix futures engine; its commands are listed in /help.")
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": text}],
            "max_tokens": 800,
            "temperature": 0.2,
        }
        url = f"{self.s.base_url.rstrip('/')}/chat/completions"
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    url,
                    headers={"Authorization": f"Bearer {self.s.api_key}",
                             "Content-Type": "application/json"},
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=90),
                ) as r:
                    body = await r.text()
                    if r.status != 200:
                        return (f"Model call failed: HTTP {r.status}: {body[:400]}\n"
                                f"You said: {text!r}")
                    d = json.loads(body)
        except Exception as e:  # noqa: BLE001
            return f"Model call failed: {type(e).__name__}: {e}"

        choices = d.get("choices") or []
        if not choices:
            return f"Model returned no choices: {body[:300]}"
        msg = choices[0].get("message") or {}
        out = (msg.get("content") or "").strip()
        self.memory.episode("chat", text[:120], out[:120], ok=True)
        return out or "(model returned an empty message)"

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

    def cmd_think(self, question: str) -> str:
        if not question:
            return "Usage: /think <question>"
        recalled = self.memory.search(question, k=5)
        lines = [f"THINK: {question}", "", "1) What is actually being asked",
                 f"   - {question}", "", "2) What I know (memory)"]
        if recalled:
            for m in recalled:
                lines.append(f"   - [{m.kind}] {m.text[:160]}")
        else:
            lines.append("   - nothing relevant stored")
        lines += ["", "3) Unknowns / risks",
                  "   - anything not verified by running code is unverified",
                  "   - exchange behaviour must be confirmed against docs/bitunix/",
                  "", "4) Plan",
                  "   - inspect the real code/data first",
                  "   - make the smallest change that fixes the root cause",
                  "   - verify by running it, not by reading it",
                  "", "5) Next action",
                  "   - run /check_system or /diagnose to get facts before editing"]
        self.memory.episode("think", question[:120], "reasoned", ok=True)
        return "\n".join(lines)

    def cmd_memory(self, query: str) -> str:
        if not query:
            st = self.memory.stats()
            lines = [f"memory: {st['total']} items at {st['path']}",
                     f"by kind: {st['by_kind']}", "recent:"]
            for m in self.memory.recent(8):
                lines.append(f"  [{m.kind}] {m.text[:120]}")
            return "\n".join(lines)
        hits = self.memory.search(query, k=10)
        if not hits:
            return f"no memory matches for {query!r}"
        return "\n".join(f"[{m.kind} w={m.weight}] {m.text[:200]}" for m in hits)

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
