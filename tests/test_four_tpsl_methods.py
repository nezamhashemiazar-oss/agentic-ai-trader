"""Regression tests for the FOUR documented Bitunix TP/SL methods.

Source: the official academy article
"Bitunix Futures: 4 TP/SL Methods Guide" (bitunix-futures-positions-guide-to-
four-tp-sl-methods), which defines exactly four methods:

    1. Position TP/SL  - one TP/SL closes the ENTIRE position
    2. Partial TP/SL   - a defined % closes at each trigger (staged exits)
    3. Trailing TP/SL  - dynamic exit after a trend + retracement
    4. Account TP/SL   - closes ALL positions when total account PnL crosses
                         a threshold

Position and Partial map to native endpoints and are enforced by the exchange.
Trailing and Account have NO native endpoint and are software - they only run
while the manage loop is up. These tests pin that distinction, because
pretending a software stop is exchange-enforced would be a lie about risk.
"""
from __future__ import annotations

import asyncio

import pytest

from jrock.config import Settings
from jrock.trader.account_tpsl import (AccountTpSlConfig, account_tpsl_decision,
                                       total_unrealized_pnl)
from jrock.trader.engine import TraderEngine
from jrock.trader.risk import PairRules

AKE = PairRules(
    symbol="AKEUSDT", min_trade_volume=1.0, max_market_order_volume=100_000_000.0,
    max_limit_order_volume=1_500_000_000.0, base_precision=0, quote_precision=6,
    min_leverage=1, max_leverage=50, default_leverage=10,
    price_protect_scope=0.05, symbol_status="OPEN", api_supported=True,
)

PAIR_ROW = {
    "symbol": "AKEUSDT", "minTradeVolume": "1",
    "maxMarketOrderVolume": "100000000", "maxLimitOrderVolume": "1500000000",
    "basePrecision": 0, "quotePrecision": 6, "maxLeverage": 50, "minLeverage": 1,
    "defaultLeverage": 10, "priceProtectScope": "0.05", "symbolStatus": "OPEN",
    "isApiSupported": True,
}


# ============================================================ 1. POSITION
def test_position_method_is_the_default_and_is_exchange_enforced():
    s = Settings()
    assert s.tpsl_method == "POSITION"
    e = TraderEngine(s)
    report = e.tpsl_method_report()
    assert "POSITION" in report
    assert "exchange-enforced" in report


def test_position_method_uses_the_position_endpoint_not_the_standalone_one():
    """Position TP/SL has no qty field - it can only close the whole position."""
    from jrock.trader.client import BitunixClient
    c = BitunixClient(Settings())
    calls: list[tuple] = []

    async def fake_request(method, path, **kw):
        calls.append((method, path, kw.get("body")))
        return {"ok": True}

    c.request = fake_request  # type: ignore[assignment]
    asyncio.run(c.place_position_tp_sl_order("AKEUSDT", "P1", "0.006", "0.004"))
    method, path, body = calls[0]
    assert path == "/api/v1/futures/tpsl/position/place_order"
    assert "tpQty" not in body and "slQty" not in body, (
        "the position endpoint has no quantity field - sending one would be "
        "inventing a parameter")


# ============================================================ 2. PARTIAL
def test_partial_method_closes_a_configured_percentage_not_a_hardcoded_half():
    """The old code always closed exactly 50%. The method makes it configurable."""
    s = Settings()
    s.tpsl_method = "PARTIAL"
    s.partial_close_pct = 30.0
    e = TraderEngine(s)
    assert e.state.partial_close_pct == 30.0
    assert "PARTIAL" in e.tpsl_method_report()
    assert "30%" in e.tpsl_method_report()


def test_partial_close_pct_is_clamped_to_a_sane_range():
    s = Settings()
    e = TraderEngine(s)
    asyncio.run(e.command("partial close pct 0"))
    assert e.state.partial_close_pct == 1.0, "0% would close nothing"
    asyncio.run(e.command("partial close pct 100"))
    assert e.state.partial_close_pct == 99.0, "100% is the Position method, not Partial"


def test_partial_tp_sl_sends_tpqty_and_slqty():
    """tpQty/slQty are what make a staged exit possible."""
    from jrock.trader.client import BitunixClient
    c = BitunixClient(Settings())
    calls: list[dict] = []

    async def fake_request(method, path, **kw):
        calls.append({"path": path, "body": kw.get("body")})
        return {"ok": True}

    c.request = fake_request  # type: ignore[assignment]
    asyncio.run(c.place_tp_sl_order("AKEUSDT", "P1", tp_price="0.006",
                                    sl_price="0.004", tp_qty="1000", sl_qty="1000"))
    body = calls[0]["body"]
    assert calls[0]["path"] == "/api/v1/futures/tpsl/place_order"
    assert body["tpQty"] == "1000" and body["slQty"] == "1000"
    assert body["positionId"] == "P1"


def test_partial_tp_sl_requires_a_quantity():
    """Official: at least one of tpQty/slQty is required."""
    from jrock.trader.client import BitunixClient
    c = BitunixClient(Settings())
    with pytest.raises(ValueError, match="tpQty/slQty"):
        asyncio.run(c.place_tp_sl_order("AKEUSDT", "P1", tp_price="0.006"))


def test_partial_tp_sl_requires_a_price():
    from jrock.trader.client import BitunixClient
    c = BitunixClient(Settings())
    with pytest.raises(ValueError, match="tpPrice/slPrice"):
        asyncio.run(c.place_tp_sl_order("AKEUSDT", "P1", tp_qty="1000"))


def test_engine_partial_path_uses_the_native_endpoint_when_method_is_partial():
    """The engine must route through place_tp_sl_order, not a plain close."""
    s = Settings()
    s.tpsl_method = "PARTIAL"
    s.partial_close_pct = 50.0
    e = TraderEngine(s)
    sent: list[dict] = []

    class C:
        async def place_tp_sl_order(self, **kw):
            sent.append(kw)
            return {"orderId": "T9"}

    e.client = C()  # type: ignore[assignment]
    res = asyncio.run(e.place_partial_tp_sl(
        "AKEUSDT", "P1", "LONG", 2000.0, 0.006, 0.004, AKE))
    assert res == {"orderId": "T9"}
    assert sent[0]["tpQty"] == "2000" and sent[0]["slQty"] == "2000"
    assert sent[0]["tpPrice"] == "0.006000"
    assert sent[0]["tpStopType"] == "MARK_PRICE"


# ============================================================ 3. TRAILING
def test_trailing_method_is_reported_as_software_not_exchange_enforced():
    s = Settings()
    s.tpsl_method = "TRAILING"
    e = TraderEngine(s)
    report = e.tpsl_method_report()
    assert "TRAILING" in report
    assert "software" in report, (
        "a software ratchet must not be described as exchange-enforced")
    assert "manage loop" in report


def test_trailing_method_is_settable_from_the_command_line():
    s = Settings()
    e = TraderEngine(s)
    out = asyncio.run(e.command("tpsl method TRAILING"))
    assert e.state.tpsl_method == "TRAILING"
    assert "TRAILING" in out


def test_unknown_method_is_refused():
    s = Settings()
    e = TraderEngine(s)
    out = asyncio.run(e.command("tpsl method BANANA"))
    assert "must be" in out
    assert e.state.tpsl_method == "POSITION", "a bad value must not be stored"


# ============================================================ 4. ACCOUNT
def test_account_method_is_off_by_default():
    cfg = AccountTpSlConfig()
    assert not cfg.enabled
    assert "off" in cfg.describe()


def test_account_tp_fires_on_total_pnl():
    cfg = AccountTpSlConfig(tp_usdt=100.0)
    d = account_tpsl_decision(total_pnl=120.0, equity=1000.0, cfg=cfg)
    assert d.should_close_all and d.trigger == "tp"
    assert d.threshold == 100.0
    assert "closing all positions" in d.reason


def test_account_sl_fires_on_total_pnl():
    cfg = AccountTpSlConfig(sl_usdt=50.0)
    d = account_tpsl_decision(total_pnl=-60.0, equity=1000.0, cfg=cfg)
    assert d.should_close_all and d.trigger == "sl"


def test_account_thresholds_do_not_fire_inside_the_band():
    cfg = AccountTpSlConfig(tp_usdt=100.0, sl_usdt=50.0)
    d = account_tpsl_decision(total_pnl=10.0, equity=1000.0, cfg=cfg)
    assert not d.should_close_all
    assert d.trigger == ""


def test_account_pct_thresholds_scale_with_equity():
    cfg = AccountTpSlConfig(tp_pct=5.0)
    # 5% of 1000 = 50, so 60 fires; 5% of 2000 = 100, so the same 60 does not.
    assert account_tpsl_decision(total_pnl=60.0, equity=1000.0,
                                 cfg=cfg).should_close_all
    assert not account_tpsl_decision(total_pnl=60.0, equity=2000.0,
                                     cfg=cfg).should_close_all


def test_account_pct_is_inert_without_equity_but_usdt_still_works():
    """A zero equity must not make a %-threshold fire on nothing."""
    cfg = AccountTpSlConfig(tp_pct=5.0)
    assert not account_tpsl_decision(total_pnl=1.0, equity=0.0,
                                     cfg=cfg).should_close_all
    cfg2 = AccountTpSlConfig(tp_usdt=1.0, tp_pct=5.0)
    assert account_tpsl_decision(total_pnl=2.0, equity=0.0,
                                 cfg=cfg2).should_close_all


def test_total_unrealized_pnl_sums_the_exchange_field():
    positions = [{"unrealizedPNL": "12.5"}, {"unrealizedPNL": "-2.5"},
                 {"unrealizedPNL": None}, {"no_pnl": 1}]
    assert total_unrealized_pnl(positions) == 10.0


def test_account_method_is_reported_as_software():
    s = Settings()
    s.tpsl_method = "ACCOUNT"
    s.account_tp_usdt = 100.0
    e = TraderEngine(s)
    report = e.tpsl_method_report()
    assert "ACCOUNT" in report and "software" in report
    assert "100" in report


def test_account_method_closes_all_positions_when_triggered():
    """End to end: the manage loop must flatten the account."""
    s = Settings()
    s.tpsl_method = "ACCOUNT"
    s.account_tp_usdt = 100.0
    e = TraderEngine(s)
    closed: list[str] = []

    class C:
        async def get_pending_positions(self, sym=None):
            return [{"positionId": "P1", "symbol": "AKEUSDT", "side": "LONG",
                     "avgOpenPrice": "0.005", "qty": "1000", "leverage": "10",
                     "liqPrice": "0.004", "markPrice": "0.0051",
                     "unrealizedPNL": "150.0"}]

        async def get_single_account(self):
            return [{"available": "1000"}]

        async def close_all_position(self):
            closed.append("all")
            return {"ok": True}

        async def get_trading_pairs(self, symbols=None):
            return [PAIR_ROW]

    e.client = C()  # type: ignore[assignment]

    async def go():
        await e.load_rules(["AKEUSDT"])
        return await e.manage_positions()

    actions = asyncio.run(go())
    assert closed == ["all"], "the account threshold must flatten everything"
    assert actions[0]["action"] == "account_tp"
    assert actions[0]["total_pnl"] == 150.0


def test_account_method_does_not_fire_below_the_threshold():
    s = Settings()
    s.tpsl_method = "ACCOUNT"
    s.account_tp_usdt = 100.0
    e = TraderEngine(s)
    closed: list[str] = []

    class C:
        async def get_pending_positions(self, sym=None):
            return [{"positionId": "P1", "symbol": "AKEUSDT", "side": "LONG",
                     "avgOpenPrice": "0.005", "qty": "1000", "leverage": "10",
                     "liqPrice": "0.004", "markPrice": "0.0051",
                     "unrealizedPNL": "10.0"}]

        async def get_single_account(self):
            return [{"available": "1000"}]

        async def get_tickers(self, sym):
            return [{"symbol": sym, "lastPrice": "0.0051",
                     "markPrice": "0.0051"}]

        async def modify_position_tp_sl_order(self, *a, **k):
            return {"orderId": "T1"}

        async def close_all_position(self):
            closed.append("all")
            return {"ok": True}

        async def get_trading_pairs(self, symbols=None):
            return [PAIR_ROW]

    e.client = C()  # type: ignore[assignment]

    async def go():
        await e.load_rules(["AKEUSDT"])
        return await e.manage_positions()

    asyncio.run(go())
    assert closed == [], "10 USDT must not trip a 100 USDT threshold"


def test_close_all_positions_falls_back_to_per_position_close():
    """A bulk-call failure must not leave the account half-flattened."""
    from jrock.trader.errors import BitunixAPIError
    s = Settings()
    e = TraderEngine(s)
    flashed: list[str] = []

    class C:
        async def close_all_position(self):
            raise BitunixAPIError(10001, "boom")

        async def get_pending_positions(self, sym=None):
            return [{"positionId": "P1", "symbol": "AKEUSDT"},
                    {"positionId": "P2", "symbol": "ETHUSDT"}]

        async def flash_close_position(self, pid):
            flashed.append(pid)
            return {"ok": True}

    e.client = C()  # type: ignore[assignment]
    res = asyncio.run(e.close_all_positions())
    assert flashed == ["P1", "P2"]
    assert res["via"] == "flash_close_position" and res["ok"]


# ============================================================ the four, together
def test_all_four_methods_are_recognised():
    s = Settings()
    e = TraderEngine(s)
    for m in ("POSITION", "PARTIAL", "TRAILING", "ACCOUNT"):
        out = asyncio.run(e.command(f"tpsl method {m}"))
        assert e.state.tpsl_method == m
        assert m in out


def test_method_survives_a_restart(tmp_path, monkeypatch):
    """The chosen method is a setting, so it must persist like one."""
    monkeypatch.setattr("jrock.config.ROOT", tmp_path)
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)
    from jrock.store import Store

    s = Settings()
    store = Store(data_dir=tmp_path / "data")
    e = TraderEngine(s, store=store)
    asyncio.run(e.command("tpsl method PARTIAL"))
    asyncio.run(e.command("partial close pct 25"))
    e._persist_if_changed()

    store2 = Store(data_dir=tmp_path / "data")
    e2 = TraderEngine(Settings(), store=store2)
    assert e2.state.tpsl_method == "PARTIAL"
    assert e2.state.partial_close_pct == 25.0
