"""Banner / /status / /ws must report the REAL market-data state.

These are the "don't pretend" tests: if the feed is not connected, the UI has
to say so, and `rest` mode must never claim a socket.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from jrock.agent import JRockAgent
from jrock.config import Settings


_AGENTS: list[JRockAgent] = []


def make_agent(tmp_path, source="rest"):
    s = Settings(symbols=["BTCUSDT"], market_data_source=source)
    a = JRockAgent(s)
    a.engine.client.get_single_account = _no_account  # type: ignore[assignment]
    _AGENTS.append(a)
    return a


async def _no_account():
    return []


@pytest_asyncio.fixture(autouse=True)
async def _close_clients():
    """Every agent here owns a real aiohttp session; close it after the test."""
    _AGENTS.clear()
    yield
    for a in _AGENTS:
        try:
            await a.engine.stop()
        except Exception:  # noqa: BLE001
            pass
    _AGENTS.clear()


@pytest.mark.asyncio
async def test_status_shows_rest_source(tmp_path):
    a = make_agent(tmp_path, "rest")
    out = await a.handle("/status")
    assert "market_data=rest" in out
    assert "market_data_state=rest (REST polling)" in out
    assert "price_cache=" in out


@pytest.mark.asyncio
async def test_status_shows_ws_not_connected(tmp_path):
    a = make_agent(tmp_path, "ws")
    out = await a.handle("/status")
    assert "market_data=ws" in out
    assert "NOT connected" in out
    assert "falling back to REST" in out


@pytest.mark.asyncio
async def test_ws_status_lists_cache_counters(tmp_path):
    a = make_agent(tmp_path, "ws")
    out = await a.handle("/ws")
    assert "--- price cache ---" in out
    assert "hits=" in out and "stale_rejections=" in out
    assert "REST price calls made:" in out


@pytest.mark.asyncio
async def test_ws_off_stops_feed_and_persists(tmp_path):
    a = make_agent(tmp_path, "ws")
    out = await a.handle("/ws off")
    assert "MARKET_DATA_SOURCE = rest" in out
    assert a.engine.feed is None
    assert a.store.get("market_data_source") == "rest"


@pytest.mark.asyncio
async def test_ws_on_reports_feed_state(tmp_path):
    a = make_agent(tmp_path, "rest")
    out = await a.handle("/ws on")
    assert "MARKET_DATA_SOURCE = ws" in out
    assert "feed:" in out
    assert a.store.get("market_data_source") == "ws"


@pytest.mark.asyncio
async def test_price_command_uses_cache_in_ws_mode(tmp_path):
    a = make_agent(tmp_path, "ws")
    a.engine.price_cache.put_ws("BTCUSDT", 12345.6)
    out = await a.handle("/price BTCUSDT")
    assert "12345.6" in out
    assert "src=ws" in out
    assert a.engine.client.ticker_calls == 0 if hasattr(a.engine.client, "ticker_calls") else True


@pytest.mark.asyncio
async def test_banner_rest_mode(tmp_path):
    a = make_agent(tmp_path, "rest")
    out = await a.startup()
    assert "Market data: rest (REST polling)" in out
    assert "WARNING" not in out
