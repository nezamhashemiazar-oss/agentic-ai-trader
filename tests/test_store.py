"""Regression tests for the persistent store.

Every rule the task requires is covered here:
  * settings round-trip through a real file
  * SIGKILL-style persistence (write, then re-read from a *fresh* Store)
  * precedence store > .env > default
  * atomic write leaves no partial/temp file behind
  * corrupt file backed up and recovered, engine still starts
  * schema migration from an older version
  * memory round-trip and eviction at the cap
  * secrets never written to disk
  * /memory and /status reflect the real store state
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from jrock.memory import MemoryStore
from jrock.store import (SCHEMA_VERSION, SecretInStoreError, Store,
                         atomic_write_text, is_secret_key)


# --------------------------------------------------------------------- helpers
def _store(tmp_path: Path, name: str = "state.json") -> Store:
    return Store(path=tmp_path / name)


# ============================================================ 1. round-trip
def test_settings_round_trip_through_a_real_file(tmp_path):
    s = _store(tmp_path)
    s.set("leverage", 25)
    s.set("symbols", ["BTCUSDT", "ETHUSDT"])
    s.set("risk_pct", 1.5)
    s.set("mid_management", False)

    assert (tmp_path / "state.json").exists()
    raw = json.loads((tmp_path / "state.json").read_text())
    assert raw["version"] == SCHEMA_VERSION
    assert raw["settings"]["leverage"] == 25

    again = _store(tmp_path)
    assert again.get("leverage") == 25
    assert again.get("symbols") == ["BTCUSDT", "ETHUSDT"]
    assert again.get("risk_pct") == 1.5
    assert again.get("mid_management") is False


def test_engine_section_round_trip(tmp_path):
    s = _store(tmp_path)
    s.set_section("engine", {"leverage": 20, "symbols": ["BTCUSDT"],
                             "autotrade": True, "engine_on": True})
    again = _store(tmp_path)
    assert again.section("engine")["leverage"] == 20
    assert again.section("engine")["autotrade"] is True


def test_positions_round_trip(tmp_path):
    s = _store(tmp_path)
    s.set_section("positions", {"p1": {"symbol": "BTCUSDT", "breakeven_done": True,
                                       "trailing_stop": 76000.0, "peak_roi": 31.2}})
    again = _store(tmp_path)
    body = again.section("positions")["p1"]
    assert body["breakeven_done"] is True
    assert body["trailing_stop"] == 76000.0
    assert body["peak_roi"] == 31.2


# ================================================= 2. SIGKILL-style persistence
def test_sigkill_persistence_via_a_fresh_process(tmp_path):
    """Write in a child process, SIGKILL it, then read from a new process.

    This is the real test: the value must be on disk *before* the kill, not
    flushed at interpreter exit. A `finally`/atexit flush would pass a naive
    in-process test and still lose the data here.
    """
    script = textwrap.dedent(f"""
        import os, signal, sys
        sys.path.insert(0, {str(Path.cwd())!r})
        from jrock.store import Store
        s = Store(path={str(tmp_path / "state.json")!r})
        s.set("leverage", 33)
        s.set("symbols", ["SOLUSDT"])
        s.set_section("engine", {{"leverage": 33, "autotrade": True}})
        # Hard-kill ourselves: no atexit, no finally, no flush-on-exit.
        os.kill(os.getpid(), signal.SIGKILL)
    """)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True)
    assert proc.returncode == -9, f"expected SIGKILL, got {proc.returncode}"

    fresh = Store(path=tmp_path / "state.json")
    assert fresh.get("leverage") == 33
    assert fresh.get("symbols") == ["SOLUSDT"]
    assert fresh.section("engine")["autotrade"] is True


def test_memory_survives_sigkill(tmp_path):
    script = textwrap.dedent(f"""
        import os, signal, sys
        sys.path.insert(0, {str(Path.cwd())!r})
        from jrock.memory import MemoryStore
        m = MemoryStore({str(tmp_path / "memory.jsonl")!r})
        m.feedback("user prefers 5x leverage on BTCUSDT", weight=3.0)
        os.kill(os.getpid(), signal.SIGKILL)
    """)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True)
    assert proc.returncode == -9

    fresh = MemoryStore(tmp_path / "memory.jsonl")
    assert fresh.stats()["total"] == 1
    hits = fresh.search("leverage BTCUSDT")
    assert hits and "5x leverage" in hits[0].text


# ============================================================ 3. precedence
def test_precedence_store_beats_env(tmp_path, monkeypatch):
    """A runtime change must win over .env on the next start."""
    monkeypatch.setenv("TRADE_LEVERAGE", "10")
    s = _store(tmp_path)
    s.set("leverage", 40)          # changed at runtime

    # simulate the next start: .env still says 10, store says 40
    monkeypatch.setenv("TRADE_LEVERAGE", "10")
    again = _store(tmp_path)
    assert again.get("leverage") == 40
    assert again.shadowed("leverage", "10") is True


def test_precedence_env_beats_default_when_store_is_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADE_LEVERAGE", "17")
    s = _store(tmp_path)
    assert s.get("leverage") is None      # store silent -> .env applies
    assert os.getenv("TRADE_LEVERAGE") == "17"


def test_precedence_default_when_neither_is_set(tmp_path):
    s = _store(tmp_path)
    assert s.get("leverage", 10) == 10


def test_agent_applies_stored_market_data_source_over_env(tmp_path, monkeypatch):
    """End-to-end precedence through the real agent."""
    from jrock.agent import JRockAgent
    from jrock.config import Settings

    monkeypatch.setattr("jrock.config.ROOT", tmp_path)
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)
    monkeypatch.setenv("MARKET_DATA_SOURCE", "rest")

    # The agent's store lives in <ROOT>/data/state.json, not <ROOT>/state.json.
    st = Store(path=tmp_path / "data" / "state.json")
    st.set("market_data_source", "ws")

    a = JRockAgent(Settings(market_data_source="rest"))
    assert a.s.market_data_source == "ws", "store must override .env"


# ============================================================ 4. atomic write
def test_atomic_write_leaves_no_temp_file(tmp_path):
    target = tmp_path / "state.json"
    s = Store(path=target)
    for i in range(5):
        s.set("n", i)
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert leftovers == [], f"temp files left behind: {leftovers}"
    assert json.loads(target.read_text())["settings"]["n"] == 4


def test_atomic_write_text_is_all_or_nothing(tmp_path):
    target = tmp_path / "x.json"
    atomic_write_text(target, '{"a": 1}')
    assert json.loads(target.read_text()) == {"a": 1}
    atomic_write_text(target, '{"a": 2}')
    assert json.loads(target.read_text()) == {"a": 2}
    assert not [p for p in tmp_path.iterdir() if ".tmp" in p.name]


def test_truncated_file_is_recovered_not_crashed(tmp_path):
    """A half-written file (crash mid-write) must not take the process down."""
    target = tmp_path / "state.json"
    target.write_text('{"version": 2, "settings": {"leverage": 2')  # truncated

    s = Store(path=target)          # must not raise
    assert s.loaded is False
    assert s.recovered_backup, "corrupt file must be backed up"
    assert Path(s.recovered_backup).exists()
    assert s.get("leverage") is None

    # and the store is usable again
    s.set("leverage", 7)
    assert Store(path=target).get("leverage") == 7


def test_corrupt_json_is_backed_up_and_engine_still_starts(tmp_path):
    from jrock.agent import JRockAgent
    from jrock.config import Settings

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "state.json").write_text("{not json at all")
    a = JRockAgent(Settings())
    assert a.store.recovered_backup
    banner = a._state_banner()
    assert "corrupt" in banner.lower()
    assert "backed up" in banner.lower()


def test_non_object_json_is_treated_as_corrupt(tmp_path):
    target = tmp_path / "state.json"
    target.write_text("[1, 2, 3]")
    s = Store(path=target)
    assert s.recovered_backup
    assert s.loaded is False


# ============================================================ 5. migration
def test_migration_from_v1_flat_file(tmp_path):
    """v1 was a bare flat dict with no `version` key. It must be upgraded."""
    target = tmp_path / "state.json"
    target.write_text(json.dumps({
        "thinking_level": "high",
        "market_data_source": "ws",
        "leverage": 20,
    }))

    s = Store(path=target)
    assert s.migrated_from == 1
    assert s.version == SCHEMA_VERSION
    # nothing discarded
    assert s.get("thinking_level") == "high"
    assert s.get("market_data_source") == "ws"
    assert s.get("leverage") == 20
    # and the upgrade is written back in the new shape
    raw = json.loads(target.read_text())
    assert raw["version"] == SCHEMA_VERSION
    assert raw["settings"]["leverage"] == 20


def test_migration_preserves_existing_v2_sections(tmp_path):
    target = tmp_path / "state.json"
    target.write_text(json.dumps({
        "version": 2,
        "settings": {"leverage": 5},
        "engine": {"symbols": ["BTCUSDT"]},
        "positions": {"p1": {"symbol": "BTCUSDT"}},
    }))
    s = Store(path=target)
    assert s.migrated_from is None
    assert s.get("leverage") == 5
    assert s.section("engine")["symbols"] == ["BTCUSDT"]
    assert s.section("positions")["p1"]["symbol"] == "BTCUSDT"


def test_newer_schema_is_read_not_discarded(tmp_path):
    target = tmp_path / "state.json"
    target.write_text(json.dumps({
        "version": SCHEMA_VERSION + 5,
        "settings": {"leverage": 9},
        "future_section": {"x": 1},
    }))
    s = Store(path=target)
    assert s.get("leverage") == 9
    assert s.recovered_backup == "", "a newer version is not corruption"


# ============================================================ 6. memory
def test_memory_round_trip(tmp_path):
    m = MemoryStore(tmp_path / "memory.jsonl")
    m.feedback("never trade AKEUSDT", weight=3.0)
    m.fact("leverage", "20")
    m.episode("scan", "BTCUSDT", "no signal", ok=True)

    again = MemoryStore(tmp_path / "memory.jsonl")
    assert again.stats()["total"] == 3
    assert again.stats()["by_kind"] == {"feedback": 1, "fact": 1, "episode": 1}
    hits = again.search("AKEUSDT")
    assert hits and "never trade AKEUSDT" in hits[0].text


def test_memory_eviction_at_the_cap(tmp_path):
    m = MemoryStore(tmp_path / "memory.jsonl", max_items=10)
    for i in range(25):
        m.episode("scan", f"sym{i}", "no signal", ok=True)

    assert len(m.items) == 10
    assert m.evicted == 15
    # the file itself is bounded too, not just the in-memory list
    on_disk = [ln for ln in (tmp_path / "memory.jsonl").read_text().splitlines() if ln.strip()]
    assert len(on_disk) == 10
    # and a fresh load agrees
    assert MemoryStore(tmp_path / "memory.jsonl", max_items=10).stats()["total"] == 10


def test_memory_eviction_keeps_high_weight_entries(tmp_path):
    """A fact (w=3) must outlive routine episodes (w=1) of the same age."""
    m = MemoryStore(tmp_path / "memory.jsonl", max_items=3)
    m.fact("critical", "keep me")            # weight 3.0
    for i in range(5):
        m.episode("scan", f"sym{i}", "no signal", ok=True)   # weight 1.0

    texts = [i.text for i in m.items]
    assert any("keep me" in t for t in texts), "the high-weight fact was evicted"


def test_memory_clear(tmp_path):
    m = MemoryStore(tmp_path / "memory.jsonl")
    m.feedback("a")
    m.feedback("b")
    assert m.clear() == 2
    assert m.stats()["total"] == 0
    assert MemoryStore(tmp_path / "memory.jsonl").stats()["total"] == 0


def test_memory_stats_report_size_and_cap(tmp_path):
    m = MemoryStore(tmp_path / "memory.jsonl", max_items=100)
    m.feedback("hello world")
    st = m.stats()
    assert st["max_items"] == 100
    assert st["size_bytes"] > 0
    assert st["last_write"] > 0


# ============================================================ 7. secret guard
def test_secret_key_names_are_refused(tmp_path):
    s = _store(tmp_path)
    for key in ("bitunix_api_secret", "BITUNIX_API_KEY", "api_key",
                "llm_secret", "db_password", "private_key", "access_token"):
        with pytest.raises(SecretInStoreError):
            s.set(key, "whatever")


def test_ordinary_keys_are_not_mistaken_for_secrets():
    for key in ("max_tokens", "completion_tokens", "kline_cache_max",
                "min_confidence", "leverage", "symbols", "thinking_level"):
        assert is_secret_key(key) is False, f"{key} wrongly flagged as secret"
    for key in ("api_key", "bitunix_api_secret", "password", "auth_token"):
        assert is_secret_key(key) is True


def test_registered_secret_value_never_reaches_disk(tmp_path):
    """The strongest guard: even under an innocent key, the VALUE is refused."""
    secret = "sk-live-DEADBEEF1234567890"
    s = _store(tmp_path)
    s.register_secret(secret)
    s.set("note", "hello")                       # fine
    with pytest.raises(SecretInStoreError):
        s.set("some_innocent_key", f"the key is {secret}")
    # the good write landed, the bad one did not
    assert json.loads((tmp_path / "state.json").read_text())["settings"]["note"] == "hello"


def test_no_secret_value_in_any_data_file(tmp_path):
    """Grep every file under a data dir for the secret values: zero matches."""
    api_key = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    api_secret = "bitunix-secret-9876543210zyxwvuts"
    data = tmp_path / "data"
    data.mkdir()

    s = Store(path=data / "state.json")
    s.register_secret(api_key)
    s.register_secret(api_secret)
    s.set("leverage", 20)
    s.set("symbols", ["BTCUSDT"])
    s.set_section("engine", {"leverage": 20, "autotrade": False})
    s.set_section("positions", {"p1": {"symbol": "BTCUSDT", "entry": 75000.0}})

    m = MemoryStore(data / "memory.jsonl")
    m.fact("leverage", "20")
    m.feedback("user set leverage to 20")

    hits = []
    for p in data.rglob("*"):
        if p.is_file():
            body = p.read_text(encoding="utf-8", errors="ignore")
            if api_key in body or api_secret in body:
                hits.append(str(p))
    assert hits == [], f"secret leaked into: {hits}"


def test_agent_registers_its_secrets(tmp_path, monkeypatch):
    from jrock.agent import JRockAgent
    from jrock.config import Settings

    monkeypatch.setattr("jrock.config.ROOT", tmp_path)
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)
    a = JRockAgent(Settings(api_key="sk-llm-1234567890abcdef",
                            bitunix_api_key="bk-1234567890abcdef",
                            bitunix_api_secret="bs-1234567890abcdef"))
    assert len(a.store._secrets) == 3
    with pytest.raises(SecretInStoreError):
        a.store.set("innocent", "value is bk-1234567890abcdef")


# ============================================================ 8. UI surfaces
def test_memory_command_shows_real_store_state(tmp_path, monkeypatch):
    from jrock.agent import JRockAgent
    from jrock.config import Settings

    monkeypatch.setattr("jrock.config.ROOT", tmp_path)
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)
    a = JRockAgent(Settings())
    a.memory.feedback("prefers 3x leverage", weight=3.0)

    out = a.cmd_memory("")
    assert "=== memory ===" in out
    assert "entries: 1" in out
    assert "size on disk:" in out
    assert "last write:" in out
    assert "prefers 3x leverage" in out


def test_memory_clear_requires_confirmation(tmp_path, monkeypatch):
    from jrock.agent import JRockAgent
    from jrock.config import Settings

    monkeypatch.setattr("jrock.config.ROOT", tmp_path)
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)
    a = JRockAgent(Settings())
    a.memory.feedback("keep me")

    ask = a.cmd_memory("clear")
    assert "cannot be undone" in ask
    assert a.memory.stats()["total"] == 1, "clear must not fire without confirmation"

    done = a.cmd_memory("clear confirm")
    assert "cleared" in done
    assert a.memory.stats()["total"] == 0


def test_status_shows_store_path_version_and_last_save(tmp_path, monkeypatch):
    import asyncio
    from jrock.agent import JRockAgent
    from jrock.config import Settings

    monkeypatch.setattr("jrock.config.ROOT", tmp_path)
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)
    a = JRockAgent(Settings())
    a.engine.state.leverage = 21
    a.engine._persist_if_changed()

    out = asyncio.run(a.engine.status())
    assert "store:" in out
    assert "schema=v2" in out
    assert "last_save=" in out
    assert "state.json" in out


def test_banner_reports_what_was_loaded(tmp_path, monkeypatch):
    from jrock.agent import JRockAgent
    from jrock.config import Settings

    monkeypatch.setattr("jrock.config.ROOT", tmp_path)
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)

    first = JRockAgent(Settings())
    assert "fresh start" in first._state_banner()

    first.store.set("leverage", 20)
    first.memory.feedback("remembered thing")

    second = JRockAgent(Settings())
    banner = second._state_banner()
    assert "State: loaded" in banner
    assert "1 settings" in banner
    assert "1 memory entries" in banner


def test_banner_warns_when_autotrade_was_on(tmp_path, monkeypatch):
    from jrock.agent import JRockAgent
    from jrock.config import Settings

    monkeypatch.setattr("jrock.config.ROOT", tmp_path)
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)

    a = JRockAgent(Settings())
    a.engine.state.autotrade = True
    a.engine._persist_if_changed()
    assert a.store.section("engine")["autotrade"] is True

    b = JRockAgent(Settings())
    assert b.engine.state.autotrade is False, "autotrade must NOT auto-resume"
    assert "NOT auto-resumed" in b._state_banner()


# ============================================================ 9. engine wiring
def test_last_save_survives_a_restart(tmp_path):
    """A restarted process must not report `last_save=never`.

    The file's own `updated_ts` is the truth; without reading it back, /status
    claimed the store had never been saved even though it was written seconds
    earlier.
    """
    s = _store(tmp_path)
    s.set("leverage", 20)
    assert s.last_save_ts > 0

    again = _store(tmp_path)
    assert again.last_save_ts > 0, "last_save was lost on reload"
    assert again.summary()["last_save"] == s.last_save_ts


def test_summary_counts_engine_settings(tmp_path):
    """The banner/status count must include the engine section, not just settings."""
    s = _store(tmp_path)
    s.set("thinking_level", "high")
    s.set_section("engine", {"leverage": 20, "symbols": ["BTCUSDT"]})
    sm = s.summary()
    assert sm["settings"] == 1
    assert sm["engine_settings"] == 2


def test_engine_command_persists_state(tmp_path, monkeypatch):
    import asyncio
    from jrock.agent import JRockAgent
    from jrock.config import Settings

    monkeypatch.setattr("jrock.config.ROOT", tmp_path)
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)
    a = JRockAgent(Settings())

    asyncio.run(a.engine.command("leverage 20"))
    asyncio.run(a.engine.command("max open positions 5"))
    asyncio.run(a.engine.command("breakeven threshold pct 0.8"))
    asyncio.run(a.engine.command("min agreeing strategies 5"))

    b = JRockAgent(Settings())
    assert b.engine.state.leverage == 20
    assert b.engine.state.max_open_positions == 5
    assert b.engine.state.breakeven_pct == 0.8
    assert b.engine.state.min_agreeing == 5


def test_engine_restores_positions_and_management_state(tmp_path, monkeypatch):
    from jrock.agent import JRockAgent
    from jrock.config import Settings
    from jrock.trader.tpsl import PositionState

    monkeypatch.setattr("jrock.config.ROOT", tmp_path)
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)
    a = JRockAgent(Settings())
    a.engine.positions["p1"] = PositionState(
        position_id="p1", symbol="BTCUSDT", side="LONG", entry=75000.0,
        qty=0.01, leverage=20, sl=76000.0, tp=80000.0, liq=60000.0,
        breakeven_done=True, trailing_active=True, trailing_stop=76500.0,
        peak_roi=31.5, opened_ts=1234.0)
    a.engine.persist_positions()

    b = JRockAgent(Settings())
    st = b.engine.positions["p1"]
    assert st.breakeven_done is True
    assert st.trailing_active is True
    assert st.trailing_stop == 76500.0
    assert st.peak_roi == 31.5
    assert b.engine.restored["positions"] == 1


def test_counters_survive_restart(tmp_path, monkeypatch):
    from jrock.agent import JRockAgent
    from jrock.config import Settings

    monkeypatch.setattr("jrock.config.ROOT", tmp_path)
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)
    a = JRockAgent(Settings())
    a.engine.state.scans = 42
    a.engine.state.errors.append("boom")
    a.engine._persist_if_changed()

    b = JRockAgent(Settings())
    assert b.engine.state.scans == 42
    assert b.store.section("counters")["errors"] == 1
    assert b.store.section("counters")["last_error"] == "boom"


def test_rest_mode_opens_no_socket_and_persists_nothing_extra(tmp_path, monkeypatch):
    """A rest-mode start must not create a WS feed or a stray file."""
    import asyncio
    from jrock.agent import JRockAgent
    from jrock.config import Settings

    monkeypatch.setattr("jrock.config.ROOT", tmp_path)
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)
    a = JRockAgent(Settings(market_data_source="rest"))
    asyncio.run(a.engine.start_feed())
    assert a.engine.feed is None
    assert a.engine.feed_connected() is False
