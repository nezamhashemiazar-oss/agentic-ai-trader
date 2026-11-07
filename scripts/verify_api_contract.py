#!/usr/bin/env python3
"""Live verification of the API contract fixes.

Runs against the real Bitunix public API (no credentials needed) and shows:
  - the contract's endpoint count and structure
  - the audit result before/after
  - live responses for every endpoint that works without credentials
  - the request body the engine builds for each newly implemented endpoint
  - the private Tp Sl channel subscription frame (NOT live-tested: needs keys)

Run: python scripts/verify_api_contract.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jrock.trader.client import BitunixClient, BitunixConfig  # noqa: E402
from jrock.trader.errors import BitunixAPIError  # noqa: E402
from jrock.trader.ws import PRIVATE_CHANNELS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT = os.path.join(ROOT, "docs", "bitunix_api_contract.json")


def hr(t: str) -> None:
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


async def main() -> int:
    hr("1) THE CONTRACT")
    with open(CONTRACT, encoding="utf-8") as fh:
        c = json.load(fh)
    rest = [e for e in c["endpoints"] if e["kind"] == "rest"]
    ws = [e for e in c["endpoints"] if e["kind"] == "websocket"]
    guides = [e for e in c["endpoints"] if e["kind"] == "guide"]
    print(f"source        : {c['source']}")
    print(f"generated     : {c['generated_at']}")
    print(f"total pages   : {c['endpoint_count']}")
    print(f"  REST        : {len(rest)}")
    print(f"  WebSocket   : {len(ws)}")
    print(f"  guides      : {len(guides)}")
    print("\nsample entry (place_tp_sl_order):")
    for e in rest:
        if e["path"] == "/api/v1/futures/tpsl/place_order":
            print(json.dumps({k: e[k] for k in
                              ("title", "method", "path", "source_url", "fetched_at")},
                             indent=2))
            print("  required:", [p["name"] for p in e["request_params"] if p["required"]])
            print("  optional:", [p["name"] for p in e["request_params"] if not p["required"]])

    hr("2) THE AUDIT (after the fixes)")
    import subprocess
    out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "audit_api_contract.py")],
                         capture_output=True, text=True, cwd=ROOT)
    print(out.stdout.strip())

    hr("3) LIVE ENDPOINTS (no credentials needed)")
    cfg = BitunixConfig(api_key="", api_secret="", base_url="https://fapi.bitunix.com")
    client = BitunixClient(cfg)
    try:
        # funding rate (single)
        try:
            d = await client.get_funding_rate("BTCUSDT")
            row = d[0] if isinstance(d, list) and d else (d or {})
            print(f"funding_rate BTCUSDT      -> fundingRate={row.get('fundingRate')} "
                  f"markPrice={row.get('markPrice')}")
        except Exception as e:  # noqa: BLE001
            print(f"funding_rate FAILED: {type(e).__name__}: {e}")

        # funding rate batch - the symbols filter is ignored server-side
        try:
            d = await client.get_funding_rate_batch("BTCUSDT,ETHUSDT")
            rows = d if isinstance(d, list) else []
            print(f"funding_rate_batch        -> {len(rows)} rows returned "
                  f"(the symbols filter is IGNORED by the server)")
            kept = client.filter_funding_rows(rows, ["BTCUSDT", "ETHUSDT"])
            print(f"  after client-side filter: {len(kept)} -> "
                  f"{[r.get('symbol') for r in kept]}")
        except Exception as e:  # noqa: BLE001
            print(f"funding_rate_batch FAILED: {type(e).__name__}: {e}")

        # funding rate history - the get_ prefix
        try:
            d = await client.get_funding_rate_history("BTCUSDT", limit=3)
            rows = d if isinstance(d, list) else []
            print(f"get_funding_rate_history  -> {len(rows)} rows, first={rows[0] if rows else None}")
        except Exception as e:  # noqa: BLE001
            print(f"get_funding_rate_history FAILED: {type(e).__name__}: {e}")

        # kline
        try:
            d = await client.get_kline("BTCUSDT", "1m", limit=2)
            rows = d if isinstance(d, list) else []
            print(f"kline BTCUSDT 1m          -> {len(rows)} candles, first={rows[0] if rows else None}")
        except Exception as e:  # noqa: BLE001
            print(f"kline FAILED: {type(e).__name__}: {e}")

        # trading pairs
        try:
            d = await client.get_trading_pairs()
            rows = d if isinstance(d, list) else []
            print(f"trading_pairs             -> {len(rows)} symbols")
            for r in rows:
                if isinstance(r, dict) and r.get("symbol") == "AKEUSDT":
                    print(f"  AKEUSDT: minTradeVolume={r.get('minTradeVolume')} "
                          f"basePrecision={r.get('basePrecision')} "
                          f"quotePrecision={r.get('quotePrecision')} "
                          f"maxLeverage={r.get('maxLeverage')}")
        except Exception as e:  # noqa: BLE001
            print(f"trading_pairs FAILED: {type(e).__name__}: {e}")

        # depth
        try:
            d = await client.get_depth("BTCUSDT", limit=5)
            print(f"depth BTCUSDT             -> bids={len((d or {}).get('bids', []))} "
                  f"asks={len((d or {}).get('asks', []))}")
        except Exception as e:  # noqa: BLE001
            print(f"depth FAILED: {type(e).__name__}: {e}")

        # position tiers
        try:
            d = await client.get_position_tiers("AKEUSDT")
            rows = d if isinstance(d, list) else []
            print(f"position_tiers AKEUSDT    -> {len(rows)} tiers, "
                  f"first leverage={rows[0].get('leverage') if rows else None} "
                  f"mmr={rows[0].get('maintenanceMarginRate') if rows else None}")
        except Exception as e:  # noqa: BLE001
            print(f"position_tiers FAILED: {type(e).__name__}: {e}")
    finally:
        await client.close()

    hr("4) REQUEST BODIES FOR THE NEWLY IMPLEMENTED ENDPOINTS")
    print("These are the exact bodies the client builds. Captured by intercepting")
    print("request() so no live call is made.\n")

    captured: list[dict] = []

    class Spy(BitunixClient):
        async def request(self, method, path, *, params=None, body=None, signed=True):
            captured.append({"method": method, "path": path,
                             "params": params, "body": body, "signed": signed})
            return {"ok": True}

    spy = Spy(cfg)
    try:
        await spy.get_leverage_and_margin_mode("AKEUSDT")
        await spy.get_position_mode()
        await spy.get_trading_settings("AKEUSDT,BTCUSDT")
        await spy.get_trading_settings()
        await spy.change_position_mode("HEDGE")
        await spy.cancel_orders("AKEUSDT", [{"orderId": "1"}])
        await spy.get_pending_orders("AKEUSDT", skip=0, limit=10)
        await spy.get_history_orders("AKEUSDT", skip=0, limit=10)
        await spy.get_history_trades("AKEUSDT", skip=0, limit=10)
        await spy.get_history_positions("AKEUSDT", skip=0, limit=10)
        await spy.place_order(symbol="AKEUSDT", qty="1", side="BUY",
                              tradeSide="OPEN", orderType="MARKET")
        await spy.modify_order(orderId="1", qty="1", price="0.005")
    finally:
        await spy.close()

    for c in captured:
        print(f"{c['method']:5} {c['path']}")
        if c["params"]:
            print(f"      params: {json.dumps(c['params'], sort_keys=True)}")
        if c["body"]:
            print(f"      body  : {json.dumps(c['body'], sort_keys=True)}")

    hr("5) VALIDATION REFUSES A MALFORMED ORDER LOCALLY")
    spy2 = Spy(cfg)
    try:
        for bad in ({"symbol": "AKEUSDT", "qty": "1"},          # missing side/tradeSide/orderType
                    {"qty": "1", "side": "BUY", "tradeSide": "OPEN", "orderType": "MARKET"}):
            try:
                await spy2.place_order(**bad)
                print(f"  NOT REFUSED (bug): {bad}")
            except ValueError as e:
                print(f"  refused: {e}")
        try:
            await spy2.modify_order(orderId="1")
            print("  NOT REFUSED (bug): modify_order without qty/price")
        except ValueError as e:
            print(f"  refused: {e}")
    finally:
        await spy2.close()

    hr("6) PRIVATE TP SL CHANNEL")
    print(f"private channels defined: {list(PRIVATE_CHANNELS)}")
    print('subscribe frame: {"op":"subscribe","args":['
          + ",".join(f'{{"ch":"{c}"}}' for c in PRIVATE_CHANNELS) + "]}")
    print("documented push shape:")
    print('  {"ch":"tpsl","data":{"id":..,"positionId":..,"symbol":..,"tpPrice":..,')
    print('                       "slPrice":..,"tpQty":..,"slQty":..,"status":..,"ctime":..}}')
    has_keys = bool(os.environ.get("BITUNIX_API_KEY") and os.environ.get("BITUNIX_API_SECRET"))
    print(f"\ncredentials present: {has_keys}")
    if not has_keys:
        print("UNVERIFIED: the private login needs BITUNIX_API_KEY/SECRET.")
        print("-> the live private handshake was NOT exercised. Not claiming it works.")

    hr("7) NO LIVE ORDER")
    print(f"BITUNIX_API_KEY present: {has_keys}")
    if not has_keys:
        print("No live order was placed. The bodies above are what the engine WOULD")
        print("send; exchange acceptance is unverified.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
