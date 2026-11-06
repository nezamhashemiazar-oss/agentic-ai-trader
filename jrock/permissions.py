"""Permission model + audit log for J+Rock.

Rules:
  * Every self-edit needs permission. AUTO_ACCEPT_EDIT=true applies it and logs it;
    false returns a diff and waits for approval.
  * Whole-terminal/device access is a user-granted flag (FULL_DEVICE_ACCESS).
  * Every destructive action is logged to data/audit.jsonl, always.
"""
from __future__ import annotations

import difflib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

DESTRUCTIVE_PATTERNS = [
    r"\brm\s+-[rf]", r"\bmkfs\b", r"\bdd\s+if=", r":\(\)\s*\{", r"\bshutdown\b",
    r"\breboot\b", r"\bchmod\s+-R\s+777\s+/", r">\s*/dev/sd", r"\bgit\s+push\s+--force",
    r"\bdrop\s+database\b", r"\btruncate\s+table\b", r"\bkill\s+-9\s+1\b",
]


@dataclass
class AuditLog:
    path: Path

    def write(self, event: str, **fields) -> None:
        rec = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def tail(self, n: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out


@dataclass
class PermissionManager:
    auto_accept_edit: bool = False
    full_device_access: bool = False
    block_destructive: bool = True
    audit: AuditLog | None = None
    approver: Callable[[str], bool] | None = None
    pending: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if self.audit is None:
            self.audit = AuditLog(Path("data/audit.jsonl"))

    def is_destructive(self, command: str) -> bool:
        return any(re.search(p, command) for p in DESTRUCTIVE_PATTERNS)

    def check_shell(self, command: str) -> tuple[bool, str]:
        destructive = self.is_destructive(command)
        if destructive:
            self.audit.write("destructive_command", command=command, allowed=not self.block_destructive)
            if self.block_destructive:
                return False, "BLOCKED: destructive command (BLOCK_DESTRUCTIVE=true). Ask the user to run it manually."
        if not self.full_device_access:
            return False, ("DENIED: whole-terminal access is not granted. "
                           "Set FULL_DEVICE_ACCESS=true in .env (user permission) to enable shell execution.")
        return True, "ok"

    def diff(self, path: str, old: str, new: str) -> str:
        return "".join(difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        ))

    def request_edit(self, path: str, old: str, new: str, reason: str = "") -> tuple[bool, str]:
        d = self.diff(path, old, new)
        if self.auto_accept_edit:
            self.audit.write("self_edit_auto", path=path, reason=reason, diff=d[:4000])
            return True, d
        if self.approver is not None and self.approver(f"Apply edit to {path}?\n{d}"):
            self.audit.write("self_edit_approved", path=path, reason=reason, diff=d[:4000])
            return True, d
        self.pending.append({"path": path, "diff": d, "reason": reason})
        self.audit.write("self_edit_pending", path=path, reason=reason, diff=d[:4000])
        return False, d

    def approve_pending(self, index: int = 0) -> dict | None:
        if 0 <= index < len(self.pending):
            item = self.pending.pop(index)
            self.audit.write("self_edit_approved_late", path=item["path"])
            return item
        return None
