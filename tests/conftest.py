"""Shared test isolation.

A test must never write the repo's real `.env` or `data/` directory. The
thinking-level setter mirrors to `.env`, so any test that assigns
`agent.thinking_level` would otherwise pollute the working tree - which is
exactly what happened once and is now guarded globally.

The second guard closes the REST session of every `JRockAgent` a test builds.
`JRockAgent` constructs a real `BitunixClient`, which opens an aiohttp session
on first use; a routing test that calls `handle("price")` therefore makes a real
network call. Without this the session leaks and the suite prints
`Unclosed client session` - which is how the leak was found.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _isolate_repo(tmp_path, monkeypatch):
    """Point ROOT at a temp dir and clear env vars that leak between tests."""
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)
    monkeypatch.delenv("THINKING_LEVEL", raising=False)
    monkeypatch.delenv("FREE_ONLY", raising=False)
    yield


@pytest.fixture(autouse=True)
def _close_agent_sessions(monkeypatch):
    """Close every JRockAgent's REST session, so no test leaks a socket."""
    from jrock.agent import JRockAgent

    created: list = []
    real_init = JRockAgent.__init__

    def tracking_init(self, *a, **k):
        real_init(self, *a, **k)
        created.append(self)

    monkeypatch.setattr(JRockAgent, "__init__", tracking_init)
    yield

    for agent in created:
        client = getattr(getattr(agent, "engine", None), "client", None)
        if client is None:
            continue
        try:
            asyncio.run(client.close())
        except Exception:  # noqa: BLE001
            pass
