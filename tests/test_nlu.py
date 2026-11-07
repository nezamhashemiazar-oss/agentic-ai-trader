"""Regression tests for the natural-language intent layer.

The reported problem: the engine only reacted to exact command strings. A
normal sentence ("show me my trading settings") matched no command and fell
through to the model, which has no access to the engine.

Two things must hold at once:
  * a normal sentence is understood and mapped onto the SAME command the user
    could have typed, and
  * every existing command string routes EXACTLY as it did before.

The second half is the important one: an intent layer that breaks the commands
is worse than no intent layer.
"""
from __future__ import annotations

import asyncio

import pytest

from jrock import nlu
from jrock.agent import AGENT_BARE, HELP, JRockAgent
from jrock.config import Settings


def _agent(tmp_path, **kw) -> JRockAgent:
    s = Settings(**kw)
    s.data_dir  # noqa: B018  (ensures dir exists)
    a = JRockAgent(s)
    a.engine.data_dir = tmp_path
    return a


def _run(coro):
    return asyncio.run(coro)


# ==========================================================================
# 1. The parser maps sentences onto real commands
# ==========================================================================

@pytest.mark.parametrize("sentence,expected", [
    # settings / configs
    ("show me my trading settings", "settings trade"),
    ("what are my settings", "settings trade"),
    ("show me the configs", "settings trade"),
    ("display my trading configuration", "settings trade"),
    # balance / equity
    ("what is my balance", "balance"),
    ("show me my equity", "balance"),
    ("how much do i have", "balance"),
    ("check my wallet", "balance"),
    # positions
    ("show my positions", "positions"),
    ("am i in any positions", "positions"),
    ("list my open positions", "positions"),
    # orders
    ("what are my open orders", "orders"),
    ("show me pending orders", "orders"),
    # pnl
    ("what is my pnl", "pnl"),
    ("show me my profit", "pnl"),
    # status
    ("show me the status", "status"),
    ("what is the engine status", "status"),
    # tpsl / ladder
    ("show me the tp/sl", "tpsl"),
    ("show me the ladder", "ladder"),
    # price / funding / symbols
    ("what is the price of BTCUSDT", "price BTCUSDT"),
    ("show me the funding rate", "funding"),
    ("list the symbols", "symbols"),
    # help
    ("what can you do", "help"),
])
def test_sentences_map_to_commands(sentence, expected):
    r = nlu.parse(sentence)
    assert isinstance(r, nlu.Intent), f"{sentence!r} -> {r!r}"
    assert r.command == expected


@pytest.mark.parametrize("sentence,expected", [
    ("set leverage to 5x", "leverage 5"),
    ("change my leverage to 20", "leverage 20"),
    ("set risk to 2%", "risk 2.0"),
    ("set margin mode to cross", "margin mode CROSS"),
    ("set margin mode to isolation", "margin mode ISOLATION"),
    ("set position mode to hedge", "position mode HEDGE"),
    ("close my AKEUSDT position", "close position AKEUSDT"),
    ("close all positions", "close position"),
    ("start autotrade", "start trading"),
    ("stop trading", "stop trading"),
    ("turn off autotrade", "stop trading"),
    ("turn on autotrade", "start trading"),
    ("start the engine", "engine on"),
    ("stop the engine", "engine off"),
    ("turn off the scanner", "scan off"),
    ("turn on the scanner", "scan on"),
])
def test_setter_sentences_map_to_commands(sentence, expected):
    r = nlu.parse(sentence)
    assert isinstance(r, nlu.Intent), f"{sentence!r} -> {r!r}"
    assert r.command == expected


# ==========================================================================
# 2. Destructive intents are flagged and gated
# ==========================================================================

@pytest.mark.parametrize("sentence", [
    "set leverage to 5x",
    "set risk to 2%",
    "close my AKEUSDT position",
    "close all positions",
    "start autotrade",
    "start the engine",
    "set margin mode to cross",
    "set position mode to hedge",
    "open long BTCUSDT",
])
def test_money_moving_intents_are_destructive(sentence):
    r = nlu.parse(sentence)
    assert isinstance(r, nlu.Intent)
    assert r.destructive is True, f"{sentence!r} must require confirmation"


@pytest.mark.parametrize("sentence", [
    "show me my trading settings",
    "what is my balance",
    "show my positions",
    "what is my pnl",
    "show me the status",
    "stop trading",
    "turn off autotrade",
    "stop the engine",
    "turn off the scanner",
])
def test_read_only_intents_are_not_destructive(sentence):
    r = nlu.parse(sentence)
    assert isinstance(r, nlu.Intent)
    assert r.destructive is False, f"{sentence!r} must not require confirmation"


def test_destructive_sentence_asks_before_acting(tmp_path):
    """A money-moving sentence must NOT execute on the first turn."""
    a = _agent(tmp_path)
    out = _run(a.handle("set leverage to 5x"))
    assert "confirm" in out.lower()
    assert "leverage 5" in out
    # nothing changed yet
    assert a.engine.state.leverage != 5
    assert a._pending_action is not None


def test_confirming_runs_the_action(tmp_path):
    a = _agent(tmp_path)
    _run(a.handle("set leverage to 5x"))
    out = _run(a.handle("yes"))
    assert "confirmed" in out.lower()
    assert a.engine.state.leverage == 5
    assert a._pending_action is None


def test_cancelling_does_not_run_the_action(tmp_path):
    a = _agent(tmp_path)
    before = a.engine.state.leverage
    _run(a.handle("set leverage to 5x"))
    out = _run(a.handle("no"))
    assert "cancelled" in out.lower()
    assert a.engine.state.leverage == before
    assert a._pending_action is None


def test_autotrade_never_starts_from_a_sentence_alone(tmp_path):
    """The exact safety property: a sentence must not enable real-money trading."""
    a = _agent(tmp_path)
    _run(a.handle("start autotrade"))
    assert a.engine.state.autotrade is False, "autotrade must not start unconfirmed"
    assert a._pending_action is not None


def test_unrelated_reply_keeps_waiting(tmp_path):
    a = _agent(tmp_path)
    _run(a.handle("set leverage to 5x"))
    out = _run(a.handle("what is the weather"))
    assert "still waiting" in out.lower()
    assert a._pending_action is not None
    assert a.engine.state.leverage != 5


# ==========================================================================
# 3. Ambiguity asks instead of guessing
# ==========================================================================

def test_close_without_symbol_asks_which(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("close my position"))
    assert "which symbol" in out.lower()
    assert a._pending_action is None, "an incomplete intent must not be queued"


def test_leverage_without_value_asks(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("set my leverage"))
    assert "leverage value" in out.lower()
    assert a._pending_action is None


def test_place_order_without_side_asks(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("open a position on BTCUSDT"))
    assert "long or short" in out.lower()
    assert a._pending_action is None


def test_place_order_from_a_sentence_is_refused_not_guessed(tmp_path):
    """A direct order has no signal, so it must not invent a size."""
    a = _agent(tmp_path)
    out = _run(a.handle("open long BTCUSDT"))
    assert "confirm" in out.lower()
    out2 = _run(a.handle("yes"))
    assert "will not place it" in out2.lower()
    assert "guess" in out2.lower()


# ==========================================================================
# 4. The engine answers about its OWN settings, from the repo
# ==========================================================================

def test_tp_sl_min_stop_ticks_is_explained_from_the_repo(tmp_path):
    """The engine once said it had no access to the engine. It does."""
    a = _agent(tmp_path)
    out = _run(a.handle("what does TP_SL_MIN_STOP_TICKS do"))
    assert "no access" not in out.lower()
    assert "TP_SL_MIN_STOP_TICKS" in out
    assert "jrock/config.py" in out
    assert "tick" in out.lower()
    # it must state the ACTUAL configured value
    assert str(a.s.tpsl_min_stop_ticks) in out


def test_taker_fee_rate_is_explained(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("explain TAKER_FEE_RATE"))
    assert "TAKER_FEE_RATE" in out
    assert "jrock/config.py" in out
    assert "20003" in out


def test_margin_safety_pct_is_explained(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("what is MARGIN_SAFETY_PCT for"))
    assert "MARGIN_SAFETY_PCT" in out
    assert "jrock/config.py" in out


def test_why_did_the_last_order_fail_uses_engine_state(tmp_path):
    a = _agent(tmp_path)
    a.engine.state.errors.append("place_order AKEUSDT: [20003] Insufficient balance")
    out = _run(a.handle("why did the last order fail"))
    assert "20003" in out
    # the actionable hint, not just the raw code
    assert "margin" in out.lower()


def test_why_failed_with_no_errors_says_so(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("why did the last order fail"))
    assert "no errors" in out.lower()


# ==========================================================================
# 5. NO REGRESSIONS: existing command strings route identically
# ==========================================================================

@pytest.mark.parametrize("line", [
    "help", "?", "/help",
    "status", "pnl", "p&l",
    "scan on", "scan off",
    "report on", "report off",
    "engine on", "engine off",
    "start trading", "stop trading", "autotrade on", "autotrade off",
    "settings", "settings trade",
    "min agreeing strategies 4",
    "breakeven threshold pct 0.8",
    "max open positions 5",
    "leverage 10", "risk 1.0",
    "margin mode ISOLATION", "position mode HEDGE",
    "tpsl method PARTIAL", "partial close pct 50", "partial at roi 50",
    "ladder", "ladder off", "ladder breakeven 2", "ladder runner trail",
    "account tp usdt 100", "account sl usdt 50",
    "scans interval sec 30", "report interval sec 45",
    "timeframes 1m,3m,5m,15m",
    "min confidence 70", "tf min confidence 60",
    "reversal=true", "reversal_confidence=70",
    "mid management",
])
def test_existing_commands_still_route(tmp_path, line):
    """Every documented command must still reach a handler, not the model."""
    a = _agent(tmp_path)
    out = _run(a.handle(line))
    assert "Unknown engine command" not in out, f"{line!r} -> {out[:120]}"
    assert "Unknown command" not in out, f"{line!r} -> {out[:120]}"


def test_existing_commands_are_not_hijacked_by_nlu(tmp_path):
    """A line that is already a command must never reach the NLU layer."""
    a = _agent(tmp_path)
    # "stop trading" is BOTH an engine command and an NLU intent. The engine
    # command must win, and it must NOT ask for confirmation.
    out = _run(a.handle("stop trading"))
    assert "confirm" not in out.lower()
    assert a._pending_action is None
    assert a.engine.state.autotrade is False


def test_engine_setter_still_mutates_state(tmp_path):
    a = _agent(tmp_path)
    _run(a.handle("min agreeing strategies 5"))
    assert a.engine.state.min_agreeing == 5
    _run(a.handle("scan off"))
    assert a.engine.state.scan_on is False


def test_bare_agent_words_still_hit_agent_dispatcher(tmp_path):
    a = _agent(tmp_path)
    for word in ("soul", "memory", "mcp"):
        out = _run(a.handle(word))
        assert "Unknown engine command" not in out
        assert "Unknown command" not in out


def test_help_still_returns_the_full_list(tmp_path):
    a = _agent(tmp_path)
    assert _run(a.handle("help")) == HELP
    assert _run(a.handle("/help")) == HELP


def test_agent_bare_map_unchanged():
    assert AGENT_BARE["help"] == "/help"
    assert AGENT_BARE["settings"] == "/settings"
    assert AGENT_BARE["balance"] == "/balance"


# ==========================================================================
# 6. Free text still reaches the model (the NLU layer must not swallow it)
# ==========================================================================

@pytest.mark.parametrize("line", [
    "i like turtles",
    "the weather is nice today",
    "write me a poem about trading",
    "explain how a moving average works",
    "what do you think about the market",
])
def test_unrelated_text_still_goes_to_the_model(tmp_path, line):
    a = _agent(tmp_path)
    a.s.api_key = ""          # no key -> chat() reports that, does not crash
    out = _run(a.handle(line))
    assert "Unknown engine command" not in out
    assert "Unknown command" not in out
    assert "model" in out.lower()


def test_single_word_is_not_treated_as_a_sentence():
    """One word is the bare-word table's job, not the NLU layer's."""
    assert nlu.parse("balance") is None
    assert nlu.parse("help") is None
    assert nlu.parse("") is None


# ==========================================================================
# 7. Confirmation vocabulary
# ==========================================================================

@pytest.mark.parametrize("word", ["yes", "y", "ok", "confirm", "do it", "go ahead", "sure"])
def test_confirm_words(word):
    assert nlu.is_confirm(word) is True


@pytest.mark.parametrize("word", ["no", "n", "cancel", "stop", "abort", "never mind"])
def test_cancel_words(word):
    assert nlu.is_cancel(word) is True


def test_confirm_and_cancel_are_disjoint():
    for w in nlu.CONFIRM_WORDS:
        assert not nlu.is_cancel(w), f"{w!r} is both confirm and cancel"
