"""The agent must be talkable-to, in English and Persian.

The user's report was "the agent is like a robot, you cannot talk to it". Two
defects produced that, and both are pinned here.

1. **Persian was not understood at all.** Every rule in `nlu.RULES` was
   English-only, so a Persian sentence never matched, fell through to the
   model, and the model - which has no engine access - answered in generic
   prose. The agent looked like it could not be talked to because, in Persian,
   it genuinely could not.

2. **The persona was never sent and there was no history.** `chat()` built a
   three-sentence prompt from scratch instead of using `soul/jrock.soul.md`,
   and replayed no prior turns, so a follow-up had no referent.

The tests below cover the routing half (deterministic, no model needed) and the
prompt half (with a fake model, since no LLM key is available in this
environment - see the note at the bottom).
"""
from __future__ import annotations

import pytest

from jrock import nlu


# --- Persian: the sentences a person actually types -------------------------

PERSIAN_READS = [
    ("مارجینم چقدره؟", "margin_amount_read"),
    ("مارجین هر معامله چقدره", "margin_amount_read"),
    ("موجودی حسابم چقدره؟", "balance"),
    ("چقدر پول دارم", "balance"),
    ("سودم چقدره؟", "pnl"),
    ("چقدر سود کردم", "pnl"),
    ("وضعیت ربات چطوره؟", "status"),
    ("اهرمم چقدره؟", "leverage_read"),
    ("ریسکم چقدره؟", "risk_read"),
]


@pytest.mark.parametrize("text,expected", PERSIAN_READS)
def test_persian_read_questions_route_deterministically(text, expected):
    """A Persian question must resolve to the same command as its English twin.

    These are read-only, so they must NOT reach the model: the engine's own
    answer is the truth and a model would have to guess.
    """
    r = nlu.parse(text)
    assert r is not None, f"{text!r} fell through to the model"
    assert not isinstance(r, nlu.Clarification), f"{text!r} asked instead of answering"
    assert r.name == expected
    assert r.command, "a read intent must carry the command to run"


PERSIAN_WRITES = [
    ("مارجین هر معامله رو ۱۰ درصد کن", "margin_amount_set", "margin amount 10.0"),
    ("اهرم رو ۲۰ کن", "leverage_set", "leverage 20"),
    ("معامله رو متوقف کن", "autotrade_off", "stop trading"),
    ("همه پوزیشن‌ها رو ببند", "close_all", "close position"),
]


@pytest.mark.parametrize("text,expected,command", PERSIAN_WRITES)
def test_persian_commands_route_with_the_right_value(text, expected, command):
    """Persian imperatives must produce the exact command, with the number."""
    r = nlu.parse(text)
    assert r is not None, f"{text!r} fell through to the model"
    assert not isinstance(r, nlu.Clarification), f"{text!r} asked instead of acting"
    assert r.name == expected
    assert r.command == command


def test_persian_close_position_asks_which_symbol():
    """"close my position" is ambiguous - it must ask, not guess.

    Guessing here closes the wrong position with real money.
    """
    r = nlu.parse("پوزیشنم رو ببند")
    assert isinstance(r, nlu.Clarification), r
    assert r.candidate is not None and r.candidate.name == "close_position"
    assert r.options, "a clarification must offer concrete options"


def test_persian_vague_risk_request_asks_for_the_number():
    """"nudge my risk up a bit" has no number - ask for one."""
    r = nlu.parse("یه کم ریسک رو ببر بالا")
    assert isinstance(r, nlu.Clarification), r
    assert r.candidate is not None and r.candidate.name == "risk_set"
    assert "risk" in r.question.lower() or "risk" in " ".join(r.options).lower()


def test_persian_greeting_gets_a_reply():
    """A bare Persian greeting must not fall through to nothing."""
    for text in ("سلام", "ممنون", "چطوری"):
        r = nlu.parse(text)
        assert r is not None, f"{text!r} produced no reply at all"
        assert r.name == "smalltalk"


def test_persian_help_question_routes_to_help():
    r = nlu.parse("چه کارایی می‌تونی بکنی؟")
    assert r is not None and r.name == "help"


# --- English conversational forms still work --------------------------------

ENGLISH = [
    ("how much margin am i using?", "margin_amount_read"),
    ("what's my balance?", "balance"),
    ("how much have i made?", "pnl"),
    ("is the bot running?", "status"),
    ("use 10% of my balance per trade", "margin_amount_set"),
    ("set leverage to 20x", "leverage_set"),
    ("stop trading", "autotrade_off"),
]


@pytest.mark.parametrize("text,expected", ENGLISH)
def test_english_conversational_forms_still_route(text, expected):
    """The Persian rules must not have disturbed the English ones."""
    r = nlu.parse(text)
    assert r is not None, f"{text!r} fell through to the model"
    assert not isinstance(r, nlu.Clarification), f"{text!r} asked instead of acting"
    assert r.name == expected


def test_english_close_position_still_asks_which_symbol():
    r = nlu.parse("close my position")
    assert isinstance(r, nlu.Clarification), r
    assert r.candidate.name == "close_position"


# --- the prompt half --------------------------------------------------------

def test_system_prompt_carries_the_soul_and_the_conversational_contract():
    """The persona must actually be sent, and must say how to converse.

    Before the fix `chat()` used a hardcoded three-sentence prompt and the soul
    file was never read, so the model had no voice and no instruction to read
    intent from ordinary sentences.
    """
    from jrock.agent import JRockAgent as Agent

    a = Agent.__new__(Agent)          # no network, no config
    a.settings = None
    prompt = Agent._system_prompt(a)

    assert "How you talk" in prompt
    # the conversational contract, in the words the model needs
    assert "Persian" in prompt and "Farsi" in prompt
    assert "INTENT" in prompt
    assert "clarifying question" in prompt
    assert "wait for an explicit yes" in prompt
    # and it must not be the old three-sentence stub
    assert "commands are listed in /help" not in prompt


def test_history_is_replayed_and_bounded():
    """A follow-up must have a referent, and the buffer must not grow forever."""
    from jrock.agent import JRockAgent as Agent

    a = Agent.__new__(Agent)
    a._history = []
    Agent._remember_turn(a, "how much margin am i using?", "10% of balance.")
    Agent._remember_turn(a, "and my leverage?", "20x.")

    msgs = Agent._history_messages(a)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[0]["content"] == "how much margin am i using?"
    assert msgs[-1]["content"] == "20x."

    # bounded: a long session must not grow the prompt without limit
    for i in range(200):
        Agent._remember_turn(a, f"q{i}", f"a{i}")
    assert len(a._history) <= Agent.HISTORY_TURNS * 4
    assert len(Agent._history_messages(a)) <= Agent.HISTORY_TURNS


# NOTE ON THE MODEL PATH
# ----------------------
# No LLM API key is available in this environment, so `chat()` itself is not
# exercised end to end here. The two halves that were broken are covered
# directly: routing (deterministic, no model) and prompt construction (pure
# function of the soul file + history). What is NOT covered is the model's
# actual reply quality - that needs a live key and is reported as a limitation.
