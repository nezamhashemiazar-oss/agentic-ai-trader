"""Regression tests for empty model responses.

Bug B: an empty completion was printed as `(model returned an empty message)`
and swallowed. It is a failure with a specific cause, and the user must be told
which one - especially when a high thinking level ate the whole token budget.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from jrock.agent import JRockAgent
from jrock.config import Settings
from jrock.empty_response import (RETRYABLE_WITH_OFF, budget_warning,
                                  diagnose_empty, render_empty)

PLACEHOLDER = "(model returned an empty message)"


def _agent(tmp_path, **kw) -> JRockAgent:
    s = Settings(**kw)
    s.data_dir  # noqa: B018
    a = JRockAgent(s)
    a.engine.data_dir = tmp_path
    return a


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------- classification
def test_ok_response_is_not_empty():
    v = diagnose_empty({"choices": [{"message": {"content": "hi"},
                                     "finish_reason": "stop"}]})
    assert v.is_empty is False
    assert v.cause == "ok"


def test_length_zero_tokens():
    v = diagnose_empty({"choices": [{"message": {"content": ""},
                                     "finish_reason": "length"}],
                        "usage": {"completion_tokens": 0}})
    assert v.cause == "length_zero_tokens"
    assert v.retry_with_off is True
    assert v.completion_tokens == 0


def test_length_reasoning_only():
    v = diagnose_empty({"choices": [{"message": {"content": ""},
                                     "finish_reason": "length"}],
                        "usage": {"completion_tokens": 4096,
                                  "completion_tokens_details": {"reasoning_tokens": 4096}}})
    assert v.cause == "length_reasoning_only"
    assert v.retry_with_off is True
    assert v.reasoning_tokens == 4096


def test_length_with_tokens_but_no_text():
    v = diagnose_empty({"choices": [{"message": {"content": ""},
                                     "finish_reason": "length"}],
                        "usage": {"completion_tokens": 50}})
    assert v.cause == "length_no_text"
    assert v.retry_with_off is True


def test_content_filter():
    v = diagnose_empty({"choices": [{"message": {"content": ""},
                                     "finish_reason": "content_filter"}]})
    assert v.cause == "content_filter"
    assert v.retry_with_off is False


def test_tool_call_only():
    v = diagnose_empty({"choices": [{"message": {"content": None,
                                                 "tool_calls": [{"id": "1",
                                                                 "function": {"name": "get_balance"}}]},
                                     "finish_reason": "tool_calls"}]})
    assert v.cause == "tool_call_only"
    assert "get_balance" in v.detail


def test_no_choices():
    v = diagnose_empty({"choices": []})
    assert v.cause == "no_choices"


def test_error_object_inside_200():
    v = diagnose_empty({"error": {"message": "quota exceeded",
                                  "code": "insufficient_quota"}})
    assert v.cause == "error_in_200"
    assert "quota exceeded" in v.detail


def test_whitespace_only():
    v = diagnose_empty({"choices": [{"message": {"content": "   \n\t "},
                                     "finish_reason": "stop"}]})
    assert v.cause == "whitespace_only"


def test_non_dict_body():
    v = diagnose_empty(None)
    assert v.cause == "bad_body"


# ------------------------------------------------------------------ rendering
def test_render_never_returns_the_old_placeholder():
    for body in (
        {"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
         "usage": {"completion_tokens": 0}},
        {"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]},
        {"choices": []},
        {"error": {"message": "x"}},
    ):
        out = render_empty(diagnose_empty(body), "high", 6000)
        assert PLACEHOLDER not in out
        assert out.strip()


def test_render_mentions_thinking_level_for_budget_exhaustion():
    v = diagnose_empty({"choices": [{"message": {"content": ""},
                                     "finish_reason": "length"}],
                        "usage": {"completion_tokens": 0}})
    out = render_empty(v, "high", 6000)
    assert "high" in out
    assert "/think" in out
    assert "max_tokens=6000" in out


def test_render_names_finish_reason_and_tokens():
    v = diagnose_empty({"choices": [{"message": {"content": ""},
                                     "finish_reason": "length"}],
                        "usage": {"completion_tokens": 0}})
    out = render_empty(v, "medium", 2000)
    assert "finish_reason=length" in out
    assert "0 completion tokens" in out


# ------------------------------------------------------------ budget warning
def test_budget_warning_fires_when_cap_below_budget():
    w = budget_warning("high", 1000, 16384)
    assert w is not None
    assert "16384" in w


def test_budget_warning_silent_when_cap_is_large():
    assert budget_warning("high", 20000, 16384) is None


def test_budget_warning_silent_for_off():
    assert budget_warning("off", 100, 0) is None


def test_max_tokens_scales_with_level(tmp_path):
    a = _agent(tmp_path)
    assert a._max_tokens_for("off") < a._max_tokens_for("high")
    assert a._max_tokens_for("high") < a._max_tokens_for("max")


# ------------------------------------------------------- end-to-end via chat
class _FakeResp:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.payloads.append(json)
        return self.responses.pop(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _patch_aiohttp(monkeypatch, responses):
    import aiohttp
    sess = _FakeSession(responses)
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: sess)
    return sess


def test_chat_empty_length_retries_with_thinking_off(tmp_path, monkeypatch):
    a = _agent(tmp_path, api_key="k", base_url="https://mock/v1", model="m")
    a.thinking_level = "high"
    empty = json.dumps({"choices": [{"message": {"content": ""},
                                     "finish_reason": "length"}],
                        "usage": {"completion_tokens": 0}})
    good = json.dumps({"choices": [{"message": {"content": "Recovered."},
                                    "finish_reason": "stop"}],
                       "usage": {"completion_tokens": 5}})
    sess = _patch_aiohttp(monkeypatch, [_FakeResp(200, empty), _FakeResp(200, good)])

    out = _run(a.chat("hello"))
    assert "Recovered." in out
    assert "thinking=off" in out
    assert len(sess.payloads) == 2
    # the retry must NOT carry a reasoning parameter
    assert "reasoning_effort" not in sess.payloads[1]
    assert "thinking_budget" not in sess.payloads[1]


def test_chat_empty_length_both_fail_reports_cause(tmp_path, monkeypatch):
    a = _agent(tmp_path, api_key="k", base_url="https://mock/v1", model="m")
    a.thinking_level = "high"
    empty = json.dumps({"choices": [{"message": {"content": ""},
                                     "finish_reason": "length"}],
                        "usage": {"completion_tokens": 0}})
    _patch_aiohttp(monkeypatch, [_FakeResp(200, empty), _FakeResp(200, empty)])

    out = _run(a.chat("hello"))
    assert PLACEHOLDER not in out
    assert "length_zero_tokens" in out
    assert "/think" in out


def test_chat_content_filter_does_not_retry(tmp_path, monkeypatch):
    a = _agent(tmp_path, api_key="k", base_url="https://mock/v1", model="m")
    body = json.dumps({"choices": [{"message": {"content": ""},
                                    "finish_reason": "content_filter"}]})
    sess = _patch_aiohttp(monkeypatch, [_FakeResp(200, body)])
    out = _run(a.chat("hello"))
    assert "content_filter" in out
    assert len(sess.payloads) == 1


def test_chat_no_choices_reports_it(tmp_path, monkeypatch):
    a = _agent(tmp_path, api_key="k", base_url="https://mock/v1", model="m")
    _patch_aiohttp(monkeypatch, [_FakeResp(200, json.dumps({"choices": []}))])
    out = _run(a.chat("hello"))
    assert "no_choices" in out
    assert PLACEHOLDER not in out


def test_chat_error_in_200_reports_it(tmp_path, monkeypatch):
    a = _agent(tmp_path, api_key="k", base_url="https://mock/v1", model="m")
    body = json.dumps({"error": {"message": "quota exceeded"}})
    _patch_aiohttp(monkeypatch, [_FakeResp(200, body)])
    out = _run(a.chat("hello"))
    assert "quota exceeded" in out
    assert PLACEHOLDER not in out


def test_chat_ok_returns_content_unchanged(tmp_path, monkeypatch):
    a = _agent(tmp_path, api_key="k", base_url="https://mock/v1", model="m")
    body = json.dumps({"choices": [{"message": {"content": "Plain answer."},
                                    "finish_reason": "stop"}]})
    _patch_aiohttp(monkeypatch, [_FakeResp(200, body)])
    assert _run(a.chat("hello")) == "Plain answer."


def test_chat_off_level_sends_no_reasoning_param(tmp_path, monkeypatch):
    a = _agent(tmp_path, api_key="k", base_url="https://mock/v1", model="m")
    a.thinking_level = "off"
    body = json.dumps({"choices": [{"message": {"content": "ok"},
                                    "finish_reason": "stop"}]})
    sess = _patch_aiohttp(monkeypatch, [_FakeResp(200, body)])
    _run(a.chat("hello"))
    assert "reasoning_effort" not in sess.payloads[0]
    assert "thinking_budget" not in sess.payloads[0]


def test_chat_high_level_sends_reasoning_effort(tmp_path, monkeypatch):
    a = _agent(tmp_path, api_key="k", base_url="https://mock/v1", model="m")
    a.thinking_level = "high"
    body = json.dumps({"choices": [{"message": {"content": "ok"},
                                    "finish_reason": "stop"}]})
    sess = _patch_aiohttp(monkeypatch, [_FakeResp(200, body)])
    _run(a.chat("hello"))
    assert sess.payloads[0].get("reasoning_effort") == "high"


def test_retryable_set_is_exactly_the_length_causes():
    assert RETRYABLE_WITH_OFF == {"length_zero_tokens", "length_reasoning_only"}
