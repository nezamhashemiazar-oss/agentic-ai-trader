"""Regression tests for the Bitunix signature (bug: error 10007).

The official demo repo ships a test vector:
    Demo/Golang/open_api_http_sign_test.go

    apiKey      = 11111111111111111
    secretKey   = 22222222222222222
    nonce       = 33333333333333333
    timestamp   = 1747104711262
    queryParams = marginCoinUSDT      <-- key+value, NO '=' and NO '&'
    body        = ""
    want        = 40e87256554e263e39201a3979469b7fbb640d1c7c41759abf5c6a18f937f922

Signing the urlencoded form ("marginCoin=USDT") produces a different digest and
the exchange answers 10007. These tests pin the correct behaviour.
"""
from __future__ import annotations

import hashlib

import pytest

from jrock.trader.client import (BitunixClient, BitunixConfig, sign_query_string,
                                 url_query_string)
from jrock.trader.ws import ws_login_sign

# ---- official vector from the demo repo -------------------------------------
VEC = dict(
    api_key="11111111111111111",
    secret="22222222222222222",
    nonce="33333333333333333",
    timestamp="1747104711262",
    query="marginCoinUSDT",
    body="",
    want="40e87256554e263e39201a3979469b7fbb640d1c7c41759abf5c6a18f937f922",
)


def _client() -> BitunixClient:
    return BitunixClient(BitunixConfig(api_key=VEC["api_key"], api_secret=VEC["secret"]))


def test_official_vector_matches():
    c = _client()
    got = c.sign(VEC["nonce"], VEC["timestamp"], VEC["query"], VEC["body"])
    assert got == VEC["want"]


def test_urlencoded_form_does_not_match_official_vector():
    """Guards against regressing to the buggy urlencoded signing form."""
    c = _client()
    wrong = c.sign(VEC["nonce"], VEC["timestamp"], "marginCoin=USDT", "")
    assert wrong != VEC["want"]


def test_sign_query_string_has_no_separators():
    assert sign_query_string({"marginCoin": "USDT"}) == "marginCoinUSDT"
    assert sign_query_string({"b": "2", "a": "1"}) == "a1b2"      # sorted
    assert sign_query_string({"a": "1", "b": None}) == "a1"       # None skipped
    assert sign_query_string({"a": "1", "b": ""}) == "a1"         # empty skipped
    assert sign_query_string({}) == ""
    assert sign_query_string(None) == ""


def test_url_query_string_is_normal_form():
    assert url_query_string({"marginCoin": "USDT"}) == "marginCoin=USDT"
    assert url_query_string({"b": "2", "a": "1"}) == "a=1&b=2"
    assert url_query_string({"a": "1", "b": None}) == "a=1"
    assert url_query_string({}) == ""


def test_signed_and_sent_strings_differ_but_agree_on_params():
    """The two strings must describe the same params, differently formatted."""
    params = {"symbol": "BTCUSDT", "marginCoin": "USDT"}
    signed = sign_query_string(params)
    sent = url_query_string(params)
    assert signed == "marginCoinUSDTsymbolBTCUSDT"
    assert sent == "marginCoin=USDT&symbol=BTCUSDT"
    assert "=" not in signed and "&" not in signed


def test_body_is_compact_sorted_json():
    """Body must be byte-identical to what is signed: compact, sorted keys."""
    import json
    body = {"symbol": "BTCUSDT", "qty": "0.1", "side": "BUY"}
    s = json.dumps(body, separators=(",", ":"), sort_keys=True)
    assert s == '{"qty":"0.1","side":"BUY","symbol":"BTCUSDT"}'
    assert " " not in s


def test_debug_sign_records_preimage(monkeypatch):
    monkeypatch.setenv("BITUNIX_DEBUG_SIGN", "true")
    c = _client()
    c.debug_sign = True
    c.sign(VEC["nonce"], VEC["timestamp"], VEC["query"], "")
    d = c.last_sign_debug
    assert d["preimage"] == (
        "33333333333333333" "1747104711262" "11111111111111111" "marginCoinUSDT")
    assert d["sign"] == VEC["want"]
    assert d["digest"] == hashlib.sha256(d["preimage"].encode()).hexdigest()


def test_ws_login_sign_order_is_nonce_then_timestamp():
    """docs/bitunix/WebSocket.md: sign = sha256(sha256(nonce+ts+apiKey)+secret)."""
    api_key, secret, ts, nonce = "k", "s", 1700000000, "n"
    expected_digest = hashlib.sha256(f"{nonce}{ts}{api_key}".encode()).hexdigest()
    expected = hashlib.sha256((expected_digest + secret).encode()).hexdigest()
    assert ws_login_sign(api_key, secret, ts, nonce) == expected

    # the old (wrong) order must NOT be what we produce
    wrong_digest = hashlib.sha256(f"{ts}{nonce}{api_key}".encode()).hexdigest()
    wrong = hashlib.sha256((wrong_digest + secret).encode()).hexdigest()
    assert ws_login_sign(api_key, secret, ts, nonce) != wrong
