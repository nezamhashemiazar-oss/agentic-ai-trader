"""Shared test isolation.

A test must never write the repo's real `.env` or `data/` directory. The
thinking-level setter mirrors to `.env`, so any test that assigns
`agent.thinking_level` would otherwise pollute the working tree - which is
exactly what happened once and is now guarded globally.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_repo(tmp_path, monkeypatch):
    """Point ROOT at a temp dir and clear env vars that leak between tests."""
    monkeypatch.setattr("jrock.agent.ROOT", tmp_path)
    monkeypatch.delenv("THINKING_LEVEL", raising=False)
    monkeypatch.delenv("FREE_ONLY", raising=False)
    yield
