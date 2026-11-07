#!/usr/bin/env python3
"""Verify the natural-language intent layer end to end.

Runs the real agent against a temp data dir (no API keys needed) and prints
what each sentence resolves to and what the engine actually did.

    python scripts/verify_nlu.py
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from jrock import nlu  # noqa: E402
from jrock.agent import JRockAgent  # noqa: E402
from jrock.config import Settings  # noqa: E402

# (sentence, follow-up) - the follow-up answers a confirmation prompt.
CASES: list[tuple[str, str | None]] = [
    ("show me my trading settings", None),
    ("what is my balance", None),
    ("show my positions", None),
    ("what are my open orders", None),
    ("what is my pnl", None),
    ("show me the status", None),
    ("why did the last order fail", None),
    ("what does TP_SL_MIN_STOP_TICKS do", None),
    ("explain TAKER_FEE_RATE", None),
    ("set leverage to 5x", "yes"),
    ("set risk to 2%", "no"),
    ("close my AKEUSDT position", "no"),
    ("close my position", None),
    ("turn off autotrade", None),
    ("start autotrade", "no"),
    ("open long BTCUSDT", "yes"),
    ("i like turtles", None),
]


async def main() -> int:
    d = pathlib.Path(tempfile.mkdtemp())
    s = Settings()
    s.data_dir  # noqa: B018
    a = JRockAgent(s)
    a.engine.data_dir = d
    a.s.api_key = ""          # no model calls; the intent layer is deterministic

    print("=" * 72)
    print("PARSER: sentence -> command")
    print("=" * 72)
    for sentence, _ in CASES:
        r = nlu.parse(sentence)
        if r is None:
            verdict = "-> (model)"
        elif isinstance(r, nlu.Clarification):
            verdict = f"-> ASK: {r.question}"
        else:
            flag = " [CONFIRM]" if r.destructive else ""
            verdict = f"-> {r.command!r}{flag}"
        print(f"  {sentence!r:42} {verdict}")

    print()
    print("=" * 72)
    print("AGENT: what actually happens")
    print("=" * 72)
    for sentence, follow in CASES:
        out = await a.handle(sentence)
        first = out.splitlines()[0] if out else "(empty)"
        print(f"  > {sentence}")
        print(f"      {first[:100]}")
        if follow:
            out2 = await a.handle(follow)
            first2 = out2.splitlines()[0] if out2 else "(empty)"
            print(f"      [{follow}] {first2[:100]}")

    print()
    print("=" * 72)
    print("SAFETY: autotrade must never start from a sentence alone")
    print("=" * 72)
    a2 = JRockAgent(Settings())
    a2.engine.data_dir = pathlib.Path(tempfile.mkdtemp())
    a2.s.api_key = ""
    await a2.handle("start autotrade")
    print(f"  after 'start autotrade' (unconfirmed): autotrade={a2.engine.state.autotrade}")
    assert a2.engine.state.autotrade is False, "autotrade started without confirmation!"
    print("  OK - it asked for confirmation and did not start.")

    await a.engine.client.close()
    await a2.engine.client.close()
    print("\nall checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
