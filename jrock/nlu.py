"""Natural-language understanding for J+Rock.

The engine used to react only to exact command strings. A trader who typed
"show me my trading settings" got nothing, because the line matched no command
and fell through to the model, which has no access to the engine.

This module sits IN FRONT of the existing dispatch. It maps a normal sentence
onto the SAME command string the user could have typed, and hands that string
back to the existing router. Nothing here re-implements a handler: the intent
layer only decides WHICH command a sentence means.

Design rules
------------
1. **Never guess about money.** An intent that opens or closes a position, or
   changes leverage/risk, is returned as a *proposal* that the caller must
   confirm. `Intent.destructive` marks it.
2. **Ambiguity asks.** When two intents score equally, or a required argument
   (which symbol?) is missing, the result is a clarification, not a guess.
3. **Existing commands win.** `parse()` is only consulted for lines that the
   existing router would otherwise send to the model. A line that already
   matches engine grammar or a bare agent word never reaches here.
4. **Deterministic.** No model call, no network. Pure regex + scoring, so the
   behaviour is testable and cannot hallucinate a command.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------


@dataclass
class Intent:
    """A sentence resolved to a command.

    `command` is the exact string the existing router already understands, so
    acting on an intent is just `handle(intent.command)`.
    """

    name: str
    command: str
    confidence: float
    #: True when acting on this moves money or changes risk. The caller MUST
    #: get an explicit confirmation before executing it.
    destructive: bool = False
    #: Human-readable description of what will happen, shown at confirmation.
    describe: str = ""
    #: Symbols the sentence named (may be empty).
    symbols: list[str] = field(default_factory=list)
    #: Set when the sentence was understood but is missing something.
    needs: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.command) and not self.needs


@dataclass
class Clarification:
    """The sentence was understood, but acting on it would be a guess."""

    question: str
    options: list[str] = field(default_factory=list)
    #: The intent we would have run, for the caller to re-use after the answer.
    candidate: Intent | None = None


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

#: A symbol looks like BTCUSDT / AKEUSDT / ETHUSDT - 2-10 letters + USDT.
_SYMBOL_RE = re.compile(r"\b([A-Z]{2,10}USDT)\b")

#: Words that mean "do not actually do it, just show me".
_READ_HINTS = (
    "show", "list", "what", "whats", "what's", "display", "print", "tell",
    "give", "see", "view", "check", "get", "how", "report", "status",
)

#: Words that mean "actually do it".
_WRITE_HINTS = (
    "set", "change", "make", "turn", "switch", "enable", "disable", "start",
    "stop", "close", "open", "place", "buy", "sell", "cancel", "put",
)

_NUM_RE = re.compile(r"(-?\d+(?:\.\d+)?)")


def _has(text: str, *words: str) -> bool:
    """True when any word appears as a whole word in `text`."""
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def _num_after(text: str, *anchors: str) -> float | None:
    """The first number appearing after any anchor word."""
    for a in anchors:
        m = re.search(rf"\b{re.escape(a)}\b[^\d\-]*(-?\d+(?:\.\d+)?)", text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
    return None


def _first_num(text: str) -> float | None:
    """The first number anywhere in the text.

    Used as the fallback for the Persian rules: `_num_after` anchors on an
    English keyword, and a Persian sentence has none. The rule has already
    matched a Persian phrase naming the setting, so the first number in the
    sentence is the value - there is nothing else it could be.
    """
    m = _NUM_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _symbols_in(text: str) -> list[str]:
    return _SYMBOL_RE.findall(text.upper())


def _is_read(text: str) -> bool:
    """Read vs write, decided by the leading verb.

    "show me my leverage" is a read; "set leverage to 5x" is a write. The
    leading verb is what disambiguates, not the presence of a number.
    """
    head = text.strip().split()
    if not head:
        return True
    first = head[0]
    if first in _WRITE_HINTS:
        return False
    if first in _READ_HINTS:
        return True
    # "can you show me..." / "i want to see..." - look at the first few words
    for w in head[:4]:
        if w in _WRITE_HINTS:
            return False
        if w in _READ_HINTS:
            return True
    return True


# --------------------------------------------------------------------------
# Intent rules
# --------------------------------------------------------------------------
# Each rule: (name, regex, builder). The builder receives the matched text and
# returns an Intent. Rules are tried in order; the first match wins, so the
# more specific patterns must come first.

def _b_settings(t: str) -> Intent:
    return Intent("settings", "settings trade", 0.95,
                  describe="show the account trading settings")


def _b_balance(t: str) -> Intent:
    return Intent("balance", "balance", 0.95,
                  describe="show the account balance")


def _b_positions(t: str) -> Intent:
    return Intent("positions", "positions", 0.95,
                  describe="show open positions")


def _b_orders(t: str) -> Intent:
    return Intent("orders", "orders", 0.95,
                  describe="show pending orders")


def _b_pnl(t: str) -> Intent:
    return Intent("pnl", "pnl", 0.95, describe="show unrealized PnL")


def _b_pnl_strong(t: str) -> Intent:
    """"how are my trades doing" - a PnL question, not a positions listing.

    It contains "my trades", so the positions rule also matches; the higher
    confidence here makes the PnL reading win instead of asking the user to
    disambiguate a question that has an obvious answer.
    """
    return Intent("pnl", "pnl", 0.97, describe="show unrealized PnL")


def _b_status(t: str) -> Intent:
    return Intent("status", "status", 0.9, describe="show engine status")


def _b_help(t: str) -> Intent:
    return Intent("help", "help", 0.95, describe="show the command list")


def _b_symbols(t: str) -> Intent:
    syms = _symbols_in(t)
    return Intent("symbols", f"symbols {syms[0]}" if syms else "symbols", 0.85,
                  symbols=syms, describe="list tradable symbols")


def _b_price(t: str) -> Intent:
    syms = _symbols_in(t)
    return Intent("price", f"price {','.join(syms)}" if syms else "price", 0.9,
                  symbols=syms, describe="show the price")


def _b_funding(t: str) -> Intent:
    syms = _symbols_in(t)
    return Intent("funding", f"funding {','.join(syms)}" if syms else "funding",
                  0.9, symbols=syms, describe="show the funding rate")


def _b_leverage_read(t: str) -> Intent:
    syms = _symbols_in(t)
    # "set my leverage" contains "my" but is a WRITE. The leading verb decides;
    # a read builder must not outrank the setter just because of the word "my".
    conf = 0.9 if _is_read(t) else 0.3
    return Intent("leverage_read", f"leverage {','.join(syms)}" if syms else "leverage",
                  conf, symbols=syms, describe="show leverage and margin mode")


def _b_leverage_set(t: str) -> Intent:
    n = _num_after(t, "leverage", "lev", "to", "at")
    if n is None:
        n = _first_num(t)
    if n is None:
        return Intent("leverage_set", "", 0.5, needs="leverage value",
                      describe="set leverage")
    return Intent("leverage_set", f"leverage {int(n)}", 0.9, destructive=True,
                  describe=f"set leverage to {int(n)}x (applies on the next entry)")


def _b_risk_set(t: str) -> Intent:
    n = _num_after(t, "risk", "to", "at")
    if n is None:
        n = _first_num(t)
    if n is None:
        return Intent("risk_set", "", 0.5, needs="risk percentage",
                      describe="set risk per trade")
    return Intent("risk_set", f"risk {n}", 0.9, destructive=True,
                  describe=f"set risk per trade to {n}% of equity")


def _b_margin_amount_set(t: str) -> Intent:
    """\"use 10% of my balance per trade\" - set MARGIN_AMOUNT_PCT.

    The number is the share of the available balance one position may lock as
    margin, so it is a size knob, not a risk knob. It is destructive because it
    changes how much money the next entry commits.
    """
    n = _num_after(t, "margin", "amount", "pct", "percent", "to", "at", "use",
                   "trade", "risk", "size")
    if n is None:
        # Persian sentences have no English anchor for _num_after; the rule
        # already matched a phrase naming the setting, so the first number is
        # the value.
        n = _first_num(t)
    if n is None:
        return Intent("margin_amount_set", "", 0.5, needs="margin percentage",
                      describe="set the margin amount per trade")
    return Intent("margin_amount_set", f"margin amount {n}", 0.9, destructive=True,
                  describe=(f"use {n:g}% of the available balance as margin "
                            f"per trade (applies on the next entry)"))


def _b_margin_amount_read(t: str) -> Intent:
    """A question about how much of the balance a trade uses."""
    return Intent("margin_amount_read", "settings trade", 0.85,
                  describe="show the margin-amount-per-trade setting")


def _b_autotrade_on(t: str) -> Intent:
    return Intent("autotrade_on", "start trading", 0.95, destructive=True,
                  describe="turn autotrade ON - signals will be executed with real money")


def _b_autotrade_off(t: str) -> Intent:
    return Intent("autotrade_off", "stop trading", 0.95,
                  describe="turn autotrade OFF - scanning continues, no new orders")


def _b_engine_on(t: str) -> Intent:
    return Intent("engine_on", "engine on", 0.9, destructive=True,
                  describe="start the engine loop (scanner + trader)")


def _b_engine_off(t: str) -> Intent:
    return Intent("engine_off", "engine off", 0.9,
                  describe="stop the engine loop")


def _b_scan_on(t: str) -> Intent:
    return Intent("scan_on", "scan on", 0.9, describe="turn the scanner ON")


def _b_scan_off(t: str) -> Intent:
    return Intent("scan_off", "scan off", 0.9, describe="turn the scanner OFF")


def _b_report_on(t: str) -> Intent:
    return Intent("report_on", "report on", 0.9, describe="turn the periodic report ON")


def _b_report_off(t: str) -> Intent:
    return Intent("report_off", "report off", 0.9, describe="turn the periodic report OFF")


def _b_close_position(t: str) -> Intent:
    syms = _symbols_in(t)
    if not syms:
        return Intent("close_position", "", 0.6, needs="which symbol to close",
                      describe="close a position")
    return Intent("close_position", f"close position {syms[0]}", 0.9,
                  destructive=True, symbols=syms,
                  describe=f"CLOSE the {syms[0]} position at market")


def _b_close_all(t: str) -> Intent:
    return Intent("close_all", "close position", 0.85, destructive=True,
                  describe="CLOSE ALL open positions at market")


def _b_place_order(t: str) -> Intent:
    syms = _symbols_in(t)
    side = "LONG" if _has(t, "long", "buy") else "SHORT" if _has(t, "short", "sell") else ""
    if not syms or not side:
        missing = []
        if not syms:
            missing.append("which symbol")
        if not side:
            missing.append("long or short")
        return Intent("place_order", "", 0.6, needs=" and ".join(missing),
                      symbols=syms, describe="place an order")
    return Intent("place_order", f"__place__ {syms[0]} {side}", 0.85,
                  destructive=True, symbols=syms,
                  describe=f"OPEN a {side} position on {syms[0]} at market")


def _b_tpsl_read(t: str) -> Intent:
    return Intent("tpsl_read", "tpsl", 0.9, describe="show the TP/SL methods and state")


def _b_ladder_read(t: str) -> Intent:
    return Intent("ladder_read", "ladder", 0.9, describe="show the TP ladder")


def _b_margin_mode(t: str) -> Intent:
    if _has(t, "cross"):
        return Intent("margin_mode", "margin mode CROSS", 0.9, destructive=True,
                      describe="set margin mode to CROSS (applies on the next entry)")
    # "isolation" must match on its PREFIX: \bisolat\b does not match
    # "isolation", because the word boundary falls inside the word.
    if re.search(r"\bisolat", t):
        return Intent("margin_mode", "margin mode ISOLATION", 0.9, destructive=True,
                      describe="set margin mode to ISOLATION (applies on the next entry)")
    return Intent("margin_mode", "", 0.5, needs="ISOLATION or CROSS",
                  describe="set the margin mode")


def _b_position_mode(t: str) -> Intent:
    if _has(t, "hedge"):
        return Intent("position_mode", "position mode HEDGE", 0.9, destructive=True,
                      describe="set position mode to HEDGE on the exchange")
    if _has(t, "one_way", "oneway") or re.search(r"one[\s_-]?way", t):
        return Intent("position_mode", "position mode ONE_WAY", 0.9, destructive=True,
                      describe="set position mode to ONE_WAY on the exchange")
    return Intent("position_mode", "", 0.5, needs="HEDGE or ONE_WAY",
                  describe="set the position mode")


def _b_why_failed(t: str) -> Intent:
    return Intent("why_failed", "__why_failed__", 0.9,
                  describe="explain the last error")


#: Friendly names a user may use for a setting defined in this repo, mapped to
#: the canonical name. A question about a known setting is answered from the
#: repo, never by the model.
_SETTING_ALIASES: dict[str, tuple[str, ...]] = {
    "TP_SL_MIN_STOP_TICKS": ("tp_sl_min_stop_ticks", "min stop ticks",
                             "stop ticks", "min stop distance"),
    "TAKER_FEE_RATE": ("taker_fee_rate", "taker fee rate", "taker fee",
                       "fee rate", "trading fee"),
    "MARGIN_SAFETY_PCT": ("margin_safety_pct", "margin safety pct",
                          "margin safety", "safety pct", "safety buffer"),
    "MARGIN_AMOUNT_PCT": ("margin_amount_pct", "margin amount pct",
                          "margin amount", "margin per trade", "margin pct"),
}


def _b_what_is(t: str) -> Intent:
    """A question about a setting defined in THIS repo.

    The engine once answered "I have no access to the engine" for
    TP_SL_MIN_STOP_TICKS, which is defined in jrock/config.py. A question about
    a known setting is answered from the repo, never by the model.
    """
    low = t.lower()
    for canonical, aliases in _SETTING_ALIASES.items():
        if any(a in low for a in aliases):
            return Intent("explain_setting", f"__explain__ {canonical}", 0.95,
                          describe=f"explain {canonical}")
    return Intent("explain_setting", "", 0.4, needs="which setting",
                  describe="explain a setting")


def _b_smalltalk(t: str) -> Intent:
    """A greeting, thanks, or "are you there".

    Answered directly so a normal hello never falls through to nothing. It is
    not destructive and does not touch the engine.
    """
    return Intent("smalltalk", "__smalltalk__", 0.8,
                  describe="reply to a greeting")


#: A single word that is still a complete message (a greeting or thanks).
#: Without this, `parse`'s "a single word is not a sentence" guard dropped
#: "hi", "thanks", "thx" ... and the agent answered nothing at all.
_SMALLTALK_SINGLE = re.compile(
    r"^\s*(hi|hello|hey|yo|hiya|howdy|thanks|thank you|thx|ty|cheers|"
    r"good\s+(morning|afternoon|evening)|"
    # Persian greetings/thanks are single words too, and must get a reply
    # rather than falling through to nothing.
    r"سلام|درود|سلام\s*علیکم|چطوری|خوبی|ممنون|مرسی|صبح\s*بخیر|شب\s*بخیر|"
    r"دستت\s*درد\s*نکنه)\s*[!.?؟]*\s*$", re.I)


def _b_risk_read(t: str) -> Intent:
    """A question or concern about the risk setting.

    "risk is too high for me" is a request to SEE the setting, not to change
    it - the setter needs an explicit number.
    """
    return Intent("risk_read", "settings trade", 0.85,
                  describe="show the risk-per-trade setting")


def _b_account(t: str) -> Intent:
    """"tell me about my account" - a general account question."""
    return Intent("status", "status", 0.85, describe="show the account status")


def _b_analysis(t: str) -> Intent:
    """"what do you think about ETHUSDT" - show the price so the model can
    comment on it, rather than doing nothing."""
    syms = _symbols_in(t)
    return Intent("price", f"price {','.join(syms)}" if syms else "price", 0.8,
                  symbols=syms, describe="show the price and comment")


#: Ordered rules. Specific before general.
RULES: list[tuple[str, re.Pattern, object]] = [
    # --- questions about this repo's own settings (must beat everything)
    ("explain_setting", re.compile(
        r"\b(what|whats|what's|explain|how)\b.*\b(tp_sl_min_stop_ticks|taker_fee_rate|"
        r"margin_safety_pct)\b", re.I), _b_what_is),
    ("explain_setting", re.compile(
        r"\b(tp_sl_min_stop_ticks|taker_fee_rate|margin_safety_pct)\b.*"
        r"\b(do|does|mean|means|work|works|for)\b", re.I), _b_what_is),

    # --- why did it fail
    ("why_failed", re.compile(
        r"\b(why|what)\b.*\b(fail|failed|failing|error|rejected|reject)\b", re.I),
     _b_why_failed),
    ("why_failed", re.compile(r"\blast\s+(error|failure)\b", re.I), _b_why_failed),

    # --- help
    ("help", re.compile(r"^\s*(help|what can you do|what can i (do|type)|"
                        r"list (the )?commands?|show (me )?(the )?commands?)\s*\??\s*$", re.I),
     _b_help),

    # --- settings / configs
    ("settings", re.compile(
        r"\b(show|list|what|whats|what's|display|print|see|view|check|get|tell)\b"
        r".*\b(settings?|configs?|configuration|trading settings?|preferences?)\b", re.I),
     _b_settings),
    ("settings", re.compile(r"\b(my|the)\s+(trading\s+)?(settings?|configs?)\b", re.I),
     _b_settings),

    # --- balance / equity
    ("balance", re.compile(
        r"\b(show|list|what|whats|what's|display|print|see|check|get|tell|how much)\b"
        r".*\b(balance|equity|wallet|funds|money|margin balance|available)\b", re.I),
     _b_balance),
    ("balance", re.compile(r"\bhow much\b.*\b(do i have|have i got|is (in|on) my)\b", re.I),
     _b_balance),

    # --- positions
    ("positions", re.compile(
        r"\b(show|list|what|whats|what's|display|print|see|view|check|get|any)\b"
        r".*\b(positions?|open positions?|holdings?)\b", re.I), _b_positions),
    ("positions", re.compile(r"\b(am i|are we)\s+(in|holding)\b", re.I), _b_positions),

    # --- orders
    ("orders", re.compile(
        r"\b(show|list|what|whats|what's|display|print|see|view|check|get|any)\b"
        r".*\b(orders?|open orders?|pending orders?)\b", re.I), _b_orders),

    # --- pnl
    ("pnl", re.compile(
        r"\b(show|list|what|whats|what's|display|print|see|check|get|tell)\b"
        r".*\b(pnl|p&l|profit|loss|unrealized|unrealised)\b", re.I), _b_pnl),

    # --- status
    ("status", re.compile(
        r"\b(show|what|whats|what's|display|print|see|check|get|tell)\b"
        r".*\b(status|state|health|how('s| is) (it|the engine) (going|doing))\b", re.I),
     _b_status),

    # --- close (before place, so "close my X position" is not read as an order)
    ("close_all", re.compile(
        r"\b(close|exit|flatten|kill)\b.*\b(all|every|everything)\b.*\b(positions?|trades?)\b",
        re.I), _b_close_all),
    ("close_position", re.compile(
        r"\b(close|exit|flatten|kill|get (me )?out of)\b.*\b(position|trade|long|short)\b",
        re.I), _b_close_position),
    ("close_position", re.compile(
        r"\b(close|exit|flatten)\b\s+([A-Z]{2,10}USDT)\b", re.I), _b_close_position),

    # --- place an order
    ("place_order", re.compile(
        r"\b(open|place|enter|take|go)\b.*\b(long|short|buy|sell)\b", re.I), _b_place_order),
    ("place_order", re.compile(
        r"\b(buy|sell|long|short)\b\s+([A-Z]{2,10}USDT)\b", re.I), _b_place_order),
    # "open a position on BTCUSDT" - understood, but the side is missing, so
    # this must ASK rather than fall through to the model.
    ("place_order", re.compile(
        r"\b(open|place|enter|take)\b.*\b(position|trade|order)\b", re.I), _b_place_order),

    # --- leverage
    ("leverage_set", re.compile(
        r"\b(set|change|make|put|use|switch)\b.*\bleverage\b", re.I), _b_leverage_set),
    ("leverage_set", re.compile(r"\bleverage\b.*\b(to|at|=)\s*\d+", re.I), _b_leverage_set),
    ("leverage_read", re.compile(
        r"\b(show|what|whats|what's|display|print|see|check|get|tell|my)\b.*\bleverage\b",
        re.I), _b_leverage_read),

    # --- risk
    ("risk_set", re.compile(r"\b(set|change|make|put)\b.*\brisk\b", re.I), _b_risk_set),

    # --- margin amount per trade (MARGIN_AMOUNT_PCT). Must beat the generic
    # "margin mode" rule below, which would otherwise read "margin" alone.
    ("margin_amount_set", re.compile(
        r"\b(set|change|use|make|put|trade with|size)\b.*\b(margin amount|margin pct|"
        r"margin percent|margin per trade|margin percentage)\b", re.I),
     _b_margin_amount_set),
    ("margin_amount_set", re.compile(
        r"\b(margin amount|margin pct|margin percent|margin per trade|"
        r"margin percentage)\b.*\b(to|at|=)\s*\d+", re.I), _b_margin_amount_set),
    # The bare engine form: "margin amount 15" / "margin pct 20". Without this
    # the sentence fell through to the model even though the engine accepts it.
    ("margin_amount_set", re.compile(
        r"^\s*(margin amount|margin pct|margin percent|margin per trade|"
        r"margin percentage)\s+\d+(?:\.\d+)?\s*%?\s*$", re.I), _b_margin_amount_set),
    ("margin_amount_set", re.compile(
        r"\b(use|trade with|risk)\b.*\b\d+(?:\.\d+)?\s*%\s*(?:of\b)?.*\b(balance|equity|"
        r"account|per trade|each trade)\b", re.I), _b_margin_amount_set),
    ("margin_amount_read", re.compile(
        r"\b(show|what|whats|what's|display|print|see|check|get|tell|my|how much)\b"
        r".*\b(margin amount|margin pct|margin percent|margin per trade|"
        r"margin percentage)\b", re.I), _b_margin_amount_read),
    # "how much margin am i using?" / "what margin am i on?" - the natural
    # phrasing names "margin" alone, which the rule above does not match.
    ("margin_amount_read", re.compile(
        r"\b(how much|what|whats|what's|which)\b.*\bmargin\b"
        r"(?!\s*(mode|ratio|coin|call))", re.I), _b_margin_amount_read),

    # --- autotrade
    ("autotrade_on", re.compile(
        r"\b(turn|switch|put)\b.*\bautotrade\b.*\b(on|enable|enabled|start)\b", re.I),
     _b_autotrade_on),
    ("autotrade_on", re.compile(
        r"\b(turn|switch|put)\b.*\b(on|enable|enabled)\b.*\bautotrade\b", re.I),
     _b_autotrade_on),
    ("autotrade_on", re.compile(r"\b(start|enable)\b.*\b(autotrade|auto[- ]?trading|trading)\b",
                                re.I), _b_autotrade_on),
    ("autotrade_off", re.compile(
        r"\b(turn|switch|put)\b.*\bautotrade\b.*\b(off|disable|disabled|stop)\b", re.I),
     _b_autotrade_off),
    ("autotrade_off", re.compile(
        r"\b(turn|switch|put)\b.*\b(off|disable|disabled)\b.*\bautotrade\b", re.I),
     _b_autotrade_off),
    ("autotrade_off", re.compile(r"\b(stop|disable|halt)\b.*\b(autotrade|auto[- ]?trading|trading)\b",
                                 re.I), _b_autotrade_off),

    # --- engine
    ("engine_on", re.compile(r"\b(start|turn on|enable|boot)\b.*\bengine\b", re.I), _b_engine_on),
    ("engine_off", re.compile(r"\b(stop|turn off|disable|shut down|kill)\b.*\bengine\b", re.I),
     _b_engine_off),

    # --- scan
    ("scan_on", re.compile(r"\b(start|turn on|enable|resume)\b.*\b(scan|scanner|scanning)\b",
                           re.I), _b_scan_on),
    ("scan_off", re.compile(r"\b(stop|turn off|disable|pause)\b.*\b(scan|scanner|scanning)\b",
                            re.I), _b_scan_off),

    # --- report
    ("report_on", re.compile(r"\b(start|turn on|enable)\b.*\breport", re.I), _b_report_on),
    ("report_off", re.compile(r"\b(stop|turn off|disable)\b.*\breport", re.I), _b_report_off),

    # --- margin / position mode
    ("margin_mode", re.compile(r"\b(margin mode|margin)\b.*\b(isolation|cross)\b", re.I),
     _b_margin_mode),
    ("margin_mode", re.compile(r"\b(set|change|switch|use)\b.*\bmargin\b", re.I), _b_margin_mode),
    ("position_mode", re.compile(r"\bposition mode\b.*\b(hedge|one[\s_-]?way)\b", re.I),
     _b_position_mode),
    ("position_mode", re.compile(r"\b(set|change|switch)\b.*\bposition mode\b", re.I),
     _b_position_mode),

    # --- tpsl / ladder
    ("tpsl_read", re.compile(r"\b(show|what|whats|what's|my|the)\b.*\b(tp/?sl|take profit|"
                             r"stop loss)\b", re.I), _b_tpsl_read),
    ("ladder_read", re.compile(r"\b(show|what|whats|what's|my|the)\b.*\bladder\b", re.I),
     _b_ladder_read),

    # --- price / funding / symbols
    ("price", re.compile(r"\b(price|quote|how much is|whats the price of|"
                         r"what's the price of)\b", re.I), _b_price),
    ("funding", re.compile(r"\bfunding\b", re.I), _b_funding),
    ("symbols", re.compile(
        r"\b(show|list|what|whats|what's|display|print|see|check|get|tell)\b"
        r".*\b(symbols?|pairs?)\b", re.I), _b_symbols),
    ("symbols", re.compile(r"\bwhat can i trade\b", re.I), _b_symbols),

    # --- a bare mention of a setting/symbols, even without a read verb
    #     ("settings look wrong", "symbols are not working")
    ("settings", re.compile(r"\bsettings?\b", re.I), _b_settings),
    ("symbols", re.compile(r"\bsymbols?\b", re.I), _b_symbols),

    # --- risk, as a question or a concern (the setter needs a number)
    ("risk_read", re.compile(
        r"\brisk\b.*\b(too (high|low|much|big)|is|are|what|whats|what's|"
        r"show|current|setting|level)\b", re.I), _b_risk_read),
    ("risk_read", re.compile(
        r"\b(what|whats|what's|show|my|the)\b.*\brisk\b", re.I), _b_risk_read),

    # --- leverage, as a question or a concern
    ("leverage_read", re.compile(
        r"\bleverage\b.*\b(too (high|low|much|big)|is|are|what|whats|what's|"
        r"show|current|setting|level|should)\b", re.I), _b_leverage_read),

    # --- a general account question
    ("status", re.compile(
        r"\b(tell me about|how('s| is)|what about)\b.*\b(account|portfolio)\b",
        re.I), _b_account),

    # --- "what do you think about X" - show the price so the model can comment
    ("analysis", re.compile(
        r"\b(what do you think|your (view|take|opinion)|thoughts?|analysis|"
        r"outlook)\b.*\b([A-Z]{2,10}USDT)\b", re.I), _b_analysis),

    # --- small talk: a greeting must never "do nothing"
    ("smalltalk", re.compile(
        r"^\s*(hi|hello|hey|yo|hiya|howdy|good\s+(morning|afternoon|evening)|"
        r"thanks|thank you|thx|ty|cheers|are you there|you there|"
        r"what'?s up|what'?s going on|how are you)\b", re.I), _b_smalltalk),

    # --- help, in the many ways a person asks for it
    ("help", re.compile(
        r"\b(help me out|i need help|need help|i'?m stuck|"
        r"what can you do( for me)?|how can you help( me)?|"
        r"what are you able to do|what do you do)\b", re.I), _b_help),

    # --- status, phrased as a question
    ("status", re.compile(
        r"\b(is|are)\b.*\b(engine|bot|it|system)\b.*"
        r"\b(running|on|up|alive|working|ok)\b", re.I), _b_status),
    ("status", re.compile(
        r"\b(status|state|health)\b.*\b(account|engine|bot|system|everything)\b",
        re.I), _b_status),
    ("status", re.compile(
        r"\b(account|engine|bot|system)\b.*\b(status|state|health)\b", re.I),
     _b_status),

    # --- balance, in the many ways a person asks
    ("balance", re.compile(
        r"\b(buying power|how much can i (trade|spend|use)|"
        r"available (balance|funds|margin)|account balance|my funds)\b",
        re.I), _b_balance),

    # --- positions / trades
    # A close/exit verb must NOT be read as a read-only positions query, or
    # "close my position" would be answered with a listing instead of asking
    # which symbol to close.
    ("positions", re.compile(
        r"^(?!.*\b(close|exit|flatten|kill|get (me )?out of)\b)"
        r".*\b(open trades?|my trades?|my positions?|current trades?|"
        r"open positions?|holdings?|what am i holding)\b", re.I), _b_positions),

    # --- pnl, phrased as a question
    ("pnl", re.compile(
        r"\b(how much (have i|did i|am i) (made|up|down|profit|loss)|"
        r"am i (up|down|in profit|in loss)|my (profit|loss|pnl)|"
        r"how are my trades doing|how am i doing)\b", re.I), _b_pnl_strong),

    # --- autotrade off, in the many ways a person says it
    ("autotrade_off", re.compile(
        r"\b(turn off|switch off|shut down|shut off|stop|pause|halt|kill)\b.*"
        r"\b(bot|trading|autotrade|auto[- ]?trading)\b", re.I), _b_autotrade_off),
    ("autotrade_off", re.compile(
        r"\b(pause|stop|halt|kill)\b.*\b(everything|all|it)\b", re.I),
     _b_autotrade_off),

    # --- autotrade on
    ("autotrade_on", re.compile(
        r"\b(resume|restart|start|turn on|switch on|enable)\b.*"
        r"\b(bot|trading|autotrade|auto[- ]?trading)\b", re.I), _b_autotrade_on),

    # --- explain a setting, phrased as a question
    ("explain_setting", re.compile(
        r"\b(explain|what is|what'?s|how does|tell me about|what does)\b.*"
        r"\b(taker fee|fee rate|trading fee|margin safety|safety pct|"
        r"min stop ticks|stop ticks|min stop distance)\b", re.I), _b_what_is),

    # --- Persian (Farsi) conversational forms.
    #
    # The user writes in Persian as often as English, and none of the rules
    # above match it - so every Persian sentence fell through to the model,
    # which has no engine access and answered in generic prose. That is a large
    # part of why the agent felt like it could not be talked to. These rules
    # map the common phrasings onto the SAME commands the English rules use;
    # they add no new handlers.
    #
    # Persian is written with ZWNJ (\u200c) inside many compound words, so the
    # patterns allow it explicitly rather than assuming a space.
    ("margin_amount_read", re.compile(
        r"(مارجین|مارجینم|حاشیه\s*نقد|حاشیه\u200cی\s*نقد|مارجین\s*هر\s*معامله)"
        r".*(چقدر|چند|چیه|چیست|بگو|نشون|نمایش|درصد)"
        r"|(چقدر|چند)\s*(مارجین|حاشیه\s*نقد)", re.I), _b_margin_amount_read),
    ("margin_amount_set", re.compile(
        r"(مارجین|حاشیه\s*نقد|مارجین\s*هر\s*معامله).*"
        r"(\d+(?:\.\d+)?)\s*(درصد|٪|%)?", re.I), _b_margin_amount_set),
    ("balance", re.compile(
        r"(موجودی|بالانس|سرمایه|مانده\s*حساب|چقدر\s*پول)"
        r".*(چقدر|چند|چیه|چیست|بگو|نشون|نمایش|دارم|هست)"
        r"|(چقدر|چند)\s*(پول|موجودی|بالانس)\s*(دارم|هست|داری)", re.I), _b_balance),
    ("positions", re.compile(
        r"^(?!.*(ببند|بستن|خروج|کلوز))"
        r".*(پوزیشن|پوزیشن\u200cها|معامله|معامله\u200cها|پوزیشن\u200cهام)"
        r".*(چیه|چیست|بگو|نشون|نمایش|دارم|هست|چطور|چطوره|وضعیت)"
        r"|(پوزیشن|معامله)\s*(هام|هایم|ها)\s*(چیه|چطوره|چطور)", re.I), _b_positions),
    ("close_position", re.compile(
        r"(ببند|بستن|کلوز|خروج|بسته)\s*(کن|کنید)?.*"
        r"(پوزیشن|معامله|پوزیشنمو|پوزیشنمو|همه)"
        # "پوزیشنم رو ببند" - the verb comes LAST in Persian, so the rule must
        # also match with the noun first. Without this the most natural way to
        # say "close my position" fell through to the model.
        r"|(پوزیشن|معامله|پوزیشنمو|پوزیشنمو|پوزیشنم|معاملم)\s*(رو|را)?\s*(هم)?\s*"
        r"(ببند|بستن|کلوز|خروج|بسته)", re.I), _b_close_position),
    ("close_all", re.compile(
        r"(همه|تمام|کل)\s*(ی|ی\s*)?(پوزیشن|معامله|پوزیشن\u200cها)"
        r".*(ببند|بستن|کلوز|خروج)", re.I), _b_close_all),
    ("pnl", re.compile(
        r"(سود|ضرر|سودم|ضررم|پی\s*ال|پی\u200cال|پروفیت|لاس)"
        r".*(چقدر|چند|چیه|چیست|بگو|نشون|نمایش|دارم|هست|کردم)"
        r"|(چقدر|چند)\s*(سود|ضرر)\s*(کردم|دارم|هست)", re.I), _b_pnl_strong),
    ("status", re.compile(
        r"(وضعیت|حال|استاتوس|چطوره|چطور\s*است|سالمه)"
        r".*(ربات|موتور|سیستم|حساب|همه|انجین|ایجنت)?"
        r"|(ربات|موتور|سیستم|انجین)\s*(روشن|فعال|کار\s*می\u200cکند|خاموش)", re.I),
     _b_status),
    ("leverage_read", re.compile(
        r"(اهرم|لوریج|لوریج\u200cم|اهرمم).*(چقدر|چند|چیه|چیست|بگو|نشون|نمایش)"
        r"|(چقدر|چند)\s*(اهرم|لوریج)", re.I), _b_leverage_read),
    ("leverage_set", re.compile(
        r"(اهرم|لوریج)\s*(رو|را)?\s*(\d+)\s*(برسان|کن|بذار|بزار|تنظیم)"
        r"|(اهرم|لوریج)\s*(رو|را)?\s*(\d+)", re.I), _b_leverage_set),
    ("risk_set", re.compile(
        r"(ریسک)\s*(رو|را)?\s*(\d+(?:\.\d+)?)\s*(درصد|٪|%)?"
        r"|(ریسک)\s*(رو|را)?\s*(برسان|کن|ببر|بالا|پایین)", re.I), _b_risk_set),
    ("risk_read", re.compile(
        r"(ریسک).*(چقدر|چند|چیه|چیست|بگو|نشون|نمایش|بالاست|زیاده)", re.I),
     _b_risk_read),
    ("autotrade_off", re.compile(
        r"(معامله|ربات|اتوترید|ترید)\s*(رو|را)?\s*(متوقف|خاموش|قطع|بس|نگه\s*دار)"
        r"|(متوقف|خاموش|قطع)\s*(کن)?\s*(معامله|ربات|اتوترید|ترید)", re.I),
     _b_autotrade_off),
    ("autotrade_on", re.compile(
        r"(معامله|ربات|اتوترید|ترید)\s*(رو|را)?\s*(روشن|شروع|فعال|استارت)"
        r"|(شروع|روشن|فعال)\s*(کن)?\s*(معامله|ربات|اتوترید|ترید)", re.I),
     _b_autotrade_on),
    ("smalltalk", re.compile(
        r"^\s*(سلام|درود|سلام\s*علیکم|چطوری|خوبی|ممنون|مرسی|دستت\s*درد\s*نکنه|"
        r"صبح\s*بخیر|شب\s*بخیر|هستی\s*\?*)\s*[!.؟?]*\s*$", re.I), _b_smalltalk),
    ("help", re.compile(
        r"(چه\s*کار|چیکار|چی\s*می\u200cتونی|کمک|راهنما|چطور\s*می\u200cتونی)"
        r".*(کنی|بکنی|داری|هستی)?", re.I), _b_help),
]


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def parse(text: str) -> Intent | Clarification | None:
    """Resolve a sentence to an Intent, a Clarification, or None.

    None means "not understood" - the caller should fall through to the model,
    exactly as before. This function never raises.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    # A single word is not a sentence; the existing bare-word table owns those.
    # EXCEPT a greeting/thanks, which must still get a reply rather than
    # falling through to nothing.
    if len(raw.split()) < 2 and not _SMALLTALK_SINGLE.match(raw):
        return None

    matches: list[Intent] = []
    for _name, rx, builder in RULES:
        if rx.search(raw):
            try:
                matches.append(builder(raw))
            except Exception:  # noqa: BLE001 - a bad rule must not break routing
                continue
    if not matches:
        return None

    # Highest confidence wins; ties are ambiguous and must ask.
    matches.sort(key=lambda i: i.confidence, reverse=True)
    best = matches[0]
    if len(matches) > 1 and abs(matches[1].confidence - best.confidence) < 1e-9:
        # Two different intents, same confidence -> ask rather than guess.
        if matches[1].name != best.name:
            return Clarification(
                question=(f"I read that as two things: {best.describe} or "
                          f"{matches[1].describe}. Which one?"),
                options=[best.describe, matches[1].describe],
                candidate=best)

    if best.needs:
        return Clarification(
            question=f"I can do that, but I need to know {best.needs}.",
            options=_options_for(best),
            candidate=best)
    return best


def _options_for(intent: Intent) -> list[str]:
    """Concrete follow-up options for a clarification."""
    if intent.name == "close_position":
        return ["close BTCUSDT", "close AKEUSDT", "close all positions"]
    if intent.name == "place_order":
        return ["open long BTCUSDT", "open short BTCUSDT", "never mind"]
    if intent.name == "leverage_set":
        return ["set leverage to 5x", "set leverage to 10x", "never mind"]
    if intent.name == "risk_set":
        return ["set risk to 1%", "set risk to 2%", "never mind"]
    if intent.name == "margin_amount_set":
        return ["use 10% of my balance per trade", "use 20% of my balance per trade",
                "never mind"]
    if intent.name == "margin_mode":
        return ["set margin mode to ISOLATION", "set margin mode to CROSS", "never mind"]
    if intent.name == "position_mode":
        return ["set position mode to HEDGE", "set position mode to ONE_WAY", "never mind"]
    return ["never mind"]


# --------------------------------------------------------------------------
# Confirmation
# --------------------------------------------------------------------------

#: Words that confirm a pending destructive action.
CONFIRM_WORDS = frozenset({"yes", "y", "yeah", "yep", "ok", "okay", "confirm",
                           "confirmed", "do it", "go ahead", "proceed", "sure",
                           "yes please", "yep do it", "affirmative"})

#: Words that cancel a pending destructive action.
CANCEL_WORDS = frozenset({"no", "n", "nope", "cancel", "stop", "abort", "never mind",
                          "nevermind", "forget it", "dont", "don't", "negative"})


def is_confirm(text: str) -> bool:
    return (text or "").strip().lower().rstrip("!.") in CONFIRM_WORDS


def is_cancel(text: str) -> bool:
    return (text or "").strip().lower().rstrip("!.") in CANCEL_WORDS


def confirmation_prompt(intent: Intent) -> str:
    """The exact text shown before a destructive action runs."""
    return (f"About to: {intent.describe}\n"
            f"  command: {intent.command}\n"
            f"This uses real money. Reply 'yes' to confirm, or 'no' to cancel.")
