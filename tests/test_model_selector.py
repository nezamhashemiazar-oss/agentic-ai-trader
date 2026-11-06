"""Regression tests for AutoSetModelByKey (bug: never falls back to free models).

The observed failure: the probe list was a hardcoded premium list, every model
returned HTTP 402 "Paid Model - Credits Required", and the whole probe aborted
with one giant concatenated line.

These tests use a fake aiohttp session so no network is touched.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from jrock.model_selector import AutoSetResult, ModelProbe, autoset_model, parse_model


# --------------------------------------------------------------------- parsing
def test_parse_openrouter_zero_pricing_is_free():
    m = parse_model({"id": "vendor/model:free",
                     "pricing": {"prompt": "0", "completion": "0"}})
    assert m is not None and m.free is True


def test_parse_nonzero_pricing_is_paid():
    m = parse_model({"id": "openai/gpt-5.6-luna-pro",
                     "pricing": {"prompt": "0.000003", "completion": "0.000012"}})
    assert m is not None and m.free is False
    assert m.prompt_price == pytest.approx(0.000003)


def test_parse_free_flag():
    assert parse_model({"id": "x", "free": True}).free is True
    assert parse_model({"id": "y", "free": False}).free is False


def test_parse_cost_zero():
    assert parse_model({"id": "z", "cost": 0}).free is True
    assert parse_model({"id": "w", "cost": 0.5}).free is False


def test_parse_plain_string_id():
    m = parse_model("some/model")
    assert m is not None and m.id == "some/model" and m.free is False


# ------------------------------------------------------------- fake transport
class FakeResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Serves /models and /chat/completions from canned data."""

    def __init__(self, models: list[dict], behaviour: dict[str, tuple[int, str]]):
        self.models = models
        self.behaviour = behaviour
        self.calls: list[str] = []

    def get(self, url, **kw):
        return FakeResponse(200, json.dumps({"data": self.models}))

    def post(self, url, json=None, **kw):  # noqa: A002
        model = (json or {}).get("model", "")
        self.calls.append(model)
        status, body = self.behaviour.get(model, (404, '{"error":"unknown model"}'))
        return FakeResponse(status, body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


PAID_402 = (402, json.dumps({
    "error": {"title": "Paid Model - Credits Required",
              "balance": -0.025288,
              "buyCreditsUrl": "https://app.kilo.ai/profile"}}))
OK = (200, json.dumps({"choices": [{"message": {"content": "ok"}}]}))


def _run(coro):
    return asyncio.run(coro)


def test_402_then_free_model_is_selected(monkeypatch):
    """The exact reported scenario: paid models 402, a free model works."""
    models = [
        {"id": "openai/gpt-5.6-luna-pro", "pricing": {"prompt": "0.000003", "completion": "0.000012"}},
        {"id": "anthropic/claude-opus-4", "pricing": {"prompt": "0.000015", "completion": "0.000075"}},
        {"id": "meta/llama-3.3-70b:free", "pricing": {"prompt": "0", "completion": "0"}},
    ]
    behaviour = {
        "openai/gpt-5.6-luna-pro": PAID_402,
        "anthropic/claude-opus-4": PAID_402,
        "meta/llama-3.3-70b:free": OK,
    }
    fake = FakeSession(models, behaviour)
    monkeypatch.setattr("jrock.model_selector.aiohttp.ClientSession", lambda *a, **k: fake)

    res = _run(autoset_model("https://api.kilo.ai/api/gateway", "k", max_probe=8))

    assert res.ok is True
    assert res.selected == "meta/llama-3.3-70b:free"
    assert res.free_count == 1
    # the free model must be probed BEFORE any paid model
    assert fake.calls[0] == "meta/llama-3.3-70b:free"


def test_402_does_not_abort_the_probe(monkeypatch):
    """A 402 on one model must not stop the loop."""
    models = [
        {"id": "a/paid-1", "pricing": {"prompt": "0.1", "completion": "0.1"}},
        {"id": "b/paid-2", "pricing": {"prompt": "0.1", "completion": "0.1"}},
        {"id": "c/free-1", "pricing": {"prompt": "0", "completion": "0"}},
    ]
    behaviour = {"a/paid-1": PAID_402, "b/paid-2": PAID_402, "c/free-1": OK}
    fake = FakeSession(models, behaviour)
    monkeypatch.setattr("jrock.model_selector.aiohttp.ClientSession", lambda *a, **k: fake)

    res = _run(autoset_model("https://x/v1", "k"))
    assert res.ok and res.selected == "c/free-1"
    assert len(fake.calls) >= 1


def test_negative_balance_skips_paid_models(monkeypatch):
    """When a 402 reports balance <= 0, paid models are skipped, not probed."""
    models = [
        {"id": "a/paid-1", "pricing": {"prompt": "0.1", "completion": "0.1"}},
        {"id": "b/paid-2", "pricing": {"prompt": "0.1", "completion": "0.1"}},
        {"id": "c/paid-3", "pricing": {"prompt": "0.1", "completion": "0.1"}},
    ]
    behaviour = {"a/paid-1": PAID_402, "b/paid-2": PAID_402, "c/paid-3": PAID_402}
    fake = FakeSession(models, behaviour)
    monkeypatch.setattr("jrock.model_selector.aiohttp.ClientSession", lambda *a, **k: fake)

    res = _run(autoset_model("https://x/v1", "k", max_probe=8, max_paid_probe=3))

    assert res.ok is False
    assert res.balance == pytest.approx(-0.025288)
    # only the first paid model was actually probed; the rest were skipped
    assert fake.calls == ["a/paid-1"]
    skipped = [p for p in res.probes if p.status == "skipped"]
    assert len(skipped) == 2
    assert "balance is -0.025288" in skipped[0].error


def test_failure_message_is_a_table_not_one_line(monkeypatch):
    models = [{"id": f"p/paid-{i}", "pricing": {"prompt": "0.1", "completion": "0.1"}}
              for i in range(3)]
    behaviour = {f"p/paid-{i}": PAID_402 for i in range(3)}
    fake = FakeSession(models, behaviour)
    monkeypatch.setattr("jrock.model_selector.aiohttp.ClientSession", lambda *a, **k: fake)

    res = _run(autoset_model("https://x/v1", "k", max_probe=8, max_paid_probe=3))

    assert res.ok is False
    msg = res.message
    assert "\n" in msg, "failure must be multi-line, not one concatenated line"
    assert "model" in msg and "status" in msg and "reason" in msg
    assert "ACCOUNT BALANCE IS -0.025288" in msg
    assert "Next action:" in msg


def test_free_only_restricts_to_free_models(monkeypatch):
    models = [
        {"id": "a/paid", "pricing": {"prompt": "0.1", "completion": "0.1"}},
        {"id": "b/free", "pricing": {"prompt": "0", "completion": "0"}},
    ]
    fake = FakeSession(models, {"b/free": OK, "a/paid": OK})
    monkeypatch.setattr("jrock.model_selector.aiohttp.ClientSession", lambda *a, **k: fake)

    res = _run(autoset_model("https://x/v1", "k", free_only=True))
    assert res.ok and res.selected == "b/free"
    assert "a/paid" not in fake.calls


def test_free_only_with_no_free_models_reports_honestly(monkeypatch):
    models = [{"id": "a/paid", "pricing": {"prompt": "0.1", "completion": "0.1"}}]
    fake = FakeSession(models, {"a/paid": OK})
    monkeypatch.setattr("jrock.model_selector.aiohttp.ClientSession", lambda *a, **k: fake)

    res = _run(autoset_model("https://x/v1", "k", free_only=True))
    assert res.ok is False
    assert "FREE_ONLY" in res.message
    assert fake.calls == []


def test_free_only_env_var(monkeypatch):
    monkeypatch.setenv("FREE_ONLY", "true")
    models = [
        {"id": "a/paid", "pricing": {"prompt": "0.1", "completion": "0.1"}},
        {"id": "b/free", "pricing": {"prompt": "0", "completion": "0"}},
    ]
    fake = FakeSession(models, {"b/free": OK, "a/paid": OK})
    monkeypatch.setattr("jrock.model_selector.aiohttp.ClientSession", lambda *a, **k: fake)

    res = _run(autoset_model("https://x/v1", "k"))
    assert res.selected == "b/free"
    assert "a/paid" not in fake.calls


def test_empty_api_key_is_reported_not_crashed():
    res = _run(autoset_model("https://x/v1", ""))
    assert res.ok is False
    assert "API_KEY is empty" in res.message


def test_table_renders_aligned_rows():
    res = AutoSetResult("u", False, probes=[
        ModelProbe(id="a/very-long-model-name", status="402",
                   error="paid model - credits required"),
    ])
    t = res.table()
    assert "a/very-long-model-name" in t
    assert "402" in t
    assert "paid model" in t
