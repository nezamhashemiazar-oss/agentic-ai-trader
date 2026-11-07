"""Regression tests for CLI routing (bug: everything went to the engine).

Observed:
    j+rock> help                  -> Unknown engine command: 'help'
    j+rock> switch to free model  -> Unknown engine command: 'switch to free model'

Required order:
    1. /command            -> agent dispatcher
    2. bare agent word     -> agent dispatcher
    3. engine grammar      -> engine
    4. anything else       -> the model (never "Unknown engine command")
"""
from __future__ import annotations

import asyncio

import pytest

from jrock.agent import AGENT_BARE, HELP, JRockAgent
from jrock.config import Settings


def _agent(tmp_path, **kw) -> JRockAgent:
    s = Settings(**kw)
    s.data_dir  # noqa: B018  (ensures dir exists)
    a = JRockAgent(s)
    # keep tests off the real data dir
    a.engine.data_dir = tmp_path
    return a


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- help routing
def test_slash_help_returns_full_list(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("/help"))
    assert out == HELP
    assert "AGENT COMMANDS" in out
    assert "TRADER ENGINE COMMANDS" in out
    assert "FREE TEXT" in out


def test_bare_help_returns_full_list(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("help"))
    assert out == HELP, f"bare 'help' must print the command list, got: {out[:120]}"


def test_question_mark_returns_full_list(tmp_path):
    a = _agent(tmp_path)
    assert _run(a.handle("?")) == HELP


def test_bare_help_never_says_unknown_engine_command(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("help"))
    assert "Unknown engine command" not in out


# ------------------------------------------------------- bare agent commands
@pytest.mark.parametrize("word,expected_fragment", [
    ("soul", "J+Rock"),
    ("memory", "memory"),
    ("mcp", "MCP"),
])
def test_bare_agent_words_hit_agent_dispatcher(tmp_path, word, expected_fragment):
    a = _agent(tmp_path)
    out = _run(a.handle(word))
    assert "Unknown engine command" not in out
    assert expected_fragment.lower() in out.lower()


def test_agent_bare_map_covers_help():
    assert AGENT_BARE["help"] == "/help"
    assert AGENT_BARE["?"] == "/help"


# ------------------------------------------------------------ engine routing
@pytest.mark.parametrize("line", [
    "status",
    "scan off",
    "scan on",
    "report interval sec 45",
    "min agreeing strategies 4",
    "breakeven threshold pct 0.8",
    "max open positions 5",
])
def test_engine_grammar_reaches_engine(tmp_path, line):
    a = _agent(tmp_path)
    out = _run(a.handle(line))
    assert "Unknown engine command" not in out
    assert "Unknown command" not in out


def test_engine_state_actually_mutates(tmp_path):
    a = _agent(tmp_path)
    _run(a.handle("min agreeing strategies 5"))
    assert a.engine.state.min_agreeing == 5
    _run(a.handle("scan off"))
    assert a.engine.state.scan_on is False


# --------------------------------------------------------- free text routing
def test_free_text_goes_to_model_not_engine(tmp_path):
    a = _agent(tmp_path)
    a.s.api_key = ""          # no key -> chat() reports that, does not crash
    out = _run(a.handle("write me a poem about trading"))
    assert "Unknown engine command" not in out
    assert "model" in out.lower()


def test_why_did_my_order_fail_is_answered_from_the_engine(tmp_path):
    """This used to fall through to the model. It is now a real intent.

    The engine owns the error log, so it answers from its own state instead of
    asking a model to guess. The old assertion ("goes to the model") encoded
    the very behaviour the natural-language layer was added to fix.
    """
    a = _agent(tmp_path)
    a.s.api_key = ""
    out = _run(a.handle("why did my order fail yesterday?"))
    assert "Unknown engine command" not in out
    assert "no errors" in out.lower() or "error" in out.lower()


def test_switch_to_free_model_is_understood(tmp_path):
    """The exact reported input must not fall through to the engine."""
    a = _agent(tmp_path)
    a.s.api_key = ""          # no key -> honest message, not an engine error
    out = _run(a.handle("switch to free model"))
    assert "Unknown engine command" not in out
    assert "API_KEY" in out or "free model" in out.lower()


def test_switch_to_free_model_with_key_probes_free_only(tmp_path, monkeypatch):
    a = _agent(tmp_path)
    a.s.api_key = "k"
    a.s.base_url = "https://x/v1"
    # never write the repo's real .env from a test
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)

    calls = {}

    async def fake_autoset(base_url, api_key, **kw):
        calls["free_only"] = kw.get("free_only")
        from jrock.model_selector import AutoSetResult
        return AutoSetResult(base_url, True, selected="meta/llama:free",
                             message="Selected meta/llama:free (free)")

    monkeypatch.setattr("jrock.model_selector.autoset_model", fake_autoset)
    out = _run(a.handle("switch to free model"))
    assert calls["free_only"] is True
    assert "meta/llama:free" in out
    # the model was persisted to the TEMP .env, not the repo one
    assert (tmp_path / ".env").exists()
    assert "meta/llama:free" in (tmp_path / ".env").read_text()


# ------------------------------------------------------------- engine guard
def test_looks_like_command_is_strict(tmp_path):
    a = _agent(tmp_path)
    e = a.engine
    assert e.looks_like_command("status") is True
    assert e.looks_like_command("scan off") is True
    assert e.looks_like_command("min agreeing strategies 3") is True
    # natural language must NOT look like engine syntax
    assert e.looks_like_command("switch to free model") is False
    assert e.looks_like_command("why did my order fail?") is False
    assert e.looks_like_command("help") is False
    assert e.looks_like_command("what is my pnl today") is False


def test_unknown_slash_command_offers_help(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("/nonsense"))
    assert "Unknown command" in out
    assert "/help" in out
