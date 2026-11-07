#!/usr/bin/env python3
"""Verify the 4 TP/SL variants, the 3 order units, and the trigger-type config.

Runs against the LIVE Bitunix public API for real prices/rules, but sends NO
order (there is no API key in the sandbox). Everything printed is real output.
"""
from __future__ import annotations

import asyncio
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jrock.config import load_settings                      # noqa: E402
from jrock.trader.client import BitunixClient, BitunixConfig  # noqa: E402
from jrock.trader.order_builder import (ORDER_PRICE_TYPES,  # noqa: E402
                                        ORDER_TYPES, STOP_TYPES,
                                        build_order_body)
from jrock.trader.risk import (ORDER_UNITS, PairRules,      # noqa: E402
                               qty_from_unit, unit_from_qty)

SYM = "AKEUSDT"


def hr(t: str) -> None:
    print(f"\n=== {t} ===")


async def main() -> int:
    s = load_settings()
    client = BitunixClient(BitunixConfig(
        api_key=s.bitunix_api_key, api_secret=s.bitunix_api_secret,
        base_url=s.bitunix_base_url))

    hr("0) LIVE SYMBOL RULES")
    pairs = await client.get_trading_pairs(SYM)
    row = pairs[0] if isinstance(pairs, list) and pairs else pairs
    rules = PairRules.from_api(row)
    tick = await client.get_tickers(SYM)
    t = tick[0] if isinstance(tick, list) and tick else tick
    last = float(t.get("lastPrice") or 0)
    mark = float(t.get("markPrice") or 0)
    print(f"  {SYM}: last={last} mark={mark} "
          f"quotePrecision={rules.quote_precision} basePrecision={rules.base_precision} "
          f"minTradeVolume={rules.min_trade_volume}")

    hr("1) THE 4 TP/SL VARIANTS (tpOrderType/slOrderType x tpStopType/slStopType)")
    print(f"  ORDER_PRICE_TYPES (tpOrderType/slOrderType) = {ORDER_PRICE_TYPES}")
    print(f"  STOP_TYPES        (tpStopType/slStopType)   = {STOP_TYPES}")
    combos = list(itertools.product(ORDER_PRICE_TYPES, STOP_TYPES))
    print(f"  -> {len(combos)} documented combinations:")
    for ot, st in combos:
        print(f"     tpOrderType={ot:<6} tpStopType={st}")

    hr("2) THE 3 ORDER UNITS (official help article)")
    print(f"  ORDER_UNITS = {ORDER_UNITS}")
    for u in ORDER_UNITS:
        q = qty_from_unit(100.0, u, last, 10, rules)
        back = unit_from_qty(q, u, last, 10)
        print(f"     {u:<9} 100.0 -> qty={q}  (round-trip {back:.4f})")

    hr("3) ORDER BODY: all 4 TP/SL variants, LONG and SHORT")
    for direction, side in (("LONG", "BUY"), ("SHORT", "SELL")):
        for ot, st in combos:
            if direction == "LONG":
                sl, tp = last * 0.90, last * 1.10
            else:
                sl, tp = last * 1.10, last * 0.90
            body = build_order_body(
                symbol=SYM, qty=1000, side=side, tradeSide="OPEN",
                orderType="MARKET", reduceOnly=False,
                tpPrice=tp, tpStopType=st, tpOrderType=ot,
                slPrice=sl, slStopType=st, slOrderType=ot,
                rules=rules, known_symbols={SYM},
                mark_price=mark, entry_price=last,
            )
            print(f"  {direction:<5} {ot:<6}/{st:<10} -> "
                  f"tpOrderType={body['tpOrderType']} tpStopType={body['tpStopType']} "
                  f"slOrderType={body['slOrderType']} slStopType={body['slStopType']}")

    hr("4) ORDER BODY: the 3 order units (wire format is always base-coin qty)")
    for u in ORDER_UNITS:
        q = qty_from_unit(100.0, u, last, 10, rules)
        body = build_order_body(
            symbol=SYM, qty=q, side="BUY", tradeSide="OPEN",
            orderType="MARKET", reduceOnly=False,
            rules=rules, known_symbols={SYM},
        )
        print(f"  ORDER_UNIT={u:<9} amount=100.0 -> qty={body['qty']}")

    hr("5) TRIGGER TYPE: MARK_PRICE vs LAST_PRICE in the body")
    for st in STOP_TYPES:
        body = build_order_body(
            symbol=SYM, qty=1000, side="BUY", tradeSide="OPEN",
            orderType="MARKET", reduceOnly=False,
            tpPrice=last * 1.10, tpStopType=st, tpOrderType="MARKET",
            slPrice=last * 0.90, slStopType=st, slOrderType="MARKET",
            rules=rules, known_symbols={SYM},
            mark_price=mark, entry_price=last,
        )
        print(f"  TP_SL_TRIGGER_TYPE={st:<10} -> "
              f"tpStopType={body['tpStopType']} slStopType={body['slStopType']}")

    hr("6) ORDER TYPES: MARKET and LIMIT (the 2 place_order orderType values)")
    print(f"  ORDER_TYPES = {ORDER_TYPES}")
    m = build_order_body(symbol=SYM, qty=1000, side="BUY", tradeSide="OPEN",
                         orderType="MARKET", reduceOnly=False,
                         rules=rules, known_symbols={SYM})
    print(f"  MARKET -> {m}")
    lim = build_order_body(symbol=SYM, qty=1000, side="BUY", tradeSide="OPEN",
                           orderType="LIMIT", price=last, effect="GTC",
                           rules=rules, known_symbols={SYM})
    print(f"  LIMIT  -> {lim}")

    hr("7) NO LIVE ORDER")
    print(f"  BITUNIX_API_KEY present: {bool(s.bitunix_api_key)}")
    print("  -> no order was sent; the exchange response is UNVERIFIED")

    await client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
