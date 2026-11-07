#!/usr/bin/env python3
"""Verify the TP/SL side fix and the order-size pre-flight against LIVE data.

Run:  python scripts/verify_tpsl_side.py

Fetches the real AKEUSDT rules and mark price from Bitunix, then shows:
  1. the arithmetic that produced the reported 30029
  2. the fixed plan for both directions at several leverages
  3. the pre-flight refusing an inverted SL locally
  4. the pre-flight passing a correct SL for both directions
  5. the minimum-stop-distance guard
  6. the 20003 margin pre-flight
"""
from __future__ import annotations

import json
import sys
import urllib.request

sys.path.insert(0, ".")

from jrock.trader.order_builder import OrderValidationError, build_order_body
from jrock.trader.risk import (PairRules, Tier, TpSlSideError, liquidation_price,
                               margin_required, min_stop_distance,
                               notional_from_qty, pick_tier,
                               position_size_by_risk, round_price,
                               sl_from_liq_distance, tick_size,
                               validate_tpsl_side)
from jrock.trader.tpsl import plan_tpsl

BASE = "https://fapi.bitunix.com"
SYMBOL = "AKEUSDT"


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.load(r)


class _Tier:
    level = 1
    start_value = 0.0
    end_value = 1e18
    leverage = 50
    maintenance_margin_rate = 0.003


TIERS = [_Tier()]


def main() -> int:
    pairs = get("/api/v1/futures/market/trading_pairs")["data"]
    row = next(r for r in pairs if r["symbol"] == SYMBOL)
    rules = PairRules.from_api(row)
    tick = get(f"/api/v1/futures/market/tickers?symbols={SYMBOL}")["data"][0]
    last = float(tick["lastPrice"])
    mark = float(tick["markPrice"])

    print(f"=== LIVE {SYMBOL} ===")
    print(f"  lastPrice={last}  markPrice={mark}")
    print(f"  quotePrecision={rules.quote_precision} basePrecision={rules.base_precision} "
          f"minTradeVolume={rules.min_trade_volume} maxLeverage={rules.max_leverage}")
    print(f"  tick_size={tick_size(rules)}  min_stop_distance(3 ticks)="
          f"{min_stop_distance(rules, 3)}")

    print("\n=== 1) THE REPORTED 30029: SL computed from entry only ===")
    print("  The old code never fetched the mark price, so the stop was only")
    print("  checked against the entry (last) price. The reported frame was")
    print("  SL=0.0051 against mark=0.0051xx, i.e. the mark sat BELOW the stop.")
    lev = 50
    liq = liquidation_price(last, lev, "LONG", 0.003)
    sl_old = round_price(sl_from_liq_distance(last, liq, "LONG", 50.0),
                         rules.quote_precision)
    print(f"  live entry(last)={last}  liq={liq:.6f}  sl={sl_old:.6f}")
    print("  the reported frame, replayed exactly:")
    for m in (0.005126, 0.005100, 0.005090, 0.005080):
        ok = 0.0051 < m
        print(f"    mark={m:.6f}  sl=0.005100  sl<mark? {ok}  "
              f"-> {'OK' if ok else 'REJECTED 30029'}")
    print("  live mark right now:")
    ok = sl_old < mark
    print(f"    mark={mark:.6f}  sl={sl_old:.6f}  sl<mark? {ok}  "
          f"-> {'OK' if ok else 'REJECTED 30029'}")

    print("\n=== 2) FIXED PLAN (mark-aware clamp), both directions ===")
    for lv in (5, 10, 20, 50):
        for side in ("LONG", "SHORT"):
            p = plan_tpsl(side=side, entry=last, leverage=lv, notional=20.0,
                          strength="MEDIUM", confidence=70.0, tiers=TIERS,
                          rules=rules, mark=mark, min_stop_ticks=3)
            v = validate_tpsl_side(symbol=SYMBOL, side=side, entry=last, mark=mark,
                                   sl=p.sl, tp=p.tp, rules=rules, liq=p.liq)
            print(f"  lev={lv:2d} {side:5s} sl={p.sl:.6f} tp={p.tp:.6f} "
                  f"liq={p.liq:.6f} valid={not v}")

    print("\n=== 3) PRE-FLIGHT REFUSES an inverted SL (would be 30029) ===")
    bad_mark = sl_old * 0.999
    try:
        build_order_body(symbol=SYMBOL, qty=4016, side="BUY", tradeSide="OPEN",
                         orderType="MARKET", tpPrice=0.00522, slPrice=sl_old,
                         tpStopType="MARK_PRICE", tpOrderType="MARKET",
                         slStopType="MARK_PRICE", slOrderType="MARKET",
                         rules=rules, mark_price=bad_mark, entry_price=last)
        print("  ERROR: should have raised")
    except OrderValidationError as e:
        for line in str(e).splitlines():
            print("  " + line)

    print("\n=== 4) PRE-FLIGHT PASSES a correct SL, both directions ===")
    for side, order_side in (("LONG", "BUY"), ("SHORT", "SELL")):
        p = plan_tpsl(side=side, entry=last, leverage=10, notional=20.0,
                      strength="MEDIUM", confidence=70.0, tiers=TIERS,
                      rules=rules, mark=mark, min_stop_ticks=3)
        b = build_order_body(symbol=SYMBOL, qty=4016, side=order_side,
                             tradeSide="OPEN", orderType="MARKET",
                             tpPrice=p.tp, slPrice=p.sl,
                             tpStopType="MARK_PRICE", tpOrderType="MARKET",
                             slStopType="MARK_PRICE", slOrderType="MARKET",
                             rules=rules, mark_price=mark, entry_price=last)
        print(f"  {side:5s} -> slPrice={b['slPrice']} tpPrice={b['tpPrice']} OK")

    print("\n=== 5) MINIMUM-STOP-DISTANCE GUARD ===")
    v = validate_tpsl_side(symbol=SYMBOL, side="LONG", entry=mark, mark=mark,
                           sl=mark, tp=mark * 1.01, rules=rules)
    print("  a stop exactly AT the mark:")
    for line in v:
        print("    " + line)
    coarse = PairRules(symbol=SYMBOL, min_trade_volume=1.0, base_precision=0,
                       quote_precision=4, max_leverage=50)
    try:
        plan_tpsl(side="LONG", entry=mark, leverage=50, notional=20.0,
                  strength="MEDIUM", confidence=70.0, tiers=TIERS, rules=coarse,
                  mark=mark, min_stop_ticks=3)
        print("  ERROR: should have raised")
    except TpSlSideError as e:
        print(f"  coarse tick + 50x -> {e}")

    print("\n=== 6) 20003 MARGIN PRE-FLIGHT ===")
    tiers_raw = get(f"/api/v1/futures/position/get_position_tiers?symbol={SYMBOL}")["data"]
    live_tiers = [Tier.from_api(r) for r in tiers_raw]
    print("  the tier for the notional band CAPS the leverage:")
    for t in live_tiers[:5]:
        print(f"    tier {t.level}: notional [{t.start_value:.0f}, {t.end_value:.0f}) "
              f"max_leverage={t.leverage}x mmr={t.maintenance_margin_rate}")
    print("  asking for more leverage than the tier allows makes the exchange")
    print("  require MORE margin than we computed -> 20003:")
    for notional in (5000.0, 15000.0, 30000.0, 60000.0):
        t = pick_tier(live_tiers, notional)
        for eng in (50, 10):
            if eng > t.leverage:
                print(f"    notional={notional:7.0f} engine_lev={eng:2d}x "
                      f"tier_max={t.leverage:2d}x -> REFUSED locally "
                      f"(exchange wants {margin_required(notional, t.leverage):.2f} "
                      f"USDT, not {margin_required(notional, eng):.2f})")
    print("  and the balance check:")
    for equity in (100.0, 1.0, 0.1):
        qty = position_size_by_risk(equity, 1.0, last, sl_old, 10, rules)
        notional = notional_from_qty(qty, last)
        margin = margin_required(notional, 10)
        verdict = "PASS" if margin <= equity else "REFUSED locally (would be 20003)"
        print(f"    equity={equity:7.2f} qty={qty:8.0f} notional={notional:9.4f} "
              f"margin={margin:8.4f} -> {verdict}")

    print("\nNOTE: no BITUNIX_API_KEY in this sandbox, so no live order was placed.")
    print("      The exchange response for a real order is NOT claimed here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
