"""The trader engine: scanner + trader + autotrade + position management.

State machine:
    engine_on  -> scanner loop + management loop run
    scan_on    -> scanner produces signals (no orders)
    autotrade  -> scanner signals are executed as orders

All commands from the spec are implemented as methods and routed by `command()`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import Settings
from ..store import Store
from .account_tpsl import (AccountTpSlConfig, account_tpsl_decision,
                           total_unrealized_pnl)
from .aggregator import Aggregate, aggregate
from .client import BitunixClient, BitunixConfig
from .errors import BitunixAPIError
from .indicators import atr, to_candles
from .kline_cache import REST_KLINE_MAX, KlineCache
from .ladder import (Ladder, LadderError, decide as ladder_decide,
                     ladder_from_settings, level_qtys, parse_ladder,
                     remaining_after, trigger_price_for)
from .order_builder import (OrderValidationError, build_order_body,
                            debug_order_enabled, log_order_body)
from .price_cache import PriceCache
from .risk import (PairRules, Tier, TpSlSideError, clamp_sl, clamp_tp,
                   fee_for_notional, format_price, format_qty, margin_budget,
                   margin_required,
                   max_leverage_for_stop, max_notional_for_balance,
                   max_notional_for_margin,
                   pick_tier,
                   position_size_by_margin,
                   position_size_by_risk,
                   qty_from_notional, qty_from_unit, required_cost, roi_pct,
                   round_price, round_step, spendable_balance,
                   tier_for_leverage, tier_leverage_violation, tick_size,
                   unit_from_qty, validate_tpsl_side)
from .strategies import build_strategies
from .tpsl import (PositionState, mid_management_action, next_trailing_stop,
                   plan_tpsl, should_breakeven, should_trail)
from .ws import MarketDataFeed, PrivateFeed, WSConfig

log = logging.getLogger("jrock.engine")


@dataclass
class EngineState:
    engine_on: bool = False
    scan_on: bool = True
    autotrade: bool = False
    report_on: bool = False
    report_interval: int = 30
    scan_interval: int = 30
    reversal: bool = False
    reversal_confidence: float = 70.0
    mid_management: bool = True
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT"])
    leverage: int = 10
    margin_mode: str = "ISOLATION"
    position_mode: str = "ONE_WAY"
    max_open_positions: int = 3
    risk_pct: float = 1.0
    #: Share of the spendable balance one position may lock as margin (%).
    #: 0 disables the budget and sizes purely by risk_pct.
    margin_amount_pct: float = 10.0
    breakeven_pct: float = 0.6
    trailing_trigger_roi_pct: float = 25.0
    liq_sl_distance_pct: float = 50.0
    min_agreeing: int = 3
    timeframes: list[str] = field(default_factory=lambda: ["1m", "3m", "5m", "15m"])
    min_confidence: float = 70.0
    tf_min_confidence: float = 60.0
    last_scan_ts: float = 0.0
    last_report_ts: float = 0.0
    scans: int = 0
    errors: list[str] = field(default_factory=list)
    # --- the four documented TP/SL methods (see account_tpsl.py / tpsl.py)
    tpsl_method: str = "POSITION"
    partial_close_pct: float = 50.0
    partial_at_roi_pct: float = 50.0
    account_tp_usdt: float = 0.0
    account_sl_usdt: float = 0.0
    account_tp_pct: float = 0.0
    account_sl_pct: float = 0.0
    # --- TP ladder (multi-level partial take-profit)
    tp_ladder: str = ""
    tp_ladder_breakeven_after: int = 1
    tp_ladder_runner: str = "trail"
    tp_ladder_trigger_basis: str = "R"


class TraderEngine:
    def __init__(self, settings: Settings, data_dir: Path | None = None,
                 store: Store | None = None):
        self.s = settings
        self.data_dir = data_dir or settings.data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state = EngineState(
            symbols=list(settings.symbols),
            leverage=settings.leverage,
            margin_mode=settings.margin_mode,
            position_mode=settings.position_mode,
            max_open_positions=settings.max_open_positions,
            risk_pct=settings.risk_pct,
            margin_amount_pct=settings.margin_amount_pct,
            breakeven_pct=settings.breakeven_pct,
            trailing_trigger_roi_pct=settings.trailing_trigger_roi_pct,
            liq_sl_distance_pct=settings.liq_sl_distance_pct,
            min_agreeing=settings.min_agreeing,
            timeframes=list(settings.timeframes),
            min_confidence=settings.min_confidence,
            tf_min_confidence=settings.tf_min_confidence,
            scan_interval=settings.scan_interval,
            report_interval=settings.report_interval,
            tpsl_method=(settings.tpsl_method or "POSITION").upper(),
            partial_close_pct=settings.partial_close_pct,
            partial_at_roi_pct=settings.partial_at_roi_pct,
            account_tp_usdt=settings.account_tp_usdt,
            account_sl_usdt=settings.account_sl_usdt,
            account_tp_pct=settings.account_tp_pct,
            account_sl_pct=settings.account_sl_pct,
            tp_ladder=settings.tp_ladder,
            tp_ladder_breakeven_after=settings.tp_ladder_breakeven_after,
            tp_ladder_runner=settings.tp_ladder_runner,
            tp_ladder_trigger_basis=settings.tp_ladder_trigger_basis,
        )
        self.client = BitunixClient(BitunixConfig(
            api_key=settings.bitunix_api_key,
            api_secret=settings.bitunix_api_secret,
            base_url=settings.bitunix_base_url,
        ))
        self.strategies = build_strategies()
        self.rules: dict[str, PairRules] = {}
        self.tiers: dict[str, list[Tier]] = {}
        self.positions: dict[str, PositionState] = {}
        self.last_aggregates: dict[str, Aggregate] = {}
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        # --- market data: one cache, one optional WS feed, one REST counter
        self.price_cache = PriceCache(max_age=settings.price_max_age_sec)
        self.kline_cache = KlineCache(max_age_mult=settings.kline_max_age_mult,
                                      max_candles=settings.kline_cache_max)
        self.feed: MarketDataFeed | None = None
        self.feed_error: str = ""
        self.feed_started_at: float = 0.0
        #: Private WS (balance/position/order/tpsl). Only opened when the
        #: credentials are present AND the source is ws - it is never opened in
        #: rest mode, so rest mode still opens zero sockets.
        self.private_feed: PrivateFeed | None = None
        self.private_feed_error: str = ""
        self.rest_price_calls: int = 0
        self.rest_kline_calls: int = 0
        self.kline_warmup_calls: int = 0
        self.trades_path = self.data_dir / "trades.jsonl"
        self.tpsl_path = self.data_dir / "tpsl.jsonl"
        self.signals_path = self.data_dir / "signals.jsonl"
        # --- persistence: one store owns every durable value
        self.store = store if store is not None else Store(data_dir=self.data_dir)
        self._snapshot: dict[str, Any] = {}
        if settings.bitunix_api_key:
            self.store.register_secret(settings.bitunix_api_key)
        if settings.bitunix_api_secret:
            self.store.register_secret(settings.bitunix_api_secret)
        self.restored = self._restore_from_store()
        #: set on restore when autotrade was ON before the restart, so the
        #: banner can say so out loud instead of silently resuming live orders.
        self.autotrade_was_on: bool = False
        #: set on restore when the engine loop was ON before the restart.
        self.engine_was_on: bool = False
        # Optional hook so the agent can add its own settings to `status`.
        self.status_extra: Callable[[], list[str]] | None = None

    # ============================================================== persistence
    #: EngineState fields that survive a restart. Deliberately excludes
    #: last_*_ts (wall-clock, meaningless later) and `errors` (kept as counters).
    PERSISTED_STATE_FIELDS: tuple[str, ...] = (
        "scan_on", "report_on", "report_interval", "scan_interval",
        "reversal", "reversal_confidence", "mid_management",
        "symbols", "leverage", "margin_mode", "position_mode",
        "max_open_positions", "risk_pct", "margin_amount_pct", "breakeven_pct",
        "trailing_trigger_roi_pct", "liq_sl_distance_pct",
        "min_agreeing", "timeframes", "min_confidence", "tf_min_confidence",
        "scans",
        "tpsl_method", "partial_close_pct", "partial_at_roi_pct",
        "account_tp_usdt", "account_sl_usdt", "account_tp_pct", "account_sl_pct",
        "tp_ladder", "tp_ladder_breakeven_after", "tp_ladder_runner",
        "tp_ladder_trigger_basis",
    )

    def _state_snapshot(self) -> dict[str, Any]:
        """Everything whose change should trigger a save.

        Includes `autotrade` and `engine_on` even though they are never
        auto-resumed: toggling autotrade alone must still be recorded, or the
        banner cannot warn that it was on before a crash.
        """
        snap = {f: getattr(self.state, f) for f in self.PERSISTED_STATE_FIELDS}
        snap["autotrade"] = self.state.autotrade
        snap["engine_on"] = self.state.engine_on
        return snap

    def _restore_from_store(self) -> dict[str, Any]:
        """Apply persisted state over the .env-seeded defaults.

        Precedence: store > .env > default. `autotrade` and `engine_on` are
        intentionally NOT auto-resumed - see the note in `_persist_if_changed`.
        """
        saved = self.store.section("engine")
        applied: list[str] = []
        for field_name, value in saved.items():
            if field_name in ("autotrade", "engine_on"):
                continue
            if field_name in self.PERSISTED_STATE_FIELDS:
                try:
                    setattr(self.state, field_name, value)
                    applied.append(field_name)
                except (TypeError, ValueError):
                    continue
        self.autotrade_was_on = bool(saved.get("autotrade"))
        self.engine_was_on = bool(saved.get("engine_on"))

        # positions
        raw_pos = self.store.section("positions")
        for pid, body in raw_pos.items():
            try:
                self.positions[pid] = PositionState(**body)
            except TypeError:
                continue

        # counters
        saved_counters = self.store.section("counters")
        self.state.scans = int(saved_counters.get("scans", self.state.scans) or 0)

        self._snapshot = self._state_snapshot()
        return {"settings": applied, "positions": len(self.positions),
                "scans": self.state.scans,
                "autotrade_was_on": self.autotrade_was_on,
                "engine_was_on": self.engine_was_on,
                "from": self.store.loaded_from if self.store.loaded else ""}

    def _persist_if_changed(self) -> None:
        """Save engine state if any persisted field moved.

        Called after every command, so a runtime change hits the disk in the
        same call - `kill -9` one millisecond later cannot lose it. autotrade
        and engine_on are recorded as *desired* state only and are never
        resumed automatically: silently restarting live order flow after a
        crash is exactly the failure mode this project must not have.
        """
        snap = self._state_snapshot()
        if snap == self._snapshot:
            return
        self.store.set_section("engine", {
            **snap,
            "autotrade": self.state.autotrade,
            "engine_on": self.state.engine_on,
        }, save=False)
        self.store.counters = {"scans": self.state.scans,
                               "errors": len(self.state.errors),
                               "last_error": self.state.errors[-1] if self.state.errors else ""}
        self.store.save()
        self._snapshot = snap

    # ================================================================ lifecycle
    async def start(self) -> None:
        await self.client.start()
        self._stop.clear()
        await self.start_feed()

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        await self.stop_feed()
        await self.client.close()

    # ============================================================ market data
    async def start_feed(self) -> None:
        """Open the public WS feed when MARKET_DATA_SOURCE=ws.

        With `rest` this returns immediately and NO socket is ever opened -
        that is asserted by a test.
        """
        if self.s.market_data_source != "ws":
            return
        if self.feed is not None:
            return
        symbols = self._feed_symbols()
        if not symbols:
            return
        cfg = WSConfig.from_settings(self.s)
        self.feed = MarketDataFeed(cfg, symbols, self.kline_timeframes(),
                                   cache=self.price_cache,
                                   kline_cache=self.kline_cache)
        self.feed_started_at = time.time()
        try:
            await self.feed.start()
            # Wait (bounded) for the handshake so the banner tells the truth
            # instead of reporting "NOT connected" during a normal connect.
            for _ in range(50):
                if self.feed.ws.connected:
                    break
                await asyncio.sleep(0.1)
            # Seed the kline cache from REST once. The WS only gives candles
            # from the moment we subscribe, so without this a 200-candle
            # strategy would need 200 minutes of uptime before it could run.
            await self.warmup_klines()
        except Exception as e:  # noqa: BLE001
            self.feed_error = f"{type(e).__name__}: {e}"
            log.warning("ws feed failed to start: %s - falling back to REST", e)
            self.feed = None
        # The private feed carries balance/position/order/tpsl. It needs
        # credentials, so it is skipped (with a reason) when they are absent -
        # the public feed keeps working either way.
        if self.s.bitunix_api_key and self.s.bitunix_api_secret:
            try:
                self.private_feed = PrivateFeed(cfg)
                await self.private_feed.start()
                for _ in range(30):
                    if self.private_feed.ws.logged_in:
                        break
                    await asyncio.sleep(0.1)
            except Exception as e:  # noqa: BLE001
                self.private_feed_error = f"{type(e).__name__}: {e}"
                log.warning("private ws feed failed: %s", e)
                self.private_feed = None
        else:
            self.private_feed_error = "no BITUNIX_API_KEY/SECRET - private channels not subscribed"

    async def stop_feed(self) -> None:
        if self.private_feed is not None:
            try:
                await self.private_feed.stop()
            except Exception as e:  # noqa: BLE001
                log.debug("private ws feed stop: %s", e)
            self.private_feed = None
        if self.feed is not None:
            try:
                await self.feed.stop()
            except Exception as e:  # noqa: BLE001
                log.debug("ws feed stop: %s", e)
            self.feed = None

    def private_feed_report(self) -> str:
        """Honest one-liner for the private channels."""
        if self.s.market_data_source != "ws":
            return "private ws: not opened (MARKET_DATA_SOURCE=rest)"
        if self.private_feed is None:
            why = f" - {self.private_feed_error}" if self.private_feed_error else ""
            return f"private ws: NOT connected{why}"
        st = self.private_feed.stats()
        state = "logged in" if st["logged_in"] else (
            "connected, login pending" if st["connected"] else "DISCONNECTED")
        return (f"private ws: {state}, channels={','.join(st['channels'])} "
                f"msgs={sum(st['counts'].values())} tpsl_events={st['tpsl_events']}")

    def _feed_symbols(self) -> list[str]:
        """Every symbol the engine needs a price for."""
        out: list[str] = []
        for s in list(self.state.symbols) + [p.symbol for p in self.positions.values()]:
            u = (s or "").upper()
            if u and u not in out:
                out.append(u)
        return out

    def feed_connected(self) -> bool:
        return bool(self.feed is not None and self.feed.ws.connected)

    def market_data_report(self) -> str:
        """One honest line for the banner and /status."""
        src = self.s.market_data_source
        if src != "ws":
            return "rest (REST polling)"
        if self.feed is None:
            why = f" - {self.feed_error}" if self.feed_error else ""
            return f"ws (NOT connected - falling back to REST{why})"
        if not self.feed.ws.connected:
            return "ws (NOT connected - falling back to REST)"
        return f"ws (connected, {len(self.feed.symbols)} symbols)"

    async def get_price(self, symbol: str) -> float:
        """THE single price accessor. Every price read in the engine goes here.

        ws mode:  fresh cache -> return it. Stale/missing -> REST fallback.
        rest mode: straight to REST, exactly as before.
        """
        sym = (symbol or "").upper()
        if self.s.market_data_source == "ws":
            q = self.price_cache.get_fresh(sym)
            if q is not None:
                return q.price
            age = self.price_cache.age(sym)
            if age is None:
                self.price_cache.note_miss()
            else:
                self.price_cache.note_stale()
                log.warning("stale price for %s: %.1fs old (max %.1fs) - "
                            "refusing it, falling back to REST", sym, age,
                            self.price_cache.max_age)
            self.price_cache.note_rest_fallback()
        return await self._rest_price(sym)

    async def _rest_price(self, symbol: str) -> float:
        self.rest_price_calls += 1
        data = await self.client.get_tickers(symbol)
        if isinstance(data, list) and data:
            d = data[0]
        elif isinstance(data, dict):
            d = data
        else:
            return 0.0
        for k in ("lastPrice", "last", "price", "markPrice"):
            if d.get(k):
                try:
                    px = float(d[k])
                except (TypeError, ValueError):
                    continue
                if self.s.market_data_source == "ws":
                    self.price_cache.put_rest(symbol, px)
                return px
        return 0.0

    async def get_mark_price(self, symbol: str) -> float:
        """The exchange MARK price, used to validate TP/SL placement.

        This is deliberately separate from `get_price()`: the engine trades on
        the last price, but Bitunix validates TP/SL triggers against the mark
        price (errors 30020-30033). Using the last price for that check lets a
        stop pass locally and be rejected by the exchange.
        """
        sym = (symbol or "").upper()
        try:
            data = await self.client.get_tickers(sym)
        except BitunixAPIError as e:
            self.state.errors.append(f"mark_price {sym}: {e}")
            return 0.0
        if isinstance(data, list) and data:
            d = data[0]
        elif isinstance(data, dict):
            d = data
        else:
            return 0.0
        for k in ("markPrice", "mark"):
            if d.get(k):
                try:
                    return float(d[k])
                except (TypeError, ValueError):
                    continue
        return 0.0

    async def get_trigger_price(self, symbol: str, last: float = 0.0) -> float:
        """The price the exchange compares a TP/SL trigger against.

        Controlled by TP_SL_TRIGGER_TYPE:
            MARK_PRICE (default) - the exchange mark price
            LAST_PRICE           - the last traded price

        This is the SINGLE place the choice is made. Both the local pre-flight
        validation and the tpStopType/slStopType sent in the order body read
        this same setting, so they can never disagree - a disagreement is
        exactly what produced 30029 (checked against last, rejected against
        mark).
        """
        if (self.s.tp_sl_trigger_type or "MARK_PRICE").upper() == "LAST_PRICE":
            px = last if last > 0 else await self.get_price(symbol)
            return px
        return await self.get_mark_price(symbol)

    @staticmethod
    def _conservative_mark(last: float, side: str, rules: PairRules) -> float:
        """A mark-price stand-in that is safe for BOTH directions.

        Used only when the real mark price cannot be fetched. The last price can
        be on either side of the mark, so we widen it by the symbol's
        price-protection scope and pick the end that makes the stop harder to
        place: for a LONG the reference is pushed DOWN (the stop must clear a
        lower mark), for a SHORT it is pushed UP. A stop that clears this
        reference clears any mark inside the protection band.
        """
        scope = rules.price_protect_scope if rules.price_protect_scope > 0 else 0.02
        if side.upper() in ("LONG", "BUY"):
            return last * (1 - scope)
        return last * (1 + scope)

    def persist_positions(self) -> None:
        """Mirror the in-memory position-management state to the store.

        Without this, a restart forgets `breakeven_done`, `trailing_stop` and
        `peak_roi`, so the engine would re-arm breakeven and re-tighten a
        trailing stop it had already tightened - acting on a trade it believed
        was fresh. The exchange is still the source of truth for the position
        itself; this is the management state on top of it.
        """
        body: dict[str, dict] = {}
        for pid, st in self.positions.items():
            body[pid] = {
                "position_id": st.position_id, "symbol": st.symbol,
                "side": st.side, "entry": st.entry, "qty": st.qty,
                "leverage": st.leverage, "sl": st.sl, "tp": st.tp,
                "liq": st.liq, "breakeven_done": st.breakeven_done,
                "trailing_active": st.trailing_active,
                "trailing_stop": st.trailing_stop,
                "partial_done": st.partial_done,
                "opened_ts": st.opened_ts, "peak_roi": st.peak_roi,
                # Ladder state. `fired_levels` MUST persist: without it a
                # restart re-fires a level that has already closed its slice,
                # silently doubling the exit. `original_qty` must persist too,
                # because close_pct is measured against it - restoring only the
                # live qty would shrink every remaining level.
                "fired_levels": st.fired_levels,
                "original_qty": st.original_qty,
                "initial_risk": st.initial_risk,
            }
        if body == self.store.section("positions"):
            return
        self.store.set_section("positions", body)

    def _spawn(self, coro, name: str) -> None:
        t = asyncio.create_task(coro, name=name)
        self._tasks.append(t)

    async def engine_on(self) -> str:
        if self.state.engine_on:
            return "Engine already ON."
        self.state.engine_on = True
        self.state.scan_on = True
        await self.start()
        self._spawn(self._scan_loop(), "scanner")
        self._spawn(self._manage_loop(), "manager")
        self._spawn(self._report_loop(), "reporter")
        return "Engine ON (scanner + trader + management)."

    async def engine_off(self) -> str:
        self.state.engine_on = False
        self.state.autotrade = False
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()
        return "Engine OFF (all loops stopped, autotrade disabled)."

    # ================================================================ market data
    async def load_rules(self, symbols: list[str] | None = None) -> None:
        syms = symbols or self.state.symbols
        try:
            data = await self.client.get_trading_pairs(",".join(syms))
            for d in data or []:
                r = PairRules.from_api(d)
                self.rules[r.symbol] = r
        except BitunixAPIError as e:
            self.state.errors.append(f"load_rules: {e}")

    async def load_tiers(self, symbol: str) -> list[Tier]:
        if symbol in self.tiers:
            return self.tiers[symbol]
        try:
            data = await self.client.get_position_tiers(symbol)
            tiers = [Tier.from_api(d) for d in data or []]
            self.tiers[symbol] = tiers
            return tiers
        except BitunixAPIError as e:
            self.state.errors.append(f"load_tiers {symbol}: {e}")
            return []

    def kline_timeframes(self) -> list[str]:
        """Every interval the engine reads candles on.

        Derived from the configured timeframes, not a hardcoded list. The
        trailing-stop ATR reads 5m, so 5m is always included - otherwise that
        one read would poll REST forever while everything else used the cache.
        """
        tfs = list(self.state.timeframes)
        if "5m" not in tfs:
            tfs.append("5m")
        return tfs

    def kline_keys(self) -> list[tuple[str, str]]:
        """Every (symbol, interval) the engine actually needs."""
        out: list[tuple[str, str]] = []
        for sym in self._feed_symbols():
            for tf in self.kline_timeframes():
                if (sym, tf) not in out:
                    out.append((sym, tf))
        return out

    async def warmup_klines(self) -> int:
        """Seed the kline cache from REST once, then let WS keep it current.

        Returns the number of REST calls spent, so the banner can report it.
        """
        if self.s.market_data_source != "ws":
            return 0
        calls = 0
        for sym, tf in self.kline_keys():
            try:
                await self._rest_candles(sym, tf, REST_KLINE_MAX)
                calls += 1
            except BitunixAPIError as e:
                self.state.errors.append(f"kline warmup {sym} {tf}: {e}")
        self.kline_warmup_calls = calls
        return calls

    def kline_subscription_warnings(self) -> list[str]:
        """Intervals a strategy needs that the feed is not subscribed to.

        Said at startup instead of silently polling REST forever.
        """
        if self.s.market_data_source != "ws" or self.feed is None:
            return []
        subscribed = set(self.feed.timeframes)
        return [f"strategy interval {tf} is NOT subscribed on the WS feed "
                f"- it will poll REST"
                for tf in self.kline_timeframes() if tf not in subscribed]

    def kline_cache_report(self) -> str:
        """Kline cache state for /status and /ws."""
        c = self.kline_cache
        st = c.stats
        lines = [
            f"kline_cache=keys={len(c.keys())} hits={st.hits} misses={st.misses} "
            f"stale={st.stale_rejections} short={st.short_series} "
            f"rest_fallbacks={st.rest_fallbacks} ws_updates={st.ws_updates} "
            f"replaced={st.replaced} appended={st.appended} "
            f"rest_kline_calls={self.rest_kline_calls} warmup={self.kline_warmup_calls}"
        ]
        for sym, tf in c.keys():
            age = c.age(sym, tf)
            lines.append(
                f"  {sym} {tf}: {c.count(sym, tf)} candles src={c.source(sym, tf)} "
                f"newest_age={('%.0fs' % age) if age is not None else 'n/a'} "
                f"max={c.max_age(tf):.0f}s")
        return "\n".join(lines)

    async def get_candles(self, symbol: str, tf: str, limit: int = 300):
        """THE single candle accessor. Every candle read in the engine goes here.

        ws mode:  a fresh, long-enough, WS-fed series -> return it, zero REST.
                  stale / short / missing -> REST fallback, counted, warned.
        rest mode: straight to REST, exactly as before.
        """
        sym = (symbol or "").upper()
        want = min(limit, REST_KLINE_MAX)
        if self.s.market_data_source == "ws":
            res = self.kline_cache.try_get(sym, tf, want, min_required=want)
            if res.usable:
                return res.candles
            if res.reason:
                log.warning("kline cache miss for %s %s: %s - falling back to REST",
                            sym, tf, res.reason)
            self.kline_cache.note_rest_fallback()
        return await self._rest_candles(sym, tf, want)

    async def _rest_candles(self, symbol: str, tf: str, limit: int):
        self.rest_kline_calls += 1
        raw = await self.client.get_kline(symbol, tf, limit=limit)
        candles = to_candles(raw or [])
        if self.s.market_data_source == "ws" and candles:
            self.kline_cache.put_rest(symbol, tf, candles)
        return candles

    # ================================================================ scanning
    async def scan_symbol(self, symbol: str) -> Aggregate:
        per_tf: dict[str, list] = {}
        for tf in self.state.timeframes:
            try:
                candles = await self.get_candles(symbol, tf, 300)
            except BitunixAPIError as e:
                self.state.errors.append(f"kline {symbol} {tf}: {e}")
                continue
            if not candles:
                continue
            sigs = []
            for strat in self.strategies:
                try:
                    sig = strat.evaluate(candles, timeframe=tf, symbol=symbol)
                    sigs.append(sig)
                except Exception as e:  # noqa: BLE001
                    self.state.errors.append(f"{strat.name} {symbol} {tf}: {type(e).__name__}: {e}")
            per_tf[tf] = sigs

        agg = aggregate(
            symbol, per_tf,
            min_agreeing=self.state.min_agreeing,
            min_confidence=self.state.min_confidence,
            tf_min_confidence=self.state.tf_min_confidence,
        )
        self.last_aggregates[symbol] = agg
        self.state.scans += 1
        self.state.last_scan_ts = time.time()
        self._log_signal(agg)
        return agg

    def _log_signal(self, agg: Aggregate) -> None:
        rec = {
            "ts": time.time(), "symbol": agg.symbol, "direction": agg.direction,
            "confidence": round(agg.confidence, 2), "agreeing": agg.agreeing,
            "opposing": agg.opposing, "strength": agg.strength, "reason": agg.reason,
            "votes": [{"s": s.strategy, "tf": s.timeframe, "d": s.direction,
                       "c": round(s.confidence, 1)} for s in agg.votes_long + agg.votes_short],
        }
        with self.signals_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    async def _scan_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.state.scan_on:
                    for sym in self.state.symbols:
                        agg = await self.scan_symbol(sym)
                        if self.state.autotrade and agg.actionable:
                            await self.try_enter(agg)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.state.errors.append(f"scan_loop: {type(e).__name__}: {e}")
                log.exception("scan loop error")
            await asyncio.sleep(max(5, self.state.scan_interval))

    # ================================================================ trading
    async def open_positions(self) -> list[dict]:
        try:
            data = await self.client.get_pending_positions()
            return data or []
        except BitunixAPIError as e:
            self.state.errors.append(f"open_positions: {e}")
            return []

    async def try_enter(self, agg: Aggregate) -> dict:
        sym = agg.symbol
        if not agg.actionable:
            return {"ok": False, "reason": "not actionable"}

        positions = await self.open_positions()
        if len(positions) >= self.state.max_open_positions:
            return {"ok": False, "reason": f"max open positions ({self.state.max_open_positions}) reached"}
        if any(p.get("symbol") == sym for p in positions):
            return {"ok": False, "reason": f"position already open on {sym}"}

        if sym not in self.rules:
            await self.load_rules([sym])
        rules = self.rules.get(sym)
        if rules is None:
            return {"ok": False, "reason": f"no trading rules for {sym}"}
        if not rules.api_supported or rules.symbol_status != "OPEN":
            return {"ok": False, "reason": f"{sym} not tradable (status={rules.symbol_status})"}

        price = await self.get_price(sym)
        if price <= 0:
            return {"ok": False, "reason": f"no price for {sym}"}

        # Direction is resolved here because the mark-price fallback below
        # needs it. Using it before assignment raised UnboundLocalError on the
        # no-mark path.
        side = "LONG" if agg.direction == "LONG" else "SHORT"

        # The exchange validates TP/SL against the MARK price, not the last
        # price. Fetch it so the plan is built on the same reference the
        # exchange will use. If it is unavailable we do NOT silently fall back
        # to the last price: the last price can sit on the wrong side of the
        # mark, which is exactly how a stop passes locally and is rejected with
        # 30029. Instead the fallback is widened by the price-protection scope
        # so the stop clears a mark that could be anywhere inside that band.
        mark = await self.get_mark_price(sym)
        if mark <= 0:
            mark = self._conservative_mark(price, side, rules)
            log.warning("no mark price for %s - using a conservative reference "
                        "%s (last price %s widened by the protection scope)",
                        sym, mark, price)

        # account balance: the SPENDABLE amount, not total equity.
        # `available` already excludes the margin locked by open positions and
        # the frozen amount locked by resting orders, so it is what a new order
        # may actually spend. Sizing against total equity (available + margin +
        # unrealized) is what made the engine believe it could afford an order
        # the exchange then refused with 20003 Insufficient balance.
        try:
            acct = await self.client.get_single_account()
            a = acct[0] if isinstance(acct, list) and acct else (acct or {})
            equity = spendable_balance(a)
        except BitunixAPIError as e:
            return {"ok": False, "reason": f"account: {e}"}
        if equity <= 0:
            return {"ok": False, "reason": "no available balance"}

        tiers = await self.load_tiers(sym)

        # --- leverage feasibility against the STOP, not just the balance.
        #
        # A stop must sit strictly between the liquidation price and the
        # mark-side limit. The liquidation price is derived from the ENTRY, and
        # a MARKET order fills at the last price - which can be on the far side
        # of the mark. At 100x with a 0.5% maintenance margin the liquidation
        # price lands ~0.5% below the entry, so when the mark already sits
        # 1.3% below the last price the liquidation price is ABOVE the mark and
        # there is no room for a stop at all. The engine then raised
        # "no valid stop-loss exists" on every scan, forever, for a symbol it
        # could have traded perfectly well at a lower leverage.
        #
        # The worst-case fill is the price that makes the liquidation price
        # closest to the mark: the HIGHER of last/mark for a LONG, the LOWER
        # for a SHORT. Solving the liquidation formula for L gives the highest
        # leverage at which a stop still fits.
        worst_entry = max(price, mark) if side == "LONG" else min(price, mark)

        # --- leverage for THIS order, resolved locally.
        #
        # `self.state.leverage` is the user's setting and is NEVER written here.
        # It used to be mutated in place when a stop did not fit, which meant a
        # single awkward symbol silently rewrote the leverage for every other
        # symbol and every later scan - and, worse, made a margin problem look
        # like it had been "fixed" by lowering leverage. Leverage and margin are
        # independent knobs: margin decides how much of the balance one position
        # locks, leverage only decides how much notional that margin controls.
        # The ONLY reasons to use a different leverage for one order are the two
        # the exchange itself imposes: no room for a stop, or a tier cap.
        leverage = int(self.state.leverage)
        lev_notes: list[str] = []

        lev_tier = tier_for_leverage(tiers, leverage) or pick_tier(tiers, 0.0)
        max_lev_for_stop = max_leverage_for_stop(
            entry=worst_entry, mark=mark, side=side,
            maintenance_margin_rate=lev_tier.maintenance_margin_rate,
            rules=rules, min_ticks=self.s.tpsl_min_stop_ticks)

        if max_lev_for_stop <= 0:
            msg = (f"{sym}: no stop-loss can fit at any leverage - the mark "
                   f"price {mark:.{rules.quote_precision}f} has already moved "
                   f"past the entry {price:.{rules.quote_precision}f} by more "
                   f"than the maintenance margin allows. Wait for the price to "
                   f"come back, or skip this signal.")
            self.state.errors.append(f"preflight {sym}: {msg}")
            return {"ok": False, "reason": msg}

        if leverage > max_lev_for_stop:
            if self.s.leverage_auto_reduce:
                # Clamp to the highest leverage that still leaves room for a
                # stop. This is the difference between "the bot never trades
                # this symbol" and "the bot trades it at a sane leverage".
                log.warning(
                    "%s: leverage %dx leaves no room for a stop-loss between "
                    "the liquidation price and the mark %s (entry %s) - "
                    "using %dx for this order (the setting is unchanged)",
                    sym, leverage, mark, price, max_lev_for_stop)
                lev_notes.append(
                    f"leverage {sym}: {leverage}x leaves no room for a "
                    f"stop-loss (mark {mark:.{rules.quote_precision}f}, entry "
                    f"{price:.{rules.quote_precision}f}); using "
                    f"{max_lev_for_stop}x for this order")
                leverage = max_lev_for_stop
            else:
                msg = (f"leverage {leverage}x leaves no room for a "
                       f"stop-loss on {sym}: the stop must sit between the "
                       f"liquidation price and the mark "
                       f"{mark:.{rules.quote_precision}f} (entry "
                       f"{price:.{rules.quote_precision}f}), and at "
                       f"{leverage}x the liquidation price is already "
                       f"past the mark. The highest leverage that still fits is "
                       f"{max_lev_for_stop}x. Lower the leverage, or set "
                       f"LEVERAGE_AUTO_REDUCE=true to let the engine clamp it.")
                self.state.errors.append(f"preflight {sym}: {msg}")
                return {"ok": False, "reason": msg}

        # The largest notional this balance can actually PAY FOR at the
        # configured leverage - margin PLUS the taker fee, with the safety
        # headroom. This is the true upper bound of the order.
        # (`equity * leverage` ignored the fee and over-stated the bound, which
        # also pushed the tier selection into a higher band than the real order.)
        max_notional = max_notional_for_balance(
            equity, leverage, self.s.taker_fee_rate,
            self.s.margin_safety_pct)

        # Provisional plan: only the stop-loss is needed here, to size the
        # order. It is built at a notional whose tier still permits the
        # configured leverage - at a high leverage the affordable maximum can
        # land in a tier whose maintenance-margin rate makes the liquidation
        # formula degenerate (liq on the wrong side of entry), which would
        # raise TpSlSideError for an order that is actually fine. The tier the
        # REAL order notional lands in is checked after sizing.
        prov_tier = tier_for_leverage(tiers, leverage)
        if prov_tier is None:
            # The exchange caps leverage below the setting at EVERY size. That
            # is the one documented reason to lower it, so lower it to the
            # exchange's own maximum and carry on - refusing here would leave
            # the user with a symbol that can never trade.
            max_lev = max((t.leverage for t in tiers), default=0)
            if max_lev <= 0:
                msg = (f"leverage {leverage}x is above the maximum allowed for "
                       f"{sym} at any position size, and the exchange reported "
                       f"no usable tier. Lower the leverage.")
                self.state.errors.append(f"preflight {sym}: {msg}")
                return {"ok": False, "reason": msg}
            lev_notes.append(
                f"leverage {sym}: {leverage}x is above the exchange maximum "
                f"{max_lev}x for this symbol; using {max_lev}x for this order")
            leverage = max_lev
            prov_tier = tier_for_leverage(tiers, leverage) or pick_tier(tiers, 0.0)

        prov_notional = max_notional
        if prov_tier.end_value != float("inf") and prov_tier.end_value > 0:
            prov_notional = min(max_notional, prov_tier.end_value * 0.9999)

        try:
            plan = plan_tpsl(
                side=side, entry=price, leverage=leverage,
                notional=prov_notional,
                strength=agg.strength, confidence=agg.confidence, tiers=tiers, rules=rules,
                liq_sl_distance_pct=self.state.liq_sl_distance_pct,
                breakeven_pct=self.state.breakeven_pct,
                trailing_trigger_roi_pct=self.state.trailing_trigger_roi_pct,
                mark=mark, min_stop_ticks=self.s.tpsl_min_stop_ticks,
            )
        except TpSlSideError as e:
            self.state.errors.append(f"tpsl {sym}: {e}")
            return {"ok": False, "reason": f"cannot place a valid stop-loss: {e}"}

        # --- sizing.
        #
        # MARGIN_AMOUNT_PCT is the SIZING AUTHORITY when it is set: "use this
        # share of my balance as margin on this position". It used to be passed
        # to position_size_by_risk as an upper bound, and because the risk-based
        # size was almost always the smaller of the two the cap never bound - so
        # the margin actually locked was whatever risk sizing produced (a 10%
        # setting produced ~2% of the balance) and the setting looked like it
        # did nothing. Sizing FROM the percentage is what makes the number the
        # user set the number they see on the position.
        #
        # risk_pct is the fallback for when the knob is 0 (disabled), not a
        # silent override of it. The risk implied at the stop is reported so the
        # user can see what the margin target actually costs them.
        if self.state.margin_amount_pct > 0:
            qty = position_size_by_margin(
                equity, self.state.margin_amount_pct, price, leverage, rules,
                fee_rate=self.s.taker_fee_rate,
                safety_pct=self.s.margin_safety_pct)
            if qty > 0 and plan.sl > 0:
                implied_risk = abs(price - plan.sl) * qty
                lev_notes.append(
                    f"size {sym}: MARGIN_AMOUNT_PCT={self.state.margin_amount_pct:g}% "
                    f"-> qty {qty} (notional {qty * price:.4f} USDT, margin "
                    f"{margin_required(qty * price, leverage):.4f} USDT at "
                    f"{leverage}x); risk at the stop is {implied_risk:.4f} USDT "
                    f"({implied_risk / equity * 100:.2f}% of balance, "
                    f"risk_pct={self.state.risk_pct:g}% is not applied while "
                    f"the margin target is set)")
        else:
            qty = position_size_by_risk(
                equity, self.state.risk_pct, price, plan.sl, leverage, rules,
                fee_rate=self.s.taker_fee_rate,
                safety_pct=self.s.margin_safety_pct)
        if qty <= 0:
            return {"ok": False, "reason": "computed qty is 0 (risk too small or below min volume)"}

        # ORDER_UNIT only changes how the size is EXPRESSED; the wire format is
        # always base-coin qty. QUANTITY (the default) is a no-op, so the
        # historical behaviour is unchanged. NOTIONAL/COST re-derive qty from
        # the risk-sized notional/margin so the two can never disagree.
        order_unit = (self.s.order_unit or "QUANTITY").upper()
        if order_unit != "QUANTITY":
            sized_notional = qty * price
            amount = unit_from_qty(qty, order_unit, price, leverage)
            qty = qty_from_unit(amount, order_unit, price, leverage, rules)
            if qty <= 0:
                return {"ok": False,
                        "reason": f"computed qty is 0 in {order_unit} units "
                                  f"(amount={amount}, notional={sized_notional})"}

        # --- pre-flight: the required margin must fit the real available
        # balance, and the engine's own bounds. Without this the request is
        # sent and the exchange answers 20003 Insufficient balance.
        notional_real = qty * price

        # The tier the REAL order notional lands in caps the leverage. Checked
        # against the actual notional, not `equity * leverage` (which lands in a
        # higher tier and produced a bogus 20003 for an order that was fine).
        #
        # When the tier does cap it, the exchange's own maximum is the answer:
        # lower the leverage to that cap, re-size and re-plan, and say so. The
        # loop is bounded because each pass can only lower the leverage, and a
        # lower leverage at a fixed margin budget means a smaller notional,
        # which lands in a tier that permits at least as much leverage.
        for _ in range(3):
            tier_msg = tier_leverage_violation(tiers, notional_real, leverage)
            if not tier_msg:
                break
            cap = pick_tier(tiers, notional_real).leverage
            if cap <= 0 or cap >= leverage:
                msg = f"{sym}: {tier_msg}"
                self.state.errors.append(f"preflight {sym}: {msg}")
                return {"ok": False, "reason": msg, "hint": "20003 Insufficient balance"}
            lev_notes.append(
                f"leverage {sym}: {leverage}x exceeds the exchange tier cap "
                f"{cap}x for a {notional_real:.2f} USDT position; using {cap}x "
                f"for this order")
            leverage = cap
            if self.state.margin_amount_pct > 0:
                qty = position_size_by_margin(
                    equity, self.state.margin_amount_pct, price, leverage, rules,
                    fee_rate=self.s.taker_fee_rate,
                    safety_pct=self.s.margin_safety_pct)
            else:
                qty = position_size_by_risk(
                    equity, self.state.risk_pct, price, plan.sl, leverage, rules,
                    fee_rate=self.s.taker_fee_rate,
                    safety_pct=self.s.margin_safety_pct)
            if qty <= 0:
                return {"ok": False,
                        "reason": "computed qty is 0 after the exchange tier "
                                  "capped the leverage"}
            notional_real = qty * price
            try:
                plan = plan_tpsl(
                    side=side, entry=price, leverage=leverage,
                    notional=notional_real, strength=agg.strength,
                    confidence=agg.confidence, tiers=tiers, rules=rules,
                    liq_sl_distance_pct=self.state.liq_sl_distance_pct,
                    breakeven_pct=self.state.breakeven_pct,
                    trailing_trigger_roi_pct=self.state.trailing_trigger_roi_pct,
                    mark=mark, min_stop_ticks=self.s.tpsl_min_stop_ticks,
                )
            except TpSlSideError as e:
                self.state.errors.append(f"tpsl {sym}: {e}")
                return {"ok": False, "reason": f"cannot place a valid stop-loss: {e}"}
        else:
            msg = (f"{sym}: the exchange tier cap could not be satisfied at any "
                   f"leverage for a {notional_real:.2f} USDT position.")
            self.state.errors.append(f"preflight {sym}: {msg}")
            return {"ok": False, "reason": msg, "hint": "20003 Insufficient balance"}

        for note in lev_notes:
            self.state.errors.append(note)

        margin_real = margin_required(notional_real, leverage)
        # Opening a MARKET order costs the initial margin AND a taker fee, and
        # the exchange deducts BOTH from the available balance at once. Checking
        # only the margin let `margin == equity` pass locally and then fail on
        # the exchange with 20003 once the fee was added.
        fee_real = fee_for_notional(notional_real, self.s.taker_fee_rate)
        required_real = margin_real + fee_real
        # Reserve the configured headroom so a price move between sizing and the
        # request landing cannot push the cost back over the balance.
        usable_equity = equity * (1.0 - max(0.0, min(0.9, self.s.margin_safety_pct / 100.0)))

        if required_real > usable_equity:
            msg = (f"insufficient balance for {sym}: required {required_real:.4f} USDT "
                   f"(margin {margin_real:.4f} + fee {fee_real:.4f}) vs available "
                   f"{equity:.4f} USDT"
                   + (f" (usable {usable_equity:.4f} after "
                      f"MARGIN_SAFETY_PCT={self.s.margin_safety_pct:g}%)"
                      if self.s.margin_safety_pct else "")
                   + f" for qty={qty}, notional={notional_real:.4f} at "
                     f"{leverage}x. Lower the leverage, lower risk_pct, "
                     f"or add funds.")
            self.state.errors.append(f"preflight {sym}: {msg}")
            return {"ok": False, "reason": msg, "hint": "20003 Insufficient balance"}
        # MARGIN_AMOUNT_PCT: the margin this ONE order locks may not exceed the
        # configured share of the spendable balance. Sizing already targets it,
        # so this only fires when something downstream (an ORDER_UNIT
        # re-derivation, a min-volume floor) pushed the size back up. The
        # correct response is to CLAMP the size to the budget and say so - not
        # to refuse, and never to touch the leverage, which has nothing to do
        # with how much of the balance one position may lock.
        if self.state.margin_amount_pct > 0:
            budget = margin_budget(equity, self.state.margin_amount_pct,
                                   self.s.margin_safety_pct)
            if margin_real > budget:
                max_notional_budget = max_notional_for_margin(
                    budget, leverage, self.s.taker_fee_rate)
                clamped = round_step(max_notional_budget / price, rules.base_precision)
                if clamped < rules.min_trade_volume:
                    msg = (f"margin {margin_real:.4f} USDT for {sym} exceeds the "
                           f"MARGIN_AMOUNT_PCT={self.state.margin_amount_pct:g}% "
                           f"budget of {budget:.4f} USDT, and clamping to the "
                           f"budget leaves {clamped} below the symbol's minimum "
                           f"volume {rules.min_trade_volume}. Raise "
                           f"MARGIN_AMOUNT_PCT or add funds.")
                    self.state.errors.append(f"preflight {sym}: {msg}")
                    return {"ok": False, "reason": msg}
                self.state.errors.append(
                    f"margin {sym}: qty {qty} would lock {margin_real:.4f} USDT, "
                    f"above the MARGIN_AMOUNT_PCT={self.state.margin_amount_pct:g}% "
                    f"budget of {budget:.4f} USDT; clamped to qty {clamped} "
                    f"(notional {clamped * price:.4f} USDT). Leverage is "
                    f"unchanged at {leverage}x.")
                qty = clamped
                notional_real = qty * price
                margin_real = margin_required(notional_real, leverage)
                fee_real = fee_for_notional(notional_real, self.s.taker_fee_rate)
                required_real = margin_real + fee_real
        if self.s.min_margin_usdt and margin_real < self.s.min_margin_usdt:
            msg = (f"required margin {margin_real:.4f} USDT is below the configured "
                   f"MIN_MARGIN_USDT={self.s.min_margin_usdt} for {sym} "
                   f"(qty={qty}, notional={notional_real:.4f}).")
            self.state.errors.append(f"preflight {sym}: {msg}")
            return {"ok": False, "reason": msg}
        if self.s.max_margin_usdt and margin_real > self.s.max_margin_usdt:
            msg = (f"required margin {margin_real:.4f} USDT exceeds the configured "
                   f"MAX_MARGIN_USDT={self.s.max_margin_usdt} for {sym} "
                   f"(qty={qty}, notional={notional_real:.4f}).")
            self.state.errors.append(f"preflight {sym}: {msg}")
            return {"ok": False, "reason": msg}
        if self.s.max_notional_usdt and notional_real > self.s.max_notional_usdt:
            msg = (f"notional {notional_real:.4f} USDT exceeds the configured "
                   f"MAX_NOTIONAL_USDT={self.s.max_notional_usdt} for {sym} "
                   f"(qty={qty}, margin={margin_real:.4f}).")
            self.state.errors.append(f"preflight {sym}: {msg}")
            return {"ok": False, "reason": msg}

        # re-plan with the real notional
        try:
            plan = plan_tpsl(
                side=side, entry=price, leverage=leverage,
                notional=qty * price, strength=agg.strength, confidence=agg.confidence,
                tiers=tiers, rules=rules,
                liq_sl_distance_pct=self.state.liq_sl_distance_pct,
                breakeven_pct=self.state.breakeven_pct,
                trailing_trigger_roi_pct=self.state.trailing_trigger_roi_pct,
                mark=mark, min_stop_ticks=self.s.tpsl_min_stop_ticks,
            )
        except TpSlSideError as e:
            self.state.errors.append(f"tpsl {sym}: {e}")
            return {"ok": False, "reason": f"cannot place a valid stop-loss: {e}"}

        # ensure leverage / margin mode
        try:
            await self.client.change_margin_mode(sym, self.state.margin_mode)
        except BitunixAPIError as e:
            if e.code not in (20006, 20009):
                self.state.errors.append(f"margin_mode {sym}: {e}")
        try:
            await self.client.change_leverage(sym, leverage)
        except BitunixAPIError as e:
            if e.code not in (20006,):
                self.state.errors.append(f"leverage {sym}: {e}")

        order_side = "BUY" if side == "LONG" else "SELL"

        # The trigger type is read ONCE and used for both the local validation
        # below and the tpStopType/slStopType sent on the wire. Reading it twice
        # is how the two can drift apart.
        trigger_type = (self.s.tp_sl_trigger_type or "MARK_PRICE").upper()

        # --- re-read the mark price immediately before sending and re-clamp.
        # The plan above was built from a mark read several round-trips ago
        # (account, tiers, margin mode, leverage). On a fast-moving symbol the
        # mark can cross the stop in that window, and the exchange validates
        # against the mark at the moment the request lands - not the one we
        # planned with. Re-clamping here is what makes the stop safe on the
        # wire rather than only at planning time.
        mark_now = await self.get_trigger_price(sym, last=price)
        if mark_now > 0:
            mark = mark_now
        plan.sl = clamp_sl(side=side, sl=plan.sl, entry=price, mark=mark,
                           rules=rules, min_ticks=self.s.tpsl_min_stop_ticks,
                           liq=plan.liq)
        plan.tp = clamp_tp(side=side, tp=plan.tp, entry=price, mark=mark,
                           rules=rules, min_ticks=self.s.tpsl_min_stop_ticks)
        if not self._tpsl_side_ok(sym, side, price, mark, plan.sl, plan.tp, rules):
            return {"ok": False,
                    "reason": "TP/SL would be rejected by the exchange after the "
                              "mark price moved; order not sent"}

        try:
            body = build_order_body(
                symbol=sym, qty=qty, side=order_side, tradeSide="OPEN",
                orderType="MARKET", reduceOnly=False,
                tpPrice=plan.tp, tpStopType=trigger_type, tpOrderType="MARKET",
                slPrice=plan.sl, slStopType=trigger_type, slOrderType="MARKET",
                rules=rules, known_symbols=set(self.rules) or None,
                mark_price=mark, entry_price=price,
                min_stop_ticks=self.s.tpsl_min_stop_ticks,
            )
        except OrderValidationError as e:
            self.state.errors.append(f"order validation {sym}: {e}")
            return {"ok": False, "reason": f"order validation failed: {e}"}
        try:
            res = await self.client.place_order(**body)
        except BitunixAPIError as e:
            log_order_body(body, e)
            self.state.errors.append(f"place_order {sym}: {e}")
            return {"ok": False, "reason": f"place_order failed: {e}", "hint": e.hint}

        order_id = (res or {}).get("orderId")
        self._log_trade({
            "event": "open", "ts": time.time(), "symbol": sym, "side": side,
            "qty": qty, "entry": price, "order_id": order_id,
            "confidence": round(agg.confidence, 2), "strength": agg.strength,
            "agreeing": agg.agreeing, "plan": plan.as_dict(),
        })
        self._log_tpsl({"event": "placed", "ts": time.time(), "symbol": sym,
                        "order_id": order_id, **plan.as_dict()})

        # register runtime state
        await asyncio.sleep(1.0)
        positions = await self.open_positions()
        for p in positions:
            if p.get("symbol") == sym:
                pid = str(p.get("positionId"))
                self.positions[pid] = PositionState(
                    position_id=pid, symbol=sym, side=side,
                    entry=float(p.get("avgOpenPrice") or price),
                    qty=float(p.get("qty") or qty),
                    leverage=int(p.get("leverage") or self.state.leverage),
                    sl=plan.sl, tp=plan.tp, liq=float(p.get("liqPrice") or plan.liq),
                    opened_ts=time.time(),
                    # The ladder measures every close_pct against THIS number.
                    # Captured once at entry: measuring against the live
                    # (shrinking) qty would compound the exits.
                    original_qty=float(p.get("qty") or qty),
                    fired_levels=0,
                    # The R basis is measured against the INITIAL risk. Once
                    # the stop moves to breakeven, |entry - sl| collapses and
                    # every remaining R level would fire at the entry price.
                    initial_risk=abs(price - plan.sl),
                )
                break
        self.persist_positions()

        return {"ok": True, "order_id": order_id, "symbol": sym, "side": side,
                "qty": qty, "entry": price, "plan": plan.as_dict()}

    async def close_position(self, symbol: str | None = None,
                             position_id: str | None = None) -> dict:
        try:
            if position_id:
                res = await self.client.flash_close_position(position_id)
            else:
                res = await self.client.close_all_position(symbol)
            self._log_trade({"event": "close", "ts": time.time(),
                             "symbol": symbol, "position_id": position_id, "result": res})
            if position_id:
                self.positions.pop(str(position_id), None)
            self.persist_positions()
            return {"ok": True, "result": res}
        except BitunixAPIError as e:
            return {"ok": False, "reason": str(e), "hint": e.hint}

    # ================================================================ management
    async def place_partial_tp_sl(self, sym: str, position_id: str, side: str,
                                  qty: float, tp: float, sl: float,
                                  rules: PairRules) -> dict:
        """PARTIAL TP/SL (method 2 of 4) via the native standalone endpoint.

        The position TP/SL endpoint has NO quantity field - it can only close
        the whole position. The standalone `/tpsl/place_order` endpoint takes
        `tpQty`/`slQty`, which is what makes a staged exit possible: a defined
        portion closes at the trigger and the remainder stays open.

        Official requirements enforced here: `symbol` + `positionId` required,
        at least one of tpPrice/slPrice, and at least one of tpQty/slQty.
        """
        trigger_type = (self.s.tp_sl_trigger_type or "MARK_PRICE").upper()
        body: dict[str, Any] = {
            "symbol": sym, "positionId": str(position_id),
            "tpStopType": trigger_type, "slStopType": trigger_type,
            "tpOrderType": "MARKET", "slOrderType": "MARKET",
        }
        if tp and tp > 0:
            body["tpPrice"] = format_price(tp, rules)
            body["tpQty"] = format_qty(qty, rules)
        if sl and sl > 0:
            body["slPrice"] = format_price(sl, rules)
            body["slQty"] = format_qty(qty, rules)
        if "tpPrice" not in body and "slPrice" not in body:
            raise ValueError(
                f"partial TP/SL for {sym}: no valid tp/sl price to attach")
        return await self.client.place_tp_sl_order(**body)

    # ================================================================ TP ladder
    def ladder(self) -> Ladder:
        """The active ladder, parsed from the persisted spec.

        Parsed on every read rather than cached: the spec is a single string in
        the store, and a stale cached parse is one more thing that can disagree
        with what `/ladder` shows.
        """
        return parse_ladder(
            self.state.tp_ladder or "",
            basis=self.state.tp_ladder_trigger_basis or "R",
            breakeven_after=int(self.state.tp_ladder_breakeven_after or 0),
            runner=self.state.tp_ladder_runner or "trail",
        )

    def _ladder_or_none(self) -> Ladder | None:
        """The active ladder, or None when it is disabled or the spec is bad.

        A bad spec must not stop the manage loop: it is recorded once and the
        engine falls back to the single-level PARTIAL method.
        """
        try:
            lad = self.ladder()
        except LadderError as e:
            msg = f"ladder: invalid spec: {e}"
            if msg not in self.state.errors:
                self.state.errors.append(msg)
            return None
        return lad if lad.levels else None

    def ladder_report(self) -> str:
        """`/ladder` - the spec, the fired levels per position, the remainder."""
        try:
            lad = self.ladder()
        except LadderError as e:
            return (f"TP ladder: INVALID - {e}\n"
                    f"spec={self.state.tp_ladder!r} basis="
                    f"{self.state.tp_ladder_trigger_basis}\n"
                    f"The ladder is disabled until the spec is fixed; the "
                    f"single-level PARTIAL method applies.")
        lines = ["=== TP ladder ===", lad.describe()]
        if not lad.levels:
            lines.append("")
            lines.append("Set one with:  ladder set 1R:25,2R:25,3R:25")
            lines.append("Sub-settings:  ladder breakeven 2 | ladder runner close")
            lines.append("Disable:       ladder off")
            return "\n".join(lines)

        lines.append(f"spec={lad.raw!r} basis={lad.basis} "
                     f"breakeven_after={lad.breakeven_after} runner={lad.runner}")
        lines.append("")
        lines.append("levels:")
        for i, lv in enumerate(lad.levels, start=1):
            lines.append(f"  {i}. trigger {lv.trigger:g} ({lad.basis}) "
                         f"-> close {lv.close_pct:g}% of the ORIGINAL size")
        lines.append(f"  total {lad.total_pct:g}% closed, "
                     f"{lad.runner_pct:g}% left for the runner ({lad.runner})")

        if not self.positions:
            lines.append("")
            lines.append("no tracked positions - fired levels appear here once "
                         "a position is open")
            return "\n".join(lines)

        lines.append("")
        lines.append("open positions:")
        for pid, st in self.positions.items():
            rules = self.rules.get(st.symbol)
            prec = rules.base_precision if rules else 0
            orig = st.original_qty or st.qty
            qtys = level_qtys(orig, lad, prec)
            fired = max(0, min(st.fired_levels, len(lad.levels)))
            rem = remaining_after(orig, lad, fired, prec)
            lines.append(f"  {st.symbol} {st.side} id={pid} "
                         f"original={orig:g} fired={fired}/{len(lad.levels)} "
                         f"remaining={rem:g}")
            for i, lv in enumerate(lad.levels):
                mark = "x" if i < fired else " "
                q = qtys[i] if i < len(qtys) else 0.0
                trig = trigger_price_for(st.side, lv, basis=lad.basis,
                                         entry=st.entry, sl=st.sl,
                                         leverage=st.leverage,
                                         initial_risk=st.initial_risk)
                lines.append(f"    [{mark}] level {i + 1}: close {q:g} at "
                             f"{trig:.{rules.quote_precision if rules else 6}f}")
        return "\n".join(lines)

    async def _ladder_fire(self, sym: str, pid: str, st: PositionState,
                           rules: PairRules, decision, mark: float,
                           trigger_type: str) -> dict | None:
        """Close one ladder level and re-place the remainder's TP/SL.

        Two documented calls per level:
          1. a reduce-only MARKET order for the level's slice (the close), and
          2. `modify_position_tp_sl_order` for the REMAINING quantity, so the
             stop that is left on the exchange matches what is still open.

        The level is only marked fired once the close has been accepted, so a
        rejected order is retried on the next pass instead of being skipped.
        """
        qty = decision.fire_qty
        if qty <= 0:
            return None
        close_side = "SELL" if st.side.upper() in ("LONG", "BUY") else "BUY"
        try:
            body = build_order_body(
                symbol=sym, qty=qty, side=close_side, tradeSide="CLOSE",
                positionId=pid, orderType="MARKET", reduceOnly=True,
                rules=rules, known_symbols=set(self.rules) or None,
            )
            await self.client.place_order(**body)
        except (BitunixAPIError, OrderValidationError, ValueError) as e:
            self.state.errors.append(f"ladder {sym} level "
                                     f"{decision.fire_index + 1}: {e}")
            return None

        st.fired_levels = decision.fire_index + 1
        st.qty = max(0.0, round(st.qty - qty, 12))
        self._log_trade({"event": "ladder_level", "ts": time.time(),
                         "symbol": sym, "position_id": pid,
                         "level": decision.fire_index + 1, "qty": qty,
                         "remaining": st.qty, "price": mark})

        # --- re-place the remainder's TP/SL with the REMAINING qty. The
        # position TP/SL endpoint has no quantity field, so the remainder is
        # expressed by the position itself; the standalone endpoint is used
        # when a quantity must be stated explicitly.
        remaining = st.qty
        if remaining > 0 and rules.min_trade_volume > 0 \
                and remaining < rules.min_trade_volume:
            # Dust: the exchange would reject any attempt to close this later.
            try:
                await self.client.flash_close_position(pid)
                st.qty = 0.0
                self._log_trade({"event": "ladder_dust_close", "ts": time.time(),
                                 "symbol": sym, "position_id": pid,
                                 "qty": remaining})
            except BitunixAPIError as e:
                self.state.errors.append(f"ladder dust close {sym}: {e}")
        elif remaining > 0:
            try:
                await self.client.modify_position_tp_sl_order(
                    sym, pid,
                    tp_price=format_price(st.tp, rules) if st.tp else None,
                    sl_price=format_price(st.sl, rules) if st.sl else None,
                    tp_stop_type=trigger_type, sl_stop_type=trigger_type)
            except BitunixAPIError as e:
                self.state.errors.append(f"ladder re-place {sym}: {e}")

        return {"action": "ladder_level", "symbol": sym,
                "level": decision.fire_index + 1, "qty": qty,
                "remaining": st.qty, "reason": decision.reason}

    async def close_all_positions(self) -> dict:
        """Close every open position - the ACCOUNT TP/SL action.

        Uses the documented `close_all_position` endpoint (market close of all
        positions). Falls back to per-position flash close if the bulk call
        fails, so a single API hiccup cannot leave the account half-flattened.
        """
        try:
            await self.client.close_all_position()
            self.positions.clear()
            self.persist_positions()
            return {"ok": True, "via": "close_all_position"}
        except BitunixAPIError as e:
            log.warning("close_all_position failed (%s) - falling back to "
                        "per-position close", e)
        closed, failed = 0, 0
        for p in await self.open_positions():
            pid = str(p.get("positionId") or "")
            if not pid:
                continue
            try:
                await self.client.flash_close_position(pid)
                closed += 1
            except BitunixAPIError:
                failed += 1
        self.positions.clear()
        self.persist_positions()
        return {"ok": failed == 0, "via": "flash_close_position",
                "closed": closed, "failed": failed}

    def tpsl_method_report(self) -> str:
        """One honest line about which of the four TP/SL methods is active.

        The four are the ones the official academy article defines: Position,
        Partial, Trailing and Account TP/SL. Position and Partial are enforced
        by the exchange; Trailing and Account are software and only run while
        the manage loop is up - which this line says out loud.
        """
        m = (self.state.tpsl_method or "POSITION").upper()
        if m == "PARTIAL":
            return (f"TP/SL method: PARTIAL (native, closes "
                    f"{self.state.partial_close_pct:g}% at "
                    f"{self.state.partial_at_roi_pct:g}% ROI; exchange-enforced)")
        if m == "TRAILING":
            return (f"TP/SL method: TRAILING (software ratchet, arms at "
                    f"{self.state.trailing_trigger_roi_pct:g}% ROI; "
                    f"needs the manage loop running)")
        if m == "ACCOUNT":
            cfg = AccountTpSlConfig(
                tp_usdt=self.state.account_tp_usdt,
                sl_usdt=self.state.account_sl_usdt,
                tp_pct=self.state.account_tp_pct, sl_pct=self.state.account_sl_pct)
            return (f"TP/SL method: ACCOUNT ({cfg.describe()}; software - "
                    f"needs the manage loop running)")
        return ("TP/SL method: POSITION (native, closes the whole position; "
                "exchange-enforced)")

    async def _manage_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.state.mid_management:
                    await self.manage_positions()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.state.errors.append(f"manage_loop: {type(e).__name__}: {e}")
            await asyncio.sleep(max(5, self.state.scan_interval))

    def _tpsl_side_ok(self, sym: str, side: str, entry: float, mark: float,
                      sl: float | None, tp: float | None,
                      rules: PairRules) -> bool:
        """Last gate before a managed TP/SL is sent.

        `clamp_sl`/`clamp_tp` already put the prices on the correct side; this
        re-checks the exact values that will go on the wire and refuses to send
        a request the exchange would reject with 30020-30033. A refusal is
        recorded as an error so it is visible in /status instead of silently
        dropping the update.
        """
        violations = validate_tpsl_side(
            symbol=sym, side=side, entry=entry, mark=mark, sl=sl, tp=tp,
            rules=rules, min_ticks=self.s.tpsl_min_stop_ticks)
        if violations:
            self.state.errors.append(
                "manage TP/SL refused locally: " + "; ".join(violations))
            return False
        return True

    async def manage_positions(self) -> list[dict]:
        actions: list[dict] = []
        positions = await self.open_positions()
        live_ids = {str(p.get("positionId")) for p in positions}

        for pid in list(self.positions):
            if pid not in live_ids:
                self.positions.pop(pid, None)

        # --- ACCOUNT TP/SL (method 4 of 4). The whole futures account is the
        # control unit: when TOTAL unrealized PnL crosses the configured
        # threshold, every position closes. Bitunix exposes no API for this, so
        # it is enforced here in software - which means it only fires while
        # this loop is running. That limitation is stated in /status, not hidden.
        acct_cfg = AccountTpSlConfig(
            tp_usdt=self.state.account_tp_usdt, sl_usdt=self.state.account_sl_usdt,
            tp_pct=self.state.account_tp_pct, sl_pct=self.state.account_sl_pct)
        if acct_cfg.enabled and positions:
            total_pnl = total_unrealized_pnl(positions)
            equity = 0.0
            try:
                acct = await self.client.get_single_account()
                a = acct[0] if isinstance(acct, list) and acct else (acct or {})
                equity = float(a.get("available", 0) or 0)
            except (BitunixAPIError, TypeError, ValueError):
                equity = 0.0
            decision = account_tpsl_decision(total_pnl=total_pnl, equity=equity,
                                             cfg=acct_cfg)
            if decision.should_close_all:
                log.warning("ACCOUNT TP/SL: %s", decision.reason)
                closed = await self.close_all_positions()
                actions.append({"action": f"account_{decision.trigger}",
                                "total_pnl": round(total_pnl, 2),
                                "threshold": decision.threshold,
                                "reason": decision.reason, "result": closed})
                self._log_tpsl({"event": f"account_{decision.trigger}",
                                "ts": time.time(), "total_pnl": round(total_pnl, 2),
                                "threshold": decision.threshold,
                                "reason": decision.reason})
                self.persist_positions()
                return actions

        for p in positions:
            pid = str(p.get("positionId"))
            sym = p.get("symbol", "")
            side = p.get("side", "LONG")
            entry = float(p.get("avgOpenPrice") or 0)
            if entry <= 0:
                continue
            price = await self.get_price(sym)
            if price <= 0:
                continue
            lev = int(p.get("leverage") or self.state.leverage)
            roi = roi_pct(entry, price, side, lev)

            st = self.positions.get(pid)
            if st is None:
                st = PositionState(
                    position_id=pid, symbol=sym, side=side, entry=entry,
                    qty=float(p.get("qty") or 0), leverage=lev,
                    sl=0.0, tp=0.0, liq=float(p.get("liqPrice") or 0),
                    opened_ts=time.time(),
                    original_qty=float(p.get("qty") or 0),
                )
                self.positions[pid] = st
            # Backfill the ladder's reference values for a position that was
            # restored from an older store (or first seen here) without them.
            # Without this, initial_risk stays 0, the R basis falls back to
            # |entry - sl|, and once the stop is at breakeven every remaining
            # level fires at the entry price.
            if st.original_qty <= 0:
                st.original_qty = st.qty
            if st.initial_risk <= 0 and st.sl > 0:
                st.initial_risk = abs(entry - st.sl)
            st.peak_roi = max(st.peak_roi, roi)

            # The exchange validates every TP/SL trigger against the MARK price
            # at the moment the request lands, so the mark must be read fresh
            # here - not reused from entry time, and never assumed to equal the
            # last price. Without this the management path sent stops that were
            # only ever checked against the entry price (30029/30031).
            mark = await self.get_trigger_price(sym, last=price)
            if mark <= 0:
                mark = price
                log.warning("no trigger price for %s - validating the managed "
                            "TP/SL against the last price %s", sym, price)
            trigger_type = (self.s.tp_sl_trigger_type or "MARK_PRICE").upper()
            rules = self.rules.get(sym)
            if rules is None:
                await self.load_rules([sym])
                rules = self.rules.get(sym)
            if rules is None:
                self.state.errors.append(
                    f"manage {sym}: no trading rules, skipping TP/SL update")
                continue

            # 1) breakeven
            #
            # Skipped while a ladder is active: the ladder owns the breakeven
            # move (TP_LADDER_BREAKEVEN_AFTER), and running both would send two
            # identical modify calls on the same pass.
            ladder_cfg = self._ladder_or_none()
            ladder_owns_exits = bool(ladder_cfg and ladder_cfg.levels)
            if not ladder_owns_exits and should_breakeven(st, roi, self.state.breakeven_pct):
                sl = clamp_sl(side=side, sl=entry, entry=entry, mark=mark,
                              rules=rules, min_ticks=self.s.tpsl_min_stop_ticks,
                              liq=st.liq or None)
                if self._tpsl_side_ok(sym, side, entry, mark, sl, st.tp, rules):
                    try:
                        await self.client.modify_position_tp_sl_order(
                            sym, pid, tp_price=format_price(st.tp, rules) if st.tp else None,
                            sl_price=format_price(sl, rules),
                            tp_stop_type=trigger_type, sl_stop_type=trigger_type)
                        st.breakeven_done = True
                        st.sl = sl
                        actions.append({"action": "breakeven", "symbol": sym,
                                        "sl": sl, "roi": round(roi, 2)})
                        self._log_tpsl({"event": "breakeven", "ts": time.time(), "symbol": sym,
                                        "position_id": pid, "sl": sl, "roi": round(roi, 2)})
                    except BitunixAPIError as e:
                        self.state.errors.append(f"breakeven {sym}: {e}")

            # 2) trailing
            if should_trail(st, roi, self.state.trailing_trigger_roi_pct):
                try:
                    candles = await self.get_candles(sym, "5m", 60)
                    a = atr(candles, 14)
                    atr_val = a[-1] if a and a[-1] else price * 0.002
                except Exception:  # noqa: BLE001
                    atr_val = price * 0.002
                new_stop = next_trailing_stop(st, price, atr_val)
                if new_stop is not None:
                    sl = clamp_sl(side=side, sl=new_stop, entry=entry, mark=mark,
                                  rules=rules, min_ticks=self.s.tpsl_min_stop_ticks,
                                  liq=st.liq or None)
                    if self._tpsl_side_ok(sym, side, entry, mark, sl, st.tp, rules):
                        try:
                            await self.client.modify_position_tp_sl_order(
                                sym, pid, tp_price=format_price(st.tp, rules) if st.tp else None,
                                sl_price=format_price(sl, rules),
                                tp_stop_type=trigger_type, sl_stop_type=trigger_type)
                            st.trailing_stop = sl
                            st.trailing_active = True
                            st.sl = sl
                            actions.append({"action": "trail", "symbol": sym,
                                            "new_sl": sl, "roi": round(roi, 2)})
                            self._log_tpsl({"event": "trail", "ts": time.time(), "symbol": sym,
                                            "position_id": pid, "sl": sl,
                                            "roi": round(roi, 2)})
                        except BitunixAPIError as e:
                            self.state.errors.append(f"trail {sym}: {e}")

            # 2b) TP LADDER - multi-level partial take-profit.
            #
            # Runs BEFORE mid-management so a ladder level and the single-level
            # partial can never both fire on the same pass (that would close
            # two slices for one trigger). When a ladder is configured it OWNS
            # the staged exits and the partial_close branch below is skipped.
            ladder_active = False
            lad = self._ladder_or_none()
            if lad is not None:
                ladder_active = True
                orig = st.original_qty or st.qty
                decision = ladder_decide(
                    ladder=lad, side=side, entry=entry, sl=st.sl,
                    leverage=st.leverage, price=price, original_qty=orig,
                    fired=st.fired_levels, base_precision=rules.base_precision,
                    min_trade_volume=rules.min_trade_volume,
                    breakeven_done=st.breakeven_done,
                    initial_risk=st.initial_risk)
                if decision.fire_index is not None:
                    res = await self._ladder_fire(sym, pid, st, rules, decision,
                                                  mark, trigger_type)
                    if res is not None:
                        actions.append(res)
                        self._log_tpsl({"event": "ladder_level", "ts": time.time(),
                                        "symbol": sym, "position_id": pid,
                                        "level": decision.fire_index + 1,
                                        "qty": decision.fire_qty,
                                        "remaining": st.qty,
                                        "reason": decision.reason})
                # breakeven after the configured level
                if decision.move_breakeven and not st.breakeven_done:
                    sl = clamp_sl(side=side, sl=entry, entry=entry, mark=mark,
                                  rules=rules,
                                  min_ticks=self.s.tpsl_min_stop_ticks,
                                  liq=st.liq or None)
                    # The trailing branch above may already have moved the stop
                    # to this exact price on this pass. Sending the same modify
                    # twice is a wasted round-trip and a second chance to be
                    # rejected, so skip it and just record the state.
                    if abs(sl - st.sl) <= (tick_size(rules) if rules else 0) / 2:
                        st.breakeven_done = True
                        st.sl = sl
                    elif self._tpsl_side_ok(sym, side, entry, mark, sl, st.tp, rules):
                        try:
                            await self.client.modify_position_tp_sl_order(
                                sym, pid,
                                tp_price=format_price(st.tp, rules) if st.tp else None,
                                sl_price=format_price(sl, rules),
                                tp_stop_type=trigger_type,
                                sl_stop_type=trigger_type)
                            st.breakeven_done = True
                            st.sl = sl
                            actions.append({"action": "ladder_breakeven",
                                            "symbol": sym, "sl": sl,
                                            "after_level": decision.fire_index + 1})
                            self._log_tpsl({"event": "ladder_breakeven",
                                            "ts": time.time(), "symbol": sym,
                                            "position_id": pid, "sl": sl})
                        except BitunixAPIError as e:
                            self.state.errors.append(f"ladder breakeven {sym}: {e}")
                # runner handling once the ladder is exhausted
                if decision.runner_close and st.qty > 0:
                    res = await self.close_position(symbol=sym, position_id=pid)
                    actions.append({"action": "ladder_runner_close", "symbol": sym,
                                    "result": res})
                elif decision.runner_trail:
                    # Hand the remainder to the trailing stop. `trailing_active`
                    # is what the trailing branch above keys off, so arming it
                    # here is enough - the next pass ratchets the stop.
                    st.trailing_active = True

            # 3) mid-management
            agg = self.last_aggregates.get(sym)
            reversal = bool(agg and agg.actionable and agg.direction != side)
            rev_conf = agg.confidence if agg else 0.0
            action = mid_management_action(
                st, roi, reversal and self.state.reversal, rev_conf,
                self.state.reversal_confidence,
                self.state.partial_at_roi_pct)
            if action == "close_reversal":
                res = await self.close_position(symbol=sym, position_id=pid)
                actions.append({"action": "close_reversal", "symbol": sym,
                                "roi": round(roi, 2), "result": res})
            elif action == "partial_close" and not st.partial_done and not ladder_active:
                # PARTIAL TP/SL (method 2 of 4): close a DEFINED % of the
                # position, leaving the rest open. When the engine is set to the
                # PARTIAL method this goes through the native standalone TP/SL
                # endpoint, which takes tpQty/slQty - the position endpoint has
                # no quantity field and can only ever close the whole position.
                pct = max(1.0, min(99.0, self.state.partial_close_pct))
                part = round_step(st.qty * pct / 100.0,
                                  self.rules.get(sym, PairRules(sym)).base_precision)
                if part > 0:
                    try:
                        if self.state.tpsl_method == "PARTIAL":
                            res = await self.place_partial_tp_sl(
                                sym, pid, side, part, st.tp, st.sl, rules)
                            actions.append({"action": "partial_tpsl", "symbol": sym,
                                            "qty": part, "pct": pct,
                                            "roi": round(roi, 2), "result": res})
                            self._log_tpsl({"event": "partial_tpsl", "ts": time.time(),
                                            "symbol": sym, "position_id": pid,
                                            "qty": part, "pct": pct,
                                            "roi": round(roi, 2)})
                        else:
                            close_body = build_order_body(
                                symbol=sym, qty=part,
                                side="SELL" if side.upper() == "LONG" else "BUY",
                                tradeSide="CLOSE", positionId=pid,
                                orderType="MARKET", reduceOnly=True,
                                rules=self.rules.get(sym),
                                known_symbols=set(self.rules) or None,
                            )
                            await self.client.place_order(**close_body)
                            actions.append({"action": "partial_close", "symbol": sym,
                                            "qty": part, "roi": round(roi, 2)})
                            self._log_trade({"event": "partial_close", "ts": time.time(),
                                             "symbol": sym, "qty": part,
                                             "roi": round(roi, 2)})
                        st.partial_done = True
                    except (BitunixAPIError, OrderValidationError, ValueError) as e:
                        self.state.errors.append(f"partial {sym}: {e}")
        # Management state (breakeven_done / trailing_stop / peak_roi) must hit
        # the disk even if the process is killed a moment later.
        self.persist_positions()
        return actions

    # ================================================================ reporting
    async def _report_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.state.report_on:
                    rep = await self.report()
                    print("\n" + rep + "\n", flush=True)
                    self.state.last_report_ts = time.time()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.state.errors.append(f"report_loop: {e}")
            await asyncio.sleep(max(5, self.state.report_interval))

    async def report(self) -> str:
        lines = [f"=== J+Rock report {time.strftime('%H:%M:%S')} ==="]
        for sym in self.state.symbols:
            try:
                price = await self.get_price(sym)
            except Exception:  # noqa: BLE001
                price = 0.0
            agg = self.last_aggregates.get(sym)
            sig = (f"{agg.direction} {agg.confidence:.1f}% ({agg.agreeing} agree)"
                   if agg else "no scan yet")
            lines.append(f"{sym}: price {price} | signal {sig}")
        positions = await self.open_positions()
        if positions:
            lines.append("-- open positions --")
            for p in positions:
                sym = p.get("symbol")
                entry = float(p.get("avgOpenPrice") or 0)
                price = await self.get_price(sym)
                roi = roi_pct(entry, price, p.get("side", "LONG"),
                              int(p.get("leverage") or 1))
                lines.append(
                    f"  {sym} {p.get('side')} qty={p.get('qty')} entry={entry} "
                    f"px={price} roi={roi:.2f}% upnl={p.get('unrealizedPNL')} "
                    f"liq={p.get('liqPrice')}")
        else:
            lines.append("no open positions")
        if self.state.errors:
            lines.append(f"errors: {len(self.state.errors)} (last: {self.state.errors[-1]})")
        return "\n".join(lines)

    async def pnl(self) -> dict:
        positions = await self.open_positions()
        total = 0.0
        rows = []
        for p in positions:
            try:
                u = float(p.get("unrealizedPNL") or 0)
            except (TypeError, ValueError):
                u = 0.0
            total += u
            rows.append({"symbol": p.get("symbol"), "side": p.get("side"),
                         "unrealized": u, "realized": p.get("realizedPNL")})
        return {"unrealized_total": round(total, 4), "positions": rows}

    # ================================================================ history
    def _log_trade(self, rec: dict) -> None:
        with self.trades_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def _log_tpsl(self, rec: dict) -> None:
        with self.tpsl_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def _read_jsonl(self, path: Path, n: int = 50) -> list[dict]:
        if not path.exists():
            return []
        out = []
        for ln in path.read_text(encoding="utf-8").splitlines()[-n:]:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out

    def trades_history(self, n: int = 20) -> list[dict]:
        return self._read_jsonl(self.trades_path, n)

    def tpsl_history(self, n: int = 20) -> list[dict]:
        return self._read_jsonl(self.tpsl_path, n)

    async def positions_history(self, n: int = 20) -> list[dict]:
        try:
            data = await self.client.get_history_positions(skip=0, limit=n)
            return data or []
        except BitunixAPIError as e:
            self.state.errors.append(f"positions_history: {e}")
            return []

    # ================================================================ settings
    def store_line(self) -> str:
        """One honest line about persistence for `/status`."""
        sm = self.store.summary()
        last = (time.strftime("%H:%M:%S", time.localtime(sm["last_save"]))
                if sm["last_save"] else "never")
        extra = ""
        if sm["migrated_from"]:
            extra += f" migrated_from=v{sm['migrated_from']}"
        if sm["recovered_backup"]:
            extra += f" CORRUPT_BACKUP={Path(sm['recovered_backup']).name}"
        return (f"store: {sm['path']} schema=v{sm['version']} "
                f"settings={sm['settings'] + sm['engine_settings']} "
                f"positions={sm['positions']} "
                f"size={sm['size_bytes']}B last_save={last}{extra}")

    def settings_report(self) -> str:
        s = self.state
        return "\n".join([
            "=== engine settings ===",
            f"engine_on={s.engine_on} scan_on={s.scan_on} autotrade={s.autotrade} "
            f"report_on={s.report_on} mid_management={s.mid_management}",
            f"symbols={','.join(s.symbols)} leverage={s.leverage}x "
            f"margin={s.margin_mode} position={s.position_mode} max_open={s.max_open_positions}",
            f"risk_pct={s.risk_pct}% margin_amount_pct={s.margin_amount_pct:g}% "
            f"breakeven_pct={s.breakeven_pct}% "
            f"trailing_trigger_roi={s.trailing_trigger_roi_pct}% "
            f"liq_sl_distance={s.liq_sl_distance_pct}%",
            f"min_agreeing={s.min_agreeing} timeframes={','.join(s.timeframes)} "
            f"min_conf={s.min_confidence} tf_min_conf={s.tf_min_confidence}",
            f"scan_interval={s.scan_interval}s report_interval={s.report_interval}s "
            f"reversal={s.reversal} reversal_conf={s.reversal_confidence}",
            f"tpsl_min_stop_ticks={self.s.tpsl_min_stop_ticks} "
            f"({self.s.tpsl_min_stop_ticks} x tick = "
            f"{self.s.tpsl_min_stop_ticks * 10.0 ** -6:.6f} on a 6-dp symbol) "
            f"taker_fee={self.s.taker_fee_rate * 100:g}% "
            f"margin_safety={self.s.margin_safety_pct:g}%",
            f"scans={s.scans} errors={len(s.errors)}",
        ])

    # ================================================================ command router
    # Prefixes that unambiguously start an engine command. Used by the CLI to
    # decide whether a bare line is engine syntax or free text for the model.
    COMMAND_PREFIXES: tuple[str, ...] = (
        "start trading", "stop trading", "autotrade", "engine on", "engine off",
        "status", "report ", "trades_history", "trades history", "tpsl_history",
        "tpsl history", "pnl", "p&l", "settings", "scan on", "scan off",
        "mid management", "reversal", "scans interval", "breakeven threshold",
        "trailing trigger", "liq sl distance", "min agreeing", "timeframes",
        "min confidence", "tf min confidence", "positions history", "close position",
        "margin mode", "position mode", "symbols", "symbol ", "max open positions",
        "margin amount", "margin add", "margin reduce",
        "leverage", "risk ",
        # the four documented TP/SL methods
        "tpsl method", "partial close pct", "partial at roi",
        "account tp usdt", "account sl usdt", "account tp pct", "account sl pct",
        # the TP ladder
        "ladder",
    )

    def looks_like_command(self, line: str) -> bool:
        """True only if the line matches engine command grammar.

        Deliberately strict: a natural-language sentence must NOT be swallowed
        by the engine and answered with "Unknown engine command".

        The old check was `any(low.startswith(p) for p in COMMAND_PREFIXES)`,
        which is greedy: "leverage is too high, what should I do?" starts with
        "leverage" and was therefore treated as the `leverage` setter, so a
        normal question got "Unknown engine command" instead of an answer. A
        prefix only counts as a command when the REST of the line is the
        argument shape that command actually accepts.
        """
        low = line.strip().lower()
        if not low:
            return False
        if low in ("status", "pnl", "p&l", "trades_history", "tpsl_history",
                   "trades history", "tpsl history", "engine on", "engine off",
                   "start trading", "stop trading", "scan on", "scan off",
                   "report on", "report off", "settings", "settings trade",
                   "ladder"):
            return True
        for p in self.COMMAND_PREFIXES:
            if not low.startswith(p):
                continue
            rest = low[len(p):].strip()
            if self._is_command_argument(p, rest):
                return True
        return False

    #: Commands whose argument is a bare number (optionally with a unit word).
    _NUMERIC_ARG_COMMANDS: tuple[str, ...] = (
        "leverage", "risk", "max open positions", "scans interval",
        "report interval", "breakeven threshold", "trailing trigger",
        "liq sl distance", "min agreeing", "min confidence",
        "tf min confidence", "partial close pct", "partial at roi",
        "account tp usdt", "account sl usdt", "account tp pct", "account sl pct",
        "margin amount",
    )

    def _is_command_argument(self, prefix: str, rest: str) -> bool:
        """Whether `rest` is a valid argument for the command `prefix`.

        This is what separates "leverage 20" (a command) from "leverage is too
        high" (a sentence). A command with no argument is only valid for the
        prefixes that are complete on their own.
        """
        p = prefix.strip()
        if not rest:
            # Bare prefixes that are complete commands on their own.
            return p in ("status", "pnl", "p&l", "settings", "ladder",
                         "margin amount",
                         "trades_history", "tpsl_history", "trades history",
                         "tpsl history", "symbols", "positions history",
                         "engine on", "engine off", "scan on", "scan off",
                         "report on", "report off", "start trading",
                         "stop trading", "autotrade")
        # A numeric command takes a number, optionally preceded by a unit word
        # ("leverage to 20", "risk pct 1.5", "min confidence 70").
        if p in self._NUMERIC_ARG_COMMANDS:
            return bool(re.search(r"-?\d+(?:\.\d+)?", rest))
        # Symbol lists: "symbols BTCUSDT,ETHUSDT" / "symbol BTCUSDT".
        if p in ("symbols", "symbol"):
            return bool(re.search(r"[A-Za-z]{2,10}USDT", rest, re.I))
        # Mode setters take a known keyword.
        if p == "margin mode":
            return rest.replace(":", "").strip().upper() in ("ISOLATION", "CROSS")
        if p == "position mode":
            return rest.replace(":", "").strip().upper().replace(" ", "_") in (
                "HEDGE", "ONE_WAY")
        if p == "tpsl method":
            return rest.split()[-1].upper() in ("POSITION", "PARTIAL",
                                                "TRAILING", "ACCOUNT")
        if p == "close position":
            return bool(re.search(r"[A-Za-z]{2,10}USDT", rest, re.I))
        if p in ("margin add", "margin reduce"):
            # "margin add BTCUSDT 100" - a symbol AND an amount are required.
            return bool(re.search(r"[A-Za-z]{2,10}USDT", rest, re.I)
                        and re.search(r"-?\d+(?:\.\d+)?", rest))
        if p == "ladder":
            return rest.split()[0].lower() in ("set", "off", "none", "disable",
                                               "clear", "breakeven", "runner",
                                               "basis")
        if p == "reversal":
            return rest.strip("= ").lower() in ("true", "false", "1", "0",
                                                "on", "off", "yes", "no")
        if p == "mid management":
            return True
        if p == "timeframes":
            return bool(re.search(r"\d+[mhd]", rest, re.I))
        if p == "autotrade":
            return rest.strip().lower() in ("on", "off", "enable", "disable",
                                            "enabled", "disabled", "start", "stop")
        if p == "report":
            return rest.split()[0].lower() in ("on", "off", "interval")
        return False

    async def command(self, line: str) -> str:
        """Route an engine command, persisting any state it changed.

        The persistence wrapper is here rather than in each of the ~25 branches
        so that a future branch cannot forget it.
        """
        try:
            return await self._command(line)
        finally:
            self._persist_if_changed()

    async def _command(self, line: str) -> str:
        """Route an engine command. Returns a human-readable result."""
        raw = line.strip()
        if not raw:
            return ""
        low = raw.lower()
        s = self.state

        def arg(prefix: str) -> str:
            return raw[len(prefix):].strip()

        # --- trading control
        if low in ("start trading", "autotrade on"):
            s.autotrade = True
            if not s.engine_on:
                await self.engine_on()
            return "Autotrade ON - signals will be executed."
        if low in ("stop trading", "autotrade off"):
            s.autotrade = False
            return "Autotrade OFF - scanning continues, no new orders."
        if low == "engine off":
            return await self.engine_off()
        if low in ("engine on", "engine on (scanner, trader, autotrade)"):
            return await self.engine_on()
        if low == "status":
            return await self.status()
        if low == "report on":
            s.report_on = True
            return f"Report ON every {s.report_interval}s."
        if low == "report off":
            s.report_on = False
            return "Report OFF."
        if low.startswith("report interval"):
            try:
                s.report_interval = int(arg("report interval").split()[-1])
                return f"Report interval = {s.report_interval}s."
            except (ValueError, IndexError):
                return "Usage: report interval sec 30"
        if low in ("trades_history", "trades history"):
            return json.dumps(self.trades_history(), indent=2)
        if low in ("tpsl_history", "tpsl history"):
            return json.dumps(self.tpsl_history(), indent=2)
        if low in ("pnl", "p&l"):
            return json.dumps(await self.pnl(), indent=2)
        if low.startswith("settings trade") or low == "settings":
            return self.settings_report()
        if low == "scan on":
            s.scan_on = True
            return "Scanner ON."
        if low == "scan off":
            s.scan_on = False
            return "Scanner OFF."
        if low.startswith("mid management"):
            s.mid_management = not s.mid_management
            return f"Mid management = {s.mid_management}."
        if low.startswith("reversal_confidence"):
            try:
                s.reversal_confidence = float(arg("reversal_confidence").strip("= "))
                return f"Reversal confidence = {s.reversal_confidence}."
            except ValueError:
                return "Usage: Reversal_confidence=70"
        if low.startswith("reversal"):
            val = arg("reversal").strip("= ").lower()
            s.reversal = val in ("true", "1", "on", "yes")
            return f"Reversal = {s.reversal}."
        if low.startswith("scans interval"):
            try:
                s.scan_interval = int(arg("scans interval").split()[-1])
                return f"Scan interval = {s.scan_interval}s."
            except (ValueError, IndexError):
                return "Usage: Scans interval sec 30"
        if low.startswith("breakeven threshold"):
            try:
                s.breakeven_pct = float(arg("breakeven threshold").split()[-1])
                return f"Breakeven threshold = {s.breakeven_pct}% ROI."
            except (ValueError, IndexError):
                return "Usage: Breakeven threshold pct 0.6"
        if low.startswith("trailing trigger"):
            try:
                s.trailing_trigger_roi_pct = float(arg("trailing trigger").split()[-1])
                return f"Trailing trigger = {s.trailing_trigger_roi_pct}% ROI."
            except (ValueError, IndexError):
                return "Usage: Trailing trigger stop ROI pct 25"
        if low.startswith("liq sl distance"):
            try:
                s.liq_sl_distance_pct = float(arg("liq sl distance").split()[-1])
                return f"Liq SL distance = {s.liq_sl_distance_pct}%."
            except (ValueError, IndexError):
                return "Usage: Liq sl distance pct 50"
        if low.startswith("min agreeing"):
            try:
                s.min_agreeing = int(arg("min agreeing").split()[-1])
                return f"Min agreeing strategies = {s.min_agreeing}."
            except (ValueError, IndexError):
                return "Usage: Min agreeing strategies 3"
        if low.startswith("timeframes"):
            tfs = [t.strip() for t in arg("timeframes").replace("=", " ").split(",") if t.strip()]
            if tfs:
                s.timeframes = tfs
                return f"Timeframes = {','.join(tfs)}"
            return "Usage: Timeframes 1m,3m,5m,15m"
        if low.startswith("min confidence"):
            try:
                s.min_confidence = float(arg("min confidence").split()[-1])
                return f"Min confidence = {s.min_confidence}."
            except (ValueError, IndexError):
                return "Usage: Min Confidence 70"
        if low.startswith("tf min confidence"):
            try:
                s.tf_min_confidence = float(arg("tf min confidence").split()[-1])
                return f"TF min confidence = {s.tf_min_confidence}."
            except (ValueError, IndexError):
                return "Usage: Tf min confidence 60"
        if low.startswith("positions history"):
            return json.dumps(await self.positions_history(), indent=2)
        if low.startswith("close position"):
            sym = arg("close position").strip() or None
            return json.dumps(await self.close_position(symbol=sym), indent=2)
        if low.startswith("margin add") or low.startswith("margin reduce"):
            return await self._margin_adjust_command(raw)
        if low.startswith("margin amount"):
            rest = arg("margin amount").strip()
            if not rest:
                # Bare "margin amount" is a READ, not a usage error: it is the
                # natural way to ask what the setting currently is.
                return (f"Margin per trade = {s.margin_amount_pct:g}% of the "
                        f"available balance (0 disables the budget).\n"
                        f"Change it with: margin amount 10")
            try:
                s.margin_amount_pct = float(rest.split()[-1])
                return (f"Margin per trade = {s.margin_amount_pct:g}% of the "
                        f"available balance (0 disables the budget).")
            except (ValueError, IndexError):
                return "Usage: margin amount 10"
        if low.startswith("margin mode"):
            val = arg("margin mode").replace(":", "").strip().upper()
            if val in ("ISOLATION", "CROSS"):
                s.margin_mode = val
                return f"Margin mode = {val} (applied on next entry)."
            return "Usage: Margin mode ISOLATION|CROSS"
        if low.startswith("position mode"):
            val = arg("position mode").replace(":", "").strip().upper().replace(" ", "_")
            if val in ("HEDGE", "ONE_WAY"):
                s.position_mode = val
                try:
                    await self.client.change_position_mode(val)
                except BitunixAPIError as e:
                    return f"Position mode set locally to {val}, exchange said: {e}"
                return f"Position mode = {val}."
            return "Usage: Position mode HEDGE|ONE_WAY"
        if low.startswith("symbols") or low.startswith("symbol "):
            val = arg("symbols") if low.startswith("symbols") else arg("symbol")
            syms = [x.strip().upper() for x in val.replace("=", " ").split(",") if x.strip()]
            if syms:
                s.symbols = syms
                await self.load_rules(syms)
                return f"Symbols = {','.join(syms)}"
            return "Usage: Symbols BTCUSDT,ETHUSDT"
        if low.startswith("max open positions"):
            try:
                s.max_open_positions = int(arg("max open positions").split()[-1])
                return f"Max open positions = {s.max_open_positions}."
            except (ValueError, IndexError):
                return "Usage: Max open positions 3"
        if low.startswith("leverage"):
            try:
                s.leverage = int(arg("leverage").split()[-1])
                return f"Leverage = {s.leverage}x (applied on next entry)."
            except (ValueError, IndexError):
                return "Usage: leverage 10"
        # ------------------------------------------------ the 4 TP/SL methods
        if low.startswith("tpsl method"):
            try:
                m = arg("tpsl method").split()[-1].upper()
            except IndexError:
                return "Usage: tpsl method POSITION|PARTIAL|TRAILING|ACCOUNT"
            if m not in ("POSITION", "PARTIAL", "TRAILING", "ACCOUNT"):
                return "Method must be POSITION | PARTIAL | TRAILING | ACCOUNT"
            s.tpsl_method = m
            return self.tpsl_method_report()
        if low.startswith("partial close pct"):
            try:
                s.partial_close_pct = max(1.0, min(99.0,
                                                   float(arg("partial close pct").split()[-1])))
                return f"Partial close = {s.partial_close_pct:g}% of the position."
            except (ValueError, IndexError):
                return "Usage: partial close pct 50"
        if low.startswith("partial at roi"):
            try:
                s.partial_at_roi_pct = float(arg("partial at roi").split()[-1])
                return f"Partial close fires at {s.partial_at_roi_pct:g}% ROI."
            except (ValueError, IndexError):
                return "Usage: partial at roi 50"
        if low.startswith("account tp usdt"):
            try:
                s.account_tp_usdt = float(arg("account tp usdt").split()[-1])
                return f"Account TP = +{s.account_tp_usdt:g} USDT (0 disables)."
            except (ValueError, IndexError):
                return "Usage: account tp usdt 100"
        if low.startswith("account sl usdt"):
            try:
                s.account_sl_usdt = float(arg("account sl usdt").split()[-1])
                return f"Account SL = -{s.account_sl_usdt:g} USDT (0 disables)."
            except (ValueError, IndexError):
                return "Usage: account sl usdt 50"
        if low.startswith("account tp pct"):
            try:
                s.account_tp_pct = float(arg("account tp pct").split()[-1])
                return f"Account TP = +{s.account_tp_pct:g}% of equity (0 disables)."
            except (ValueError, IndexError):
                return "Usage: account tp pct 5"
        if low.startswith("account sl pct"):
            try:
                s.account_sl_pct = float(arg("account sl pct").split()[-1])
                return f"Account SL = -{s.account_sl_pct:g}% of equity (0 disables)."
            except (ValueError, IndexError):
                return "Usage: account sl pct 3"
        # ------------------------------------------------------- the TP ladder
        if low == "ladder" or low.startswith("ladder "):
            return self._ladder_command(raw)
        if low.startswith("risk"):
            try:
                s.risk_pct = float(arg("risk").split()[-1])
                return f"Risk per trade = {s.risk_pct}% of equity."
            except (ValueError, IndexError):
                return "Usage: risk 1.0"

        return f"Unknown engine command: {raw!r}. Type 'help' for the list."

    async def _margin_adjust_command(self, raw: str) -> str:
        """`margin add SYMBOL AMOUNT` / `margin reduce SYMBOL AMOUNT`.

        Documented in HELP and implemented in the client
        (`adjust_position_margin`) but never wired into the router, so the
        documented command answered "Unknown engine command". Isolated margin
        only: the endpoint is meaningless for a CROSS position, and the
        exchange rejects it, so it is refused locally with a clear reason
        instead of a raw API error.

        The position is looked up first so the call carries the real
        `positionId` (and side) rather than guessing - the endpoint requires
        one of them.
        """
        low = raw.strip().lower()
        reduce_ = low.startswith("margin reduce")
        rest = raw.strip()[len("margin reduce" if reduce_ else "margin add"):].strip()
        parts = rest.replace(",", " ").split()
        sym = next((p.upper() for p in parts if re.fullmatch(r"[A-Za-z]{2,10}USDT", p)), "")
        amount = next((p for p in parts if re.fullmatch(r"-?\d+(?:\.\d+)?", p)), "")
        if not sym or not amount:
            return ("Usage: margin add BTCUSDT 100 | margin reduce BTCUSDT 50\n"
                    "(isolated margin only; the amount is in USDT)")
        try:
            amt = abs(float(amount))
        except ValueError:
            return "Usage: margin add BTCUSDT 100 | margin reduce BTCUSDT 50"
        if amt <= 0:
            return "The amount must be greater than 0."

        if (self.state.margin_mode or "").upper() != "ISOLATION":
            return (f"Margin can only be adjusted on an ISOLATED position; "
                    f"margin mode is {self.state.margin_mode}. "
                    f"Set it with: margin mode ISOLATION")

        positions = await self.open_positions()
        pos = next((p for p in positions
                    if str(p.get("symbol", "")).upper() == sym), None)
        if pos is None:
            return f"No open {sym} position to adjust margin on."

        pid = str(pos.get("positionId") or "")
        side = str(pos.get("side") or "").upper() or None
        signed = -amt if reduce_ else amt
        try:
            await self.client.adjust_position_margin(
                symbol=sym, amount=str(signed), position_id=pid or None, side=side)
        except BitunixAPIError as e:
            self.state.errors.append(f"margin adjust {sym}: {e}")
            return f"Bitunix error {e.code}: {e.msg or e}"
        except ValueError as e:
            return f"Cannot adjust margin: {e}"
        verb = "Reduced" if reduce_ else "Added"
        return (f"{verb} {amt:g} USDT {'from' if reduce_ else 'to'} the {sym} "
                f"{side or ''} position (isolated margin).").replace("  ", " ")

    def _ladder_command(self, raw: str) -> str:
        """`ladder ...` - show, set, disable, and the two sub-settings.

        Every setter validates BEFORE writing, so a bad spec leaves the previous
        ladder in place rather than disabling staged exits by accident.
        """
        s = self.state
        rest = raw.strip()[len("ladder"):].strip()
        low = rest.lower()

        if not rest:
            return self.ladder_report()

        if low in ("off", "none", "disable", "clear"):
            s.tp_ladder = ""
            return ("TP ladder OFF - the single-level PARTIAL method applies.\n"
                    + self.ladder_report())

        if low.startswith("breakeven"):
            val = rest[len("breakeven"):].strip()
            if not val:
                return (f"Breakeven after level = {s.tp_ladder_breakeven_after} "
                        f"(0 disables).\nUsage: ladder breakeven 2")
            try:
                n = int(val.split()[-1])
            except (ValueError, IndexError):
                return "Usage: ladder breakeven 2"
            if n < 0:
                return "Breakeven level must be >= 0 (0 disables the move)."
            try:
                lad = self.ladder()
            except LadderError as e:
                return f"Cannot set breakeven: the current ladder is invalid ({e})"
            if lad.levels and n > len(lad.levels):
                return (f"Breakeven level {n} is outside 0..{len(lad.levels)} "
                        f"(the ladder has {len(lad.levels)} levels).")
            s.tp_ladder_breakeven_after = n
            return (f"Breakeven after level {n}."
                    if n else "Breakeven move disabled.")

        if low.startswith("runner"):
            val = rest[len("runner"):].strip().lower()
            if val not in ("trail", "close"):
                return "Usage: ladder runner trail|close"
            s.tp_ladder_runner = val
            return (f"Runner = {val} "
                    f"({'the remainder trails' if val == 'trail' else 'the remainder closes with the last level'}).")

        if low.startswith("basis"):
            val = rest[len("basis"):].strip().upper()
            if val not in ("R", "ROI", "PRICE"):
                return "Usage: ladder basis R|ROI|PRICE"
            s.tp_ladder_trigger_basis = val
            return f"Ladder trigger basis = {val}."

        if low.startswith("set"):
            spec = rest[len("set"):].strip()
            if not spec:
                return "Usage: ladder set 1R:25,2R:25,3R:25"
            try:
                lad = parse_ladder(
                    spec, basis=s.tp_ladder_trigger_basis or "R",
                    breakeven_after=int(s.tp_ladder_breakeven_after or 0),
                    runner=s.tp_ladder_runner or "trail")
            except LadderError as e:
                # Nothing is written: the previous ladder stays active.
                return f"Invalid ladder: {e}\nNothing changed."
            s.tp_ladder = spec
            return "TP ladder set.\n" + self.ladder_report()

        return ("Usage: ladder | ladder set 1R:25,2R:25,3R:25 | ladder off | "
                "ladder breakeven 2 | ladder runner trail|close | "
                "ladder basis R|ROI|PRICE")

    async def status(self) -> str:
        s = self.state
        try:
            ok, msg = await self.client.verify_credentials()
        except Exception as e:  # noqa: BLE001
            ok, msg = False, str(e)
        positions = await self.open_positions()
        lines = [
            "=== status ===",
            f"engine={'ON' if s.engine_on else 'OFF'} scan={'ON' if s.scan_on else 'OFF'} "
            f"autotrade={'ON' if s.autotrade else 'OFF'} report={'ON' if s.report_on else 'OFF'}",
            f"api: {'OK' if ok else 'FAIL'} - {msg}",
            f"symbols={','.join(s.symbols)} leverage={s.leverage}x max_open={s.max_open_positions}",
            f"open positions: {len(positions)}",
            f"scans={s.scans} last_scan={time.strftime('%H:%M:%S', time.localtime(s.last_scan_ts)) if s.last_scan_ts else 'never'}",
            f"errors={len(s.errors)}" + (f" last={s.errors[-1]}" if s.errors else ""),
            self.store_line(),
        ]
        if self.status_extra:
            try:
                lines.extend(self.status_extra())
            except Exception as e:  # noqa: BLE001
                lines.append(f"status_extra: {type(e).__name__}: {e}")
        return "\n".join(lines)
