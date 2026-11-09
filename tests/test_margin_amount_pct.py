"""Tests for MARGIN_AMOUNT_PCT - the margin-per-trade size knob.

The setting lets the user say "use at most this share of my available balance
as margin on one position" (default 10%). It is a MARGIN budget rather than a
notional budget because margin is what the exchange actually deducts from
`available`, so the number the user sets is the number they see leave their
balance.

What is pinned here:

1. The default is 10 and it is read from the environment.
2. `margin_budget` / `max_notional_for_margin` do the arithmetic, including the
   taker fee (dropping it is what produced 20003).
3. `position_size_by_risk` composes the new cap with the existing
   spendable-balance cap by taking the MINIMUM, so it can only ever make an
   order smaller - never talk the engine into an order the balance cannot pay.
4. The engine wires it through: the sized order's margin respects the budget,
   the budget is reported in `settings_report`, and the runtime command works.
5. The NLU understands "use 10% of my balance per trade".
"""
from __future__ import annotations

import asyncio

import pytest

from jrock.config import Settings, load_settings
from jrock.nlu import parse
from jrock.trader.engine import TraderEngine
from jrock.trader.risk import (TAKER_FEE_RATE, PairRules, margin_budget,
                               margin_required, max_notional_for_balance,
                               max_notional_for_margin, notional_from_qty,
                               position_size_by_risk, required_cost)

AKE = PairRules(
    symbol="AKEUSDT", min_trade_volume=1.0, max_market_order_volume=100000000.0,
    max_limit_order_volume=1500000000.0, base_precision=0, quote_precision=6,
    min_leverage=1, max_leverage=50, default_leverage=10,
    price_protect_scope=0.05, symbol_status="OPEN", api_supported=True,
)

PRICE = 0.005943
EQUITY = 1000.0
LEVERAGE = 10


# --- 1. the setting itself ---------------------------------------------------

def test_default_is_ten_percent():
    assert Settings().margin_amount_pct == 10.0


def test_env_overrides_the_default(monkeypatch):
    monkeypatch.setenv("MARGIN_AMOUNT_PCT", "25")
    assert load_settings().margin_amount_pct == 25.0


def test_env_garbage_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("MARGIN_AMOUNT_PCT", "not-a-number")
    assert load_settings().margin_amount_pct == 10.0


def test_zero_disables_the_budget(monkeypatch):
    monkeypatch.setenv("MARGIN_AMOUNT_PCT", "0")
    assert load_settings().margin_amount_pct == 0.0


# --- 2. the arithmetic -------------------------------------------------------

def test_margin_budget_is_a_share_of_the_balance():
    assert margin_budget(1000.0, 10.0) == pytest.approx(100.0)
    assert margin_budget(1000.0, 25.0) == pytest.approx(250.0)


def test_margin_budget_applies_the_safety_reserve():
    """The budget is net of the reserve, so it agrees with the balance cap."""
    assert margin_budget(1000.0, 10.0, safety_pct=2.0) == pytest.approx(98.0)


def test_margin_budget_zero_pct_returns_the_whole_usable_balance():
    assert margin_budget(1000.0, 0.0) == pytest.approx(1000.0)


def test_margin_budget_is_defensive():
    assert margin_budget(0.0, 10.0) == 0.0
    assert margin_budget(-5.0, 10.0) == 0.0
    # a nonsense percentage is clamped, never allowed to exceed the balance
    assert margin_budget(1000.0, 500.0) == pytest.approx(1000.0)


def test_max_notional_for_margin_accounts_for_the_fee():
    """margin + fee must fit the budget, exactly as on the balance path."""
    budget = 100.0
    notional = max_notional_for_margin(budget, LEVERAGE)
    assert required_cost(notional, LEVERAGE) == pytest.approx(budget, rel=1e-9)
    # and it is strictly less than the fee-blind value
    assert notional < budget * LEVERAGE


def test_max_notional_for_margin_is_defensive():
    assert max_notional_for_margin(0.0, 10) == 0.0
    assert max_notional_for_margin(100.0, 0) == 0.0


# --- 3. composition with the spendable-balance cap ---------------------------

def test_margin_pct_cap_is_tighter_than_the_balance_cap():
    """10% of the balance is a much smaller order than the whole balance."""
    qty_full = position_size_by_risk(EQUITY, 1.0, PRICE, PRICE - 0.000001,
                                     LEVERAGE, AKE, margin_pct=0.0)
    qty_pct = position_size_by_risk(EQUITY, 1.0, PRICE, PRICE - 0.000001,
                                    LEVERAGE, AKE, margin_pct=10.0)
    assert qty_pct < qty_full


def test_sized_margin_respects_the_budget():
    """The whole point: the margin locked fits the configured share."""
    qty = position_size_by_risk(EQUITY, 1.0, PRICE, PRICE - 0.000001,
                                LEVERAGE, AKE, margin_pct=10.0)
    notional = notional_from_qty(qty, PRICE)
    budget = margin_budget(EQUITY, 10.0)
    assert margin_required(notional, LEVERAGE) <= budget
    # and the cost (margin + fee) fits the budget too
    assert required_cost(notional, LEVERAGE) <= budget


def test_budget_scales_with_the_percentage():
    qty_10 = position_size_by_risk(EQUITY, 1.0, PRICE, PRICE - 0.000001,
                                   LEVERAGE, AKE, margin_pct=10.0)
    qty_20 = position_size_by_risk(EQUITY, 1.0, PRICE, PRICE - 0.000001,
                                   LEVERAGE, AKE, margin_pct=20.0)
    assert qty_20 > qty_10


def test_margin_pct_never_exceeds_the_balance_cap():
    """It composes by MINIMUM, so it can never loosen the balance cap.

    Even at 100% the order must still be payable: the balance cap already
    reserves the taker fee, and the budget is derived from the same equity.
    """
    qty = position_size_by_risk(EQUITY, 1.0, PRICE, PRICE - 0.000001,
                                LEVERAGE, AKE, margin_pct=100.0)
    notional = notional_from_qty(qty, PRICE)
    assert required_cost(notional, LEVERAGE) <= EQUITY
    # and it is not larger than the fee-aware balance cap
    assert notional <= max_notional_for_balance(EQUITY, LEVERAGE) + 1e-9


def test_risk_size_still_wins_when_it_is_the_smaller_of_the_two():
    """A wide stop makes the risk-based size tiny; the budget must not enlarge it."""
    qty_tight = position_size_by_risk(EQUITY, 0.1, PRICE, PRICE - 0.000001,
                                      LEVERAGE, AKE, margin_pct=10.0)
    qty_wide = position_size_by_risk(EQUITY, 0.1, PRICE, PRICE * 0.5,
                                     LEVERAGE, AKE, margin_pct=10.0)
    assert qty_wide < qty_tight


def test_zero_pct_restores_the_previous_behaviour():
    """0 disables the budget, so the result matches the pre-existing call."""
    with_pct = position_size_by_risk(EQUITY, 1.0, PRICE, PRICE - 0.000001,
                                     LEVERAGE, AKE, margin_pct=0.0)
    without = position_size_by_risk(EQUITY, 1.0, PRICE, PRICE - 0.000001,
                                    LEVERAGE, AKE)
    assert with_pct == without


def test_tiny_budget_yields_no_order_rather_than_a_bad_one():
    """Below the symbol's minimum volume the answer is 0, not a sub-min order."""
    qty = position_size_by_risk(EQUITY, 1.0, PRICE, PRICE - 0.000001,
                                LEVERAGE, AKE, margin_pct=0.00001)
    assert qty == 0.0


# --- 4. the engine wiring ----------------------------------------------------

PAIR = {
    "symbol": "AKEUSDT", "minTradeVolume": "1",
    "maxMarketOrderVolume": "100000000", "maxLimitOrderVolume": "1500000000",
    "basePrecision": 0, "quotePrecision": 6, "maxLeverage": 50, "minLeverage": 1,
    "defaultLeverage": 10, "priceProtectScope": "0.05", "symbolStatus": "OPEN",
    "isApiSupported": True,
}
TIERS = [
    {"symbol": "AKEUSDT", "level": 1, "startValue": "0", "endValue": "10000",
     "leverage": 50, "maintenanceMarginRate": "0.015"},
    {"symbol": "AKEUSDT", "level": 2, "startValue": "10000", "endValue": "20000",
     "leverage": 25, "maintenanceMarginRate": "0.02"},
]


class FakeClient:
    def __init__(self, equity, mark, last):
        self.equity, self.mark, self.last = equity, mark, last
        self.placed: list[dict] = []

    async def get_tickers(self, sym):
        return [{"symbol": sym, "lastPrice": str(self.last),
                 "markPrice": str(self.mark)}]

    async def get_single_account(self):
        return [{"marginCoin": "USDT", "available": str(self.equity),
                 "frozen": "0", "margin": "0", "unrealized": "0"}]

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


def _engine(equity=1000.0, mark=0.005020, last=0.005009, leverage=10,
            margin_pct=10.0):
    s = Settings()
    s.market_data_source = "rest"
    s.margin_amount_pct = margin_pct
    e = TraderEngine(s)
    e.client = FakeClient(equity, mark, last)
    e.state.leverage = leverage
    return e


def _run(**kw):
    e = _engine(**kw)

    async def go():
        await e.load_rules(["AKEUSDT"])
        return await e.try_enter(Agg())

    return asyncio.run(go()), e


def test_engine_sizes_within_the_margin_budget():
    r, e = _run()
    assert r["ok"] is True, r.get("reason")
    assert len(e.client.placed) == 1
    qty = float(e.client.placed[0]["qty"])
    notional = qty * 0.005009
    budget = margin_budget(1000.0, 10.0, e.s.margin_safety_pct)
    assert margin_required(notional, e.state.leverage) <= budget


def test_engine_places_a_smaller_order_at_a_lower_percentage():
    """At 1% the budget binds and the order is smaller than at 10%.

    At 10% and above the risk-based size (risk_pct=1% of equity) is already the
    smaller of the two, so the budget stops biting - which is the correct
    composition: the cap can only ever shrink an order, never grow it.
    """
    _, e1 = _run(margin_pct=1.0)
    _, e10 = _run(margin_pct=10.0)
    qty1 = float(e1.client.placed[0]["qty"])
    qty10 = float(e10.client.placed[0]["qty"])
    assert qty1 < qty10
    # the 1% order's margin fits the 1% budget
    budget1 = margin_budget(1000.0, 1.0, e1.s.margin_safety_pct)
    assert margin_required(qty1 * 0.005009, e1.state.leverage) <= budget1


def test_engine_budget_is_a_target_not_a_cap():
    """The percentage is the SIZING AUTHORITY, so a bigger percentage is a
    bigger order.

    This used to assert the opposite - that 10% and 30% produced the same qty
    because the risk-based size was the smaller of the two and the "cap" never
    bound. That is precisely the bug the user reported: a setting that never
    changes anything is indistinguishable from a setting that is ignored.
    """
    _, e10 = _run(margin_pct=10.0)
    _, e30 = _run(margin_pct=30.0)
    qty10 = float(e10.client.placed[0]["qty"])
    qty30 = float(e30.client.placed[0]["qty"])
    assert qty30 > qty10
    # and each one's margin lands on its own target
    for pct, e in ((10.0, e10), (30.0, e30)):
        qty = float(e.client.placed[0]["qty"])
        target = 1000.0 * pct / 100.0
        locked = margin_required(qty * 0.005009, e.state.leverage)
        assert locked <= target + 1e-9
        assert locked >= target * 0.95


def test_engine_state_carries_the_setting():
    _, e = _run(margin_pct=15.0)
    assert e.state.margin_amount_pct == 15.0


def test_setting_is_persisted_across_a_restart():
    """It is a runtime setting, so it must survive a restart like the others."""
    assert "margin_amount_pct" in TraderEngine.PERSISTED_STATE_FIELDS


def test_settings_report_shows_the_budget():
    _, e = _run(margin_pct=12.5)
    assert "margin_amount_pct=12.5%" in e.settings_report()


def test_runtime_command_sets_it():
    e = _engine()

    async def go():
        return await e.command("margin amount 25")

    out = asyncio.run(go())
    assert e.state.margin_amount_pct == 25.0
    assert "25%" in out


def test_runtime_command_bare_reports_the_setting():
    """Bare "margin amount" is a READ: it answers with the value, not a usage
    error. (It used to fall through to the model entirely, because the bare
    prefix was not recognised as a command.)"""
    e = _engine()

    async def go():
        return await e.command("margin amount")

    out = asyncio.run(go())
    assert "10%" in out
    assert "Usage" not in out
    assert e.state.margin_amount_pct == 10.0


def test_runtime_command_rejects_a_non_numeric_argument():
    e = _engine()

    async def go():
        return await e.command("margin amount abc")

    out = asyncio.run(go())
    assert "Usage" in out
    assert e.state.margin_amount_pct == 10.0


def test_command_is_recognised_as_a_command_not_free_text():
    e = _engine()
    assert e.looks_like_command("margin amount 10") is True
    # a sentence that merely mentions margin is NOT a command
    assert e.looks_like_command("is my margin too high?") is False


# --- 5. natural language -----------------------------------------------------

def test_nlu_understands_set_margin_amount():
    i = parse("set margin amount to 20")
    assert i is not None and i.name == "margin_amount_set"
    assert i.command == "margin amount 20.0"


def test_nlu_reads_the_setting_back():
    i = parse("what is my margin amount per trade?")
    assert i is not None and i.name == "margin_amount_read"
    assert i.command == "settings trade"
