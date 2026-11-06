import pytest

from jrock.trader.risk import (PairRules, Tier, liquidation_price, margin_required,
                               pick_tier, position_size_by_risk, qty_from_notional,
                               roi_pct, round_step, sl_from_liq_distance,
                               validate_price_protection)


def test_round_step_never_oversizes():
    assert round_step(1.23456, 2) == 1.23
    assert round_step(0.00019, 4) == 0.0001


def test_qty_from_notional():
    rules = PairRules("BTCUSDT", base_precision=4, min_trade_volume=0.0001)
    q = qty_from_notional(1000, 50000, rules)
    assert q == 0.02


def test_margin_required():
    assert margin_required(1000, 10) == 100


def test_liquidation_long_below_entry():
    liq = liquidation_price(100, 10, "LONG", 0.004)
    assert liq < 100
    assert liq == pytest.approx(100 * (1 - 0.1 + 0.004))


def test_liquidation_short_above_entry():
    liq = liquidation_price(100, 10, "SHORT", 0.004)
    assert liq > 100


def test_sl_halfway_to_liq_long():
    entry, liq = 100.0, 90.0
    sl = sl_from_liq_distance(entry, liq, "LONG", 50)
    assert sl == pytest.approx(95.0)


def test_sl_halfway_to_liq_short():
    entry, liq = 100.0, 110.0
    sl = sl_from_liq_distance(entry, liq, "SHORT", 50)
    assert sl == pytest.approx(105.0)


def test_roi_pct_leveraged():
    assert roi_pct(100, 101, "LONG", 10) == pytest.approx(10.0)
    assert roi_pct(100, 99, "SHORT", 10) == pytest.approx(10.0)


def test_pick_tier():
    tiers = [Tier(1, 0, 50000, 125, 0.004), Tier(2, 50000, 200000, 100, 0.005)]
    assert pick_tier(tiers, 10000).level == 1
    assert pick_tier(tiers, 60000).level == 2
    assert pick_tier(tiers, 999999).level == 2


def test_position_size_by_risk():
    rules = PairRules("BTCUSDT", base_precision=4, min_trade_volume=0.0001,
                      max_market_order_volume=1000)
    qty = position_size_by_risk(equity=1000, risk_pct=1.0, entry=100.0,
                                stop=99.0, leverage=10, rules=rules)
    # risk $10, stop distance $1 -> 10 units, capped by notional 1000*10/100 = 100
    assert qty == pytest.approx(10.0)


def test_position_size_zero_when_below_min():
    rules = PairRules("BTCUSDT", base_precision=4, min_trade_volume=1.0)
    qty = position_size_by_risk(10, 0.1, 50000, 49999, 10, rules)
    assert qty == 0.0


def test_price_protection():
    ok, _ = validate_price_protection(100, 100, "BUY", 0.02)
    assert ok
    bad, msg = validate_price_protection(200, 100, "BUY", 0.02)
    assert not bad and "outside" in msg
