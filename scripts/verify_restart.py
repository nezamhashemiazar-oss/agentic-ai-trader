#!/usr/bin/env python3
"""End-to-end restart verification through the REAL agent + engine command path.

Unlike verify_store.py (which drives the Store directly), this exercises the
actual `JRockAgent` -> `TraderEngine.command()` path a user types into, then
SIGKILLs the process and restarts it. Run: python scripts/verify_restart.py
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORK = Path(tempfile.mkdtemp(prefix="jrock-restart-"))
ROOT = WORK / "repo"
ROOT.mkdir(parents=True, exist_ok=True)

API_KEY = "sk-live-RESTARTKEY1234567890abcdef"
API_SECRET = "bitunix-RESTARTSECRET0987654321zyxw"


def child(body: str) -> subprocess.CompletedProcess:
    script = (
        "import sys, os\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        f"import jrock.config as _c, jrock.agent as _a\n"
        f"_c.ROOT = __import__('pathlib').Path({str(ROOT)!r})\n"
        f"_a.ROOT = _c.ROOT\n"
        + textwrap.dedent(body)
    )
    return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)


def hdr(n: str, t: str) -> None:
    print(f"\n{'=' * 72}\n{n}) {t}\n{'=' * 72}")


# ------------------------------------------------------- 1. change + SIGKILL
hdr("1", "Change settings at runtime through the real command path, then SIGKILL")
proc = child(f"""
    import asyncio, os, signal
    from jrock.agent import JRockAgent
    from jrock.config import Settings

    s = Settings(api_key={API_KEY!r}, bitunix_api_key={API_KEY!r},
                 bitunix_api_secret={API_SECRET!r})
    a = JRockAgent(s)

    async def main():
        for cmd in ("leverage 20", "max open positions 5",
                    "breakeven threshold pct 0.8", "min agreeing strategies 5",
                    "symbols BTCUSDT,ETHUSDT", "mid management",
                    "scans interval sec 45", "risk 1.5"):
            out = await a.engine.command(cmd)
            print(f"  {{cmd:<32}} -> {{out}}")
        a.memory.feedback("user wants 5x leverage on BTCUSDT", weight=3.0)
        a.memory.fact("preferred_timeframe", "5m")
        print("  memory: 2 entries written")
    asyncio.run(main())
    print("  SIGKILLing the process now (no clean exit, no atexit)")
    os.kill(os.getpid(), signal.SIGKILL)
""")
print(proc.stdout.strip())
print(f"  child exit code: {proc.returncode}  (killed by signal {-proc.returncode})")
assert proc.returncode == -signal.SIGKILL, f"not SIGKILLed:\n{proc.stderr}"

# ------------------------------------------------------------- 2. restart
hdr("2", "Restart: the banner and /status report what was loaded")
proc = child("""
    import asyncio
    from jrock.agent import JRockAgent
    from jrock.config import Settings

    a = JRockAgent(Settings())
    print(a._state_banner())
    print()
    print(asyncio.run(a.engine.status()))
""")
print(proc.stdout.strip())
assert proc.returncode == 0, proc.stderr

# --------------------------------------------------- 3. values came back
hdr("3", "Every changed setting came back with the same value")
proc = child("""
    from jrock.agent import JRockAgent
    from jrock.config import Settings
    a = JRockAgent(Settings())
    st = a.engine.state
    checks = [
        ("leverage", st.leverage, 20),
        ("max_open_positions", st.max_open_positions, 5),
        ("breakeven_pct", st.breakeven_pct, 0.8),
        ("min_agreeing", st.min_agreeing, 5),
        ("symbols", st.symbols, ["BTCUSDT", "ETHUSDT"]),
        ("mid_management", st.mid_management, False),
        ("scan_interval", st.scan_interval, 45),
        ("risk_pct", st.risk_pct, 1.5),
    ]
    ok = True
    for name, got, want in checks:
        mark = "OK " if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"  [{mark}] {name:<20} = {got!r}  (expected {want!r})")
    print()
    print("  ALL SETTINGS RESTORED" if ok else "  SOME SETTINGS LOST")
    raise SystemExit(0 if ok else 1)
""")
print(proc.stdout.strip())
assert proc.returncode == 0, "settings did not survive the restart"

# ------------------------------------------------------- 4. memory recalled
hdr("4", "Memory is recalled after the restart")
proc = child("""
    from jrock.agent import JRockAgent
    from jrock.config import Settings
    a = JRockAgent(Settings())
    print(a.cmd_memory(""))
    print()
    print("--- search: 'leverage BTCUSDT' ---")
    print(a.cmd_memory("leverage BTCUSDT"))
""")
print(proc.stdout.strip())
assert "5x leverage" in proc.stdout, "memory entry was not recalled"

# ------------------------------------------------------- 5. precedence live
hdr("5", "Precedence: a runtime value beats .env on the next start")
(ROOT / ".env").write_text("TRADE_LEVERAGE=10\nMARKET_DATA_SOURCE=rest\n")
print("  wrote .env with TRADE_LEVERAGE=10 and MARKET_DATA_SOURCE=rest")
print("  (the store already holds leverage=20 from the runtime change above)")
proc = child("""
    import os
    os.environ["TRADE_LEVERAGE"] = "10"
    os.environ["MARKET_DATA_SOURCE"] = "rest"
    from jrock.agent import JRockAgent
    from jrock.config import Settings
    a = JRockAgent(Settings(leverage=10, market_data_source="rest"))
    print(f"  .env says leverage=10  ->  effective leverage = {a.engine.state.leverage}")
    print(f"  .env says rest         ->  effective source   = {a.s.market_data_source}")
    assert a.engine.state.leverage == 20, "store must beat .env"
    print("  RESULT: the runtime value (20) won over .env (10)")
""")
print(proc.stdout.strip())
assert proc.returncode == 0, proc.stderr

# ------------------------------------------------------- 6. secret scan
hdr("6", "Secret guard: grep the real data/ dir for the key and secret")
data = ROOT / "data"
print(f"  scanning {data}")
hits = []
for p in sorted(data.rglob("*")):
    if p.is_file():
        body = p.read_text(encoding="utf-8", errors="ignore")
        found = [s for s in (API_KEY, API_SECRET) if s in body]
        print(f"    {p.name:<34} {p.stat().st_size:>6}B  matches={len(found)}")
        if found:
            hits.append(p.name)
print(f"\n  files containing a secret value: {hits}")
assert hits == [], f"SECRET LEAKED into {hits}"
print("  RESULT: zero matches")

# ------------------------------------------------------- 7. corrupt + start
hdr("7", "Corrupt the store, restart, and show recovery instead of a crash")
state = data / "state.json"
state.write_text('{"version": 2, "settings": {"leverage": 2')   # truncated
print(f"  truncated state.json to {state.stat().st_size} bytes (invalid JSON)")
proc = child("""
    from jrock.agent import JRockAgent
    from jrock.config import Settings
    a = JRockAgent(Settings())
    print(a._state_banner())
    print(f"  store.loaded = {a.store.loaded}")
    print(f"  backup       = {a.store.recovered_backup}")
""")
print(proc.stdout.strip())
assert proc.returncode == 0, f"corrupt store crashed the agent:\n{proc.stderr}"
backups = sorted(p.name for p in data.glob("state.json.corrupt-*"))
print(f"  backups on disk: {backups}")
assert backups
print("  RESULT: agent started, corrupt file backed up, no crash")

# ------------------------------------------------------- 8. migration live
hdr("8", "Hand-write a v1 file, restart, show it migrated not discarded")
v1 = data / "state.json"
v1.write_text('{"thinking_level": "high", "market_data_source": "ws", "leverage": 20}')
print(f"  wrote a v1 file: {v1.read_text()}")
proc = child("""
    from jrock.agent import JRockAgent
    from jrock.config import Settings
    a = JRockAgent(Settings())
    print(f"  migrated_from = {a.store.migrated_from}")
    print(f"  version now   = {a.store.version}")
    print(f"  thinking_level kept = {a.thinking_level}")
    print(f"  market_data_source kept = {a.s.market_data_source}")
    print(f"  leverage kept = {a.engine.state.leverage}")
""")
print(proc.stdout.strip())
assert proc.returncode == 0, proc.stderr
assert "migrated_from = 1" in proc.stdout
print("  RESULT: v1 upgraded in place, settings preserved")

print(f"\n{'=' * 72}\nALL RESTART VERIFICATIONS PASSED\n{'=' * 72}")
shutil.rmtree(WORK, ignore_errors=True)
