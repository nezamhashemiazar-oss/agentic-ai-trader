"""Persistent memory + learning loop.

Stores:
  * feedback  - user corrections/preferences, weighted, recalled by keyword
  * episode   - what happened (task, action, outcome) for experience-based improvement
  * fact      - durable project facts (symbols, settings, decisions)

Retrieval is a simple BM25-ish keyword score; no external deps.

Durability
----------
Appends are flushed + fsynced immediately, so a ``kill -9`` after ``add()``
cannot lose the entry. The file is *bounded*: once it exceeds ``max_items`` the
oldest entries are evicted by a weighted score (low weight first, then oldest),
and the surviving set is rewritten atomically so the file cannot grow without
limit and cannot be left half-written.

Eviction is weighted, not pure FIFO: a ``fact`` (weight 3.0) or a user
``feedback`` (weight 3.0) outlives a routine ``episode`` (weight 1.0) of the
same age, because the cheap chatter is what should be dropped first.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .store import atomic_write_text

log = logging.getLogger("jrock.memory")

_TOKEN = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass
class MemoryItem:
    kind: str
    text: str
    weight: float = 1.0
    tags: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)
    meta: dict = field(default_factory=dict)


#: Upper bound on retained entries. Beyond this the oldest low-weight entries
#: are evicted. Sized so the file stays a few MB at worst.
DEFAULT_MAX_ITEMS = 5000


class MemoryStore:
    def __init__(self, path: Path | str = "data/memory.jsonl",
                 max_items: int = DEFAULT_MAX_ITEMS):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_items = max(1, int(max_items))
        self.items: list[MemoryItem] = []
        self.evicted: int = 0
        self.last_write_ts: float = 0.0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                self.items.append(MemoryItem(**d))
            except (json.JSONDecodeError, TypeError):
                continue

    def add(self, kind: str, text: str, weight: float = 1.0,
            tags: list[str] | None = None, meta: dict | None = None) -> MemoryItem:
        item = MemoryItem(kind=kind, text=text, weight=weight, tags=tags or [], meta=meta or {})
        self.items.append(item)
        self._append_durable(item)
        if len(self.items) > self.max_items:
            self._evict()
        return item

    def _append_durable(self, item: MemoryItem) -> None:
        """Append one JSON line and fsync it - survives kill -9 immediately."""
        line = json.dumps(asdict(item), ensure_ascii=False) + "\n"
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        self.last_write_ts = time.time()

    def _evict(self) -> None:
        """Drop the weakest entries until we are back at the cap.

        Ranking: keep high weight; among equal weights keep the newest. The
        survivors are written atomically, so a crash mid-eviction leaves the
        previous (valid) file intact.
        """
        excess = len(self.items) - self.max_items
        if excess <= 0:
            return
        # newest-first for ties, so the oldest equal-weight entry is dropped first
        order = sorted(self.items, key=lambda i: (i.weight, i.ts), reverse=True)
        keep = order[: self.max_items]
        # restore chronological order for stable reads
        keep.sort(key=lambda i: i.ts)
        self.items = keep
        self.evicted += excess
        self._rewrite()
        log.debug("memory evicted %d entries, %d remain", excess, len(self.items))

    def _rewrite(self) -> None:
        body = "".join(json.dumps(asdict(i), ensure_ascii=False) + "\n"
                       for i in self.items)
        atomic_write_text(self.path, body)
        self.last_write_ts = time.time()

    def clear(self) -> int:
        """Wipe all memory. Returns how many entries were removed."""
        n = len(self.items)
        self.items = []
        atomic_write_text(self.path, "")
        self.last_write_ts = time.time()
        return n

    def feedback(self, text: str, weight: float = 2.0, tags: list[str] | None = None) -> MemoryItem:
        return self.add("feedback", text, weight=weight, tags=tags)

    def episode(self, task: str, action: str, outcome: str, ok: bool = True) -> MemoryItem:
        return self.add("episode", f"{task} | {action} | {outcome}",
                        weight=1.0 if ok else 1.5, meta={"ok": ok})

    def fact(self, key: str, value: str) -> MemoryItem:
        return self.add("fact", f"{key} = {value}", weight=3.0, tags=[key])

    def search(self, query: str, k: int = 8, kind: str | None = None) -> list[MemoryItem]:
        q = _tokens(query)
        if not q:
            return []
        pool = [i for i in self.items if kind is None or i.kind == kind]
        if not pool:
            return []
        df: dict[str, int] = {}
        docs = [_tokens(i.text) for i in pool]
        for d in docs:
            for t in set(d):
                df[t] = df.get(t, 0) + 1
        n = len(pool)
        scored: list[tuple[float, MemoryItem]] = []
        for item, d in zip(pool, docs):
            tf: dict[str, int] = {}
            for t in d:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            for t in q:
                if t in tf:
                    idf = math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
                    s += (tf[t] / (len(d) + 1)) * idf
            if s > 0:
                scored.append((s * item.weight, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [i for _, i in scored[:k]]

    def stats(self) -> dict:
        by_kind: dict[str, int] = {}
        for i in self.items:
            by_kind[i.kind] = by_kind.get(i.kind, 0) + 1
        return {"total": len(self.items), "by_kind": by_kind, "path": str(self.path),
                "max_items": self.max_items, "evicted": self.evicted,
                "size_bytes": self.path.stat().st_size if self.path.exists() else 0,
                "last_write": self.last_write_ts}

    def recent(self, n: int = 10) -> list[MemoryItem]:
        return self.items[-n:]
