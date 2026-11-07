"""Reproduce Bitunix error 20015 from the `settings trade` command.

No API key needed: a fake client mimics the live exchange, which returns
20015 "this Futures is not allowed to trade" when `symbols` names a pair
that is not a tradable futures contract.

Run:  python scripts/repro_20015.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jrock.agent import AGENT_BARE, QUERY_COMMANDS  # noqa: E402
from jrock.trader.errors import BitunixAPIError  # noqa: E402

# A real, tradable pair (verified against the live public API).
VALID = {"BTCUSDT", "ETHUSDT", "AKEUSDT"}


class FakeBitunix:
    """Mimics the live exchange's trading_settings endpoint."""

    def __init__(self) -> None:
        self.calls: list[str | None] = []

    async def get_position_mode(self):
        return {"positionMode": "HEDGE"}

    async def get_trading_settings(self, symbols: str | None = None):
        self.calls.append(symbols)
        if symbols:
            for s in symbols.split(","):
                if s.strip().upper() not in VALID:
                    # Exactly what the live API returns for an unknown pair.
                    raise BitunixAPIError(
                        20015, "this Futures is not allowed to trade.")
        return [{"symbol": "BTCUSDT", "marginMode": "CROSS", "leverage": 20}]


def route(raw: str) -> str:
    """Mirror Agent.route_bare() step 1 for a bare command word."""
    low = raw.strip().lower()
    head = low.split()[0] if low.split() else ""
    if head in AGENT_BARE:
        mapped = AGENT_BARE[head]
        rest = raw.strip()[len(head):].strip()
        if mapped in QUERY_COMMANDS and rest:
            return f"ENGINE:{raw}"
        return f"AGENT:{mapped} {rest}".strip()
    return f"MODEL:{raw}"


async def main() -> int:
    print("=== 1. routing of the literal command the user typed ===")
    for line in ("settings trade", "settings", "settings BTCUSDT"):
        print(f"  {line!r:24} -> {route(line)}")

    print("\n=== 2. what cmd_settings() does with the parsed rest ===")
    rest = "trade"
    syms = [x.strip().upper() for x in rest.replace(",", " ").split() if x.strip()]
    print(f"  rest={rest!r} -> syms={syms} -> symbols param={','.join(syms)!r}")

    print("\n=== 3. the API call that produces 20015 ===")
    fake = FakeBitunix()
    try:
        await fake.get_trading_settings(",".join(syms))
        print("  no error (unexpected)")
    except BitunixAPIError as e:
        print(f"  -> error {e.code}: {e.msg}")

    print("\n=== 4. the same call with the symbol omitted (the fix) ===")
    fake2 = FakeBitunix()
    data = await fake2.get_trading_settings(None)
    print(f"  symbols param={fake2.calls[-1]!r} -> {len(data)} row(s), no error")

    print("\n=== 5. a genuine symbol still works ===")
    fake3 = FakeBitunix()
    await fake3.get_trading_settings("BTCUSDT")
    print(f"  symbols param={fake3.calls[-1]!r} -> ok")

    print("\n=== 6. a genuinely bad symbol is still rejected ===")
    fake4 = FakeBitunix()
    try:
        await fake4.get_trading_settings("NOTACOIN")
        print("  no error (unexpected)")
    except BitunixAPIError as e:
        print(f"  -> error {e.code}: {e.msg}  (correct: real bad symbol)")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))