"""Account balance: fetch, normalise, and render as a real table.

The Bitunix `GET /api/v1/futures/account` endpoint returns a LIST of margin
coins (see docs/bitunix/get_single_account.md):

    {"code":0,"data":[{"marginCoin":"USDT","available":"1000","frozen":"0",
      "margin":"10","transfer":"1000","positionMode":"HEDGE",
      "crossUnrealizedPNL":"2","isolationUnrealizedPNL":"0","bonus":"0"}]}

Every numeric field is a STRING on the wire. We parse defensively: a missing or
malformed field becomes 0.0 rather than crashing the CLI, but we never invent a
number that the exchange did not send.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Fields the exchange documents. Anything else is ignored.
NUMERIC_FIELDS = ("available", "frozen", "margin", "transfer",
                  "crossUnrealizedPNL", "isolationUnrealizedPNL", "bonus")


def _f(value: Any) -> float:
    """Parse an exchange numeric string. Never raises."""
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class MarginAccount:
    margin_coin: str = "USDT"
    available: float = 0.0
    frozen: float = 0.0
    margin: float = 0.0
    transfer: float = 0.0
    position_mode: str = ""
    cross_unrealized: float = 0.0
    isolation_unrealized: float = 0.0
    bonus: float = 0.0

    @property
    def unrealized(self) -> float:
        return self.cross_unrealized + self.isolation_unrealized

    @property
    def equity(self) -> float:
        """Total account equity.

        available + frozen + margin + unrealized. `available` already excludes
        margin locked by positions, so adding `margin` back gives the whole
        account value rather than only the free part.
        """
        return self.available + self.frozen + self.margin + self.unrealized

    @classmethod
    def from_api(cls, row: dict) -> "MarginAccount":
        return cls(
            margin_coin=str(row.get("marginCoin") or "USDT"),
            available=_f(row.get("available")),
            frozen=_f(row.get("frozen")),
            margin=_f(row.get("margin")),
            transfer=_f(row.get("transfer")),
            position_mode=str(row.get("positionMode") or ""),
            cross_unrealized=_f(row.get("crossUnrealizedPNL")),
            isolation_unrealized=_f(row.get("isolationUnrealizedPNL")),
            bonus=_f(row.get("bonus")),
        )


@dataclass
class BalanceReport:
    accounts: list[MarginAccount] = field(default_factory=list)
    is_demo: bool = False
    base_url: str = ""

    @property
    def total_equity(self) -> float:
        return sum(a.equity for a in self.accounts)

    @property
    def total_unrealized(self) -> float:
        return sum(a.unrealized for a in self.accounts)


def parse_accounts(data: Any) -> list[MarginAccount]:
    """Normalise the endpoint's `data` into MarginAccount rows.

    The endpoint returns a list, but a single dict is tolerated because some
    gateway versions wrap it.
    """
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    return [MarginAccount.from_api(r) for r in data if isinstance(r, dict)]


def is_demo_url(base_url: str) -> bool:
    """True when the configured base URL points at Bitunix demo trading."""
    return "demo" in (base_url or "").lower()


def render_balance(report: BalanceReport) -> str:
    """Render a fixed-width table. No colour, no ANSI - safe in any terminal."""
    if not report.accounts:
        return ("No account data returned by the exchange.\n"
                "The API answered but `data` was empty - check that the key has "
                "futures permissions and that marginCoin=USDT is correct.")

    env = "DEMO" if report.is_demo else "LIVE"
    lines = [f"=== account balance ({env}) ===",
             f"endpoint: {report.base_url}", ""]

    headers = ("asset", "available", "frozen", "margin", "unrealized", "equity")
    rows = []
    for a in report.accounts:
        rows.append((a.margin_coin,
                     f"{a.available:.4f}",
                     f"{a.frozen:.4f}",
                     f"{a.margin:.4f}",
                     f"{a.unrealized:+.4f}",
                     f"{a.equity:.4f}"))

    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(len(headers))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    lines.append(fmt.format(*headers))
    lines.append("  ".join("-" * w for w in widths))
    for r in rows:
        lines.append(fmt.format(*r))

    if len(report.accounts) > 1:
        lines.append("  ".join("-" * w for w in widths))
        lines.append(fmt.format("TOTAL", "", "", "",
                                f"{report.total_unrealized:+.4f}",
                                f"{report.total_equity:.4f}"))

    lines.append("")
    for a in report.accounts:
        lines.append(f"{a.margin_coin}: position mode={a.position_mode or '?'} "
                     f"transferable={a.transfer:.4f} bonus={a.bonus:.4f}")
    lines.append("")
    lines.append("equity = available + frozen + margin + unrealized")
    return "\n".join(lines)


def missing_keys_message() -> str:
    """The exact message shown when Bitunix credentials are absent."""
    return ("Bitunix API keys are not set - cannot read the account.\n"
            "\n"
            "Set these in .env:\n"
            "  BITUNIX_API_KEY=...\n"
            "  BITUNIX_API_SECRET=...\n"
            "\n"
            "Optional:\n"
            "  BITUNIX_BASE_URL=https://fapi.bitunix.com   (live)\n"
            "  BITUNIX_BASE_URL=https://demo-fapi.bitunix.com   (demo)\n"
            "\n"
            "Then run: balance")
