"""J+Rock web dashboard: a browser controller in place of the CLI.

    python -m jrock.web                      # http://127.0.0.1:8080/?token=...
    WEB_PORT=8090 WEB_TOKEN=secret python -m jrock.web

Every line the dashboard sends goes through the same JRockAgent.handle() the
CLI uses, so routing, yes/no confirmations and permissions are unchanged.

The dashboard is a 1:1 view of the engine, not a subset of it:

  * ``/api/state``      a structured snapshot (engine state, equity, positions,
                        PnL, open orders, the read-only settings) refreshed by
                        the page every ``REPORT_INTERVAL_SEC`` seconds, with a
                        visible last-updated indicator.
  * ``/api/command``    the same free-text/command channel the CLI has.
  * ``/api/autotrade``  the autotrade toggle, with the engine's own safety
                        confirmations (an explicit ``confirm`` token, and the
                        restart warning when autotrade was on before a crash).
  * ``/api/settings``   every engine setting that used to be CLI/chat-only,
                        written through ``engine.command()`` so validation and
                        persistence are identical to the CLI.
  * ``/api/toggle``     the boolean engine toggles (scan / report / reversal /
                        mid management).

Security: binds to 127.0.0.1 by default and requires a token on every API
call (header X-Auth-Token). Without WEB_TOKEN a random token is generated and
printed at startup. Do not bind to a public interface without a reverse proxy
and TLS.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import secrets
import socket
import sys
import time

from aiohttp import web

from .agent import JRockAgent
from .config import load_settings

log = logging.getLogger("jrock.web")

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
MAX_BODY_BYTES = 64 * 1024

#: How often the page re-reads /api/state. The engine's own report loop uses
#: the same default (EngineState.report_interval), so the dashboard and the
#: engine agree on what "a report every 30 seconds" means.
REPORT_INTERVAL_SEC = 30

#: Settings the dashboard may write, mapped to the exact engine command
#: template. The value is substituted with ``{v}``. Every one of these is a
#: command the CLI already accepts, so validation, clamping and persistence are
#: the engine's, not a second implementation that can drift.
SETTING_COMMANDS: dict[str, str] = {
    "margin_amount_pct": "margin amount {v}",
    "leverage": "leverage {v}",
    "risk_pct": "risk {v}",
    "max_open_positions": "max open positions {v}",
    "partial_close_pct": "partial close pct {v}",
    "partial_at_roi_pct": "partial at roi {v}",
    "breakeven_pct": "breakeven threshold pct {v}",
    "trailing_trigger_roi_pct": "trailing trigger stop ROI pct {v}",
    "liq_sl_distance_pct": "liq sl distance pct {v}",
    "min_agreeing": "min agreeing strategies {v}",
    "min_confidence": "min confidence {v}",
    "tf_min_confidence": "tf min confidence {v}",
    "scan_interval": "scans interval sec {v}",
    "report_interval": "report interval sec {v}",
    "reversal_confidence": "reversal_confidence={v}",
    "account_tp_usdt": "account tp usdt {v}",
    "account_sl_usdt": "account sl usdt {v}",
    "account_tp_pct": "account tp pct {v}",
    "account_sl_pct": "account sl pct {v}",
    "margin_mode": "margin mode {v}",
    "position_mode": "position mode {v}",
    "tpsl_method": "tpsl method {v}",
    "symbols": "symbols {v}",
    "timeframes": "timeframes {v}",
    "tp_ladder": "ladder set {v}",
    "tp_ladder_breakeven_after": "ladder breakeven {v}",
    "tp_ladder_runner": "ladder runner {v}",
    "tp_ladder_trigger_basis": "ladder basis {v}",
}

#: Commands that need a symbol as well as a value, so the dashboard can offer
#: them without inventing a second argument grammar. ``{s}`` is the symbol and
#: ``{v}`` the amount.
SYMBOL_SETTING_COMMANDS: dict[str, str] = {
    "margin_add": "margin add {s} {v}",
    "margin_reduce": "margin reduce {s} {v}",
}

#: Read-only engine/agent commands surfaced as dashboard readouts. Each is a
#: command the CLI accepts verbatim - including the bare ``margin amount`` read.
READOUT_COMMANDS: dict[str, str] = {
    "status": "status",
    "settings": "settings trade",
    "margin_amount": "margin amount",
    "ladder": "ladder",
    "tpsl": "tpsl",
    "pnl": "pnl",
    "trades_history": "trades_history",
    "tpsl_history": "tpsl_history",
}

#: Boolean engine toggles. The value is the (on_command, off_command) pair; the
#: endpoint picks one from the CURRENT state, so the button really flips.
TOGGLE_COMMANDS: dict[str, tuple[str, str]] = {
    "scan_on": ("scan on", "scan off"),
    "report_on": ("report on", "report off"),
    "reversal": ("reversal=true", "reversal=false"),
    "mid_management": ("mid management", "mid management"),
}

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>J+Rock dashboard</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
header{padding:12px 16px;background:#171a21;font-weight:600;display:flex;
       justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
main{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:12px}
@media (max-width:800px){main{grid-template-columns:1fr}}
section{background:#171a21;border-radius:8px;padding:12px}
h2{font-size:14px;margin:0 0 8px;color:#9aa4b2}
pre{white-space:pre-wrap;word-break:break-word;margin:0;font-size:12px;max-height:60vh;overflow:auto}
button{background:#2a3140;color:#e6e6e6;border:0;border-radius:6px;padding:8px 10px;margin:3px;font-size:13px}
button:hover{background:#39424f}
input,select{width:68%;padding:8px;border-radius:6px;border:1px solid #2a3140;background:#0f1115;color:#e6e6e6}
select{width:auto}
.wide{grid-column:1/-1}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:4px 0}
.row label{font-size:12px;color:#9aa4b2;min-width:170px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;font-size:12px}
.kv b{color:#9aa4b2;font-weight:500}
.pill{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.on{background:#1d3b2a;color:#5fd68a}
.off{background:#3b1d1d;color:#e07a7a}
.warn{background:#3b331d;color:#e0c97a}
#updated{font-size:12px;color:#9aa4b2}
#updated.stale{color:#e0c97a}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:4px 6px;border-bottom:1px solid #232833}
th{color:#9aa4b2;font-weight:500}
.pos{color:#5fd68a}.neg{color:#e07a7a}
</style>
</head>
<body>
<header>
  <span>J+Rock dashboard</span>
  <span id="updated">last updated: never</span>
</header>
<main>
<section>
<h2>Engine</h2>
<div id="engine" class="kv"></div>
<div class="row" style="margin-top:8px">
  <button id="autotrade">autotrade</button>
  <button id="enginebtn">engine</button>
  <button id="scanbtn">scan</button>
  <button id="reportbtn">report</button>
</div>
</section>
<section>
<h2>Account</h2>
<div id="account" class="kv"></div>
</section>
<section class="wide">
<h2>Open positions</h2>
<div id="positions"></div>
</section>
<section>
<h2>Open orders</h2>
<div id="orders"></div>
</section>
<section>
<h2>Strategies &amp; permissions</h2>
<div id="strategies" class="kv"></div>
</section>
<section>
<h2>Readouts</h2>
<div id="readouts"></div>
</section>
<section class="wide">
<h2>Settings</h2>
<div id="settings"></div>
</section>
<section>
<h2>Quick actions (read-only)</h2>
<div id="quick"></div>
</section>
<section>
<h2>Command or question</h2>
<form id="form">
<input id="line" placeholder="e.g. balance, close my AKEUSDT position, why did the last order fail" autocomplete="off">
<button type="submit">Run</button>
</form>
</section>
<section class="wide">
<h2>Output</h2>
<pre id="out">Ready.</pre>
</section>
</main>
<script>
const token = new URLSearchParams(location.search).get("token") || "";
const REPORT_INTERVAL_SEC = __REPORT_INTERVAL_SEC__;
const out = document.getElementById("out");
const QUICK = ["status", "balance", "positions", "orders", "pnl", "price",
               "funding", "leverage", "settings", "symbols", "help"];

function hdr() { return {"Content-Type": "application/json", "X-Auth-Token": token}; }

async function api(path, opts) {
  const r = await fetch(path, Object.assign({headers: hdr()}, opts || {}));
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error("HTTP " + r.status + ": " + (j.error || "request failed"));
  return j;
}

async function run(line) {
  out.textContent = "> " + line + "\\n...";
  try {
    const j = await api("/api/command", {method: "POST", body: JSON.stringify({line: line})});
    out.textContent = "> " + line + "\\n" + j.output;
  } catch (e) {
    out.textContent = String(e);
  }
  refresh();
}

function pill(v, onText, offText) {
  return '<span class="pill ' + (v ? "on" : "off") + '">' +
         (v ? (onText || "ON") : (offText || "OFF")) + "</span>";
}

function kv(el, pairs) {
  el.innerHTML = pairs.map(([k, v]) => "<b>" + k + "</b><span>" + v + "</span>").join("");
}

function money(v) {
  const n = Number(v || 0);
  const cls = n > 0 ? "pos" : (n < 0 ? "neg" : "");
  return '<span class="' + cls + '">' + n.toFixed(4) + "</span>";
}

function esc(s) {
  return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
                                .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function table(rows, cols) {
  if (!rows || !rows.length) return "<pre>none</pre>";
  let h = "<table><thead><tr>" + cols.map(c => "<th>" + c[0] + "</th>").join("") + "</tr></thead><tbody>";
  for (const r of rows) {
    h += "<tr>" + cols.map(c => "<td>" + c[1](r) + "</td>").join("") + "</tr>";
  }
  return h + "</tbody></table>";
}

let lastState = null;

function render(s) {
  lastState = s;
  const e = s.engine || {};
  kv(document.getElementById("engine"), [
    ["engine", pill(e.engine_on)],
    ["autotrade", pill(e.autotrade)],
    ["scan", pill(e.scan_on)],
    ["report", pill(e.report_on) + " every " + e.report_interval + "s"],
    ["mid management", pill(e.mid_management)],
    ["reversal", pill(e.reversal) + " conf " + e.reversal_confidence],
    ["symbols", esc((e.symbols || []).join(", "))],
    ["leverage", e.leverage + "x"],
    ["scans", e.scans + " (last " + (e.last_scan || "never") + ")"],
    ["errors", e.errors + (e.last_error ? " - " + esc(e.last_error) : "")],
  ]);

  const a = s.account || {};
  kv(document.getElementById("account"), [
    ["equity", money(a.equity)],
    ["available", money(a.available)],
    ["margin", money(a.margin)],
    ["unrealized", money(a.unrealized)],
    ["realized", money(a.realized)],
    ["open positions", a.open_positions],
    ["open orders", a.open_orders],
    ["api", a.api_ok ? '<span class="pill on">OK</span>'
                     : '<span class="pill off">FAIL</span> ' + esc(a.api_msg || "")],
  ]);

  document.getElementById("positions").innerHTML = table(s.positions, [
    ["symbol", r => esc(r.symbol)], ["side", r => esc(r.side)], ["qty", r => esc(r.qty)],
    ["entry", r => r.entry], ["mark", r => r.mark], ["liq", r => esc(r.liq)],
    ["lev", r => r.leverage + "x"], ["roi", r => r.roi + "%"],
    ["uPnL", r => money(r.unrealized)],
  ]);

  document.getElementById("orders").innerHTML = table(s.orders, [
    ["symbol", r => esc(r.symbol)], ["side", r => esc(r.side)], ["type", r => esc(r.type)],
    ["qty", r => esc(r.qty)], ["price", r => esc(r.price)], ["status", r => esc(r.status)],
  ]);

  const sg = s.strategies || {};
  const pm = s.permissions || {};
  kv(document.getElementById("strategies"), [
    ["strategies", sg.count + " registered"],
    ["by category", esc(Object.entries(sg.by_category || {})
                          .map(([k, v]) => k + ":" + v).join(", "))],
    ["auto accept edit", pill(pm.auto_accept_edit)],
    ["full device access", pill(pm.full_device_access)],
    ["block destructive", pill(pm.block_destructive)],
  ]);

  const ro = document.getElementById("readouts");
  ro.innerHTML = Object.keys(s.readouts || {}).map(k =>
    "<div class='row'><label>" + esc(k) + "</label></div><pre>" +
    esc(s.readouts[k]) + "</pre>").join("");

  const st = document.getElementById("settings");
  const mp = s.margin_plan || {};
  st.innerHTML = (mp.enabled === false ? "" :
    "<div class='row'><label>margin per position</label><b>" +
    esc(mp.pct) + "% = " + money(mp.target_usdt) + " USDT</b></div>" +
    "<div class='row'><label>margin basis</label><span>" +
    esc(mp.note) + "</span></div>") +
    (s.settings || []).map(f =>
    "<div class='row'><label>" + esc(f.label) + "</label>" +
    "<input id='set_" + f.key + "' value='" + esc(f.value) + "'>" +
    "<button data-set='" + f.key + "'>set</button></div>").join("") +
    "<div class='row'><label>tpsl method</label><select id='set_tpsl_method'>" +
    ["POSITION", "PARTIAL", "TRAILING", "ACCOUNT"].map(m =>
      "<option" + (m === e.tpsl_method ? " selected" : "") + ">" + m + "</option>").join("") +
    "</select><button data-set='tpsl_method'>set</button></div>" +
    "<div class='row'><label>margin mode</label><select id='set_margin_mode'>" +
    ["ISOLATION", "CROSS"].map(m =>
      "<option" + (m === e.margin_mode ? " selected" : "") + ">" + m + "</option>").join("") +
    "</select><button data-set='margin_mode'>set</button></div>" +
    "<div class='row'><label>position mode</label><select id='set_position_mode'>" +
    ["ONE_WAY", "HEDGE"].map(m =>
      "<option" + (m === e.position_mode ? " selected" : "") + ">" + m + "</option>").join("") +
    "</select><button data-set='position_mode'>set</button></div>" +
    "<div class='row'><label>ladder runner</label><select id='set_tp_ladder_runner'>" +
    ["trail", "close"].map(m =>
      "<option" + (m === e.tp_ladder_runner ? " selected" : "") + ">" + m + "</option>").join("") +
    "</select><button data-set='tp_ladder_runner'>set</button></div>" +
    "<div class='row'><label>ladder basis</label><select id='set_tp_ladder_trigger_basis'>" +
    ["R", "ROI", "PRICE"].map(m =>
      "<option" + (m === e.tp_ladder_trigger_basis ? " selected" : "") + ">" + m + "</option>").join("") +
    "</select><button data-set='tp_ladder_trigger_basis'>set</button></div>";

  st.querySelectorAll("button[data-set]").forEach(b => {
    b.addEventListener("click", () => {
      const key = b.getAttribute("data-set");
      setSetting(key, document.getElementById("set_" + key).value);
    });
  });

  const upd = document.getElementById("updated");
  upd.textContent = "last updated: " + s.updated_at + " (every " + REPORT_INTERVAL_SEC + "s)";
  upd.className = "";
}

async function refresh() {
  try {
    render(await api("/api/state"));
  } catch (e) {
    const upd = document.getElementById("updated");
    upd.textContent = "last updated: " + new Date().toLocaleTimeString() +
                      " - refresh failed: " + e;
    upd.className = "stale";
  }
}

async function setSetting(key, value) {
  try {
    const j = await api("/api/settings", {method: "POST",
      body: JSON.stringify({key: key, value: value})});
    out.textContent = "> set " + key + " = " + value + "\\n" + j.output;
  } catch (e) {
    out.textContent = String(e);
  }
  refresh();
}

async function toggleAutotrade() {
  const on = !(lastState && lastState.engine && lastState.engine.autotrade);
  const msg = on
    ? "Turn AUTOTRADE ON? The engine will place real orders on signals."
    : "Turn autotrade OFF? Scanning continues, no new orders.";
  if (!confirm(msg)) return;
  try {
    const j = await api("/api/autotrade", {method: "POST",
      body: JSON.stringify({on: on, confirm: true})});
    out.textContent = "> autotrade " + (on ? "on" : "off") + "\\n" + j.output;
  } catch (e) {
    out.textContent = String(e);
  }
  refresh();
}

async function toggleEngine() {
  const on = !(lastState && lastState.engine && lastState.engine.engine_on);
  if (!confirm(on ? "Start the engine loop?" : "Stop the engine loop?")) return;
  await run(on ? "engine on" : "engine off");
}

async function toggleKey(key) {
  try {
    const j = await api("/api/toggle", {method: "POST", body: JSON.stringify({key: key})});
    out.textContent = "> toggle " + key + "\\n" + j.output;
  } catch (e) {
    out.textContent = String(e);
  }
  refresh();
}

document.getElementById("autotrade").addEventListener("click", toggleAutotrade);
document.getElementById("enginebtn").addEventListener("click", toggleEngine);
document.getElementById("scanbtn").addEventListener("click", () => toggleKey("scan_on"));
document.getElementById("reportbtn").addEventListener("click", () => toggleKey("report_on"));

const quick = document.getElementById("quick");
for (const cmd of QUICK) {
  const b = document.createElement("button");
  b.textContent = cmd;
  b.addEventListener("click", () => run(cmd));
  quick.appendChild(b);
}

document.getElementById("form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const input = document.getElementById("line");
  const line = input.value.trim();
  if (line) run(line);
});

refresh();
setInterval(refresh, REPORT_INTERVAL_SEC * 1000);
</script>
</body>
</html>
"""


def _authorized(request: web.Request) -> bool:
    supplied = request.headers.get("X-Auth-Token") or request.query.get("token", "")
    expected = request.app["token"]
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


async def index(request: web.Request) -> web.Response:
    if not _authorized(request):
        return web.Response(status=401, content_type="text/plain",
                            text="Unauthorized. Open the URL printed at startup (it contains ?token=...).")
    html = INDEX_HTML.replace("__REPORT_INTERVAL_SEC__", str(REPORT_INTERVAL_SEC))
    return web.Response(text=html, content_type="text/html")


async def command(request: web.Request) -> web.Response:
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": 'expected JSON {"line": "..."}'}, status=400)
    line = body.get("line") if isinstance(body, dict) else None
    if not isinstance(line, str) or not line.strip():
        return web.json_response({"error": 'expected a non-empty "line"'}, status=400)

    agent: JRockAgent = request.app["agent"]
    # The agent is stateful and the CLI runs one command at a time; keep that.
    async with request.app["lock"]:
        try:
            output = await agent.handle(line)
        except Exception as e:  # noqa: BLE001
            output = f"ERROR: {type(e).__name__}: {e}"
    return web.json_response({"output": output})


# --------------------------------------------------------------------- state
def _f(value) -> float:
    """Parse an exchange numeric string. Never raises."""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _has_keys(agent: JRockAgent) -> bool:
    """True when Bitunix credentials are configured (same test the CLI uses)."""
    s = agent.s
    return bool(s.bitunix_api_key and s.bitunix_api_secret)


def _position_rows(positions: list[dict]) -> list[dict]:
    """Normalise open positions for the dashboard table.

    ROI is computed with the engine's own ``roi_pct`` so the dashboard and the
    engine's report cannot disagree about what a position is doing.
    """
    from .trader.risk import roi_pct

    rows = []
    for p in positions or []:
        entry = _f(p.get("avgOpenPrice"))
        mark = _f(p.get("markPrice")) or entry
        lev = int(_f(p.get("leverage")) or 1)
        side = str(p.get("side") or "")
        try:
            roi = roi_pct(entry, mark, side, lev)
        except Exception:  # noqa: BLE001
            roi = 0.0
        rows.append({
            "symbol": p.get("symbol"),
            "side": side,
            "qty": p.get("qty"),
            "entry": entry,
            "mark": mark,
            "liq": p.get("liqPrice"),
            "leverage": lev,
            "roi": round(roi, 2),
            "unrealized": _f(p.get("unrealizedPNL")),
            "margin": p.get("margin"),
            "margin_mode": p.get("marginMode"),
        })
    return rows


def _order_rows(orders: list[dict]) -> list[dict]:
    return [{
        "symbol": o.get("symbol"),
        "side": o.get("side"),
        "type": o.get("orderType"),
        "qty": o.get("qty"),
        "price": o.get("price"),
        "status": o.get("status"),
        "id": o.get("orderId"),
    } for o in (orders or [])]


def _account_summary(account_rows, positions, orders) -> dict:
    """Equity / available / margin / unrealized, from the exchange's own rows."""
    from .account import parse_accounts

    accounts = parse_accounts(account_rows)
    equity = sum(a.equity for a in accounts)
    available = sum(a.available for a in accounts)
    margin = sum(a.margin for a in accounts)
    unrealized = sum(a.unrealized for a in accounts)
    realized = 0.0
    for p in positions or []:
        realized += _f(p.get("realizedPNL"))
    return {
        "equity": round(equity, 4),
        "available": round(available, 4),
        "margin": round(margin, 4),
        "unrealized": round(unrealized, 4),
        "realized": round(realized, 4),
        "open_positions": len(positions or []),
        "open_orders": len(orders or []),
        "coins": [a.margin_coin for a in accounts],
    }


def _setting_fields(agent: JRockAgent) -> list[dict]:
    """Every engine setting the dashboard can write, with its current value."""
    s = agent.engine.state
    fields = [
        ("margin_amount_pct", "margin per trade (%)", s.margin_amount_pct),
        ("leverage", "leverage (x)", s.leverage),
        ("risk_pct", "risk per trade (%)", s.risk_pct),
        ("max_open_positions", "max open positions", s.max_open_positions),
        ("partial_close_pct", "partial close (%)", s.partial_close_pct),
        ("partial_at_roi_pct", "partial at ROI (%)", s.partial_at_roi_pct),
        ("breakeven_pct", "breakeven threshold (%)", s.breakeven_pct),
        ("trailing_trigger_roi_pct", "trailing trigger ROI (%)", s.trailing_trigger_roi_pct),
        ("liq_sl_distance_pct", "liq SL distance (%)", s.liq_sl_distance_pct),
        ("min_agreeing", "min agreeing strategies", s.min_agreeing),
        ("min_confidence", "min confidence", s.min_confidence),
        ("tf_min_confidence", "tf min confidence", s.tf_min_confidence),
        ("scan_interval", "scan interval (s)", s.scan_interval),
        ("report_interval", "report interval (s)", s.report_interval),
        ("reversal_confidence", "reversal confidence", s.reversal_confidence),
        ("account_tp_usdt", "account TP (USDT)", s.account_tp_usdt),
        ("account_sl_usdt", "account SL (USDT)", s.account_sl_usdt),
        ("account_tp_pct", "account TP (% equity)", s.account_tp_pct),
        ("account_sl_pct", "account SL (% equity)", s.account_sl_pct),
        ("symbols", "symbols", ",".join(s.symbols)),
        ("timeframes", "timeframes", ",".join(s.timeframes)),
        ("tp_ladder", "TP ladder spec", s.tp_ladder),
        ("tp_ladder_breakeven_after", "ladder breakeven after", s.tp_ladder_breakeven_after),
    ]
    return [{"key": k, "label": lab, "value": v} for k, lab, v in fields]


def _margin_plan(s, account: dict) -> dict:
    """What MARGIN_AMOUNT_PCT actually means right now, in USDT.

    The setting is a percentage, and a percentage of what is not obvious from
    the number alone - the user's complaint was that the margin on a real
    position did not match the percentage they set. Showing the derived USDT
    amount next to the percentage makes the two directly comparable, and makes
    a clamp visible instead of silent.

    ``target_usdt`` is the margin one position will aim to lock. It is derived
    from the spendable balance (``available``), which is what the engine sizes
    from - not equity, which includes unrealized PnL that cannot be spent.
    """
    pct = float(getattr(s, "margin_amount_pct", 0.0) or 0.0)
    available = float(account.get("available") or 0.0)
    equity = float(account.get("equity") or 0.0)
    target = available * pct / 100.0 if pct > 0 else 0.0
    return {
        "pct": pct,
        "basis": "available",
        "available": round(available, 4),
        "equity": round(equity, 4),
        "target_usdt": round(target, 4),
        "leverage": int(getattr(s, "leverage", 0) or 0),
        # margin * leverage = the notional the target margin controls. Shown so
        # the user can see that leverage changes the SIZE, not the margin.
        "target_notional": round(target * int(getattr(s, "leverage", 0) or 0), 4),
        "enabled": pct > 0,
        "note": ("0 disables the margin target; sizing falls back to risk_pct."
                 if pct <= 0 else
                 f"{pct:g}% of the {available:.2f} USDT available balance "
                 f"= {target:.2f} USDT margin per position at "
                 f"{int(getattr(s, 'leverage', 0) or 0)}x."),
    }


async def _readouts(agent: JRockAgent) -> dict[str, str]:
    """The read/query commands, surfaced as dashboard readouts.

    Each goes through ``agent.handle()`` - the same entry point the CLI uses -
    so the dashboard shows exactly what the CLI would print, including the bare
    ``margin amount`` read and the account-level ``settings trade``.
    """
    out: dict[str, str] = {}
    for name, cmd in READOUT_COMMANDS.items():
        try:
            out[name] = await agent.handle(cmd)
        except Exception as e:  # noqa: BLE001
            out[name] = f"ERROR: {type(e).__name__}: {e}"
    return out


def _strategy_summary() -> dict:
    """The registered strategies, by category - the engine's own registry."""
    from .trader.strategies import registry

    reg = registry()
    cats: dict[str, int] = {}
    for v in reg.values():
        cats[v["category"]] = cats.get(v["category"], 0) + 1
    return {"count": len(reg), "by_category": cats, "names": sorted(reg)}


def _permission_summary(agent: JRockAgent) -> dict:
    """The permission flags the agent enforces, as the CLI reports them."""
    p = agent.perms
    return {
        "auto_accept_edit": p.auto_accept_edit,
        "full_device_access": p.full_device_access,
        "block_destructive": p.block_destructive,
    }


async def build_state(agent: JRockAgent) -> dict:
    """Build the dashboard snapshot. Shared by /api/state and the tests."""
    s = agent.engine.state
    api_ok, api_msg = False, "not checked"
    account_rows: list = []
    positions: list = []
    orders: list = []
    if _has_keys(agent):
        try:
            api_ok, api_msg = await agent.engine.client.verify_credentials()
        except Exception as e:  # noqa: BLE001
            api_ok, api_msg = False, f"{type(e).__name__}: {e}"
        try:
            account_rows = await agent.engine.client.get_single_account() or []
        except Exception as e:  # noqa: BLE001
            api_msg = f"{api_msg}; account: {type(e).__name__}: {e}"
        try:
            positions = await agent.engine.client.get_pending_positions() or []
        except Exception as e:  # noqa: BLE001
            api_msg = f"{api_msg}; positions: {type(e).__name__}: {e}"
        try:
            orders = await agent.engine.client.get_pending_orders() or []
        except Exception as e:  # noqa: BLE001
            api_msg = f"{api_msg}; orders: {type(e).__name__}: {e}"
    else:
        api_msg = "Bitunix API keys are not set - account data unavailable."

    account = _account_summary(account_rows, positions, orders)
    account["api_ok"] = api_ok
    account["api_msg"] = api_msg

    return {
        "updated_at": time.strftime("%H:%M:%S"),
        "updated_ts": time.time(),
        "report_interval": REPORT_INTERVAL_SEC,
        "margin_plan": _margin_plan(s, account),
        "engine": {
            "engine_on": s.engine_on,
            "autotrade": s.autotrade,
            "scan_on": s.scan_on,
            "report_on": s.report_on,
            "report_interval": s.report_interval,
            "scan_interval": s.scan_interval,
            "mid_management": s.mid_management,
            "reversal": s.reversal,
            "reversal_confidence": s.reversal_confidence,
            "symbols": list(s.symbols),
            "leverage": s.leverage,
            "margin_mode": s.margin_mode,
            "position_mode": s.position_mode,
            "tpsl_method": s.tpsl_method,
            "tp_ladder_runner": s.tp_ladder_runner,
            "tp_ladder_trigger_basis": s.tp_ladder_trigger_basis,
            "scans": s.scans,
            "last_scan": (time.strftime("%H:%M:%S", time.localtime(s.last_scan_ts))
                          if s.last_scan_ts else ""),
            "errors": len(s.errors),
            "last_error": s.errors[-1] if s.errors else "",
            "autotrade_was_on": agent.engine.autotrade_was_on,
            "engine_was_on": agent.engine.engine_was_on,
        },
        "account": account,
        "positions": _position_rows(positions),
        "orders": _order_rows(orders),
        "settings": _setting_fields(agent),
        "strategies": _strategy_summary(),
        "permissions": _permission_summary(agent),
        "readouts": await _readouts(agent),
    }


async def state(request: web.Request) -> web.Response:
    """The structured snapshot the page polls every REPORT_INTERVAL_SEC."""
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    agent: JRockAgent = request.app["agent"]
    async with request.app["lock"]:
        return web.json_response(await build_state(agent))


# ------------------------------------------------------------------ autotrade
async def autotrade(request: web.Request) -> web.Response:
    """The autotrade toggle, with the engine's own safety confirmations.

    Turning autotrade ON places real orders, so it requires an explicit
    ``confirm: true`` in the body - the same "reply yes" gate the CLI and the
    NLU layer enforce. Turning it OFF never needs a confirmation.
    """
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": 'expected JSON {"on": true}'}, status=400)
    if not isinstance(body, dict) or not isinstance(body.get("on"), bool):
        return web.json_response({"error": 'expected a boolean "on"'}, status=400)

    on = body["on"]
    if on and body.get("confirm") is not True:
        return web.json_response({
            "error": "confirmation required",
            "detail": ("Turning autotrade ON lets the engine place real orders. "
                       'Resend with {"on": true, "confirm": true}.'),
        }, status=409)

    agent: JRockAgent = request.app["agent"]
    async with request.app["lock"]:
        try:
            output = await agent.engine.command("start trading" if on else "stop trading")
        except Exception as e:  # noqa: BLE001
            return web.json_response({"error": f"{type(e).__name__}: {e}"}, status=500)
        state_now = {
            "autotrade": agent.engine.state.autotrade,
            "engine_on": agent.engine.state.engine_on,
        }
    return web.json_response({"output": output, "state": state_now})


# ------------------------------------------------------------------- settings
def _fmt_value(value) -> str:
    """Render a setting value the way the CLI would type it.

    A JSON number arrives as ``15.0``; the engine's own command grammar and its
    confirmation text use ``15``. Formatting here keeps the command the
    dashboard sends byte-identical to the one a user would type, so the
    dashboard cannot produce a command the CLI never would.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


async def settings(request: web.Request) -> web.Response:
    """Write one engine setting through the engine's own command router.

    The dashboard never writes ``EngineState`` directly: it builds the exact
    command the CLI accepts and lets ``engine.command()`` validate, clamp and
    persist it. That is what keeps the dashboard 1:1 with the engine instead of
    a second, drifting implementation.
    """
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": 'expected JSON {"key": "...", "value": ...}'}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "expected a JSON object"}, status=400)

    key = body.get("key")
    value = body.get("value")
    if not isinstance(key, str) or key not in SETTING_COMMANDS:
        return web.json_response({
            "error": f"unknown setting {key!r}",
            "allowed": sorted(SETTING_COMMANDS),
        }, status=400)
    if value is None or (isinstance(value, str) and not value.strip()):
        return web.json_response({"error": "value is required"}, status=400)

    cmd = SETTING_COMMANDS[key].format(v=_fmt_value(value))
    agent: JRockAgent = request.app["agent"]
    async with request.app["lock"]:
        try:
            output = await agent.engine.command(cmd)
        except Exception as e:  # noqa: BLE001
            return web.json_response({"error": f"{type(e).__name__}: {e}"}, status=500)
    return web.json_response({"output": output, "command": cmd})


async def margin(request: web.Request) -> web.Response:
    """`margin add SYMBOL AMOUNT` / `margin reduce SYMBOL AMOUNT`.

    Isolated-margin adjustment, which the CLI documents and the client
    implements. It needs a symbol as well as an amount, so it has its own
    endpoint rather than overloading /api/settings.
    """
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response(
            {"error": 'expected JSON {"action": "add|reduce", "symbol": "...", "amount": 100}'},
            status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "expected a JSON object"}, status=400)

    action = body.get("action")
    symbol = body.get("symbol")
    amount = body.get("amount")
    if action not in ("add", "reduce"):
        return web.json_response({"error": 'action must be "add" or "reduce"'}, status=400)
    if not isinstance(symbol, str) or not symbol.strip():
        return web.json_response({"error": "symbol is required"}, status=400)
    if amount is None or (isinstance(amount, str) and not amount.strip()):
        return web.json_response({"error": "amount is required"}, status=400)

    cmd = SYMBOL_SETTING_COMMANDS[f"margin_{action}"].format(
        s=symbol.strip().upper(), v=_fmt_value(amount))
    agent: JRockAgent = request.app["agent"]
    async with request.app["lock"]:
        try:
            output = await agent.engine.command(cmd)
        except Exception as e:  # noqa: BLE001
            return web.json_response({"error": f"{type(e).__name__}: {e}"}, status=500)
    return web.json_response({"output": output, "command": cmd})


async def toggle(request: web.Request) -> web.Response:
    """Flip a boolean engine toggle (scan / report / reversal / mid management)."""
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return web.json_response({"error": 'expected JSON {"key": "..."}'}, status=400)
    key = body.get("key") if isinstance(body, dict) else None
    if not isinstance(key, str) or key not in TOGGLE_COMMANDS:
        return web.json_response({
            "error": f"unknown toggle {key!r}",
            "allowed": sorted(TOGGLE_COMMANDS),
        }, status=400)

    agent: JRockAgent = request.app["agent"]
    s = agent.engine.state
    on_cmd, off_cmd = TOGGLE_COMMANDS[key]
    current = {"scan_on": s.scan_on, "report_on": s.report_on,
               "reversal": s.reversal, "mid_management": s.mid_management}[key]
    cmd = off_cmd if current else on_cmd
    async with request.app["lock"]:
        try:
            output = await agent.engine.command(cmd)
        except Exception as e:  # noqa: BLE001
            return web.json_response({"error": f"{type(e).__name__}: {e}"}, status=500)
    return web.json_response({"output": output, "command": cmd})


def make_app(agent: JRockAgent, token: str) -> web.Application:
    app = web.Application(client_max_size=MAX_BODY_BYTES)
    app["agent"] = agent
    app["token"] = token
    app["lock"] = asyncio.Lock()

    async def on_startup(app: web.Application) -> None:
        print(await agent.startup())

    async def on_cleanup(app: web.Application) -> None:
        await agent.engine.stop()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/", index)
    app.router.add_post("/api/command", command)
    app.router.add_get("/api/state", state)
    app.router.add_post("/api/autotrade", autotrade)
    app.router.add_post("/api/settings", settings)
    app.router.add_post("/api/margin", margin)
    app.router.add_post("/api/toggle", toggle)
    return app


def port_in_use(host: str, port: int) -> bool:
    """True if we cannot bind host:port. Checked before the agent starts, so a
    busy port does not leave the trading feed running with no dashboard."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jrock-web", description="J+Rock web dashboard")
    ap.add_argument("--host", default=os.getenv("WEB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("WEB_PORT") or 8080))
    ap.add_argument("--env", help="path to .env")
    args = ap.parse_args(argv)

    if port_in_use(args.host, args.port):
        print(f"ERROR: {args.host}:{args.port} is already in use. Another dashboard "
              "may still be running, or another app holds the port. Stop it, or set "
              "WEB_PORT to a free port (e.g. WEB_PORT=8090 python -m jrock.web).",
              file=sys.stderr)
        return 1

    # Hex only: no I/l or 0/O confusion when copying from a terminal.
    token = os.getenv("WEB_TOKEN") or secrets.token_hex(16)
    if args.host not in LOOPBACK_HOSTS:
        print(f"WARNING: binding to {args.host}, which is reachable from the network. "
              "Use TLS and a reverse proxy.", file=sys.stderr)

    agent = JRockAgent(load_settings(args.env))
    print(f"J+Rock dashboard: http://{args.host}:{args.port}/?token={token}")
    web.run_app(make_app(agent, token), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
