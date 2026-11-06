"""Regression tests for the thinking-level system.

The reported problem: `/think` existed in the command list but there was no
visible level, no way to see the current one, and no explanation of what it did.

These tests pin the behaviour:
  * `/think` with no args prints the current level and never errors
  * `/think high` sets AND persists it (a fresh settings load still says high)
  * an invalid level is rejected with the valid list and a closest match
  * aliases map correctly
  * the request builder sends reasoning_effort / thinking_budget per level
  * `off` sends nothing at all
  * a model that does not support reasoning gets no parameter
"""
from __future__ import annotations

import asyncio
import json

import pytest

from jrock.agent import JRockAgent
from jrock.config import Settings
from jrock.settings_store import SettingsStore
from jrock.thinking import (ALIASES, BUDGETS, LEVELS, build_reasoning_params,
                            closest_level, detect_reasoning, normalize_level)


def _agent(tmp_path, **kw) -> JRockAgent:
    s = Settings(**kw)
    a = JRockAgent(s)
    # keep every test off the real data dir and the real .env
    a.engine.data_dir = tmp_path
    a.store = SettingsStore(tmp_path / "settings.json")
    return a


@pytest.fixture(autouse=True)
def _isolate_repo(tmp_path, monkeypatch):
    """Never let a test write the repo's real .env or data/ dir."""
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)
    monkeypatch.delenv("THINKING_LEVEL", raising=False)
    yield


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------- levels
def test_levels_are_in_order():
    assert LEVELS == ["off", "low", "medium", "high", "max"]


def test_default_is_medium(tmp_path):
    a = _agent(tmp_path)
    assert a.thinking_level == "medium"


@pytest.mark.parametrize("raw,expected", [
    ("off", "off"), ("OFF", "off"), ("none", "off"),
    ("low", "low"), ("min", "low"),
    ("medium", "medium"), ("med", "medium"), ("MED", "medium"),
    ("high", "high"), ("hi", "high"),
    ("max", "max"), ("maximum", "max"),
])
def test_aliases_map_correctly(raw, expected):
    assert normalize_level(raw) == expected


def test_alias_table_covers_every_level():
    for lvl in LEVELS:
        assert ALIASES[lvl] == lvl


def test_invalid_level_returns_none():
    assert normalize_level("banana") is None
    assert normalize_level("") is None
    assert normalize_level(None) is None


def test_closest_level_suggests():
    assert closest_level("hihg") == "high"
    assert closest_level("maxx") == "max"
    assert closest_level("zzzzzz") is None


# ------------------------------------------------------------- /think output
def test_think_no_args_prints_current_level_and_does_not_error(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("/think"))
    assert "Thinking level: medium" in out
    assert "not a chat mode" in out
    for lvl in LEVELS:
        assert lvl in out
    assert "Error" not in out and "Usage:" not in out


def test_think_status_is_alias_for_no_args(tmp_path):
    a = _agent(tmp_path)
    assert _run(a.handle("/think status")) == _run(a.handle("/think"))


def test_bare_think_routes_to_same_handler(tmp_path):
    a = _agent(tmp_path)
    assert _run(a.handle("think")) == _run(a.handle("/think"))


def test_think_sets_level(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("/think high"))
    assert "Thinking level = high" in out
    assert a.thinking_level == "high"


def test_think_persists_across_a_fresh_load(tmp_path):
    a = _agent(tmp_path)
    _run(a.handle("/think high"))
    # a brand new store reading the same file must still say high
    fresh = SettingsStore(tmp_path / "settings.json")
    assert fresh.get("thinking_level") == "high"


def test_think_persists_across_a_new_agent(tmp_path):
    a = _agent(tmp_path)
    _run(a.handle("/think max"))
    b = _agent(tmp_path)
    b.store = SettingsStore(tmp_path / "settings.json")
    assert b.thinking_level == "max"


def test_think_invalid_level_lists_options_and_closest(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("/think hihg"))
    assert "not a thinking level" in out
    assert "off | low | medium | high | max" in out
    assert "Closest match: high" in out
    assert a.thinking_level == "medium"      # unchanged


def test_think_invalid_level_does_not_crash(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("/think banana"))
    assert "not a thinking level" in out
    assert "Current level: medium" in out


def test_think_accepts_alias(tmp_path):
    a = _agent(tmp_path)
    _run(a.handle("/think maximum"))
    assert a.thinking_level == "max"


def test_think_off_is_valid(tmp_path):
    a = _agent(tmp_path)
    _run(a.handle("/think off"))
    assert a.thinking_level == "off"


# ------------------------------------------------------- request parameters
@pytest.mark.parametrize("level,expected", [
    ("off", {}),
    ("low", {"reasoning_effort": "low"}),
    ("medium", {"reasoning_effort": "medium"}),
    ("high", {"reasoning_effort": "high"}),
    ("max", {"reasoning_effort": "high"}),
])
def test_reasoning_effort_per_level(level, expected):
    assert build_reasoning_params(level, True, "reasoning_effort") == expected


@pytest.mark.parametrize("level,expected", [
    ("off", {}),
    ("low", {"thinking_budget": 1024}),
    ("medium", {"thinking_budget": 4096}),
    ("high", {"thinking_budget": 16384}),
    ("max", {"thinking_budget": 32768}),
])
def test_thinking_budget_per_level(level, expected):
    assert build_reasoning_params(level, True, "thinking_budget") == expected


def test_off_sends_nothing_even_when_supported():
    assert build_reasoning_params("off", True, "reasoning_effort") == {}
    assert build_reasoning_params("off", True, "thinking_budget") == {}


def test_unsupported_model_gets_no_parameter():
    for lvl in LEVELS:
        assert build_reasoning_params(lvl, False, None) == {}


def test_budget_table_matches_spec():
    assert BUDGETS == {"off": 0, "low": 1024, "medium": 4096,
                       "high": 16384, "max": 32768}


def test_agent_reasoning_params_uses_cached_metadata(tmp_path):
    a = _agent(tmp_path)
    a.model = "vendor/thinker"
    a.store.set("model_meta", {"vendor/thinker": {
        "supports_reasoning": True, "reasoning_param": "thinking_budget"}})
    _run(a.handle("/think high"))
    assert a.reasoning_params() == {"thinking_budget": 16384}


def test_agent_reasoning_params_skips_unsupported_model(tmp_path):
    a = _agent(tmp_path)
    a.model = "vendor/plain"
    a.store.set("model_meta", {"vendor/plain": {
        "supports_reasoning": False, "reasoning_param": None}})
    _run(a.handle("/think max"))
    assert a.reasoning_params() == {}


def test_chat_payload_carries_the_parameter(tmp_path, monkeypatch):
    """The level must reach the actual HTTP payload, not just the store."""
    a = _agent(tmp_path)
    a.s.api_key = "k"
    a.s.base_url = "https://x/v1"
    a.model = "vendor/thinker"
    a.store.set("model_meta", {"vendor/thinker": {
        "supports_reasoning": True, "reasoning_param": "reasoning_effort"}})
    _run(a.handle("/think high"))

    captured = {}

    class Resp:
        status = 200

        async def text(self):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

    class Sess:
        def post(self, url, json=None, **kw):  # noqa: A002
            captured.update(json or {})
            return Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

    monkeypatch.setattr("aiohttp.ClientSession", lambda *a, **k: Sess())
    _run(a.chat("hello"))
    assert captured.get("reasoning_effort") == "high"


def test_chat_payload_has_no_reasoning_field_when_off(tmp_path, monkeypatch):
    a = _agent(tmp_path)
    a.s.api_key = "k"
    a.s.base_url = "https://x/v1"
    a.model = "vendor/thinker"
    a.store.set("model_meta", {"vendor/thinker": {
        "supports_reasoning": True, "reasoning_param": "reasoning_effort"}})
    _run(a.handle("/think off"))

    captured = {}

    class Resp:
        status = 200

        async def text(self):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

    class Sess:
        def post(self, url, json=None, **kw):  # noqa: A002
            captured.update(json or {})
            return Resp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

    monkeypatch.setattr("aiohttp.ClientSession", lambda *a, **k: Sess())
    _run(a.chat("hello"))
    assert "reasoning_effort" not in captured
    assert "thinking_budget" not in captured


# ------------------------------------------------------- capability detection
def test_detect_reasoning_from_supported_parameters():
    assert detect_reasoning({"supported_parameters": ["reasoning_effort"]},
                            "x") == (True, "reasoning_effort")
    assert detect_reasoning({"supported_parameters": ["thinking"]},
                            "x") == (True, "thinking_budget")
    assert detect_reasoning({"supported_parameters": ["tools", "temperature"]},
                            "x") == (False, None)


def test_detect_reasoning_from_capabilities():
    assert detect_reasoning({"capabilities": {"reasoning": True}}, "x") == \
        (True, "reasoning_effort")
    assert detect_reasoning({"capabilities": {"reasoning": False}}, "x") == \
        (False, None)


def test_detect_reasoning_from_id_heuristic():
    assert detect_reasoning({}, "openai/o3-mini")[0] is True
    assert detect_reasoning({}, "deepseek/deepseek-r1")[0] is True
    assert detect_reasoning({}, "meta/llama-3.3-70b")[0] is None


# --------------------------------------------------------------- UI surfaces
def test_think_writes_env_mirror(tmp_path):
    """The level is mirrored into .env so the user can see it."""
    a = _agent(tmp_path)
    _run(a.handle("/think high"))
    env = tmp_path / ".env"
    assert env.exists()
    assert "THINKING_LEVEL=high" in env.read_text()


def test_think_does_not_touch_the_repo_env(tmp_path):
    """Guard against the test suite polluting the real repo .env."""
    from jrock.config import ROOT as REAL_ROOT
    before = (REAL_ROOT / ".env").read_text() if (REAL_ROOT / ".env").exists() else None
    a = _agent(tmp_path)
    _run(a.handle("/think max"))
    after = (REAL_ROOT / ".env").read_text() if (REAL_ROOT / ".env").exists() else None
    assert before == after


def test_default_is_medium_with_no_store_and_no_env(tmp_path):
    a = _agent(tmp_path)
    assert a.store.get("thinking_level") is None
    assert a.thinking_level == "medium"


def test_help_lists_think_with_description():
    from jrock.agent import HELP
    assert "/think [level]" in HELP
    assert "off|low|medium|high|max" in HELP
    assert "not a chat mode" in HELP


def test_status_includes_thinking_level(tmp_path):
    a = _agent(tmp_path)
    _run(a.handle("/think high"))
    out = _run(a.handle("status"))
    assert "thinking=high" in out


def test_startup_banner_includes_thinking_level(tmp_path, monkeypatch):
    a = _agent(tmp_path)
    _run(a.handle("/think low"))

    async def fake_verify():
        return True, "ok"

    monkeypatch.setattr(a.engine.client, "verify_credentials", fake_verify)
    out = _run(a.startup())
    assert "Thinking: low" in out
