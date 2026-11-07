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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import Settings
from ..store import Store
from .aggregator import Aggregate, aggregate
from .client import BitunixClient, BitunixConfig
from .errors import BitunixAPIError
from .indicators import atr, to_candles
from .kline_cache import REST_KLINE_MAX, KlineCache
from .order_builder import (OrderValidationError, build_order_body,
                            debug_order_enabled, log_order_body)
from .price_cache import PriceCache
from .risk import (PairRules, Tier, TpSlSideError, clamp_sl, clamp_tp,
                   format_price, margin_required, pick_tier,
                   position_size_by_risk, qty_from_notional, roi_pct,
                   round_price, round_step, validate_tpsl_side)
from .strategies import build_strategies
from .tpsl import (PositionState, mid_management_action, next_trailing_stop,
                   plan_tpsl, should_breakeven, should_trail)
from .ws import MarketDataFeed, WSConfig

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
            breakeven_pct=settings.breakeven_pct,
            trailing_trigger_roi_pct=settings.trailing_trigger_roi_pct,
            liq_sl_distance_pct=settings.liq_sl_distance_pct,
            min_agreeing=settings.min_agreeing,
            timeframes=list(settings.timeframes),
            min_confidence=settings.min_confidence,
            tf_min_confidence=settings.tf_min_confidence,
            scan_interval=settings.scan_interval,
            report_interval=settings.report_interval,
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
        "max_open_positions", "risk_pct", "breakeven_pct",
        "trailing_trigger_roi_pct", "liq_sl_distance_pct",
        "min_agreeing", "timeframes", "min_confidence", "tf_min_confidence",
        "scans",
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

    async def stop_feed(self) -> None:
        if self.feed is not None:
            try:
                await self.feed.stop()
            except Exception as e:  # noqa: BLE001
                log.debug("ws feed stop: %s", e)
            self.feed = None

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

        # account equity
        try:
            acct = await self.client.get_single_account()
            a = acct[0] if isinstance(acct, list) and acct else (acct or {})
            equity = float(a.get("available", 0) or 0)
        except BitunixAPIError as e:
            return {"ok": False, "reason": f"account: {e}"}
        if equity <= 0:
            return {"ok": False, "reason": "no available balance"}

        tiers = await self.load_tiers(sym)
        side = "LONG" if agg.direction == "LONG" else "SHORT"

        # The largest notional this account could open at the configured
        # leverage. Used for tier selection: a bigger notional lands in a
        # higher tier with a higher maintenance-margin rate, so this is the
        # conservative choice for the liquidation estimate.
        max_notional = equity * self.state.leverage

        # The tier for that notional band CAPS the leverage. Check it BEFORE
        # planning: if the leverage is not allowed there is no valid plan, and
        # the exchange would answer 20003 (it wants more margin than we
        # computed) rather than a leverage error.
        tier = pick_tier(tiers, max_notional)
        if self.state.leverage > tier.leverage:
            msg = (f"leverage {self.state.leverage}x exceeds the maximum "
                   f"{tier.leverage}x allowed for a {max_notional:.2f} USDT "
                   f"position on {sym} (risk tier {tier.level}). The exchange "
                   f"would require "
                   f"{margin_required(max_notional, tier.leverage):.4f} USDT "
                   f"margin, not {margin_required(max_notional, self.state.leverage):.4f}. "
                   f"Lower the leverage or reduce the position size.")
            self.state.errors.append(f"preflight {sym}: {msg}")
            return {"ok": False, "reason": msg, "hint": "20003 Insufficient balance"}

        try:
            plan = plan_tpsl(
                side=side, entry=price, leverage=self.state.leverage,
                notional=max_notional,
                strength=agg.strength, confidence=agg.confidence, tiers=tiers, rules=rules,
                liq_sl_distance_pct=self.state.liq_sl_distance_pct,
                breakeven_pct=self.state.breakeven_pct,
                trailing_trigger_roi_pct=self.state.trailing_trigger_roi_pct,
                mark=mark, min_stop_ticks=self.s.tpsl_min_stop_ticks,
            )
        except TpSlSideError as e:
            self.state.errors.append(f"tpsl {sym}: {e}")
            return {"ok": False, "reason": f"cannot place a valid stop-loss: {e}"}

        qty = position_size_by_risk(equity, self.state.risk_pct, price, plan.sl,
                                    self.state.leverage, rules)
        if qty <= 0:
            return {"ok": False, "reason": "computed qty is 0 (risk too small or below min volume)"}

        # --- pre-flight: the required margin must fit the real available
        # balance, and the engine's own bounds. Without this the request is
        # sent and the exchange answers 20003 Insufficient balance.
        notional_real = qty * price
        margin_real = margin_required(notional_real, self.state.leverage)

        if margin_real > equity:
            msg = (f"required margin {margin_real:.4f} USDT exceeds available "
                   f"balance {equity:.4f} USDT for {sym} "
                   f"(qty={qty}, notional={notional_real:.4f}, "
                   f"leverage={self.state.leverage}x). "
                   f"Lower the leverage, lower risk_pct, or add funds.")
            self.state.errors.append(f"preflight {sym}: {msg}")
            return {"ok": False, "reason": msg, "hint": "20003 Insufficient balance"}
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
                side=side, entry=price, leverage=self.state.leverage,
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
            await self.client.change_leverage(sym, self.state.leverage)
        except BitunixAPIError as e:
            if e.code not in (20006,):
                self.state.errors.append(f"leverage {sym}: {e}")

        order_side = "BUY" if side == "LONG" else "SELL"

        # --- re-read the mark price immediately before sending and re-clamp.
        # The plan above was built from a mark read several round-trips ago
        # (account, tiers, margin mode, leverage). On a fast-moving symbol the
        # mark can cross the stop in that window, and the exchange validates
        # against the mark at the moment the request lands - not the one we
        # planned with. Re-clamping here is what makes the stop safe on the
        # wire rather than only at planning time.
        mark_now = await self.get_mark_price(sym)
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
                tpPrice=plan.tp, tpStopType="MARK_PRICE", tpOrderType="MARKET",
                slPrice=plan.sl, slStopType="MARK_PRICE", slOrderType="MARKET",
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
                )
                self.positions[pid] = st
            st.peak_roi = max(st.peak_roi, roi)

            # The exchange validates every TP/SL trigger against the MARK price
            # at the moment the request lands, so the mark must be read fresh
            # here - not reused from entry time, and never assumed to equal the
            # last price. Without this the management path sent stops that were
            # only ever checked against the entry price (30029/30031).
            mark = await self.get_mark_price(sym)
            if mark <= 0:
                mark = price
                log.warning("no mark price for %s - validating the managed "
                            "TP/SL against the last price %s", sym, price)
            rules = self.rules.get(sym)
            if rules is None:
                await self.load_rules([sym])
                rules = self.rules.get(sym)
            if rules is None:
                self.state.errors.append(
                    f"manage {sym}: no trading rules, skipping TP/SL update")
                continue

            # 1) breakeven
            if should_breakeven(st, roi, self.state.breakeven_pct):
                sl = clamp_sl(side=side, sl=entry, entry=entry, mark=mark,
                              rules=rules, min_ticks=self.s.tpsl_min_stop_ticks,
                              liq=st.liq or None)
                if self._tpsl_side_ok(sym, side, entry, mark, sl, st.tp, rules):
                    try:
                        await self.client.modify_position_tp_sl_order(
                            sym, pid, tp_price=format_price(st.tp, rules) if st.tp else None,
                            sl_price=format_price(sl, rules))
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
                                sl_price=format_price(sl, rules))
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

            # 3) mid-management
            agg = self.last_aggregates.get(sym)
            reversal = bool(agg and agg.actionable and agg.direction != side)
            rev_conf = agg.confidence if agg else 0.0
            action = mid_management_action(
                st, roi, reversal and self.state.reversal, rev_conf,
                self.state.reversal_confidence)
            if action == "close_reversal":
                res = await self.close_position(symbol=sym, position_id=pid)
                actions.append({"action": "close_reversal", "symbol": sym,
                                "roi": round(roi, 2), "result": res})
            elif action == "partial_close" and not st.partial_done:
                half = round_step(st.qty / 2, self.rules.get(sym, PairRules(sym)).base_precision)
                if half > 0:
                    try:
                        close_body = build_order_body(
                            symbol=sym, qty=half,
                            side="SELL" if side.upper() == "LONG" else "BUY",
                            tradeSide="CLOSE", positionId=pid,
                            orderType="MARKET", reduceOnly=True,
                            rules=self.rules.get(sym),
                            known_symbols=set(self.rules) or None,
                        )
                        await self.client.place_order(**close_body)
                        st.partial_done = True
                        actions.append({"action": "partial_close", "symbol": sym,
                                        "qty": half, "roi": round(roi, 2)})
                        self._log_trade({"event": "partial_close", "ts": time.time(),
                                         "symbol": sym, "qty": half, "roi": round(roi, 2)})
                    except BitunixAPIError as e:
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
            data = await self.client.get_history_positions(page=1, page_size=n)
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
            f"risk_pct={s.risk_pct}% breakeven_pct={s.breakeven_pct}% "
            f"trailing_trigger_roi={s.trailing_trigger_roi_pct}% "
            f"liq_sl_distance={s.liq_sl_distance_pct}%",
            f"min_agreeing={s.min_agreeing} timeframes={','.join(s.timeframes)} "
            f"min_conf={s.min_confidence} tf_min_conf={s.tf_min_confidence}",
            f"scan_interval={s.scan_interval}s report_interval={s.report_interval}s "
            f"reversal={s.reversal} reversal_conf={s.reversal_confidence}",
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
        "leverage", "risk ",
    )

    def looks_like_command(self, line: str) -> bool:
        """True only if the line matches engine command grammar.

        Deliberately strict: a natural-language sentence must NOT be swallowed
        by the engine and answered with "Unknown engine command".
        """
        low = line.strip().lower()
        if not low:
            return False
        if low in ("status", "pnl", "p&l", "trades_history", "tpsl_history",
                   "trades history", "tpsl history", "engine on", "engine off",
                   "start trading", "stop trading", "scan on", "scan off",
                   "report on", "report off", "settings", "settings trade"):
            return True
        return any(low.startswith(p) for p in self.COMMAND_PREFIXES)

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
        if low.startswith("risk"):
            try:
                s.risk_pct = float(arg("risk").split()[-1])
                return f"Risk per trade = {s.risk_pct}% of equity."
            except (ValueError, IndexError):
                return "Usage: risk 1.0"

        return f"Unknown engine command: {raw!r}. Type 'help' for the list."

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
