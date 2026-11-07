#!/usr/bin/env python3
"""Verify the SECOND 30029 fix (the running-engine TP/SL path) against LIVE data.

Run:  python scripts/verify_managed_tpsl.py

The first fix (58f901c) made `plan_tpsl` mark-aware but left the management
loop (`manage_positions`: breakeven + trailing) building its own stop-loss with
a hardcoded `round_price(..., 4)` and never fetching the mark price. On AKEUSDT
(quotePrecision 6) that turns 0.005051 into 0.005100 - above the mark - and the
exchange answers:

    [30029] SL price must be less than mark price: 0.0051

This script fetches the real AKEUSDT rules and mark price and shows:
  1. the exact reported failure, replayed
  2. the fixed clamp for both directions
  3. the running-engine path end to end, with a fake client
  4. direction-aware rounding (a clamp can never round back across the mark)
  5. the conservative mark fallback when the mark is unavailable
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, ".")

from jrock.config import Settings
from jrock.trader.engine import TraderEngine
from jrock.trader.risk import (PairRules, clamp_sl, clamp_tp, format_price,
                               round_price, tick_size, validate_tpsl_side)

BASE = "https://fapi.bitunix.com"
SYMBOL = "AKEUSDT"


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.load(r)


class FakeClient:
    """Fake Bitunix client: records every TP/SL modification it is asked to send."""

    def __init__(self, mark, last, entry, side, rules_row):
        self.mark, self.last, self.entry, self.side = mark, last, entry, side
        self.rules_row = rules_row
        self.modified: list[dict] = []
        self.ticker_calls = 0

    async def get_tickers(self, sym):
        self.ticker_calls += 1
        return [{"symbol": sym, "lastPrice": str(self.last),
                 "markPrice": str(self.mark)}]

    async def get_pending_positions(self, sym=None):
        liq = self.entry * 0.92 if self.side == "LONG" else self.entry * 1.08
        return [{"positionId": "P1", "symbol": SYMBOL, "side": self.side,
                 "avgOpenPrice": str(self.entry), "qty": "4000",
                 "leverage": "10", "liqPrice": str(liq),
                 "markPrice": str(self.mark)}]

    async def get_trading_pairs(self, symbols=None):
        return [self.rules_row]

    async def modify_position_tp_sl_order(self, sym, pid, tp_price=None, sl_price=None):
        self.modified.append({"symbol": sym, "positionId": pid,
                              "tpPrice": tp_price, "slPrice": sl_price})
        return {"orderId": "T1"}

    async def get_kline(self, *a, **k):
        return []


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
          f"tick={tick_size(rules)} priceProtectScope={rules.price_protect_scope}")

    print("\n=== 1) THE REPORTED 30029, replayed ===")
    entry, m = 0.005051, 0.005062
    old = round_price(entry, 4)
    print(f"  entry={entry:.6f} mark={m:.6f}")
    print(f"  OLD (round_price(entry,4)) = {old:.4f}  sl<mark? {old < m}  "
          f"-> {'OK' if old < m else 'REJECTED 30029: SL price must be less than mark price: 0.0051'}")
    new = clamp_sl(side="LONG", sl=entry, entry=entry, mark=m, rules=rules)
    print(f"  NEW (clamp_sl)             = {new:.6f}  sl<mark? {new < m}  "
          f"-> {'OK' if new < m else 'REJECTED'}   sent as slPrice={format_price(new, rules)}")

    print("\n=== 2) FIXED CLAMP, both directions ===")
    for side in ("LONG", "SHORT"):
        for e, mk in ((0.005051, 0.005062), (0.005051, 0.005040)):
            sl = clamp_sl(side=side, sl=e, entry=e, mark=mk, rules=rules)
            tp = clamp_tp(side=side, tp=e, entry=e, mark=mk, rules=rules)
            v = validate_tpsl_side(symbol=SYMBOL, side=side, entry=e, mark=mk,
                                   sl=sl, tp=tp, rules=rules)
            print(f"  {side:5s} entry={e:.6f} mark={mk:.6f} -> sl={sl:.6f} "
                  f"tp={tp:.6f} valid={not v}")

    print("\n=== 3) RUNNING-ENGINE PATH end to end (fake client) ===")
    for side, entry, mk, last_px in (
        ("LONG", 0.005000, 0.005062, 0.005051),
        ("SHORT", 0.005000, 0.004940, 0.004940),
    ):
        s = Settings()
        s.market_data_source = "rest"
        s.breakeven_pct = 0.6
        # A throwaway data dir: the engine restores persisted positions from
        # `data/`, and a leftover one would shadow the fake position below.
        tmp = Path(tempfile.mkdtemp(prefix="jrock-verify-"))
        e = TraderEngine(s, data_dir=tmp)
        e.client = FakeClient(mk, last_px, entry, side, row)

        async def go():
            await e.load_rules([SYMBOL])
            return await e.manage_positions()

        acts = asyncio.run(go())
        print(f"  {side:5s} mark={mk:.6f} actions={[a['action'] for a in acts]}")
        for mod in e.client.modified:
            sl = float(mod["slPrice"])
            ok = sl < mk if side == "LONG" else sl > mk
            print(f"    SENT slPrice={mod['slPrice']}  correct side of mark? {ok}")
        if e.state.errors:
            print(f"    errors={e.state.errors}")

    print("\n=== 4) DIRECTION-AWARE ROUNDING (sub-tick mark) ===")
    for mk in (0.0047831, 0.0047834, 0.0047835, 0.0047836):
        sl = clamp_sl(side="SHORT", sl=0.004700, entry=0.0047830, mark=mk, rules=rules)
        print(f"  mark={mk:.7f} -> sl={sl:.6f}  sl>mark? {sl > mk}")

    print("\n=== 5) CONSERVATIVE MARK FALLBACK (mark unavailable) ===")
    for side in ("LONG", "SHORT"):
        cm = TraderEngine._conservative_mark(last, side, rules)
        print(f"  {side:5s} last={last} -> conservative mark={cm:.6f}")

    print("\nNOTE: no BITUNIX_API_KEY in this sandbox, so no live order was placed.")
    print("      The exchange response for a real order is NOT claimed here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
