"""Bitunix USDT-M futures REST client.

Signature (official, verified against the demo repo's test vector):
    message = nonce + timestamp + apiKey + queryString + body
    digest  = sha256(message)
    sign    = sha256(digest + secretKey)

CRITICAL: `queryString` in the signature is NOT the URL query string.
The official demo (Demo/Golang/open_api_http_sign.go -> SortParams, and
Demo/Java/.../SignUtils.java) concatenates sorted key+value pairs with NO
separator and NO '=':

    params {marginCoin: USDT}  ->  signed as "marginCoinUSDT"
    but the URL sent is          "?marginCoin=USDT"

Signing the urlencoded form is the #1 cause of error 10007. The official
test vector is asserted in tests/test_signing.py.

Headers: api-key, sign, nonce, timestamp, language, Content-Type.
Response envelope: {"code": 0, "data": ..., "msg": "Success"}  (code != 0 => error)

Every endpoint below is implemented from the official docs in docs/bitunix/.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import string
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import aiohttp

from .errors import BitunixAPIError, describe


def _nonce(n: int = 32) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def sign_query_string(params: dict[str, Any] | None) -> str:
    """Build the string that goes INTO the signature.

    Official demo behaviour: sorted keys, then key+value concatenated with no
    '=' and no '&'. Empty values are skipped entirely (SignUtils.java).
    """
    if not params:
        return ""
    parts = []
    for k in sorted(params):
        v = params[k]
        if v is None or v == "":
            continue
        parts.append(f"{k}{v}")
    return "".join(parts)


def url_query_string(params: dict[str, Any] | None) -> str:
    """Build the string that goes into the URL (normal urlencoded form)."""
    if not params:
        return ""
    clean = {k: v for k, v in params.items() if v is not None and v != ""}
    return urlencode(sorted(clean.items())) if clean else ""


@dataclass
class BitunixConfig:
    api_key: str = ""
    api_secret: str = ""
    base_url: str = "https://fapi.bitunix.com"
    language: str = "en-US"
    timeout: float = 20.0
    max_retries: int = 3


class BitunixClient:
    """Async REST client. Use as an async context manager."""

    def __init__(self, cfg: BitunixConfig):
        self.cfg = cfg
        self._session: aiohttp.ClientSession | None = None
        self._time_offset_ms: int = 0
        self.debug_sign: bool = os.getenv("BITUNIX_DEBUG_SIGN", "").strip().lower() in (
            "1", "true", "yes", "on")
        self.last_sign_debug: dict[str, str] = {}

    async def __aenter__(self) -> "BitunixClient":
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.cfg.timeout)
            )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------ signing
    def sign(self, nonce: str, timestamp: str, query: str = "", body: str = "") -> str:
        """sign = sha256(sha256(nonce+ts+apiKey+query+body) + secret).

        `query` here is the SIGNING form (key+value, no '='), not the URL form.
        """
        message = f"{nonce}{timestamp}{self.cfg.api_key}{query}{body}"
        digest = hashlib.sha256(message.encode()).hexdigest()
        sign = hashlib.sha256((digest + self.cfg.api_secret).encode()).hexdigest()
        if self.debug_sign:
            self.last_sign_debug = {
                "preimage": message,
                "digest": digest,
                "sign": sign,
                "query_signed": query,
                "body_signed": body,
            }
            # Spec: print the exact pre-image and the resulting signature so the
            # user can diff them against the official demo repo.
            print("[BITUNIX_DEBUG_SIGN] preimage = " + message)
            print("[BITUNIX_DEBUG_SIGN] digest   = " + digest)
            print("[BITUNIX_DEBUG_SIGN] sign     = " + sign)
        return sign

    def _headers(self, nonce: str, timestamp: str, sign: str) -> dict[str, str]:
        return {
            "api-key": self.cfg.api_key,
            "sign": sign,
            "nonce": nonce,
            "timestamp": timestamp,
            "language": self.cfg.language,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------ request
    async def request(self, method: str, path: str, *,
                      params: dict[str, Any] | None = None,
                      body: dict[str, Any] | None = None,
                      signed: bool = True) -> Any:
        await self.start()
        assert self._session is not None

        params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        # Two DIFFERENT strings: one for the URL, one for the signature.
        url_query = url_query_string(params)
        sign_query = sign_query_string(params)
        body_str = json.dumps(body, separators=(",", ":"), sort_keys=True) if body else ""
        url = f"{self.cfg.base_url}{path}" + (f"?{url_query}" if url_query else "")

        headers: dict[str, str] = {"Content-Type": "application/json", "language": self.cfg.language}
        if signed:
            if not self.cfg.api_key or not self.cfg.api_secret:
                raise BitunixAPIError(10003, "api-key can't be empty (set BITUNIX_API_KEY / BITUNIX_API_SECRET)")
            ts = str(int(time.time() * 1000) + self._time_offset_ms)
            nonce = _nonce()
            headers = self._headers(nonce, ts, self.sign(nonce, ts, sign_query, body_str))

        last_err: Exception | None = None
        for attempt in range(self.cfg.max_retries):
            try:
                async with self._session.request(
                    method, url, headers=headers,
                    data=body_str if body_str else None,
                ) as resp:
                    text = await resp.text()
                    if resp.status >= 500:
                        last_err = BitunixAPIError(resp.status, f"HTTP {resp.status}: {text[:300]}")
                        await asyncio.sleep(0.4 * (2 ** attempt))
                        continue
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        raise BitunixAPIError(resp.status, f"Non-JSON response: {text[:300]}") from None

                    code = payload.get("code", -1)
                    if code != 0:
                        if code == 10011 and attempt == 0:
                            await self.sync_time()
                            ts = str(int(time.time() * 1000) + self._time_offset_ms)
                            nonce = _nonce()
                            headers = self._headers(
                                nonce, ts, self.sign(nonce, ts, sign_query, body_str))
                            continue
                        raise BitunixAPIError(code, payload.get("msg", ""), payload.get("data"))
                    return payload.get("data")
            except BitunixAPIError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = e
                await asyncio.sleep(0.4 * (2 ** attempt))
        raise BitunixAPIError(10001, f"Network error after {self.cfg.max_retries} attempts: {last_err}")

    async def sync_time(self) -> int:
        """Align local clock with the exchange (error 10011 guard)."""
        await self.start()
        assert self._session is not None
        t0 = time.time() * 1000
        async with self._session.get(f"{self.cfg.base_url}/api/v1/futures/market/time") as r:
            if r.status == 200:
                d = await r.json()
                server = d.get("data")
                if isinstance(server, dict):
                    server = server.get("serverTime") or server.get("time")
                if server:
                    t1 = time.time() * 1000
                    self._time_offset_ms = int(float(server) - (t0 + t1) / 2)
        return self._time_offset_ms

    # ============================================================ MARKET (public)
    async def get_tickers(self, symbols: str | None = None) -> Any:
        return await self.request("GET", "/api/v1/futures/market/tickers",
                                  params={"symbols": symbols}, signed=False)

    async def get_ticker(self, symbol: str) -> Any:
        return await self.request("GET", "/api/v1/futures/market/ticker",
                                  params={"symbol": symbol}, signed=False)

    async def get_depth(self, symbol: str, limit: int = 15) -> Any:
        return await self.request("GET", "/api/v1/futures/market/depth",
                                  params={"symbol": symbol, "limit": limit}, signed=False)

    async def get_kline(self, symbol: str, interval: str, limit: int = 200,
                        end_time: int | None = None, ktype: str = "LAST_PRICE") -> Any:
        return await self.request("GET", "/api/v1/futures/market/kline",
                                  params={"symbol": symbol, "interval": interval, "limit": limit,
                                          "endTime": end_time, "type": ktype}, signed=False)

    async def get_trading_pairs(self, symbols: str | None = None) -> Any:
        return await self.request("GET", "/api/v1/futures/market/trading_pairs",
                                  params={"symbols": symbols}, signed=False)

    async def get_funding_rate(self, symbol: str) -> Any:
        return await self.request("GET", "/api/v1/futures/market/funding_rate",
                                  params={"symbol": symbol}, signed=False)

    async def get_funding_rate_batch(self, symbols: str = "") -> Any:
        """Funding rates for many symbols.

        Official: `symbols` is OPTIONAL - omitting it returns every symbol.

        VERIFIED LIVE 2026-11-07: this endpoint IGNORES the `symbols` filter.
        `?symbols=BTCUSDT,ETHUSDT`, `?symbol=BTCUSDT` and no parameter at all
        all return the same 931 rows. So the caller must filter the response
        itself; `filter_funding_rows` does that. Sending the parameter is
        harmless and kept for forward compatibility, but do not rely on it.
        """
        params: dict[str, Any] = {}
        if symbols:
            params["symbols"] = symbols
        return await self.request("GET", "/api/v1/futures/market/funding_rate/batch",
                                  params=params, signed=False)

    async def get_funding_rate_history(self, symbol: str,
                                       start_time: int | None = None,
                                       end_time: int | None = None,
                                       limit: int = 100) -> Any:
        """Funding rate history.

        VERIFIED LIVE 2026-11-07: the path is
        `/api/v1/futures/market/get_funding_rate_history` - with the `get_`
        prefix. `/funding_rate/history` returns `{"code":404,"msg":"Not Found"}`.

        Official params: `symbol` (required), `startTime`, `endTime`, `limit`
        (default 100, max 200). `page`/`pageSize` are NOT parameters - the live
        server ignores them and returns the default 100 rows.
        """
        return await self.request("GET",
                                  "/api/v1/futures/market/get_funding_rate_history",
                                  params={"symbol": symbol,
                                          "startTime": start_time,
                                          "endTime": end_time,
                                          "limit": limit},
                                  signed=False)

    # ============================================================ ACCOUNT
    async def get_single_account(self, margin_coin: str = "USDT") -> Any:
        return await self.request("GET", "/api/v1/futures/account",
                                  params={"marginCoin": margin_coin})

    @staticmethod
    def filter_funding_rows(rows: Any, symbols: list[str]) -> list[dict]:
        """Keep only the requested symbols from a funding_rate/batch response.

        Needed because the live endpoint ignores its `symbols` parameter and
        always returns every symbol (verified 2026-11-07: 931 rows regardless).
        Without this, `/funding BTCUSDT` would print the whole market.
        """
        want = {s.strip().upper() for s in symbols if s and s.strip()}
        if not want:
            return list(rows or [])
        return [r for r in (rows or [])
                if str(r.get("symbol", "")).upper() in want]

    async def get_accounts(self) -> Any:
        return await self.request("GET", "/api/v1/futures/account/accounts")

    async def change_leverage(self, symbol: str, leverage: int, margin_coin: str = "USDT") -> Any:
        return await self.request("POST", "/api/v1/futures/account/change_leverage",
                                  body={"symbol": symbol, "leverage": int(leverage),
                                        "marginCoin": margin_coin})

    async def change_margin_mode(self, symbol: str, margin_mode: str,
                                 margin_coin: str = "USDT") -> Any:
        return await self.request("POST", "/api/v1/futures/account/change_margin_mode",
                                  body={"symbol": symbol, "marginMode": margin_mode.upper(),
                                        "marginCoin": margin_coin})

    async def change_position_mode(self, position_mode: str) -> Any:
        """Switch between one-way and hedge mode.

        Official: `positionMode` is the ONLY request parameter. The old
        signature also sent `marginCoin`, which the docs do not list - an
        undocumented extra field.
        """
        return await self.request("POST", "/api/v1/futures/account/change_position_mode",
                                  body={"positionMode": position_mode.upper()})

    async def get_leverage_and_margin_mode(self, symbol: str,
                                           margin_coin: str = "USDT") -> Any:
        """Leverage and margin mode for one symbol.

        Official path is `/api/v1/futures/account/get_leverage_margin_mode`
        (with the `get_` prefix). The old code called
        `/api/v1/futures/account/leverage_margin_mode`, which is not a
        documented path. Both `symbol` and `marginCoin` are REQUIRED.
        """
        return await self.request("GET", "/api/v1/futures/account/get_leverage_margin_mode",
                                  params={"symbol": symbol, "marginCoin": margin_coin})

    async def get_position_mode(self) -> Any:
        """The account's futures position mode (one-way or hedge).

        Official: `GET /api/v1/futures/account/position_mode`, no parameters.
        """
        return await self.request("GET", "/api/v1/futures/account/position_mode")

    async def get_trading_settings(self, symbols: str | None = None) -> Any:
        """Per-symbol margin mode and leverage.

        Official: `GET /api/v1/futures/account/trading_settings`; `symbols` is
        optional (comma-separated, max 50). Omit it to get every configured
        symbol.
        """
        params: dict[str, Any] = {}
        if symbols:
            params["symbols"] = symbols
        return await self.request("GET", "/api/v1/futures/account/trading_settings",
                                  params=params)

    async def adjust_position_margin(self, symbol: str, amount: str,
                                     position_id: str | None = None,
                                     side: str | None = None,
                                     margin_coin: str = "USDT") -> Any:
        """Add or reduce isolated margin. Positive amount increases, negative reduces.

        Official: `symbol`, `marginCoin`, `amount` required; `side` (LONG/SHORT)
        OR `positionId` required - "Either side or positionId required". The old
        signature always sent positionId and never sent side, so a caller that
        only knew the side could not use this endpoint at all.
        """
        if not position_id and not side:
            raise ValueError("adjust_position_margin needs positionId or side")
        body: dict[str, Any] = {"symbol": symbol, "marginCoin": margin_coin,
                                "amount": str(amount)}
        if position_id:
            body["positionId"] = str(position_id)
        if side:
            body["side"] = str(side).upper()
        return await self.request("POST", "/api/v1/futures/account/adjust_position_margin",
                                  body=body)

    async def asset_query(self, margin_coin: str = "USDT") -> Any:
        return await self.request("GET", "/api/v1/futures/copyTrading/asset/query",
                                  params={"marginCoin": margin_coin})

    # ============================================================ POSITION
    async def get_pending_positions(self, symbol: str | None = None,
                                    position_id: str | None = None) -> Any:
        return await self.request("GET", "/api/v1/futures/position/get_pending_positions",
                                  params={"symbol": symbol, "positionId": position_id})

    async def get_history_positions(self, symbol: str | None = None, skip: int = 0,
                                    limit: int = 20,
                                    start_time: int | None = None,
                                    end_time: int | None = None) -> Any:
        """Closed positions. Official paging params are `skip`/`limit`."""
        return await self.request("GET", "/api/v1/futures/position/get_history_positions",
                                  params={"symbol": symbol, "skip": skip, "limit": limit,
                                          "startTime": start_time, "endTime": end_time})

    async def get_position_tiers(self, symbol: str) -> Any:
        return await self.request("GET", "/api/v1/futures/position/get_position_tiers",
                                  params={"symbol": symbol}, signed=False)

    # ============================================================ TRADE
    # Official required params for place_order, from the docs. Validated here so
    # a malformed order is refused locally instead of costing a round trip.
    PLACE_ORDER_REQUIRED = ("symbol", "qty", "side", "tradeSide", "orderType")
    MODIFY_ORDER_REQUIRED = ("qty", "price")

    async def place_order(self, **kwargs) -> Any:
        """Place an order.

        Official required: symbol, qty, side, tradeSide, orderType.
        Optional: price, positionId, effect, clientId, reduceOnly, and the
        tp*/sl* family. The body is passed through verbatim, but the required
        set is checked first.
        """
        missing = [k for k in self.PLACE_ORDER_REQUIRED if not kwargs.get(k)]
        if missing:
            raise ValueError(
                f"place_order is missing required parameter(s): {', '.join(missing)} "
                f"(official required: {', '.join(self.PLACE_ORDER_REQUIRED)})")
        return await self.request("POST", "/api/v1/futures/trade/place_order", body=kwargs)

    async def batch_order(self, symbol: str, order_list: list[dict]) -> Any:
        """Batch place orders.

        Official: `symbol` AND `orderList` are BOTH required, and orderList has
        a maximum length of 5. The old signature sent only orderList, so every
        batch call was missing a required parameter.
        """
        if not symbol:
            raise ValueError("batch_order requires symbol (official: required)")
        if not order_list:
            raise ValueError("batch_order requires a non-empty orderList")
        if len(order_list) > 5:
            raise ValueError(
                f"batch_order accepts at most 5 orders, got {len(order_list)}")
        return await self.request("POST", "/api/v1/futures/trade/batch_order",
                                  body={"symbol": symbol, "orderList": order_list})

    async def cancel_orders(self, symbol: str, order_list: list[dict]) -> Any:
        """Cancel a batch of orders.

        Official: BOTH `symbol` and `orderList` are required. The old signature
        sent only orderList, so every call was missing a required parameter.
        """
        if not symbol:
            raise ValueError("cancel_orders requires symbol (official: required)")
        if not order_list:
            raise ValueError("cancel_orders requires a non-empty orderList")
        return await self.request("POST", "/api/v1/futures/trade/cancel_orders",
                                  body={"symbol": symbol, "orderList": order_list})

    async def cancel_all_orders(self, symbol: str | None = None) -> Any:
        return await self.request("POST", "/api/v1/futures/trade/cancel_all_orders",
                                  body={"symbol": symbol} if symbol else {})

    async def modify_order(self, **kwargs) -> Any:
        """Modify a pending order.

        Official required: qty, price. Optional: orderId or clientId (one of
        them identifies the order), plus the tp*/sl* family.
        """
        missing = [k for k in self.MODIFY_ORDER_REQUIRED if not kwargs.get(k)]
        if missing:
            raise ValueError(
                f"modify_order is missing required parameter(s): {', '.join(missing)} "
                f"(official required: {', '.join(self.MODIFY_ORDER_REQUIRED)})")
        if not kwargs.get("orderId") and not kwargs.get("clientId"):
            raise ValueError("modify_order needs orderId or clientId")
        return await self.request("POST", "/api/v1/futures/trade/modify_order", body=kwargs)

    async def get_pending_orders(self, symbol: str | None = None, skip: int = 0,
                                 limit: int = 20) -> Any:
        """Pending orders.

        Official paging params are `skip`/`limit` (max 100), NOT
        `page`/`pageSize` - the live server ignores the latter and returns the
        default page.
        """
        return await self.request("GET", "/api/v1/futures/trade/get_pending_orders",
                                  params={"symbol": symbol, "skip": skip, "limit": limit})

    async def get_history_orders(self, symbol: str | None = None, skip: int = 0,
                                 limit: int = 20, start_time: int | None = None,
                                 end_time: int | None = None) -> Any:
        """Order history. Official paging params are `skip`/`limit`."""
        return await self.request("GET", "/api/v1/futures/trade/get_history_orders",
                                  params={"symbol": symbol, "skip": skip, "limit": limit,
                                          "startTime": start_time, "endTime": end_time})

    async def get_order_detail(self, order_id: str | None = None,
                               client_id: str | None = None) -> Any:
        return await self.request("GET", "/api/v1/futures/trade/get_order_detail",
                                  params={"orderId": order_id, "clientId": client_id})

    async def get_history_trades(self, symbol: str | None = None, skip: int = 0,
                                 limit: int = 20, start_time: int | None = None,
                                 end_time: int | None = None) -> Any:
        """Trade history. Official paging params are `skip`/`limit`."""
        return await self.request("GET", "/api/v1/futures/trade/get_history_trades",
                                  params={"symbol": symbol, "skip": skip, "limit": limit,
                                          "startTime": start_time, "endTime": end_time})

    async def close_all_position(self, symbol: str | None = None) -> Any:
        return await self.request("POST", "/api/v1/futures/trade/close_all_position",
                                  body={"symbol": symbol} if symbol else {})

    async def flash_close_position(self, position_id: str) -> Any:
        return await self.request("POST", "/api/v1/futures/trade/flash_close_position",
                                  body={"positionId": position_id})

    # ============================================================ TP/SL
    #
    # Paths and parameters verified against the official docs on 2026-11-07.
    # The previous version had three wrong paths and two wrong parameter names,
    # every one of which would have failed at the exchange:
    #   get_pending_order -> get_pending_orders
    #   get_history_order -> get_history_orders
    #   cancel_order      -> also needs `symbol`, not just `orderId`
    #   get_*_order       -> use `skip`/`limit`, not `page`/`pageSize`
    async def place_tp_sl_order(self, symbol: str, position_id: str,
                                tp_price: str | None = None,
                                sl_price: str | None = None,
                                tp_stop_type: str | None = None,
                                sl_stop_type: str | None = None,
                                tp_order_type: str | None = None,
                                tp_order_price: str | None = None,
                                sl_order_type: str | None = None,
                                sl_order_price: str | None = None,
                                tp_qty: str | None = None,
                                sl_qty: str | None = None) -> Any:
        """Standalone TP/SL order - the PARTIAL TP/SL method.

        Official: `symbol` + `positionId` required; at least one of
        tpPrice/slPrice; at least one of tpQty/slQty. The tpQty/slQty fields are
        what make a staged exit possible - the position TP/SL endpoint has no
        quantity field at all, so it can only ever close the whole position.
        """
        body: dict[str, Any] = {"symbol": symbol, "positionId": str(position_id)}
        if tp_price is not None and tp_price != "":
            body["tpPrice"] = str(tp_price)
        if sl_price is not None and sl_price != "":
            body["slPrice"] = str(sl_price)
        if tp_stop_type:
            body["tpStopType"] = str(tp_stop_type).upper()
        if sl_stop_type:
            body["slStopType"] = str(sl_stop_type).upper()
        if tp_order_type:
            body["tpOrderType"] = str(tp_order_type).upper()
        if tp_order_price is not None and tp_order_price != "":
            body["tpOrderPrice"] = str(tp_order_price)
        if sl_order_type:
            body["slOrderType"] = str(sl_order_type).upper()
        if sl_order_price is not None and sl_order_price != "":
            body["slOrderPrice"] = str(sl_order_price)
        if tp_qty is not None and tp_qty != "":
            body["tpQty"] = str(tp_qty)
        if sl_qty is not None and sl_qty != "":
            body["slQty"] = str(sl_qty)
        if "tpPrice" not in body and "slPrice" not in body:
            raise ValueError("place_tp_sl_order needs at least one of tpPrice/slPrice")
        if "tpQty" not in body and "slQty" not in body:
            raise ValueError("place_tp_sl_order needs at least one of tpQty/slQty")
        return await self.request("POST", "/api/v1/futures/tpsl/place_order", body=body)

    async def place_position_tp_sl_order(self, symbol: str, position_id: str,
                                         tp_price: str | None = None,
                                         sl_price: str | None = None,
                                         tp_stop_type: str = "MARK_PRICE",
                                         sl_stop_type: str = "MARK_PRICE") -> Any:
        """Position TP/SL - closes the ENTIRE position. One per position."""
        body: dict[str, Any] = {"symbol": symbol, "positionId": position_id,
                                "tpStopType": tp_stop_type, "slStopType": sl_stop_type}
        if tp_price:
            body["tpPrice"] = str(tp_price)
        if sl_price:
            body["slPrice"] = str(sl_price)
        return await self.request("POST", "/api/v1/futures/tpsl/position/place_order", body=body)

    async def modify_tp_sl_order(self, order_id: str,
                                 tp_price: str | None = None,
                                 sl_price: str | None = None,
                                 tp_stop_type: str | None = None,
                                 sl_stop_type: str | None = None,
                                 tp_order_type: str | None = None,
                                 tp_order_price: str | None = None,
                                 sl_order_type: str | None = None,
                                 sl_order_price: str | None = None,
                                 tp_qty: str | None = None,
                                 sl_qty: str | None = None) -> Any:
        """Modify a standalone TP/SL order. Official: `orderId` required."""
        body: dict[str, Any] = {"orderId": str(order_id)}
        if tp_price is not None and tp_price != "":
            body["tpPrice"] = str(tp_price)
        if sl_price is not None and sl_price != "":
            body["slPrice"] = str(sl_price)
        if tp_stop_type:
            body["tpStopType"] = str(tp_stop_type).upper()
        if sl_stop_type:
            body["slStopType"] = str(sl_stop_type).upper()
        if tp_order_type:
            body["tpOrderType"] = str(tp_order_type).upper()
        if tp_order_price is not None and tp_order_price != "":
            body["tpOrderPrice"] = str(tp_order_price)
        if sl_order_type:
            body["slOrderType"] = str(sl_order_type).upper()
        if sl_order_price is not None and sl_order_price != "":
            body["slOrderPrice"] = str(sl_order_price)
        if tp_qty is not None and tp_qty != "":
            body["tpQty"] = str(tp_qty)
        if sl_qty is not None and sl_qty != "":
            body["slQty"] = str(sl_qty)
        return await self.request("POST", "/api/v1/futures/tpsl/modify_order", body=body)

    async def modify_position_tp_sl_order(self, symbol: str, position_id: str,
                                          tp_price: str | None = None,
                                          sl_price: str | None = None,
                                          tp_stop_type: str | None = None,
                                          sl_stop_type: str | None = None) -> Any:
        body: dict[str, Any] = {"symbol": symbol, "positionId": position_id}
        if tp_price:
            body["tpPrice"] = str(tp_price)
        if sl_price:
            body["slPrice"] = str(sl_price)
        # Documented optional fields (LAST_PRICE | MARK_PRICE). Omitting them
        # makes the exchange fall back to its own default, which may not be the
        # price the local pre-flight validated against.
        if tp_stop_type:
            body["tpStopType"] = str(tp_stop_type).upper()
        if sl_stop_type:
            body["slStopType"] = str(sl_stop_type).upper()
        return await self.request("POST", "/api/v1/futures/tpsl/position/modify_order", body=body)

    async def cancel_tp_sl_order(self, symbol: str, order_id: str) -> Any:
        """Cancel a TP/SL order. Official: BOTH `symbol` and `orderId` required.

        The old signature sent only orderId, so every cancel was missing a
        required parameter.
        """
        return await self.request("POST", "/api/v1/futures/tpsl/cancel_order",
                                  body={"symbol": symbol, "orderId": str(order_id)})

    async def get_pending_tp_sl_order(self, symbol: str | None = None,
                                      position_id: str | None = None,
                                      side: int | None = None,
                                      position_mode: int | None = None,
                                      skip: int = 0, limit: int = 10) -> Any:
        """Pending TP/SL orders.

        Official path is get_pending_orders (PLURAL) and the paging params are
        `skip`/`limit` (max 100), not `page`/`pageSize`.
        """
        return await self.request("GET", "/api/v1/futures/tpsl/get_pending_orders",
                                  params={"symbol": symbol, "positionId": position_id,
                                          "side": side, "positionMode": position_mode,
                                          "skip": skip, "limit": limit})

    async def get_history_tp_sl_order(self, symbol: str | None = None,
                                      side: int | None = None,
                                      position_mode: int | None = None,
                                      start_time: int | None = None,
                                      end_time: int | None = None,
                                      skip: int = 0, limit: int = 10) -> Any:
        """TP/SL history.

        Official path is get_history_orders (PLURAL) and the paging params are
        `skip`/`limit` (max 100), not `page`/`pageSize`.
        """
        return await self.request("GET", "/api/v1/futures/tpsl/get_history_orders",
                                  params={"symbol": symbol, "side": side,
                                          "positionMode": position_mode,
                                          "startTime": start_time, "endTime": end_time,
                                          "skip": skip, "limit": limit})

    # ============================================================ misc
    async def change_log(self) -> Any:
        return await self.request("GET", "/api/v1/futures/market/change_log", signed=False)

    async def ping(self) -> bool:
        try:
            await self.get_tickers()
            return True
        except Exception:  # noqa: BLE001
            return False

    async def verify_credentials(self) -> tuple[bool, str]:
        try:
            acct = await self.get_single_account()
            return True, f"OK - account reachable ({len(acct or [])} margin coin(s))"
        except BitunixAPIError as e:
            return False, f"code {e.code}: {describe(e.code)} ({e})"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"
