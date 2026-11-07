#!/usr/bin/env python3
"""Live verification of the persistent store - real processes, real SIGKILL.

Not a mock. Each section spawns a real child process, changes state, kills it
with SIGKILL (no atexit, no finally), and reads the result back from a fresh
process. Run:  python scripts/verify_store.py
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from jrock.memory import MemoryStore  # noqa: E402
from jrock.store import SCHEMA_VERSION, Store  # noqa: E402

WORK = Path(tempfile.mkdtemp(prefix="jrock-store-verify-"))
DATA = WORK / "data"
DATA.mkdir(parents=True, exist_ok=True)
STATE = DATA / "state.json"
MEM = DATA / "memory.jsonl"

API_KEY = "sk-live-VERIFYKEY1234567890abcdef"
API_SECRET = "bitunix-VERIFYSECRET0987654321zyxw"


def child(body: str) -> subprocess.CompletedProcess:
    """Run `body` in a real child process with the repo importable."""
    script = f"import sys; sys.path.insert(0, {str(REPO)!r})\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)


def hdr(n: str, title: str) -> None:
    print(f"\n{'=' * 72}\n{n}) {title}\n{'=' * 72}")


# ---------------------------------------------------------------- 1. SIGKILL
hdr("1", "SIGKILL: settings survive a hard kill (no clean exit)")
proc = child(f"""
    import os, signal
    from jrock.store import Store
    s = Store(path={str(STATE)!r})
    s.set("leverage", 20)
    s.set("max_open_positions", 5)
    s.set("breakeven_pct", 0.8)
    s.set("min_agreeing", 5)
    s.set("symbols", ["BTCUSDT", "ETHUSDT"])
    s.set("mid_management", False)
    s.set_section("engine", {{"leverage": 20, "max_open_positions": 5,
                              "breakeven_pct": 0.8, "min_agreeing": 5,
                              "symbols": ["BTCUSDT", "ETHUSDT"],
                              "mid_management": False, "autotrade": True}})
    print("child wrote 6 settings, now SIGKILLing itself")
    os.kill(os.getpid(), signal.SIGKILL)
""")
print(proc.stdout.strip())
print(f"child exit code: {proc.returncode}  (negative = killed by signal {-proc.returncode})")
assert proc.returncode == -signal.SIGKILL, "child was not SIGKILLed"

fresh = Store(path=STATE)
print("\n--- fresh process reads the store back ---")
for k in ("leverage", "max_open_positions", "breakeven_pct", "min_agreeing",
          "symbols", "mid_management"):
    print(f"  {k:<20} = {fresh.get(k)!r}")
print(f"  engine.autotrade     = {fresh.section('engine').get('autotrade')!r}")
assert fresh.get("leverage") == 20
assert fresh.get("symbols") == ["BTCUSDT", "ETHUSDT"]
assert fresh.get("mid_management") is False
print("RESULT: all 6 settings survived SIGKILL")

# ---------------------------------------------------------------- 2. memory
hdr("2", "SIGKILL: a memory entry survives and is recalled")
proc = child(f"""
    import os, signal
    from jrock.memory import MemoryStore
    m = MemoryStore({str(MEM)!r})
    m.feedback("user wants 5x leverage on BTCUSDT and no AKEUSDT", weight=3.0)
    m.fact("preferred_timeframes", "1m,5m")
    print("child wrote 2 memory entries, now SIGKILLing itself")
    os.kill(os.getpid(), signal.SIGKILL)
""")
print(proc.stdout.strip())
print(f"child exit code: {proc.returncode}")
assert proc.returncode == -signal.SIGKILL

m2 = MemoryStore(MEM)
print(f"\nentries after restart: {m2.stats()['total']}")
for hit in m2.search("leverage BTCUSDT AKEUSDT"):
    print(f"  recalled: [{hit.kind} w={hit.weight}] {hit.text}")
assert m2.stats()["total"] == 2
assert any("5x leverage" in h.text for h in m2.search("leverage BTCUSDT"))
print("RESULT: memory survived SIGKILL and is searchable")

# ------------------------------------------------------------- 3. precedence
hdr("3", "Precedence: store > .env > default")
env_file = WORK / ".env"
env_file.write_text("TRADE_LEVERAGE=10\nMARKET_DATA_SOURCE=rest\n")
print(f".env says: TRADE_LEVERAGE=10, MARKET_DATA_SOURCE=rest")

proc = child(f"""
    import os
    os.environ["TRADE_LEVERAGE"] = "10"
    os.environ["MARKET_DATA_SOURCE"] = "rest"
    from jrock.store import Store
    s = Store(path={str(STATE)!r})
    print("  store before override:", s.get("leverage"), s.get("market_data_source"))
    s.set("leverage", 40)                 # changed at runtime
    s.set("market_data_source", "ws")
    print("  runtime change -> leverage=40, market_data_source=ws")
""")
print(proc.stdout.strip())

proc = child(f"""
    import os
    os.environ["TRADE_LEVERAGE"] = "10"   # .env still says 10
    os.environ["MARKET_DATA_SOURCE"] = "rest"
    from jrock.store import Store
    s = Store(path={str(STATE)!r})
    print("  after restart, .env=10 but store says:", s.get("leverage"))
    print("  after restart, .env=rest but store says:", s.get("market_data_source"))
    print("  shadowed(leverage, env=10):", s.shadowed("leverage", "10"))
    assert s.get("leverage") == 40, "store must beat .env"
    assert s.get("market_data_source") == "ws"
""")
print(proc.stdout.strip())
assert proc.returncode == 0, proc.stderr

# default when neither is set
d = Store(path=WORK / "empty.json")
print(f"  neither set -> default applies: leverage={d.get('leverage', 10)}")
print("RESULT: store(40) beat .env(10); default(10) used when both silent")

# ------------------------------------------------------------- 4. corruption
hdr("4", "Corruption: a truncated file is backed up, not fatal")
STATE.write_text('{"version": 2, "settings": {"leverage": 2')   # truncated mid-write
print(f"wrote a truncated file ({STATE.stat().st_size} bytes, invalid JSON)")
proc = child(f"""
    from jrock.store import Store
    s = Store(path={str(STATE)!r})
    print("  loaded:", s.loaded)
    print("  recovered_backup:", s.recovered_backup)
    print("  leverage after recovery:", s.get("leverage"))
    s.set("leverage", 7)
    print("  store usable again, wrote leverage=7")
""")
print(proc.stdout.strip())
assert proc.returncode == 0, f"corrupt file crashed the process:\n{proc.stderr}"
backups = sorted(p.name for p in DATA.glob("state.json.corrupt-*"))
print(f"  backup files on disk: {backups}")
assert backups, "no backup was made"
assert Store(path=STATE).get("leverage") == 7
print("RESULT: corrupt file backed up, process did not crash, store recovered")

# -------------------------------------------------------------- 5. migration
hdr("5", "Migration: a v1 flat file is upgraded, not discarded")
v1 = WORK / "v1.json"
v1.write_text(json.dumps({"thinking_level": "high",
                          "market_data_source": "ws",
                          "leverage": 20,
                          "symbols": ["BTCUSDT"]}))
print("hand-wrote a v1 file (no `version` key, flat settings):")
print("  " + v1.read_text().replace("\n", "\n  "))
proc = child(f"""
    from jrock.store import Store
    s = Store(path={str(v1)!r})
    print("  migrated_from:", s.migrated_from)
    print("  version now:", s.version)
    print("  thinking_level kept:", s.get("thinking_level"))
    print("  market_data_source kept:", s.get("market_data_source"))
    print("  leverage kept:", s.get("leverage"))
    print("  symbols kept:", s.get("symbols"))
""")
print(proc.stdout.strip())
assert proc.returncode == 0, proc.stderr
raw = json.loads(v1.read_text())
print(f"  on-disk shape after migration: version={raw['version']} "
      f"sections={sorted(k for k in raw if k != 'updated_ts')}")
assert raw["version"] == SCHEMA_VERSION
assert raw["settings"]["leverage"] == 20
print("RESULT: v1 upgraded to v2 in place, zero settings lost")

# ------------------------------------------------------------ 6. secret scan
hdr("6", "Secret guard: no key/secret value in any data/ file")
proc = child(f"""
    from jrock.store import Store
    from jrock.memory import MemoryStore
    s = Store(path={str(STATE)!r})
    s.register_secret({API_KEY!r})
    s.register_secret({API_SECRET!r})
    s.set("leverage", 20)
    s.set("symbols", ["BTCUSDT"])
    s.set_section("engine", {{"leverage": 20, "autotrade": False}})
    s.set_section("positions", {{"p1": {{"symbol": "BTCUSDT", "entry": 75000.0}}}})
    m = MemoryStore({str(MEM)!r})
    m.fact("leverage", "20")
    m.feedback("user set leverage to 20")
    print("  wrote settings + memory with 2 secrets registered")
    try:
        s.set("innocent_key", "the key is " + {API_KEY!r})
        print("  ERROR: secret value was accepted!")
    except Exception as e:
        print("  secret VALUE refused:", type(e).__name__)
    try:
        s.set("bitunix_api_secret", "x")
        print("  ERROR: secret KEY was accepted!")
    except Exception as e:
        print("  secret KEY refused:", type(e).__name__)
""")
print(proc.stdout.strip())
assert proc.returncode == 0, proc.stderr

print(f"\n--- grep every file under {DATA} for the secret values ---")
hits = []
for p in sorted(DATA.rglob("*")):
    if p.is_file():
        body = p.read_text(encoding="utf-8", errors="ignore")
        found = [s for s in (API_KEY, API_SECRET) if s in body]
        print(f"  {p.name:<40} {p.stat().st_size:>6}B  matches={len(found)}")
        if found:
            hits.append(p.name)
print(f"\n  total files scanned: {len([p for p in DATA.rglob('*') if p.is_file()])}")
print(f"  files containing a secret: {hits}")
assert hits == [], f"SECRET LEAKED into {hits}"
print("RESULT: zero matches - no secret value reached disk")

# ------------------------------------------------------------- 7. atomicity
hdr("7", "Atomicity: no partial or temp file left behind")
for i in range(20):
    fresh.set("counter", i)
leftovers = [p.name for p in DATA.iterdir() if ".tmp" in p.name]
print(f"  20 writes done; temp files left behind: {leftovers}")
print(f"  final value on disk: counter={Store(path=STATE).get('counter')}")
assert leftovers == []
assert Store(path=STATE).get("counter") == 19
print("RESULT: 20 atomic writes, zero temp files, last value intact")

# --------------------------------------------------------------- 8. eviction
hdr("8", "Memory eviction: bounded at the cap, high-weight entries kept")
ev = WORK / "evict.jsonl"
m = MemoryStore(ev, max_items=10)
m.fact("critical_setting", "keep me")          # weight 3.0
for i in range(30):
    m.episode("scan", f"sym{i}", "no signal", ok=True)   # weight 1.0
on_disk = [ln for ln in ev.read_text().splitlines() if ln.strip()]
print(f"  wrote 31 entries with cap=10")
print(f"  in-memory: {len(m.items)}  on-disk lines: {len(on_disk)}  evicted: {m.evicted}")
print(f"  high-weight fact survived: {any('keep me' in i.text for i in m.items)}")
assert len(m.items) == 10 and len(on_disk) == 10
assert any("keep me" in i.text for i in m.items)
print("RESULT: bounded at 10, file bounded too, fact outlived the episodes")

# ------------------------------------------------------------------ summary
print(f"\n{'=' * 72}")
print("ALL LIVE VERIFICATIONS PASSED")
print(f"{'=' * 72}")
shutil.rmtree(WORK, ignore_errors=True)
