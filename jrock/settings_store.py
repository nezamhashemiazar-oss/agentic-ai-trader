"""Backwards-compatible shim over `jrock.store`.

`SettingsStore` used to live here as a bare, unversioned dict with no atomic
write and no corruption handling. All persistence now goes through
`jrock.store.Store`, which adds schema versioning, atomic writes, migration,
corrupt-file backup and a secret guard.

This module is kept so existing imports keep working; it re-exports the real
implementation rather than duplicating it, so there is exactly one code path
that touches the disk.
"""
from __future__ import annotations

import re
from pathlib import Path

from .store import (SCHEMA_VERSION, SecretInStoreError, SettingsStore, Store,
                    atomic_write_json, atomic_write_text, is_secret_key)

__all__ = [
    "Store", "SettingsStore", "SecretInStoreError", "SCHEMA_VERSION",
    "atomic_write_json", "atomic_write_text", "is_secret_key", "mirror_env",
]


def mirror_env(env_path: Path | str, key: str, value: str) -> None:
    """Write KEY=value into .env, preserving every other line.

    `.env` is a *convenience mirror* so the user can see a runtime change in
    the file they already edit. The store is the source of truth on the next
    start (store > .env > default), so a stale mirror can never win.
    """
    p = Path(env_path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for i, ln in enumerate(lines):
        if pattern.match(ln):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
