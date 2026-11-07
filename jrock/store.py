"""jrock.store - the single owner of every piece of persisted state.

Why this module exists
----------------------
Before it, only two values survived a restart (the thinking level and the
market-data source) and they lived in a bare ``data/settings.json`` dict with
no schema version, no atomic write and no corruption handling. Everything
else - leverage, risk params, symbols, autotrade, strategy tuning, the
open-position management state (``breakeven_done``, ``trailing_stop``,
``peak_roi``) and every counter - was held in memory and silently reset.

Precedence (documented, and asserted by tests)
    persisted store  >  .env  >  built-in default

A value changed at runtime is written here *immediately* (atomic + fsync), so
``kill -9`` cannot lose it. Secrets are never written: keys matching
``SECRET_KEY_PATTERNS`` are refused outright, and any registered secret
*value* found in the payload aborts the write.

Files
-----
``data/state.json``    versioned settings + counters + engine state + positions
``data/memory.jsonl``  learned memory (see memory.py, which uses this module's
                       atomic rewrite helper for compaction)
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("jrock.store")

#: Bumped whenever the on-disk shape changes. v1 was a bare, unversioned dict.
SCHEMA_VERSION = 2

#: A key containing any of these substrings is refused before it reaches disk.
SECRET_KEY_PATTERNS = (
    "api_key", "apikey", "api_secret", "apisecret", "secret",
    "password", "passwd", "private_key", "authorization",
)

#: Exact key names that are secrets. Separate from the substring list so that
#: ordinary words like ``max_tokens`` / ``completion_tokens`` are NOT caught.
SECRET_KEY_EXACT = frozenset({
    "token", "auth", "secret", "password", "passwd", "credential",
    "access_token", "refresh_token", "session_token", "bearer",
})

#: Key suffixes that mark a secret (``bitunix_api_key``, ``llm_secret``, ...).
SECRET_KEY_SUFFIXES = ("_api_key", "_secret", "_password", "_passwd",
                       "_private_key", "_token", "_key")

#: Never persisted even under a non-secret name.
NON_PERSISTED_KEYS = frozenset({"api_key", "base_url"})


class SecretInStoreError(RuntimeError):
    """Raised when a secret would be written to the persistence layer."""


def is_secret_key(key: Any) -> bool:
    """True if this key must never be persisted."""
    k = str(key).strip().lower().replace("-", "_")
    if not k:
        return False
    if k in SECRET_KEY_EXACT or k in NON_PERSISTED_KEYS:
        return True
    if any(p in k for p in SECRET_KEY_PATTERNS):
        return True
    return any(k.endswith(s) for s in SECRET_KEY_SUFFIXES)


# --------------------------------------------------------------------- atomic io
def atomic_write_text(path: Path | str, text: str) -> None:
    """Write ``text`` to ``path`` atomically.

    Write to a sibling temp file, fsync it, ``os.replace`` onto the target
    (atomic on POSIX), then fsync the directory so the rename itself is
    durable. A crash at any point leaves either the old file or the new one -
    never a half-written file.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp.{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
        try:
            dirfd = os.open(p.parent, os.O_RDONLY)
        except OSError:  # pragma: no cover - platform without dir fds
            return
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def atomic_write_json(path: Path | str, data: Any) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True,
                                       ensure_ascii=False, default=str))


# ------------------------------------------------------------------------ store
class Store:
    """Versioned, atomic, secret-aware persistence for all engine/agent state."""

    def __init__(self, path: Path | str | None = None,
                 data_dir: Path | str | None = None,
                 register_secrets: bool = True) -> None:
        if path is None:
            base = Path(data_dir) if data_dir else Path("data")
            path = base / "state.json"
        self.path = Path(path)
        self.version: int = SCHEMA_VERSION
        self.settings: dict[str, Any] = {}
        self.counters: dict[str, Any] = {}
        self.engine: dict[str, Any] = {}
        self.positions: dict[str, Any] = {}
        self.meta: dict[str, Any] = {}
        #: secret *values* that must never appear in the payload
        self._secrets: list[str] = []
        self.loaded: bool = False
        self.loaded_from: str = ""
        self.migrated_from: int | None = None
        self.recovered_backup: str = ""
        self.saves: int = 0
        self.last_save_ts: float = 0.0
        self.register_secrets = register_secrets
        self._load()

    # ------------------------------------------------------------------ loading
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            self._backup_corrupt(f"{type(e).__name__}: {e}")
            return
        if not isinstance(raw, dict):
            self._backup_corrupt(f"top-level is {type(raw).__name__}, expected object")
            return

        version = raw.get("version")
        if not isinstance(version, int):
            # v1: a bare dict of settings with no schema marker.
            version = 1
        if version > SCHEMA_VERSION:
            log.warning("store %s is version %s, newer than this build (%s) - "
                        "reading it as-is, unknown keys are preserved",
                        self.path, version, SCHEMA_VERSION)

        data = self._migrate(raw, version)
        self.version = SCHEMA_VERSION
        self.settings = data["settings"]
        self.counters = data["counters"]
        self.engine = data["engine"]
        self.positions = data["positions"]
        self.meta = data["meta"]
        self.loaded = True
        self.loaded_from = str(self.path)
        # The file's own timestamp is the real "last save" - without this a
        # restarted process reports `last_save=never` even though the file was
        # written moments ago, which is exactly the kind of lie this store
        # exists to prevent.
        ts = raw.get("updated_ts")
        if isinstance(ts, (int, float)) and ts > 0:
            self.last_save_ts = float(ts)
        if self.migrated_from is not None:
            # Write the upgraded shape back immediately, so the migration is
            # durable and does not have to be redone (or re-logged) every start.
            self.save()

    def _migrate(self, raw: dict, version: int) -> dict[str, Any]:
        """Upgrade an older on-disk shape instead of discarding it."""
        out = {
            "settings": dict(raw.get("settings") or {}),
            "counters": dict(raw.get("counters") or {}),
            "engine": dict(raw.get("engine") or {}),
            "positions": dict(raw.get("positions") or {}),
            "meta": dict(raw.get("meta") or {}),
        }
        if version < 2:
            # v1 stored every setting at the top level, flat, with no sections.
            reserved = {"version", "settings", "counters", "engine", "positions",
                        "meta", "updated_ts"}
            moved = {k: v for k, v in raw.items() if k not in reserved}
            out["settings"] = {**moved, **out["settings"]}
            out["meta"]["migrated_from"] = version
            out["meta"]["migrated_ts"] = time.time()
            self.migrated_from = version
            log.warning("store %s migrated from schema v%s -> v%s (%d settings kept)",
                        self.path, version, SCHEMA_VERSION, len(out["settings"]))
        return out

    def _backup_corrupt(self, why: str) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        try:
            self.path.replace(backup)
            self.recovered_backup = str(backup)
            log.warning("store %s was unreadable (%s) - backed up to %s and "
                        "started clean", self.path, why, backup.name)
        except OSError as e:  # pragma: no cover - unreadable dir
            log.error("store %s is corrupt (%s) and could not be backed up: %s",
                      self.path, why, e)

    # ------------------------------------------------------------------- access
    def get(self, key: str, default: Any = None) -> Any:
        """Read a setting. Sections are consulted before legacy top-level keys."""
        if key in self.settings:
            return self.settings[key]
        return default

    def set(self, key: str, value: Any) -> None:
        """Write a setting and persist it immediately (SIGKILL-safe)."""
        if is_secret_key(key):
            raise SecretInStoreError(
                f"refusing to persist {key!r}: it looks like a secret. "
                f"Secrets belong in .env only.")
        self.settings[key] = value
        self.save()

    def pop(self, key: str) -> None:
        """Drop a setting so a lower-precedence source (.env/default) applies."""
        if key in self.settings:
            del self.settings[key]
            self.save()

    def section(self, name: str) -> dict[str, Any]:
        return getattr(self, name, {}) or {}

    def set_section(self, name: str, value: dict[str, Any],
                    save: bool = True) -> None:
        if name not in ("counters", "engine", "positions", "meta"):
            raise ValueError(f"unknown store section {name!r}")
        setattr(self, name, dict(value or {}))
        if save:
            self.save()

    def shadowed(self, key: str, env_value: Any) -> bool:
        """True when a store value overrides a different value from .env.

        Used to warn the user instead of silently ignoring their .env edit.
        """
        if key not in self.settings or env_value in (None, ""):
            return False
        return str(self.settings[key]) != str(env_value)

    # ------------------------------------------------------------------ secrets
    def register_secret(self, value: Any) -> None:
        """Remember a secret value so no future write can contain it."""
        if not self.register_secrets or value is None:
            return
        v = str(value)
        if len(v) >= 8 and v not in self._secrets:
            self._secrets.append(v)

    def _scrub(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Strip secret keys, then refuse to write if a secret value survives."""
        cleaned: dict[str, Any] = {}
        for section, body in payload.items():
            if isinstance(body, dict):
                cleaned[section] = {k: v for k, v in body.items()
                                    if not is_secret_key(k)}
            else:
                cleaned[section] = body
        text = json.dumps(cleaned, default=str, ensure_ascii=False)
        for secret in self._secrets:
            if secret and secret in text:
                raise SecretInStoreError(
                    "refusing to write the store: a registered secret value "
                    "appears in the payload. Secrets belong in .env only.")
        return cleaned

    # --------------------------------------------------------------------- save
    def _payload(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "updated_ts": time.time(),
            "settings": self.settings,
            "counters": self.counters,
            "engine": self.engine,
            "positions": self.positions,
            "meta": self.meta,
        }

    def save(self) -> None:
        payload = self._scrub(self._payload())
        atomic_write_json(self.path, payload)
        self.saves += 1
        self.last_save_ts = payload["updated_ts"]

    # ------------------------------------------------------------------- report
    def summary(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "version": self.version,
            "settings": len(self.settings),
            "engine_settings": len(self.engine),
            "positions": len(self.positions),
            "last_save": self.last_save_ts,
            "saves": self.saves,
            "size_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "loaded": self.loaded,
            "migrated_from": self.migrated_from,
            "recovered_backup": self.recovered_backup,
        }

    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0


# ------------------------------------------------------------------ compat shim
class SettingsStore(Store):
    """Backwards-compatible name for a Store whose path is given explicitly."""

    def __init__(self, path: Path | str) -> None:
        super().__init__(path=path)
