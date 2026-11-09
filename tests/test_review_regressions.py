"""Regression tests for the defects found in the qezawat-a review.

Each test pins ONE bug so it cannot come back:

BUG-1  engine.py:813  `pick_tier` used but never imported -> NameError on the
                      preflight path whenever the configured leverage is above
                      every tier's cap (tier_for_leverage returns None).
BUG-2  engine.py:1624 `partial_at_roi_pct` was never passed to
                      `mid_management_action`, so the user's "partial at roi N"
                      setting was silently ignored and the hardcoded 50% default
                      was used instead.
BUG-3  nlu.py         the "use N% of my balance per trade" rule ended with
                      `\\b` right after `%`; `%` is a non-word character, so the
                      boundary could never match and the rule was dead.
BUG-4  nlu.py         the bare engine form "margin amount 15" was not routed by
                      the NLU at all.
BUG-5  engine.py      bare "margin amount" was not recognised as a command, so
                      the natural read question fell through to the model.
"""
from __future__ import annotations

import asyncio

import pytest

from jrock.config import Settings
from jrock.nlu import parse
from jrock.trader.engine import TraderEngine
from jrock.trader.risk import Tier, tier_for_leverage
from jrock.trader.tpsl import PositionState, mid_management_action

PAIR = {
    "symbol": "AKEUSDT", "minTradeVolume": "1",
    "maxMarketOrderVolume": "100000000", "maxLimitOrderVolume": "1500000000",
    "basePrecision": 0, "quotePrecision": 6, "maxLeverage": 125, "minLeverage": 1,
    "defaultLeverage": 20, "priceProtectScope": "0.05", "symbolStatus": "OPEN",
    "isApiSupported": True,
}
# One tier only, capping leverage at 50x.
TIERS = [{"symbol": "AKEUSDT", "level": 1, "startValue": "0", "endValue": "10000",
          "leverage": 50, "maintenanceMarginRate": "0.015"}]


class FakeClient:
    def __init__(self, equity=1000.0, mark=0.005020, last=0.005009):
        self.equity, self.mark, self.last = equity, mark, last
        self.placed: list[dict] = []

    async def get_tickers(self, sym):
        return [{"symbol": sym, "lastPrice": str(self.last),
                 "markPrice": str(self.mark)}]

    async def get_single_account(self):
        return [{"marginCoin": "USDT", "available": str(self.equity),
                 "frozen": "0", "margin": "0"}]

    async def get_trading_pairs(self, symbols=None):
        return [PAIR]

    async def get_position_tiers(self, sym):
        return TIERS

    async def get_pending_positions(self, sym=None):
        return []

    async def change_margin_mode(self, *a, **k):
        return {}

    async def change_leverage(self, *a, **k):
        return {}

    async def place_order(self, **b):
        self.placed.append(b)
        return {"orderId": "X1"}


class Agg:
    symbol = "AKEUSDT"
    direction = "LONG"
    actionable = True
    strength = "MEDIUM"
    confidence = 75.0
    agreeing = 4


def _engine(leverage=200):
    s = Settings()
    s.market_data_source = "rest"
    e = TraderEngine(s)
    e.client = FakeClient()
    e.state.leverage = leverage
    return e


# --------------------------------------------------------------------- BUG-1
def test_tier_for_leverage_returns_none_above_every_cap():
    """The precondition that makes the fallback branch reachable."""
    tiers = [Tier(1, 0.0, 10000.0, 50, 0.015)]
    assert tier_for_leverage(tiers, 200) is None


def test_pick_tier_is_imported_in_engine():
    """BUG-1: the fallback called `pick_tier`, which was never imported."""
    import jrock.trader.engine as engine_mod
    assert hasattr(engine_mod, "pick_tier"), (
        "engine.py uses pick_tier in the preflight fallback; it must be imported")


def test_preflight_does_not_raise_nameerror_above_every_tier():
    """BUG-1 end to end: leverage above every tier cap must not crash.

    Before the fix this raised `NameError: name 'pick_tier' is not defined`
    instead of returning a clean answer. The exchange's own maximum is the one
    documented reason to use a lower leverage, so the order now goes out at
    that cap and the reason is recorded - and the user's setting is untouched.
    """
    e = _engine(leverage=200)

    async def go():
        await e.load_rules(["AKEUSDT"])
        return await e.try_enter(Agg())

    r = asyncio.run(go())
    assert r["ok"] is True, r.get("reason")
    assert len(e.client.placed) == 1
    assert e.state.leverage == 200, "the user's leverage setting must not be rewritten"
    assert any("exchange maximum" in err for err in e.state.errors), e.state.errors


# --------------------------------------------------------------------- BUG-2
def test_mid_management_action_honours_a_custom_partial_roi():
    """BUG-2: the setting must actually change when the partial fires."""
    st = PositionState(position_id="1", symbol="X", side="LONG", entry=1.0,
                       qty=1.0, leverage=10, sl=0.9, tp=1.2, liq=0.8)
    # roi 25%: fires at a 20% threshold, holds at the 50% default
    assert mid_management_action(st, 25.0, False, 0.0, 70.0, 20.0) == "partial_close"
    assert mid_management_action(st, 25.0, False, 0.0, 70.0, 50.0) == "hold"


def test_engine_passes_partial_at_roi_pct_into_mid_management():
    """BUG-2: the engine must forward state.partial_at_roi_pct, not the default.

    The call site is asserted directly because the surrounding management loop
    needs a live exchange; this is the exact argument list that was wrong.
    """
    import inspect
    import jrock.trader.engine as engine_mod
    src = inspect.getsource(engine_mod.TraderEngine.manage_positions)
    assert "mid_management_action(" in src
    call = src.split("mid_management_action(", 1)[1].split(")", 1)[0]
    assert "partial_at_roi_pct" in call, (
        "manage_positions must pass state.partial_at_roi_pct to "
        "mid_management_action; without it the user's setting is ignored")


def test_partial_at_roi_pct_is_a_live_setting():
    e = _engine()
    assert e.state.partial_at_roi_pct == 50.0

    async def go():
        return await e.command("partial at roi 20")

    asyncio.run(go())
    assert e.state.partial_at_roi_pct == 20.0


# --------------------------------------------------------------------- BUG-3
@pytest.mark.parametrize("text", [
    "use 10% of my balance per trade",
    "trade with 20% of my equity",
    "risk 5% of my account per trade",
])
def test_nlu_understands_percent_of_balance_phrasing(text):
    """BUG-3: the `\\b` after `%` made this rule unreachable."""
    i = parse(text)
    assert i is not None, f"{text!r} must be understood"
    assert i.name == "margin_amount_set"
    assert i.command.startswith("margin amount ")


def test_nlu_extracts_the_number_from_the_percent_phrasing():
    i = parse("use 10% of my balance per trade")
    assert i.command == "margin amount 10.0"
    i = parse("trade with 20% of my equity")
    assert i.command == "margin amount 20.0"


# --------------------------------------------------------------------- BUG-4
@pytest.mark.parametrize("text,expected", [
    ("margin amount 15", "margin amount 15.0"),
    ("margin pct 20", "margin amount 20.0"),
])
def test_nlu_routes_the_bare_engine_form(text, expected):
    """BUG-4: the bare engine form was not routed by the NLU."""
    i = parse(text)
    assert i is not None and i.name == "margin_amount_set"
    assert i.command == expected


# --------------------------------------------------------------------- BUG-5
def test_bare_margin_amount_is_a_command():
    """BUG-5: bare "margin amount" is a READ, not free text for the model."""
    e = _engine()
    assert e.looks_like_command("margin amount") is True
    assert e.looks_like_command("margin amount 10") is True
    # a sentence that merely mentions margin is still NOT a command
    assert e.looks_like_command("is my margin too high?") is False


def test_bare_margin_amount_reports_the_setting():
    """BUG-5: it must answer with the value, not a usage error."""
    e = _engine()

    async def go():
        return await e.command("margin amount")

    out = asyncio.run(go())
    assert "10%" in out
    assert "Usage" not in out
    assert e.state.margin_amount_pct == 10.0
