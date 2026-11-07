"""Account TP/SL - the fourth of the four documented Bitunix TP/SL methods.

Per the official academy article "Bitunix Futures: 4 TP/SL Methods Guide":

    "Account Take-Profit & Stop-Loss | Ideal for Unified Risk Management
     Across Multiple Positions"
    "Instead of monitoring the profit and loss of individual assets, the
     system automatically closes all futures positions once the account's
     overall profit or loss reaches the predefined threshold."

    "Account TP/SL | Whole futures account | All futures positions close"

WHY THIS IS SOFTWARE, NOT AN API CALL
-------------------------------------
Bitunix exposes no REST or WebSocket endpoint for account-level TP/SL. The
official API docs cover exactly two TP/SL families:

    /api/v1/futures/tpsl/position/place_order   -> Position TP/SL (whole position)
    /api/v1/futures/tpsl/place_order            -> standalone TP/SL (tpQty/slQty,
                                                   i.e. the Partial method)

There is no account-scoped endpoint. So this method is implemented in the
engine's management loop: it sums the unrealized PnL of every open position
and, when the total crosses a configured threshold, closes them all.

THE HONEST LIMITATION
---------------------
A software stop only fires while the manage loop is running. If the process is
down, or the loop is stopped, an account-level threshold is NOT enforced by the
exchange. That is a real difference from Position/Partial TP/SL, which the
exchange enforces server-side even when the engine is offline. The engine says
so in `/status` and in the startup banner rather than implying otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AccountTpSlConfig:
    """Thresholds for the Account TP/SL method. 0 disables a side."""

    tp_usdt: float = 0.0
    sl_usdt: float = 0.0
    tp_pct: float = 0.0
    sl_pct: float = 0.0

    @property
    def enabled(self) -> bool:
        return any(v > 0 for v in (self.tp_usdt, self.sl_usdt,
                                   self.tp_pct, self.sl_pct))

    def describe(self) -> str:
        if not self.enabled:
            return "account TP/SL: off"
        bits = []
        if self.tp_usdt > 0:
            bits.append(f"TP +{self.tp_usdt:g} USDT")
        if self.sl_usdt > 0:
            bits.append(f"SL -{self.sl_usdt:g} USDT")
        if self.tp_pct > 0:
            bits.append(f"TP +{self.tp_pct:g}% equity")
        if self.sl_pct > 0:
            bits.append(f"SL -{self.sl_pct:g}% equity")
        return "account TP/SL: " + ", ".join(bits)


@dataclass
class AccountTpSlDecision:
    """The outcome of one account-level check."""

    trigger: str          # "" | "tp" | "sl"
    total_pnl: float
    equity: float
    threshold: float      # the USDT threshold that was crossed (0 if pct-driven)
    reason: str = ""

    @property
    def should_close_all(self) -> bool:
        return self.trigger in ("tp", "sl")


def account_tpsl_decision(*, total_pnl: float, equity: float,
                          cfg: AccountTpSlConfig) -> AccountTpSlDecision:
    """Decide whether the whole account should be flattened.

    Both a USDT threshold and a %-of-equity threshold can be active; whichever
    is crossed first wins. The USDT check is evaluated first so the reported
    threshold is the concrete number the user configured.

    A non-positive equity disables the %-based checks (we cannot compute a
    percentage of nothing) but leaves the USDT checks working.
    """
    if not cfg.enabled:
        return AccountTpSlDecision("", total_pnl, equity, 0.0, "account TP/SL off")

    # --- take profit
    if cfg.tp_usdt > 0 and total_pnl >= cfg.tp_usdt:
        return AccountTpSlDecision(
            "tp", total_pnl, equity, cfg.tp_usdt,
            f"account TP hit: total PnL {total_pnl:+.2f} USDT >= "
            f"{cfg.tp_usdt:g} USDT - closing all positions")
    if cfg.tp_pct > 0 and equity > 0:
        limit = equity * cfg.tp_pct / 100.0
        if total_pnl >= limit:
            return AccountTpSlDecision(
                "tp", total_pnl, equity, limit,
                f"account TP hit: total PnL {total_pnl:+.2f} USDT >= "
                f"{cfg.tp_pct:g}% of equity ({limit:.2f} USDT) - closing all positions")

    # --- stop loss
    if cfg.sl_usdt > 0 and total_pnl <= -cfg.sl_usdt:
        return AccountTpSlDecision(
            "sl", total_pnl, equity, cfg.sl_usdt,
            f"account SL hit: total PnL {total_pnl:+.2f} USDT <= "
            f"-{cfg.sl_usdt:g} USDT - closing all positions")
    if cfg.sl_pct > 0 and equity > 0:
        limit = -equity * cfg.sl_pct / 100.0
        if total_pnl <= limit:
            return AccountTpSlDecision(
                "sl", total_pnl, equity, abs(limit),
                f"account SL hit: total PnL {total_pnl:+.2f} USDT <= "
                f"-{cfg.sl_pct:g}% of equity ({limit:.2f} USDT) - closing all positions")

    return AccountTpSlDecision("", total_pnl, equity, 0.0, "within account thresholds")


def total_unrealized_pnl(positions: list[dict]) -> float:
    """Sum `unrealizedPNL` across open positions.

    The exchange reports this field per position, so no price read is needed -
    which also means the account check cannot be fooled by a stale local price.
    """
    total = 0.0
    for p in positions or []:
        try:
            total += float(p.get("unrealizedPNL") or 0)
        except (TypeError, ValueError):
            continue
    return total
