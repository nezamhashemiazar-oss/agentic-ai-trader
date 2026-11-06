"""Persistent memory + learning loop.

Stores:
  * feedback  - user corrections/preferences, weighted, recalled by keyword
  * episode   - what happened (task, action, outcome) for experience-based improvement
  * fact      - durable project facts (symbols, settings, decisions)

Retrieval is a simple BM25-ish keyword score; no external deps.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

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


class MemoryStore:
    def __init__(self, path: Path | str = "data/memory.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items: list[MemoryItem] = []
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
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
        return item

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
        return {"total": len(self.items), "by_kind": by_kind, "path": str(self.path)}

    def recent(self, n: int = 10) -> list[MemoryItem]:
        return self.items[-n:]
