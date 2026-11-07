"""Reproduce (and verify the fix for) Bitunix error 20015 on `settings trade`.

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

    async def get_trading_pairs(self, symbols: str | None = None):
        return [{"symbol": s} for s in sorted(VALID)]

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

    print("\n=== 2. the OLD behaviour: `trade` parsed as a symbol ===")
    rest = "trade"
    syms = [x.strip().upper() for x in rest.replace(",", " ").split() if x.strip()]
    print(f"  rest={rest!r} -> syms={syms} -> symbols param={','.join(syms)!r}")
    fake = FakeBitunix()
    try:
        await fake.get_trading_settings(",".join(syms))
        print("  no error (unexpected)")
    except BitunixAPIError as e:
        print(f"  -> error {e.code}: {e.msg}   <-- the reported bug")

    print("\n=== 3. the FIX: `trade` is a keyword, not a symbol ===")
    from jrock.agent import JRockAgent  # noqa: E402
    from jrock.config import Settings  # noqa: E402

    s = Settings(bitunix_api_key="k", bitunix_api_secret="s")
    a = JRockAgent(s)
    f = FakeBitunix()
    a.engine.client = f
    out = await a.handle("settings trade")
    print(f"  trading_settings calls={f.calls}  (empty = no symbol sent)")
    print("  output:")
    for ln in out.splitlines():
        print(f"    {ln}")
    assert f.calls == [], "the keyword form must not send a symbol"
    assert "20015" not in out, "the keyword form must not raise 20015"
    print("  -> OK: account config printed, no 20015")

    print("\n=== 4. a genuine symbol still works ===")
    f2 = FakeBitunix()
    a.engine.client = f2
    out2 = await a.handle("settings BTCUSDT")
    print(f"  trading_settings calls={f2.calls}")
    assert f2.calls == ["BTCUSDT"]
    assert "BTCUSDT: leverage=20x marginMode=CROSS" in out2
    print("  -> OK")

    print("\n=== 5. a genuinely bad symbol is dropped, not sent ===")
    f3 = FakeBitunix()
    a.engine.client = f3
    out3 = await a.handle("settings NOTACOIN")
    print(f"  trading_settings calls={f3.calls}  (empty = not sent)")
    assert f3.calls == []
    assert "20015" not in out3
    print("  -> OK: dropped with a warning, no 20015")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
