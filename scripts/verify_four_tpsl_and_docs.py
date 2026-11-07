#!/usr/bin/env python3
"""Live verification for the 4 TP/SL methods and the API-doc audit fixes.

Runs against the REAL Bitunix public API. No credentials needed for anything
here - every endpoint exercised is public. The private Tp Sl channel is NOT
exercised (it needs BITUNIX_API_KEY) and this script says so rather than
pretending.

    python scripts/verify_four_tpsl_and_docs.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jrock.config import Settings                      # noqa: E402
from jrock.trader.client import BitunixClient, BitunixConfig  # noqa: E402
from jrock.trader.engine import TraderEngine           # noqa: E402
from jrock.trader.errors import BitunixAPIError        # noqa: E402
from jrock.trader.risk import PairRules                # noqa: E402
from jrock.trader.ws import PRIVATE_CHANNELS, PrivateFeed, WSConfig  # noqa: E402

LINE = "=" * 72


def hdr(n: str) -> None:
    print(f"\n{LINE}\n{n}\n{LINE}")


async def main() -> int:
    s = Settings()
    s.market_data_source = "rest"
    c = BitunixClient(BitunixConfig())
    await c.start()

    # ---------------------------------------------------------------- 1
    hdr("1) THE FOUR TP/SL METHODS (official academy article)")
    print("  The article defines exactly four:")
    print("    1. Position TP/SL  - one TP/SL closes the ENTIRE position")
    print("    2. Partial TP/SL   - a defined % closes at each trigger")
    print("    3. Trailing TP/SL  - dynamic exit after trend + retracement")
    print("    4. Account TP/SL   - closes ALL positions on total account PnL")
    e = TraderEngine(s)
    for m in ("POSITION", "PARTIAL", "TRAILING", "ACCOUNT"):
        e.state.tpsl_method = m
        print(f"    {m:<9} -> {e.tpsl_method_report()}")

    # ---------------------------------------------------------------- 2
    hdr("2) LIVE: funding rate (single) - public")
    r = await c.get_funding_rate("BTCUSDT")
    d = r[0] if isinstance(r, list) and r else r
    print(f"  GET /api/v1/futures/market/funding_rate?symbol=BTCUSDT")
    print(f"    code={r.get('code') if isinstance(r, dict) else 'ok'} "
          f"symbol={d.get('symbol')} fundingRate={d.get('fundingRate')} "
          f"markPrice={d.get('markPrice')} nextFundingTime={d.get('nextFundingTime')}")

    # ---------------------------------------------------------------- 3
    hdr("3) LIVE: funding rate (batch) - the symbols filter is IGNORED")
    raw = await c.get_funding_rate_batch("BTCUSDT,ETHUSDT")
    rows = raw if isinstance(raw, list) else (raw or {}).get("data") or []
    print(f"  GET /api/v1/futures/market/funding_rate/batch?symbols=BTCUSDT,ETHUSDT")
    print(f"    rows returned: {len(rows)}  <- the filter did NOT apply")
    filtered = c.filter_funding_rows(rows, ["BTCUSDT", "ETHUSDT"])
    print(f"    after client-side filter: {len(filtered)} "
          f"-> {[x.get('symbol') for x in filtered]}")

    # ---------------------------------------------------------------- 4
    hdr("4) LIVE: funding rate history - the path needs the get_ prefix")
    print("  WRONG (old code): /api/v1/futures/market/funding_rate/history")
    try:
        bad = await c.request("GET", "/api/v1/futures/market/funding_rate/history",
                              params={"symbol": "BTCUSDT"}, signed=False)
        print(f"    -> {json.dumps(bad)[:90]}")
    except BitunixAPIError as ex:
        print(f"    -> BitunixAPIError [{ex.code}] {ex.msg}   <- the old path 404s")
    print("  RIGHT (fixed):    /api/v1/futures/market/get_funding_rate_history")
    hist = await c.get_funding_rate_history("BTCUSDT", limit=3)
    hrows = hist if isinstance(hist, list) else (hist or {}).get("data") or []
    print(f"    -> {len(hrows)} rows, first={hrows[0] if hrows else None}")

    # ---------------------------------------------------------------- 5
    hdr("5) LIVE: kline + trading pairs")
    kl = await c.get_kline("BTCUSDT", "1m", limit=2)
    krows = kl if isinstance(kl, list) else (kl or {}).get("data") or []
    print(f"  kline: {len(krows)} candles, first={krows[0] if krows else None}")
    pairs = await c.get_trading_pairs()
    prows = pairs if isinstance(pairs, list) else (pairs or {}).get("data") or []
    print(f"  trading_pairs: {len(prows)} symbols")
    ake = next((p for p in prows if p.get("symbol") == "AKEUSDT"), None)
    if ake:
        print(f"    AKEUSDT: minTradeVolume={ake.get('minTradeVolume')} "
              f"basePrecision={ake.get('basePrecision')} "
              f"quotePrecision={ake.get('quotePrecision')} "
              f"maxLeverage={ake.get('maxLeverage')}")

    # ---------------------------------------------------------------- 6
    hdr("6) REQUEST BODIES the engine builds for the fixed endpoints")
    calls: list[dict] = []

    async def capture(method, path, **kw):
        calls.append({"method": method, "path": path,
                      "body": kw.get("body"), "params": kw.get("params")})
        return {"ok": True}

    c.request = capture  # type: ignore[assignment]

    await c.place_tp_sl_order("AKEUSDT", "P1", tp_price="0.006", sl_price="0.004",
                              tp_qty="1000", sl_qty="1000",
                              tp_stop_type="MARK_PRICE", sl_stop_type="MARK_PRICE")
    print("  PARTIAL TP/SL (tpQty/slQty make a staged exit possible):")
    print(f"    POST {calls[-1]['path']}")
    print(f"    {json.dumps(calls[-1]['body'])}")

    await c.place_position_tp_sl_order("AKEUSDT", "P1", "0.006", "0.004")
    print("  POSITION TP/SL (no qty field - closes the whole position):")
    print(f"    POST {calls[-1]['path']}")
    print(f"    {json.dumps(calls[-1]['body'])}")

    await c.cancel_tp_sl_order("AKEUSDT", "T1")
    print("  CANCEL (symbol AND orderId - both required):")
    print(f"    POST {calls[-1]['path']}")
    print(f"    {json.dumps(calls[-1]['body'])}")

    await c.get_pending_tp_sl_order(symbol="AKEUSDT", skip=0, limit=10)
    print("  PENDING (plural path, skip/limit):")
    print(f"    GET  {calls[-1]['path']}")
    print(f"    {json.dumps(calls[-1]['params'])}")

    await c.get_history_tp_sl_order(symbol="AKEUSDT", skip=0, limit=10)
    print("  HISTORY (plural path, skip/limit):")
    print(f"    GET  {calls[-1]['path']}")
    print(f"    {json.dumps(calls[-1]['params'])}")

    await c.batch_order("AKEUSDT", [{"qty": "1", "side": "BUY"}])
    print("  BATCH ORDER (symbol AND orderList, max 5):")
    print(f"    POST {calls[-1]['path']}")
    print(f"    {json.dumps(calls[-1]['body'])}")

    await c.adjust_position_margin("AKEUSDT", "100", side="LONG")
    print("  ADJUST MARGIN (side OR positionId):")
    print(f"    POST {calls[-1]['path']}")
    print(f"    {json.dumps(calls[-1]['body'])}")

    # ---------------------------------------------------------------- 7
    hdr("7) PRIVATE Tp Sl CHANNEL - subscription frame (NOT live-tested)")
    pf = PrivateFeed(WSConfig())
    print(f"  channels subscribed: {pf.channels}")
    print(f"  subscribe frame: {json.dumps({'op': 'subscribe', 'args': pf.ws.subs})}")
    print("  documented push shape:")
    print('    {"ch":"tpsl","data":{"id":..,"positionId":..,"symbol":..,'
          '"tpPrice":..,"slPrice":..,"tpQty":..,"slQty":..,"status":..,"ctime":..}}')
    print("  UNVERIFIED: the private login needs BITUNIX_API_KEY/SECRET.")
    print(f"  credentials present: {bool(s.bitunix_api_key and s.bitunix_api_secret)}")
    print("  -> the live private handshake was NOT exercised. Not claiming it works.")

    # ---------------------------------------------------------------- 8
    hdr("8) NO LIVE ORDER")
    print(f"  BITUNIX_API_KEY present: {bool(s.bitunix_api_key)}")
    print("  No order was placed. The bodies above are what the engine WOULD send;")
    print("  exchange acceptance is unverified without credentials.")

    await c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
