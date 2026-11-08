"""Regression tests for the command-bot behaviour fix.

The reported symptom: the agent did nothing unless given an explicit command,
and in ordinary chat it did not understand the user. Two defects produced it:

1. **Greedy command-prefix matching.** `TraderEngine.looks_like_command` was
   `any(low.startswith(p) for p in COMMAND_PREFIXES)`, so a natural sentence
   that merely STARTED with a command word was swallowed by the engine and
   answered with "Unknown engine command" instead of being understood. The
   canonical case: "leverage is too high, what should I do?" starts with
   "leverage" and was treated as the `leverage` setter.

2. **A single-word guard that dropped greetings.** `nlu.parse` returned None
   for any input shorter than two words, so "hi", "thanks", "thx" ... matched
   no rule and the agent answered nothing at all.

Both are pinned here, together with the requirement that REAL commands still
work exactly as before.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jrock import nlu
from jrock.agent import JRockAgent
from jrock.config import Settings
from jrock.trader.engine import TraderEngine


def _engine(tmp_path: Path) -> TraderEngine:
    return TraderEngine(Settings(), data_dir=tmp_path)


def _agent(tmp_path: Path) -> JRockAgent:
    s = Settings()
    s.data_dir  # noqa: B018  (ensures dir exists)
    a = JRockAgent(s)
    a.engine.data_dir = tmp_path
    return a


def _run(coro):
    return asyncio.run(coro)


# --- 1. natural sentences are NOT engine commands ---------------------------

#: Sentences that merely start with (or contain) a command word. None of them
#: is a command; each must fall through to the NLU layer / the model.
NOT_COMMANDS = [
    "leverage is too high, what should I do?",
    "risk is too high for me",
    "symbols are not working",
    "settings look wrong",
    "status of my account",
    "report is not working",
    "scan seems stuck",
    "engine is not responding",
    "margin mode looks wrong",
    "position mode is confusing",
    "timeframes are too short",
    "min confidence is too low",
    "trailing trigger is too tight",
    "breakeven threshold seems high",
    "ladder is not what I expected",
    "close position please",          # a request, not the `close position X` cmd
    "symbols please",
    "leverage please",
]


@pytest.mark.parametrize("line", NOT_COMMANDS)
def test_natural_sentence_is_not_an_engine_command(tmp_path, line):
    assert _engine(tmp_path).looks_like_command(line) is False


# --- 2. real commands still work --------------------------------------------

REAL_COMMANDS = [
    "status",
    "pnl",
    "settings",
    "settings trade",
    "ladder",
    "leverage 10",
    "leverage to 20",
    "risk 1.5",
    "risk pct 2",
    "max open positions 3",
    "scans interval 60",
    "report interval 30",
    "breakeven threshold 0.5",
    "trailing trigger 1.0",
    "liq sl distance 50",
    "min agreeing 3",
    "min confidence 80",
    "tf min confidence 60",
    "symbols BTCUSDT,ETHUSDT",
    "symbol BTCUSDT",
    "margin mode ISOLATION",
    "margin mode CROSS",
    "position mode HEDGE",
    "position mode ONE_WAY",
    "tpsl method POSITION",
    "tpsl method TRAILING",
    "close position BTCUSDT",
    "engine on",
    "engine off",
    "scan on",
    "scan off",
    "report on",
    "report off",
    "start trading",
    "stop trading",
    "autotrade on",
    "autotrade off",
    "timeframes 1m,5m,15m",
    "reversal true",
    "reversal false",
]


@pytest.mark.parametrize("line", REAL_COMMANDS)
def test_real_command_is_still_a_command(tmp_path, line):
    assert _engine(tmp_path).looks_like_command(line) is True


def test_help_is_not_an_engine_command(tmp_path):
    """`help` is an AGENT command, not an engine one - the engine must not
    claim it, or the agent's own help would be shadowed."""
    assert _engine(tmp_path).looks_like_command("help") is False


# --- 3. the NLU layer understands ordinary conversation ---------------------

#: (sentence, expected intent name) - each must resolve WITHOUT a command.
UNDERSTOOD = [
    ("leverage is too high, what should I do?", "leverage_read"),
    ("risk is too high for me", "risk_read"),
    ("symbols are not working", "symbols"),
    ("settings look wrong", "settings"),
    ("status of my account", "status"),
    ("tell me about my account", "status"),
    ("is the engine running", "status"),
    ("how much money do I have", "balance"),
    ("what is my buying power", "balance"),
    ("can you show me my balance", "balance"),
    ("i want to see my open trades", "positions"),
    ("any open positions", "positions"),
    ("how are my trades doing", "pnl"),
    ("how much have i made", "pnl"),
    ("whats my pnl", "pnl"),
    ("do i have any orders", "orders"),
    ("whats the price of btc", "price"),
    ("what do you think about ETHUSDT", "price"),
    ("whats the funding on BTCUSDT", "funding"),
    ("show me the ladder", "ladder_read"),
    ("what is my leverage", "leverage_read"),
    ("show me my risk setting", "settings"),
    ("explain taker fee rate", "explain_setting"),
    ("what is taker fee rate", "explain_setting"),
    ("why did my order fail", "why_failed"),
    ("help me out", "help"),
    ("i need help", "help"),
    ("what can you do for me", "help"),
    ("turn off the bot", "autotrade_off"),
    ("pause trading", "autotrade_off"),
    ("stop everything", "autotrade_off"),
    ("resume trading", "autotrade_on"),
    ("hi there", "smalltalk"),
    ("whats going on", "smalltalk"),
    ("good morning", "smalltalk"),
    ("are you there", "smalltalk"),
]


@pytest.mark.parametrize("line,name", UNDERSTOOD)
def test_ordinary_sentence_resolves_to_an_intent(line, name):
    r = nlu.parse(line)
    assert isinstance(r, nlu.Intent), f"{line!r} -> {r!r}"
    assert r.name == name, f"{line!r} -> {r.name}, expected {name}"


# --- 4. a single-word greeting is understood, not dropped -------------------

@pytest.mark.parametrize("word", ["hi", "hello", "hey", "thanks", "thx", "ty",
                                  "cheers", "yo"])
def test_single_word_greeting_is_understood(word):
    r = nlu.parse(word)
    assert isinstance(r, nlu.Intent), f"{word!r} -> {r!r}"
    assert r.name == "smalltalk"


def test_single_word_non_greeting_still_falls_through():
    """The guard must still let a bare word that is NOT a greeting fall through
    to the existing bare-word table / the model."""
    assert nlu.parse("BTCUSDT") is None
    assert nlu.parse("xyzzy") is None


# --- 5. end to end: a greeting gets a real reply, not silence ---------------

def test_greeting_gets_a_reply_not_silence(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("hi"))
    assert out.strip(), "a greeting must never produce an empty reply"
    assert "listening" in out.lower() or "help" in out.lower()


def test_natural_question_does_not_say_unknown_command(tmp_path):
    """The exact reported failure: a normal sentence answered with
    'Unknown engine command'."""
    a = _agent(tmp_path)
    out = _run(a.handle("leverage is too high, what should I do?"))
    assert "unknown engine command" not in out.lower()


def test_real_command_still_routes_to_the_engine(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("leverage 10"))
    assert "unknown engine command" not in out.lower()
