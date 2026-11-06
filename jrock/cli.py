"""J+Rock CLI / chat interface.

    python -m jrock.cli            # interactive
    python -m jrock.cli --once "/check_system"
    python -m jrock.cli --startup  # run startup checks then exit
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from .agent import HELP, JRockAgent
from .config import load_settings


async def run_once(agent: JRockAgent, line: str) -> None:
    try:
        out = await agent.handle(line)
    except Exception as e:  # noqa: BLE001
        out = f"ERROR: {type(e).__name__}: {e}"
    print(out)


async def interactive(agent: JRockAgent) -> None:
    print(await agent.startup())
    print("\nType /help for commands. Ctrl-D to exit.\n")
    while True:
        try:
            line = await asyncio.to_thread(input, "j+rock> ")
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break
        if line.strip() in ("exit", "quit", "/exit", "/quit"):
            break
        if not line.strip():
            continue
        await run_once(agent, line)
    await agent.engine.stop()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jrock", description="J+Rock agentic assistant + Bitunix trader")
    ap.add_argument("--once", help="run a single command and exit")
    ap.add_argument("--startup", action="store_true", help="run startup checks and exit")
    ap.add_argument("--env", help="path to .env")
    ap.add_argument("--help-cmds", action="store_true", help="print the command list")
    args = ap.parse_args(argv)

    if args.help_cmds:
        print(HELP)
        return 0

    settings = load_settings(args.env)
    agent = JRockAgent(settings)

    if args.startup:
        print(asyncio.run(agent.startup()))
        return 0
    if args.once:
        asyncio.run(run_once(agent, args.once))
        return 0

    asyncio.run(interactive(agent))
    return 0


if __name__ == "__main__":
    sys.exit(main())
