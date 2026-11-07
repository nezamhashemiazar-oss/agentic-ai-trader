"""Configuration loading for J+Rock."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .thinking import level_from_env

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*a, **k):
        return False

ROOT = Path(__file__).resolve().parent.parent


def _b(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = ""
    bitunix_api_key: str = ""
    bitunix_api_secret: str = ""
    bitunix_base_url: str = "https://fapi.bitunix.com"
    bitunix_ws_public: str = "wss://fapi.bitunix.com/public/"
    bitunix_ws_private: str = "wss://fapi.bitunix.com/private/"
    auto_accept_edit: bool = False
    full_device_access: bool = False
    block_destructive: bool = True
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT"])
    leverage: int = 10
    margin_mode: str = "ISOLATION"
    position_mode: str = "ONE_WAY"
    max_open_positions: int = 3
    risk_pct: float = 1.0
    breakeven_pct: float = 0.6
    trailing_trigger_roi_pct: float = 25.0
    liq_sl_distance_pct: float = 50.0
    min_agreeing: int = 3
    timeframes: list[str] = field(default_factory=lambda: ["1m", "3m", "5m", "15m"])
    min_confidence: float = 70.0
    tf_min_confidence: float = 60.0
    scan_interval: int = 30
    report_interval: int = 30
    thinking_level: str = "medium"
    # rest | ws. Defaults to rest: the public WS layer is live-verified, but the
    # private (position/order/balance) login handshake has never been verified
    # against the real server, so we do not silently switch the engine onto it.
    market_data_source: str = "rest"
    #: A cached WS price older than this is never used for a trading decision.
    price_max_age_sec: float = 5.0
    #: A candle set is stale when the newest candle's open time is older than
    #: `kline_max_age_mult * interval`. A 1m candle is legitimately 60s old, so
    #: the price rule (flat seconds) does not apply to candles.
    kline_max_age_mult: float = 2.0
    #: Hard cap on candles held per (symbol, interval). Bounded, never unbounded.
    kline_cache_max: int = 500
    #: Minimum distance a TP/SL must keep from the mark/entry price, in ticks
    #: (1 tick = 10^-quotePrecision). Prevents rounding from collapsing a stop
    #: onto the mark price, which the exchange rejects with 30029/30031.
    tpsl_min_stop_ticks: int = 3
    #: Which price the exchange compares a TP/SL trigger against.
    #:   MARK_PRICE - the exchange mark price (Bitunix default, and what the
    #:                engine has always sent)
    #:   LAST_PRICE - the last traded price
    #: This value is used for BOTH the local pre-flight validation and the
    #: tpStopType/slStopType actually sent in the order body. A mismatch between
    #: the two is exactly what produced 30029: the stop was checked against the
    #: last price and rejected against the mark price.
    tp_sl_trigger_type: str = "MARK_PRICE"
    #: Which unit the engine sizes an order in, per the official Bitunix help
    #: article "Explanation of the Order Units in Futures Trading":
    #:   QUANTITY - base-coin quantity (what place_order's `qty` field takes;
    #:              the engine's historical behaviour)
    #:   NOTIONAL - position value in USDT; qty = notional / price
    #:   COST     - margin paid in USDT; qty = cost * leverage / price
    #: The wire format is always `qty` (base coin) - the unit only changes how
    #: the size is specified and converted.
    order_unit: str = "QUANTITY"
    #: Order-size bounds, in USDT. 0 disables the bound. These are the engine's
    #: OWN limits, checked before the request is sent; the exchange has its own
    #: (minTradeVolume / maxMarketOrderVolume) which are enforced separately.
    min_margin_usdt: float = 0.0
    max_margin_usdt: float = 0.0
    max_notional_usdt: float = 0.0
    #: Hard cap on learned-memory entries. The store evicts by weight once the
    #: cap is reached, so the file cannot grow without bound.
    max_memory_items: int = 5000

    @property
    def data_dir(self) -> Path:
        p = ROOT / "data"
        p.mkdir(parents=True, exist_ok=True)
        return p


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    env_path = Path(env_file) if env_file else ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)
    else:
        load_dotenv(override=False)

    tfs = [t.strip() for t in os.getenv("TRADE_TIMEFRAMES", "1m,3m,5m,15m").split(",") if t.strip()]
    syms = [s.strip().upper() for s in os.getenv("TRADE_SYMBOLS", "BTCUSDT").split(",") if s.strip()]

    return Settings(
        base_url=os.getenv("BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        api_key=os.getenv("API_KEY", ""),
        model=os.getenv("MODEL", ""),
        bitunix_api_key=os.getenv("BITUNIX_API_KEY", ""),
        bitunix_api_secret=os.getenv("BITUNIX_API_SECRET", ""),
        bitunix_base_url=os.getenv("BITUNIX_BASE_URL", "https://fapi.bitunix.com").rstrip("/"),
        bitunix_ws_public=os.getenv("BITUNIX_WS_PUBLIC", "wss://fapi.bitunix.com/public/"),
        bitunix_ws_private=os.getenv("BITUNIX_WS_PRIVATE", "wss://fapi.bitunix.com/private/"),
        auto_accept_edit=_b("AUTO_ACCEPT_EDIT", False),
        full_device_access=_b("FULL_DEVICE_ACCESS", False),
        block_destructive=_b("BLOCK_DESTRUCTIVE", True),
        symbols=syms or ["BTCUSDT"],
        leverage=_i("TRADE_LEVERAGE", 10),
        margin_mode=os.getenv("TRADE_MARGIN_MODE", "ISOLATION").upper(),
        position_mode=os.getenv("TRADE_POSITION_MODE", "ONE_WAY").upper(),
        max_open_positions=_i("TRADE_MAX_OPEN_POSITIONS", 3),
        risk_pct=_f("TRADE_RISK_PCT", 1.0),
        breakeven_pct=_f("TRADE_BREAKEVEN_PCT", 0.6),
        trailing_trigger_roi_pct=_f("TRADE_TRAILING_TRIGGER_ROI_PCT", 25.0),
        liq_sl_distance_pct=_f("TRADE_LIQ_SL_DISTANCE_PCT", 50.0),
        min_agreeing=_i("TRADE_MIN_AGREEING", 3),
        timeframes=tfs or ["1m", "3m", "5m", "15m"],
        min_confidence=_f("TRADE_MIN_CONFIDENCE", 70.0),
        tf_min_confidence=_f("TRADE_TF_MIN_CONFIDENCE", 60.0),
        scan_interval=_i("TRADE_SCAN_INTERVAL", 30),
        report_interval=_i("TRADE_REPORT_INTERVAL", 30),
        thinking_level=level_from_env(),
        market_data_source=_market_data_source(),
        price_max_age_sec=_f("PRICE_MAX_AGE_SEC", 5.0),
        kline_max_age_mult=_f("KLINE_MAX_AGE_SEC", 2.0),
        kline_cache_max=_i("KLINE_CACHE_MAX", 500),
        tpsl_min_stop_ticks=_i("TP_SL_MIN_STOP_TICKS", 3),
        tp_sl_trigger_type=_tp_sl_trigger_type(),
        order_unit=_order_unit(),
        min_margin_usdt=_f("MIN_MARGIN_USDT", 0.0),
        max_margin_usdt=_f("MAX_MARGIN_USDT", 0.0),
        max_notional_usdt=_f("MAX_NOTIONAL_USDT", 0.0),
        max_memory_items=_i("MAX_MEMORY_ITEMS", 5000),
    )


def _tp_sl_trigger_type() -> str:
    """TP_SL_TRIGGER_TYPE=MARK_PRICE|LAST_PRICE, default MARK_PRICE.

    MARK_PRICE is the documented Bitunix default and the engine's historical
    behaviour, so an unset or unrecognised value keeps it.
    """
    v = (os.getenv("TP_SL_TRIGGER_TYPE") or "MARK_PRICE").strip().upper()
    return v if v in ("MARK_PRICE", "LAST_PRICE") else "MARK_PRICE"


def _order_unit() -> str:
    """ORDER_UNIT=QUANTITY|NOTIONAL|COST, default QUANTITY.

    QUANTITY is what place_order's `qty` field takes and what the engine has
    always used, so an unset or unrecognised value keeps it.
    """
    v = (os.getenv("ORDER_UNIT") or "QUANTITY").strip().upper()
    return v if v in ("QUANTITY", "NOTIONAL", "COST") else "QUANTITY"


def _market_data_source() -> str:
    """MARKET_DATA_SOURCE=rest|ws, default rest.

    Anything unrecognised falls back to rest rather than guessing.
    """
    v = (os.getenv("MARKET_DATA_SOURCE") or "rest").strip().lower()
    return v if v in ("rest", "ws") else "rest"
