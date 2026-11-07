"""J+Rock web dashboard: a browser controller in place of the CLI.

    python -m jrock.web                      # http://127.0.0.1:8080/?token=...
    WEB_PORT=8090 WEB_TOKEN=secret python -m jrock.web

Every line the dashboard sends goes through the same JRockAgent.handle() the
CLI uses, so routing, yes/no confirmations and permissions are unchanged.

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

from aiohttp import web

from .agent import JRockAgent
from .config import load_settings

log = logging.getLogger("jrock.web")

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
MAX_BODY_BYTES = 64 * 1024

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>J+Rock dashboard</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
header{padding:12px 16px;background:#171a21;font-weight:600}
main{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:12px}
@media (max-width:800px){main{grid-template-columns:1fr}}
section{background:#171a21;border-radius:8px;padding:12px}
h2{font-size:14px;margin:0 0 8px;color:#9aa4b2}
pre{white-space:pre-wrap;word-break:break-word;margin:0;font-size:12px;max-height:60vh;overflow:auto}
button{background:#2a3140;color:#e6e6e6;border:0;border-radius:6px;padding:8px 10px;margin:3px;font-size:13px}
input{width:68%;padding:8px;border-radius:6px;border:1px solid #2a3140;background:#0f1115;color:#e6e6e6}
.wide{grid-column:1/-1}
</style>
</head>
<body>
<header>J+Rock dashboard</header>
<main>
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
const out = document.getElementById("out");
const QUICK = ["status", "balance", "positions", "orders", "pnl", "price",
               "funding", "leverage", "settings", "symbols", "help"];

async function run(line) {
  out.textContent = "> " + line + "\\n...";
  try {
    const r = await fetch("/api/command", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Auth-Token": token},
      body: JSON.stringify({line: line})
    });
    const j = await r.json();
    out.textContent = r.ok ? ("> " + line + "\\n" + j.output)
                           : ("HTTP " + r.status + ": " + (j.error || "request failed"));
  } catch (e) {
    out.textContent = "Request failed: " + e;
  }
}

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
    return web.Response(text=INDEX_HTML, content_type="text/html")


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
