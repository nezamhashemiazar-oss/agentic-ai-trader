"""Typed, validated order-body builder for Bitunix futures.

Every field is validated against the official place_order spec BEFORE the
request is sent, so we never transmit a body we know is malformed. On a
validation failure we raise a clear local error naming the exact field and the
allowed values.

Official spec (docs/bitunix/place_order.md):
    symbol      string  required
    qty         string  required  (base coin, formatted to basePrecision)
    price       string  optional  (required for LIMIT, absent for MARKET)
    side        string  required  BUY | SELL
    tradeSide   string  required  OPEN | CLOSE
    positionId  string  optional  (required when tradeSide == CLOSE)
    orderType   string  required  LIMIT | MARKET
    effect      string  optional  GTC | IOC | FOK | POST_ONLY (LIMIT only)
    clientId    string  optional
    reduceOnly  boolean optional
    tpPrice / slPrice / tpStopType / slOrderType / tpOrderType / slOrderPrice
    slStopType / slOrderPrice  optional strings
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from .errors import BitunixAPIError
from .risk import PairRules, format_price, format_qty

log = logging.getLogger("jrock.order_builder")

SIDES = ("BUY", "SELL")
TRADE_SIDES = ("OPEN", "CLOSE")
ORDER_TYPES = ("LIMIT", "MARKET")
EFFECTS = ("GTC", "IOC", "FOK", "POST_ONLY")
STOP_TYPES = ("MARK_PRICE", "LAST_PRICE")
ORDER_PRICE_TYPES = ("LIMIT", "MARKET")

# clientId: alphanumeric, max 32 chars (Bitunix convention)
CLIENT_ID_MAX = 32


class OrderValidationError(ValueError):
    """Raised locally when an order body fails validation before sending."""


@dataclass
class OrderSpec:
    symbol: str
    qty: str
    side: str
    tradeSide: str
    orderType: str
    price: str | None = None
    positionId: str | None = None
    effect: str | None = None
    clientId: str | None = None
    reduceOnly: bool | None = None
    tpPrice: str | None = None
    tpStopType: str | None = None
    tpOrderType: str | None = None
    tpOrderPrice: str | None = None
    slPrice: str | None = None
    slStopType: str | None = None
    slOrderType: str | None = None
    slOrderPrice: str | None = None

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "symbol": self.symbol,
            "qty": self.qty,
            "side": self.side,
            "tradeSide": self.tradeSide,
            "orderType": self.orderType,
        }
        for k in ("price", "positionId", "effect", "clientId", "tpPrice",
                  "tpStopType", "tpOrderType", "tpOrderPrice", "slPrice",
                  "slStopType", "slOrderType", "slOrderPrice"):
            v = getattr(self, k)
            if v is not None:
                body[k] = v
        if self.reduceOnly is not None:
            body["reduceOnly"] = self.reduceOnly
        return body


def _norm(value: str | None) -> str | None:
    if value is None:
        return None
    return str(value).strip().upper()


def _check(cond: bool, field: str, allowed: str) -> None:
    if not cond:
        raise OrderValidationError(
            f"order field '{field}' is invalid. Allowed: {allowed}.")


def _check_str(value: Any, field: str) -> str:
    if value is None:
        raise OrderValidationError(f"order field '{field}' is required.")
    if isinstance(value, (int, float)):
        # Accept a numeric input but ALWAYS send a string (Bitunix rejects
        # numeric qty/price in the JSON body).
        return str(value)
    s = str(value).strip()
    if not s:
        raise OrderValidationError(f"order field '{field}' is empty.")
    return s


def build_order_body(*, symbol: str, qty: Any, side: str, orderType: str,
                     tradeSide: str = "OPEN", price: Any = None,
                     positionId: str | None = None, effect: str | None = None,
                     clientId: str | None = None, reduceOnly: bool | None = None,
                     tpPrice: Any = None, tpStopType: str | None = None,
                     tpOrderType: str | None = None, tpOrderPrice: Any = None,
                     slPrice: Any = None, slStopType: str | None = None,
                     slOrderType: str | None = None, slOrderPrice: Any = None,
                     rules: PairRules | None = None,
                     known_symbols: set[str] | None = None) -> dict[str, Any]:
    """Validate and build a place_order body. Raises OrderValidationError."""
    # --- symbol
    sym = _check_str(symbol, "symbol").upper()
    if known_symbols is not None and sym not in known_symbols:
        close = sorted(known_symbols, key=lambda s: _lev(s, sym))[:3]
        raise OrderValidationError(
            f"{sym} is not a listed futures symbol. Closest matches: {', '.join(close) or 'none'}")

    # --- enums (case-insensitive, normalized to uppercase)
    side = _norm(side)
    _check(side in SIDES, "side", "BUY, SELL")
    orderType = _norm(orderType)
    _check(orderType in ORDER_TYPES, "orderType", "LIMIT, MARKET")
    tradeSide = _norm(tradeSide)
    _check(tradeSide in TRADE_SIDES, "tradeSide", "OPEN, CLOSE")
    if effect is not None:
        effect = _norm(effect)
        _check(effect in EFFECTS, "effect", "GTC, IOC, FOK, POST_ONLY")
    if tpStopType is not None:
        tpStopType = _norm(tpStopType)
        _check(tpStopType in STOP_TYPES, "tpStopType", "MARK_PRICE, LAST_PRICE")
    if slStopType is not None:
        slStopType = _norm(slStopType)
        _check(slStopType in STOP_TYPES, "slStopType", "MARK_PRICE, LAST_PRICE")
    if tpOrderType is not None:
        tpOrderType = _norm(tpOrderType)
        _check(tpOrderType in ORDER_PRICE_TYPES, "tpOrderType", "LIMIT, MARKET")
    if slOrderType is not None:
        slOrderType = _norm(slOrderType)
        _check(slOrderType in ORDER_PRICE_TYPES, "slOrderType", "LIMIT, MARKET")

    # --- qty / price strings
    qty_s = _check_str(qty, "qty")
    price_s = _check_str(price, "price") if price is not None else None

    # --- precision / min / max against the symbol's rules
    if rules is not None:
        try:
            qty_f = float(qty_s)
        except ValueError:
            raise OrderValidationError(f"order field 'qty' is not a number: {qty_s!r}")
        if qty_f < rules.min_trade_volume:
            raise OrderValidationError(
                f"order field 'qty' {qty_s} is below the symbol minimum "
                f"minTradeVolume={rules.min_trade_volume}")
        if rules.max_market_order_volume and orderType == "MARKET" \
                and qty_f > rules.max_market_order_volume:
            raise OrderValidationError(
                f"order field 'qty' {qty_s} exceeds maxMarketOrderVolume="
                f"{rules.max_market_order_volume}")
        if rules.max_limit_order_volume and orderType == "LIMIT" \
                and qty_f > rules.max_limit_order_volume:
            raise OrderValidationError(
                f"order field 'qty' {qty_s} exceeds maxLimitOrderVolume="
                f"{rules.max_limit_order_volume}")
        # re-format to the symbol's precision (never send more decimals than allowed)
        qty_s = format_qty(qty_f, rules)
        if price_s is not None:
            try:
                price_f = float(price_s)
            except ValueError:
                raise OrderValidationError(f"order field 'price' is not a number: {price_s!r}")
            price_s = format_price(price_f, rules)

    # --- LIMIT / MARKET rules
    if orderType == "LIMIT":
        if price_s is None:
            raise OrderValidationError("order field 'price' is required for LIMIT orders.")
        if effect is None:
            effect = "GTC"
    else:  # MARKET
        if price_s is not None:
            raise OrderValidationError(
                "order field 'price' must be ABSENT for MARKET orders (got "
                f"{price_s!r}). Bitunix rejects a price on a market order.")
        if effect is not None:
            raise OrderValidationError(
                "order field 'effect' is only valid for LIMIT orders.")

    # --- tradeSide / positionId
    if tradeSide == "CLOSE" and positionId is None:
        raise OrderValidationError(
            "order field 'positionId' is required when tradeSide == CLOSE.")
    if tradeSide == "OPEN" and positionId is not None:
        raise OrderValidationError(
            "order field 'positionId' is only valid when tradeSide == CLOSE.")

    # --- reduceOnly
    if reduceOnly is not None and not isinstance(reduceOnly, bool):
        raise OrderValidationError(
            f"order field 'reduceOnly' must be a boolean, got {type(reduceOnly).__name__}.")

    # --- clientId
    if clientId is not None:
        cid = str(clientId)
        if len(cid) > CLIENT_ID_MAX or not cid.isalnum():
            raise OrderValidationError(
                f"order field 'clientId' must be alphanumeric and <= {CLIENT_ID_MAX} chars.")

    # --- TP/SL prices must be STRINGS respecting tick size
    def _price_field(v: Any, field: str) -> str | None:
        if v is None:
            return None
        s = _check_str(v, field)
        if rules is not None:
            try:
                s = format_price(float(s), rules)
            except ValueError:
                raise OrderValidationError(f"order field '{field}' is not a number: {s!r}")
        return s

    tpPrice = _price_field(tpPrice, "tpPrice")
    slPrice = _price_field(slPrice, "slPrice")
    tpOrderPrice = _price_field(tpOrderPrice, "tpOrderPrice")
    slOrderPrice = _price_field(slOrderPrice, "slOrderPrice")

    spec = OrderSpec(
        symbol=sym, qty=qty_s, side=side, tradeSide=tradeSide, orderType=orderType,
        price=price_s, positionId=positionId, effect=effect, clientId=clientId,
        reduceOnly=reduceOnly, tpPrice=tpPrice, tpStopType=tpStopType,
        tpOrderType=tpOrderType, tpOrderPrice=tpOrderPrice, slPrice=slPrice,
        slStopType=slStopType, slOrderType=slOrderType, slOrderPrice=slOrderPrice,
    )
    return spec.to_body()


def _lev(a: str, b: str) -> int:
    """Levenshtein distance for closest-match suggestions."""
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def debug_order_enabled() -> bool:
    return os.getenv("BITUNIX_DEBUG_ORDER", "").strip().lower() in ("1", "true", "yes", "on")


def log_order_body(body: dict[str, Any], error: BitunixAPIError | None = None) -> None:
    """Log the exact body we sent next to any error (redact nothing but the sign)."""
    if not debug_order_enabled():
        return
    safe = dict(body)
    # nothing secret in the body itself; keep it verbatim for diagnosis
    if error is not None:
        log.error("place_order FAILED code=%s msg=%r body=%s",
                  error.code, error.msg, json.dumps(safe, sort_keys=True))
    else:
        log.debug("place_order body=%s", json.dumps(safe, sort_keys=True))