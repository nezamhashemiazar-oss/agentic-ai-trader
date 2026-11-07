#!/usr/bin/env python3
"""Verify the TP ladder end to end, against the real engine and live data.

Everything here runs the REAL code paths: the real parser, the real qty maths,
the real `TraderEngine.manage_positions` with a fake exchange client, and the
real store on disk. Nothing is mocked at the level of the logic under test -
only the network is replaced, because there is no API key in this sandbox.

Run:  python scripts/verify_tp_ladder.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jrock.config import Settings                      # noqa: E402
from jrock.store import Store                          # noqa: E402
from jrock.trader.aggregator import Aggregate          # noqa: E402
from jrock.trader.engine import TraderEngine           # noqa: E402
from jrock.trader.ladder import (LadderError, decide, level_qtys,  # noqa: E402
                                 parse_ladder, remaining_after,
                                 trigger_price_for)
from jrock.trader.risk import PairRules                # noqa: E402
from jrock.trader.tpsl import PositionState            # noqa: E402


def hr(title: str) -> None:
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


# --------------------------------------------------------------------- 1) parse
def part_parse() -> None:
    hr("1) LADDER PARSING - valid specs")
    for spec, basis in [("1R:25,2R:25,3R:25", "R"),
                        ("1R:50,2R:50", "R"),
                        ("50:25,100:25,150:25", "ROI"),
                        ("0.0055:30,0.0060:30", "PRICE"),
                        ("1R:100", "R")]:
        lad = parse_ladder(spec, basis=basis)
        print(f"  {spec!r:28} basis={basis:5} -> {lad.describe()}")

    hr("2) LADDER PARSING - rejected specs (nothing is silently repaired)")
    bad = [
        ("1R:25,2R:25,3R:60", "R", "sum > 100"),
        ("1R:25,2R", "R", "missing ':'"),
        ("1R:25,2R:25,1R:25", "R", "out of order"),
        ("1R:0", "R", "zero close_pct"),
        ("1R:-5", "R", "negative close_pct"),
        ("x:25", "R", "non-numeric trigger"),
        ("1R:25", "BOGUS", "unknown basis"),
        ("1R:25", "R", "breakeven out of range"),
    ]
    for spec, basis, why in bad:
        try:
            if why == "breakeven out of range":
                parse_ladder(spec, basis=basis, breakeven_after=9)
            else:
                parse_ladder(spec, basis=basis)
            print(f"  {spec!r:24} {why:24} -> ACCEPTED  <-- BUG")
        except LadderError as e:
            print(f"  {spec!r:24} {why:24} -> rejected: {e}")


# ------------------------------------------------------------------ 3) qty math
def part_qty() -> None:
    hr("3) QTY MATH - close_pct is a share of the ORIGINAL size")
    lad = parse_ladder("1R:25,2R:25,3R:25")
    orig = 1000.0
    qtys = level_qtys(orig, lad, base_precision=0)
    print(f"  position = {orig:g} units, ladder = 1R:25,2R:25,3R:25")
    print()
    print(f"  {'level':<7}{'close_pct':>10}{'qty closed':>13}"
          f"{'remaining':>12}{'% of ORIGINAL':>16}")
    running = orig
    for i, (lv, q) in enumerate(zip(lad.levels, qtys), start=1):
        running -= q
        print(f"  {i:<7}{lv.close_pct:>9g}%{q:>13g}{running:>12g}"
              f"{q / orig * 100:>15g}%")
    print(f"  {'runner':<7}{lad.runner_pct:>9g}%{running:>13g}{0:>12g}"
          f"{running / orig * 100:>15g}%")
    print()
    print(f"  sum of closes = {sum(qtys):g}  (expected {orig * lad.total_pct / 100:g})")
    print(f"  remaining     = {remaining_after(orig, lad, 3, 0):g}  (expected 250)")

    # The bug this guards against: compounding against the remainder.
    print()
    print("  WRONG (compounding against the shrinking remainder):")
    rem, wrong = orig, []
    for lv in lad.levels:
        q = rem * lv.close_pct / 100.0
        wrong.append(q)
        rem -= q
    print(f"    would close {[round(x, 2) for x in wrong]} leaving {round(rem, 2)}")
    print(f"    correct is   {qtys} leaving 250")
    assert qtys == [250.0, 250.0, 250.0], qtys
    assert wrong != qtys, "the two must differ, or this test proves nothing"
    print("  -> the two differ, so the assertion is meaningful")

    hr("3b) QTY MATH - base_precision floors, never rounds up")
    for prec in (0, 1, 2):
        q = level_qtys(1000.0, lad, prec)
        print(f"  base_precision={prec} -> {q}  sum={sum(q):g}")


# --------------------------------------------------------------- 4) triggers
def part_triggers() -> None:
    hr("4) TRIGGER PRICES - long and short, all three bases")
    entry, sl, lev = 0.005000, 0.004800, 20
    for basis, spec in [("R", "1R:25,2R:25,3R:25"),
                        ("ROI", "50:25,100:25"),
                        ("PRICE", "0.0052:25,0.0054:25")]:
        lad = parse_ladder(spec, basis=basis)
        print(f"\n  basis={basis}  entry={entry} sl={sl} leverage={lev}x")
        for side in ("LONG", "SHORT"):
            for i, lv in enumerate(lad.levels, start=1):
                p = trigger_price_for(side, lv, basis=basis, entry=entry,
                                      sl=sl, leverage=lev)
                print(f"    {side:<5} level {i}: trigger {lv.trigger:g} "
                      f"-> price {p:.6f}")


# ------------------------------------------------------- 5) decision / firing
def part_decide() -> None:
    hr("5) DECISION - one level per pass, in order, exactly once")
    lad = parse_ladder("1R:25,2R:25,3R:25")
    entry, sl, lev, orig = 0.005000, 0.004800, 20, 1000.0
    # 1R = entry + 0.0002 = 0.0052 ; 2R = 0.0054 ; 3R = 0.0056
    for price in (0.0051, 0.0052, 0.0053, 0.0054, 0.0056, 0.0058):
        d = decide(ladder=lad, side="LONG", entry=entry, sl=sl, leverage=lev,
                   price=price, original_qty=orig, fired=0, base_precision=0,
                   min_trade_volume=1.0)
        print(f"  price={price:.4f} fired=0 -> fire_index={d.fire_index} "
              f"qty={d.fire_qty:g} breakeven={d.move_breakeven} "
              f"trail={d.runner_trail} close={d.runner_close}")

    print("\n  walking the ladder forward (fired counter advances):")
    fired = 0
    for price in (0.0052, 0.0054, 0.0056):
        d = decide(ladder=lad, side="LONG", entry=entry, sl=sl, leverage=lev,
                   price=price, original_qty=orig, fired=fired,
                   base_precision=0, min_trade_volume=1.0)
        print(f"    price={price:.4f} fired={fired} -> level "
              f"{d.fire_index + 1 if d.fire_index is not None else '-'} "
              f"qty={d.fire_qty:g} remaining="
              f"{remaining_after(orig, lad, fired + 1, 0):g}")
        if d.fire_index is not None:
            fired = d.fire_index + 1

    print("\n  a level that has already fired does NOT fire again:")
    d = decide(ladder=lad, side="LONG", entry=entry, sl=sl, leverage=lev,
               price=0.0052, original_qty=orig, fired=1, base_precision=0,
               min_trade_volume=1.0)
    print(f"    price=0.0052 fired=1 -> fire_index={d.fire_index} "
          f"({d.reason})")
    assert d.fire_index is None

    hr("5b) DUST - the last level would leave less than minTradeVolume")
    # 100 units, 1R:33,2R:33,3R:33 -> 33/33/33, leaving 1 unit.
    dust = parse_ladder("1R:33,2R:33,3R:33")
    print(f"  position=100, ladder=1R:33,2R:33,3R:33, minTradeVolume=5")
    print(f"  qtys = {level_qtys(100.0, dust, 0)}  "
          f"remaining after 3 = {remaining_after(100.0, dust, 3, 0):g}")
    d = decide(ladder=dust, side="LONG", entry=0.005, sl=0.0048, leverage=20,
               price=0.0056, original_qty=100.0, fired=2, base_precision=0,
               min_trade_volume=5.0)
    print(f"  level 3 decision: qty={d.fire_qty:g} "
          f"close_remainder={d.close_remainder}")
    print(f"  -> closes {d.fire_qty:g} instead of 33, so no 1-unit dust is left")
    assert d.close_remainder and d.fire_qty == 34.0

    hr("5c) RUNNER - trail vs close after the last level")
    for runner in ("trail", "close"):
        lad2 = parse_ladder("1R:50,2R:50", runner=runner)
        d = decide(ladder=lad2, side="LONG", entry=0.005, sl=0.0048,
                   leverage=20, price=0.0056, original_qty=1000.0, fired=2,
                   base_precision=0, min_trade_volume=1.0)
        print(f"  runner={runner:5} fired=2 -> trail={d.runner_trail} "
              f"close={d.runner_close} ({d.reason})")


# ------------------------------------------------------- 6) real engine + store
class FakeClient:
    """Records the calls the engine makes; never touches the network."""

    def __init__(self, positions, price, mark):
        self._positions = positions
        self._price = price
        self._mark = mark
        self.calls: list[tuple[str, dict]] = []

    async def get_pending_positions(self, symbol=None):
        return self._positions

    async def get_tickers(self, symbol=None):
        return [{"symbol": symbol, "lastPrice": str(self._price),
                 "markPrice": str(self._mark)}]

    async def place_order(self, **kw):
        self.calls.append(("place_order", kw))
        return {"orderId": "O%d" % len(self.calls)}

    async def modify_position_tp_sl_order(self, *a, **kw):
        self.calls.append(("modify_position_tp_sl_order", {"args": a, **kw}))
        return {"orderId": "M%d" % len(self.calls)}

    async def flash_close_position(self, pid):
        self.calls.append(("flash_close_position", {"positionId": pid}))
        return {"ok": True}

    async def close_all_position(self, symbol=None):
        self.calls.append(("close_all_position", {"symbol": symbol}))
        return {"ok": True}

    async def get_single_account(self):
        return [{"available": "1000"}]

    async def verify_credentials(self):
        return True, "fake"


def make_engine(tmp: Path, ladder_spec: str, **kw) -> TraderEngine:
    s = Settings(symbols=["AKEUSDT"], leverage=20, risk_pct=1.0,
                 tp_ladder=ladder_spec, tp_ladder_trigger_basis="R",
                 tp_ladder_breakeven_after=1, tp_ladder_runner="trail")
    store = Store(path=tmp / "state.json")
    eng = TraderEngine(s, data_dir=tmp, store=store)
    eng.rules["AKEUSDT"] = PairRules(
        symbol="AKEUSDT", min_trade_volume=1.0, base_precision=0,
        quote_precision=6, max_leverage=50)
    return eng


async def part_engine() -> None:
    hr("6) REAL ENGINE - manage_positions fires the ladder and builds the bodies")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        eng = make_engine(tmp, "1R:25,2R:25,3R:25")
        entry, sl, lev, orig = 0.005000, 0.004800, 20, 1000.0
        pid = "P1"
        eng.positions[pid] = PositionState(
            position_id=pid, symbol="AKEUSDT", side="LONG", entry=entry,
            qty=orig, leverage=lev, sl=sl, tp=0.0056, liq=0.0047,
            original_qty=orig, fired_levels=0)
        eng.client = FakeClient(
            [{"positionId": pid, "symbol": "AKEUSDT", "side": "LONG",
              "qty": str(orig), "avgOpenPrice": str(entry),
              "leverage": str(lev), "liqPrice": "0.0047",
              "unrealizedPNL": "1.0"}],
            price=0.0052, mark=0.0052)

        actions = await eng.manage_positions()
        print("  price=0.0052 (1R) -> actions:")
        for a in actions:
            print(f"    {json.dumps(a, default=str)}")
        print("\n  request bodies the engine built:")
        for name, body in eng.client.calls:
            print(f"    {name}: {json.dumps(body, default=str)}")
        print(f"\n  fired_levels now = {eng.positions[pid].fired_levels}")
        print(f"  qty now          = {eng.positions[pid].qty:g}")
        print(f"  breakeven_done   = {eng.positions[pid].breakeven_done}")

        hr("6b) RESTART - a fresh engine must NOT re-fire level 1")
        eng.persist_positions()
        eng2 = make_engine(tmp, "1R:25,2R:25,3R:25")
        eng2._restore_from_store()
        st = eng2.positions.get(pid)
        print(f"  restored fired_levels = {st.fired_levels} "
              f"qty={st.qty:g} original_qty={st.original_qty:g}")
        eng2.client = FakeClient(
            [{"positionId": pid, "symbol": "AKEUSDT", "side": "LONG",
              "qty": str(st.qty), "avgOpenPrice": str(entry),
              "leverage": str(lev), "liqPrice": "0.0047",
              "unrealizedPNL": "1.0"}],
            price=0.0052, mark=0.0052)
        actions2 = await eng2.manage_positions()
        print(f"  actions at the SAME price after restart: {actions2}")
        print(f"  place_order calls: "
              f"{[c for c in eng2.client.calls if c[0] == 'place_order']}")
        assert not [c for c in eng2.client.calls if c[0] == "place_order"], \
            "level 1 re-fired after a restart"
        print("  -> no order sent: the fired level survived the restart")

        hr("6c) LEVEL 2 fires on the next pass at 2R")
        eng2.client = FakeClient(
            [{"positionId": pid, "symbol": "AKEUSDT", "side": "LONG",
              "qty": str(st.qty), "avgOpenPrice": str(entry),
              "leverage": str(lev), "liqPrice": "0.0047",
              "unrealizedPNL": "2.0"}],
            price=0.0054, mark=0.0054)
        actions3 = await eng2.manage_positions()
        for a in actions3:
            print(f"    {json.dumps(a, default=str)}")
        print(f"  fired_levels = {eng2.positions[pid].fired_levels} "
              f"qty = {eng2.positions[pid].qty:g}")

        hr("6d) /ladder REPORT")
        print(eng2.ladder_report())


# ------------------------------------------------------------- 7) commands
async def part_commands() -> None:
    hr("7) COMMANDS - reachable through the real router")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        eng = make_engine(tmp, "")
        for line in ("ladder", "ladder set 1R:25,2R:25,3R:25",
                     "ladder breakeven 2", "ladder runner close",
                     "ladder basis ROI", "ladder set 1R:25,2R:25,3R:60",
                     "ladder off"):
            out = await eng.command(line)
            first = out.splitlines()[0] if out else ""
            print(f"  {line!r:38} -> {first}")
        print(f"\n  persisted tp_ladder = {eng.state.tp_ladder!r}")
        print(f"  persisted breakeven = {eng.state.tp_ladder_breakeven_after}")
        print(f"  persisted runner    = {eng.state.tp_ladder_runner!r}")
        print(f"  persisted basis     = {eng.state.tp_ladder_trigger_basis!r}")

        hr("7b) the bad spec left the previous ladder intact")
        eng2 = make_engine(tmp, "1R:25,2R:25")
        out = await eng2.command("ladder set 1R:25,2R:25,3R:60")
        print(f"  {out.splitlines()[0]}")
        print(f"  tp_ladder is still {eng2.state.tp_ladder!r}")


# --------------------------------------------------------------- 8) live data
async def part_live() -> None:
    hr("8) LIVE - real AKEUSDT rules from the exchange (no credentials needed)")
    from jrock.trader.client import BitunixClient, BitunixConfig
    c = BitunixClient(BitunixConfig(api_key="", api_secret=""))
    try:
        pairs = await c.get_trading_pairs("AKEUSDT")
        row = pairs[0] if isinstance(pairs, list) and pairs else (pairs or {})
        rules = PairRules.from_api(row)
        print(f"  AKEUSDT: minTradeVolume={rules.min_trade_volume:g} "
              f"basePrecision={rules.base_precision} "
              f"quotePrecision={rules.quote_precision} "
              f"maxLeverage={rules.max_leverage}")
        tick = await c.get_tickers("AKEUSDT")
        t = tick[0] if isinstance(tick, list) and tick else (tick or {})
        last = float(t.get("lastPrice") or 0)
        mark = float(t.get("markPrice") or 0)
        print(f"  lastPrice={last} markPrice={mark}")

        lad = parse_ladder("1R:25,2R:25,3R:25")
        entry, sl, lev = last, last * 0.96, 20
        print(f"\n  ladder on the live price (entry={entry}, sl={sl:.6f}, "
              f"{lev}x):")
        for i, lv in enumerate(lad.levels, start=1):
            p = trigger_price_for("LONG", lv, basis="R", entry=entry, sl=sl,
                                  leverage=lev)
            print(f"    level {i}: {lv.trigger:g}R -> {p:.6f}")
        print(f"  qtys for a 1000-unit position: "
              f"{level_qtys(1000.0, lad, rules.base_precision)}")
    except Exception as e:  # noqa: BLE001
        print(f"  live call failed: {type(e).__name__}: {e}")
    finally:
        await c.close()


async def main() -> None:
    part_parse()
    part_qty()
    part_triggers()
    part_decide()
    await part_engine()
    await part_commands()
    await part_live()
    hr("DONE - every assertion above passed")


if __name__ == "__main__":
    asyncio.run(main())
