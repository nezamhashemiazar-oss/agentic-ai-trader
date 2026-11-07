"""Tests for the web dashboard controller (jrock/web.py)."""
from __future__ import annotations

import asyncio

from aiohttp.test_utils import TestClient, TestServer

from jrock.agent import JRockAgent
from jrock.config import Settings
from jrock.web import make_app

TOKEN = "test-token"


def _agent(tmp_path) -> JRockAgent:
    s = Settings()
    s.data_dir  # noqa: B018  (ensures dir exists)
    a = JRockAgent(s)
    a.engine.data_dir = tmp_path
    return a


def _run(coro):
    return asyncio.run(coro)


async def _with_client(tmp_path, fn):
    agent = _agent(tmp_path)
    app = make_app(agent, TOKEN)
    # Skip the startup hook: it calls the network (model probe, Bitunix auth).
    app.on_startup.clear()
    app.on_cleanup.clear()
    async with TestClient(TestServer(app)) as client:
        return await fn(client)


def test_command_rejects_missing_token(tmp_path):
    async def go(client):
        r = await client.post("/api/command", json={"line": "help"})
        assert r.status == 401
    _run(_with_client(tmp_path, go))


def test_command_rejects_wrong_token(tmp_path):
    async def go(client):
        r = await client.post("/api/command", json={"line": "help"},
                              headers={"X-Auth-Token": "nope"})
        assert r.status == 401
    _run(_with_client(tmp_path, go))


def test_command_routes_help_through_agent(tmp_path):
    async def go(client):
        r = await client.post("/api/command", json={"line": "/help"},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 200
        body = await r.json()
        assert "J+Rock commands" in body["output"]
    _run(_with_client(tmp_path, go))


def test_command_rejects_empty_line(tmp_path):
    async def go(client):
        r = await client.post("/api/command", json={"line": "   "},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 400
    _run(_with_client(tmp_path, go))


def test_command_rejects_non_json(tmp_path):
    async def go(client):
        r = await client.post("/api/command", data="not json",
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 400
    _run(_with_client(tmp_path, go))


def test_index_requires_token(tmp_path):
    async def go(client):
        assert (await client.get("/")).status == 401
        r = await client.get(f"/?token={TOKEN}")
        assert r.status == 200
        assert "J+Rock dashboard" in await r.text()
    _run(_with_client(tmp_path, go))
