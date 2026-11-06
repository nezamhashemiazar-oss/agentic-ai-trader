"""Persistent agent settings store.

Engine settings live in EngineState (in-memory, seeded from .env). Agent-level
settings that must survive a restart - like the thinking level - live here, in
`data/settings.json`.

Precedence on read:  store value  >  .env value  >  built-in default.
Writes go to the store AND are mirrored into .env so the user can see them.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


class SettingsStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if isinstance(raw, dict):
            self._data = raw

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._save()

    def all(self) -> dict[str, Any]:
        return dict(self._data)


def mirror_env(env_path: Path | str, key: str, value: str) -> None:
    """Write KEY=value into .env, preserving every other line."""
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
