# J+Rock

An honest, relentless agentic coding assistant with a **Bitunix USDT-M futures
trading engine** built into its body and soul.

- **Soul**: clever, never lies, says "I don't know", relentless bug hunter, direct tone.
- **Full tool access**: shell, file read/write/patch, code search, git, HTTP, processes.
- **Self-healing**: inspects, diagnoses and fixes bugs in its own code and the engine.
  Every self-edit is permission-gated (`auto accept edit on|off`).
- **Learning loop**: user feedback + past episodes stored and recalled.
- **Trader engine**: 36 REST endpoints, public + private WebSocket, 16 unique
  strategies across 5 categories, multi-timeframe aggregation, agent-decided TP/SL.

---

## Quick start

```bash
cd jrock
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill in your keys
python -m jrock.cli
```

On first start, **AutoSetModelByKey** reads `BASE_URL` + `API_KEY` from `.env`,
calls `GET {BASE_URL}/models`, probes the top candidates with a real chat
completion, and pins the best working model to `.env`.

```bash
python -m jrock.cli --startup          # run startup checks and exit
python -m jrock.cli --once "/check_system"
python -m jrock.cli --help-cmds
```

---

## Web dashboard

`python -m jrock.web` serves the same agent in a browser instead of the terminal.
Every line you submit goes through the same `JRockAgent.handle()` the CLI uses, so
commands, natural language, and `yes`/`no` confirmations behave identically.

```bash
python -m jrock.web
# J+Rock dashboard: http://127.0.0.1:8080/?token=...
```

Open the printed URL. The page has quick buttons for read-only queries and a box
for any command or question.

The dashboard is a 1:1 view of the engine, not a subset of it. It refreshes
itself every 30 seconds (the same default as the engine's own report loop) and
shows a **last updated** stamp, so a stale page is visible rather than silent.

| Panel | What it shows |
|---|---|
| Engine | engine / autotrade / scan / report / mid-management / reversal state, symbols, leverage, scan count, last error |
| Account | equity, available, margin, unrealized and realized PnL, open position and order counts, API reachability |
| Open positions | symbol, side, qty, entry, mark, liquidation price, leverage, ROI, unrealized PnL |
| Open orders | symbol, side, type, qty, price, status |
| Strategies & permissions | registered strategy count by category, and the agent's permission flags |
| Readouts | the read/query commands, verbatim: `status`, `settings trade`, bare `margin amount`, `ladder`, `tpsl`, `pnl`, `trades_history`, `tpsl_history` |
| Settings | every engine setting, editable in place (see below) |
| Output | the result of the last command or question |

### Dashboard endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | the page (requires `?token=`) |
| `/api/state` | GET | the structured snapshot the page polls every 30s |
| `/api/command` | POST | any command or question, through `JRockAgent.handle()` |
| `/api/autotrade` | POST | the autotrade toggle (needs `confirm: true` to turn ON) |
| `/api/settings` | POST | write one engine setting |
| `/api/margin` | POST | `margin add` / `margin reduce` on an isolated position |
| `/api/toggle` | POST | flip scan / report / reversal / mid-management |

All of them require the `X-Auth-Token` header (or `?token=`).

### Settings you can change from the dashboard

Every setting that used to be reachable only from the CLI or chat is a control
here. The dashboard does not write engine state directly: it builds the exact
command the CLI accepts and runs it through `engine.command()`, so validation,
clamping and persistence are the engine's own.

`margin amount` · `leverage` · `risk` · `max open positions` · `partial close pct`
· `partial at roi` · `breakeven threshold pct` · `trailing trigger stop ROI pct`
· `liq sl distance pct` · `min agreeing strategies` · `min confidence`
· `tf min confidence` · `scans interval sec` · `report interval sec`
· `reversal_confidence` · `account tp/sl usdt` · `account tp/sl pct`
· `margin mode` · `position mode` · `tpsl method` · `symbols` · `timeframes`
· `ladder set` · `ladder breakeven` · `ladder runner` · `ladder basis`

### Autotrade from the dashboard

The autotrade button shows the current state and asks for confirmation before
turning ON, because that is the switch that lets the engine place real orders.
Turning it OFF never needs a confirmation. The engine still never resumes
autotrade automatically after a restart - the dashboard surfaces the
`autotrade_was_on` warning instead of silently re-arming it.

| Setting | Default | Meaning |
|---|---|---|
| `WEB_TOKEN` | *(random per start)* | Dashboard password. Sent as the `X-Auth-Token` header or `?token=` in the URL. Not an API key. |
| `WEB_HOST` | `127.0.0.1` | Interface to listen on. Non-loopback addresses print a warning. |
| `WEB_PORT` | `8080` | Port to listen on. |

Set `WEB_TOKEN` in `.env` to keep the same password across restarts. Without it, a
new random token is printed each time. Requests without a valid token get `401`.

The dashboard binds to loopback by default. Do not expose it on a network interface
without TLS and a reverse proxy in front. Autotrade and the engine loop are still
never resumed automatically after a restart.

---

## Commands

### Agent

| Command | What it does |
|---|---|
| `/help` | full command list |
| `/models` | list models on `BASE_URL`, ranked, current one marked `*` |
| `/set_model <name>` | pin a model (writes `MODEL=` to `.env`) |
| `/connect_provider <url> <key>` | set provider, then auto-probe |
| `/add_mcp <name> <cmd>` / `/mcp` | register / list MCP servers |
| `/add_skill <name> <path>` / `/skill` | register / list skills |
| `/add_soul <text\|@file>` / `/soul` | append to / show the soul |
| `/think [level]` | how hard the model reasons: `off`/`low`/`medium`/`high`/`max` |
| `/balance` `bal` `wallet` `equity` `funds` | account table: available, frozen, margin, unrealized PnL, equity |
| `/positions` | open positions: entry, mark, liq, leverage, margin, uPnL |
| `/orders` | pending orders |
| `/price [SYMBOL]` | last / mark price for your symbols |
| `/funding [SYMBOL]` | current funding rate |
| `/leverage` / `/margin` | leverage and margin mode per symbol |
| `/settings [SYMBOL]` | account trading settings: position mode, margin mode, leverage and the trading config (add a SYMBOL for its per-symbol margin mode + leverage) |
| `/pnl` | unrealized PnL across open positions |
| `/status` | engine + API + account summary |
| `/symbols [SEARCH]` | tradable symbols with precision and min volume |
| `/memory [query]` | memory stats, or search memory |
| `/code_review [path]` | AST + regex review: syntax, long functions, bare except, eval, TODOs |
| `/bug_fix [path]` | review + apply safe fixes (permission-gated) |
| `/verify` | compileall + import check + pytest |
| `/simplify [path]` | over-complex functions, oversized files |
| `/team_spawn <n> <task>` | spawn n scoped sub-agents |
| `/check_ai` | end-to-end LLM key/model health |
| `/check_system` | deps, compile, Bitunix auth, pairs, account, engine, strategies |
| `/autopsy <error\|file>` | root-cause analysis against known failure patterns |
| `/diagnose` | full bundle: system + review + simplify + audit |

### Permissions

| Command | What it does |
|---|---|
| `auto accept edit on\|off` | apply self-edits automatically vs. show diff and wait |
| `device access on\|off` | grant whole-terminal access |
| `audit` | show `data/audit.jsonl` |
| `pending` / `approve [n]` | review / approve queued edits |

### Trader engine

```
start trading | autotrade on        stop trading | autotrade off
engine on | engine off              status
report on | report off              report interval sec 30
trades_history | tpsl_history | pnl | positions history
settings trade                      scan on | scan off
mid management position trigger     reversal=true   reversal_confidence=70
scans interval sec 30               breakeven threshold pct 0.6
trailing trigger stop ROI pct 25    liq sl distance pct 50
min agreeing strategies 3           timeframes 1m,3m,5m,15m
min confidence 70                   tf min confidence 60
close position [SYMBOL]             margin mode ISOLATION|CROSS
position mode HEDGE|ONE_WAY         symbols BTCUSDT,ETHUSDT
max open positions 3                leverage 10 | risk 1.0
```

---

## Strategies (16 unique)

The spec lists 20 entries, but section 6 repeats the three classic strategies
verbatim. There are **16 unique strategies**; the duplicates resolve to the same
modules. `tests/test_strategies.py` asserts this.

| Category | Strategy | Indicators |
|---|---|---|
| classic | `vegas_tunnel_bearish` | EMA + EMA cross |
| classic | `golden_cross_50_200_long` | MA cross + volume |
| classic | `death_cross_50_200_short` | MA cross + volume |
| risk | `vwap_breakdown_alert` | VWAP + MACD |
| reversal | `hammer_oversold_bounce` | candle + RSI + EMA |
| reversal | `morning_star_bullish_divergence` | candle + MACD |
| reversal | `shooting_star_overbought_alert` | candle + RSI + EMA |
| reversal | `dark_cloud_cover_death_cross` | candle + MACD |
| breakout | `bollinger_upper_band_breakout` | BB + RSI |
| breakout | `mid_band_macd_cross` | MACD + BB + RSI |
| trend | `ma200_cross_macd_confirm` | MA + MACD |
| trend | `vegas_tunnel_bullish` | EMA + EMA cross |
| trend | `tema_cross_above_vwap` | TEMA + VWAP |
| trend | `dema55_bullish_trend_follow` | DEMA + MACD |
| trend | `vwap_bullish_confirmation` | MACD + price |
| trend | `three_white_soldiers_trend_confirm` | candle + RSI + EMA |

### Aggregation

- a vote counts only if `confidence >= tf_min_confidence` (default 60)
- a trade needs `>= min_agreeing` **distinct** strategies (default 3) on one side
- aggregate confidence must be `>= min_confidence` (default 70)
- confidence = TF-weighted mean + agreement bonus + multi-TF bonus − conflict penalty
- one strategy agreeing on 3 timeframes is still **one** strategy (multi-TF raises
  confidence, not the count)

---

## TP/SL — the agent decides

You set **two** numbers:

- `breakeven threshold pct` — move SL to entry once ROI ≥ this
- `trailing trigger stop ROI pct` — start trailing once ROI ≥ this

Everything else is derived:

1. **Liquidation price** from the isolated-margin formula with the real
   maintenance-margin rate for the position's notional tier:
   - long: `liq = entry * (1 - 1/L + mmr)`
   - short: `liq = entry * (1 + 1/L - mmr)`
2. **Stop loss** sits `liq sl distance pct` (default 50%) of the way from entry
   toward liquidation — i.e. it triggers at ~50% of the ROI that liquidation would cost.
3. **Take profit** = SL distance × R-multiple scaled by signal strength:
   `weak 1.2R · normal 1.8R · strong 2.5R · extreme 3.2R`, nudged by confidence.
4. **Size** from risk: `qty = (equity × risk_pct) / |entry − stop|`, capped by
   max notional and rounded **down** to `basePrecision`.

TP/SL are attached to the entry order (`tpPrice`/`slPrice` on `place_order`) and
later moved with `modify_position_tp_sl_order` for breakeven and trailing.

---

## Architecture

```
jrock/
  jrock/
    config.py            .env loading, Settings
    permissions.py       permission model + audit log + diff gate
    memory.py            feedback / episodes / facts, BM25-ish recall
    model_selector.py    AutoSetModelByKey (list -> probe -> pick -> persist)
    agent.py             tools, self-healing, all 22 commands
    cli.py               interactive + --once + --startup
    web.py               browser dashboard (aiohttp) over the same agent
    trader/
      client.py          REST client, double-SHA256 signing, 36 endpoints
      ws.py              public/private WS, auto-reconnect, signed login
      errors.py          full official error-code table
      api_reference.py   machine-readable endpoint contracts
      indicators.py      SMA/EMA/DEMA/TEMA/RSI/MACD/BB/VWAP/ATR (pure python)
      risk.py            order units, liquidation, tiers, sizing
      tpsl.py            agent-decided TP/SL, breakeven, trailing, mid-mgmt
      aggregator.py      multi-TF multi-strategy voting
      engine.py          scanner + trader + autotrade + management + commands
      strategies/        16 strategies in 5 category modules
  docs/bitunix/          local copy of the official API docs
  soul/jrock.soul.md     the persona
  tests/                 57 tests
```

---

## Bitunix specifics that matter

**REST signature** (official):
```
message = nonce + timestamp + apiKey + queryString + body
digest  = sha256(message)
sign    = sha256(digest + secretKey)
```
Headers: `api-key`, `sign`, `nonce`, `timestamp`, `language`, `Content-Type`.
Body must be compact JSON with sorted keys; query string sorted + urlencoded.

**WebSocket login**: `sign = sha256(sha256(timestamp + nonce + apiKey) + secretKey)`,
timestamp in **seconds**.

**`qty` is base coin**, not contracts. `minTradeVolume` / `maxMarketOrderVolume`
are base-coin amounts. Round qty down to `basePrecision`, price to `quotePrecision`.

**Error 10011** (timestamp skew) is auto-handled: the client resyncs its clock and
retries once.

**Error 10002** (Parameter error) is prevented locally. Every order body is built
by `jrock/trader/order_builder.py`, which validates each field against the
official spec **before** sending and raises a named local error instead of
transmitting a malformed request:

| Field | Rule enforced |
|---|---|
| `symbol` | must exist in the live `trading_pairs` list (else: closest matches) |
| `side` | `BUY` \| `SELL` (case-normalized) |
| `orderType` | `LIMIT` \| `MARKET` (case-normalized) |
| `tradeSide` | `OPEN` \| `CLOSE` (case-normalized) |
| `effect` | `GTC` \| `IOC` \| `FOK` \| `POST_ONLY`, LIMIT only |
| `qty` | string, `>= minTradeVolume`, `<= maxMarketOrderVolume`, formatted to `basePrecision` |
| `price` | string, required for LIMIT, **absent** for MARKET, formatted to `quotePrecision` |
| `positionId` | required when `tradeSide == CLOSE` |
| `reduceOnly` | boolean |
| `tpPrice`/`slPrice` | strings formatted to `quotePrecision` |
| `clientId` | alphanumeric, ≤ 32 chars |

> **The AKEUSDT 10002.** `AKEUSDT` has `basePrecision: 0` — it takes **integer**
> qty. `PairRules.from_api` used `int(d.get(k, default) or default)`, and `0` is
> falsy, so the real precision `0` was silently replaced by the default `4`. The
> engine then sent `"qty":"1686.3406"` and the exchange rejected it. Fixed by
> reading the value without the `or` coercion, and by formatting qty through
> `format_qty()` (which renders `basePrecision 0` as an integer).

Set `BITUNIX_DEBUG_ORDER=true` to log the exact body sent next to any error.

---

## Thinking level (`/think`)

`/think` sets **how much the model reasons before it answers**. It is a
**setting, not a chat mode**.

**You do not need `/think` to talk to the agent.** Just type normally and the
agent replies. `/think` only changes how hard it thinks.

| Level | What it means |
|---|---|
| `off` | no reasoning, fastest, cheapest |
| `low` | short reasoning |
| `medium` | default - balanced |
| `high` | long reasoning, more tokens |
| `max` | deepest reasoning, most tokens, slowest |

```
/think              show the current level and what it does
/think status       same as above
/think high         set it (persisted)
```

Aliases: `none`->`off`, `min`->`low`, `med`->`medium`, `hi`->`high`,
`maximum`->`max`. Case-insensitive. An invalid level prints the valid list and
the closest match - it never crashes.

**Where it is stored.** `data/settings.json` (survives restarts) and mirrored to
`THINKING_LEVEL=` in `.env`. Default is `medium`. Precedence on read:
store > `.env` > default.

**What it actually sends.** The level maps to a real provider parameter, chosen
from the model's own `/models` metadata - never guessed:

| Level | `reasoning_effort` (OpenAI-compatible) | `thinking_budget` (budget-style) |
|---|---|---|
| `off` | *nothing sent* | *nothing sent* |
| `low` | `low` | `1024` |
| `medium` | `medium` | `4096` |
| `high` | `high` | `16384` |
| `max` | `high` | `32768` |

`off` sends **no reasoning parameter at all**. A model that does not advertise
reasoning support gets **no parameter** either - we never send an unsupported
field and let the request 400. The level and the parameter actually sent are
logged at debug level.

**Output budget.** A high thinking level needs room for reasoning tokens *on top
of* the answer, so `max_tokens` scales with the level (`off` 800, `low` 1200,
`medium` 2000, `high` 6000, `max` 12000). If a configured cap is smaller than
the thinking budget, a warning is logged - that combination is what produces an
empty answer.

## How a line is routed

Every line you type goes through five steps, in this order:

1. a pending destructive action awaiting `yes`/`no` -> confirm or cancel
2. `/command` -> the agent command dispatcher
3. a bare word that names an agent command (`balance`, `positions`, `help`, ...) -> the agent
4. engine command grammar (`leverage 20`, `scan off`, `min agreeing strategies 3`) -> the engine
5. a natural-language sentence -> the intent layer (`jrock/nlu.py`)
6. anything else -> the model, as natural language

A word that is both a query and a setter is disambiguated by its arguments:
`leverage` reads, `leverage 20` sets. `report` reads status, `report on` toggles
the engine report.

**Trader words are never sent to the model.** `balance`, `bal`, `wallet`,
`equity`, `funds`, `positions`, `orders`, `price`, `funding`, `leverage`,
`margin`, `pnl`, `status`, `symbols`, `pairs`, `markets` all have real handlers.

## Natural language (no commands needed)

You do not have to type a command. A normal sentence is resolved to the **same
command string** you could have typed, and handed to the existing router - the
intent layer never re-implements a handler, it only decides *which* command a
sentence means.

| You type | It runs |
|---|---|
| `show me my trading settings` | `settings trade` |
| `what is my balance` | `balance` |
| `show my positions` | `positions` |
| `what are my open orders` | `orders` |
| `what is my pnl` | `pnl` |
| `show me the status` | `status` |
| `turn off autotrade` | `stop trading` |
| `stop the engine` | `engine off` |
| `close my AKEUSDT position` | `close position AKEUSDT` |
| `set leverage to 5x` | `leverage 5` |
| `set risk to 2%` | `risk 2.0` |
| `why did the last order fail` | engine error log + the code's hint |
| `what does TP_SL_MIN_STOP_TICKS do` | answered from `jrock/config.py` |

**Safety.** Anything that moves money - opening or closing a position, changing
leverage, risk, margin mode, position mode, autotrade, or the engine - is
**not** executed on the first turn. The engine prints what it is about to do and
waits for an explicit `yes`. `no` cancels. A pending action lives in memory only,
so it cannot survive a restart.

**Ambiguity asks.** `close my position` asks which symbol. `set my leverage`
asks for the value. `open a position on BTCUSDT` asks long or short. A direct
`open long BTCUSDT` is understood but **refused**: the engine sizes every order
from a signal and your risk setting, and inventing a size from a sentence would
be a guess with real money.

**Free text still reaches the model.** A sentence the intent layer does not
recognise falls through unchanged, so nothing that used to work stops working.

**The engine answers about its own settings.** `TP_SL_MIN_STOP_TICKS`,
`TAKER_FEE_RATE` and `MARGIN_SAFETY_PCT` are defined in `jrock/config.py`; a
question about them is answered from the repo, with the live value. The engine
never claims it cannot see its own code.

## Empty model responses

An empty completion is a **failure**, never a normal answer. The CLI diagnoses
the cause and names it:

| Cause | Meaning |
|---|---|
| `length_zero_tokens` | hit the token cap before producing anything |
| `length_reasoning_only` | every completion token was a reasoning token |
| `length_no_text` | hit the cap with no text emitted |
| `content_filter` | the provider blocked the output |
| `tool_call_only` | the model wanted a tool call and wrote no prose |
| `no_choices` | HTTP 200 with an empty `choices` array |
| `error_in_200` | the provider returned an error object inside a 200 |
| `whitespace_only` | content was only whitespace |

For the two `length` causes the CLI **retries once with `thinking=off`** and
tells you it did. If the retry also fails you get the cause, the finish reason,
the token counts, the configured `max_tokens`, and the next action - never
`(model returned an empty message)`.

**Which level to use.** `off`/`low` for quick chat. `high`/`max` for debugging,
code review, or strategy analysis - harder problems, but slower and more tokens.

---

## WebSocket (`/ws`)

The engine can take live prices from the Bitunix WebSocket instead of polling
REST. `MARKET_DATA_SOURCE=rest|ws` (default **`rest`**) selects it; `/ws on` and
`/ws off` switch it at runtime and persist the choice.

```
/ws            connection state, uptime, reconnects, channels, message counts
/ws on|off     switch market data source (persisted)
/ws test       open a real connection, print the first 3 raw frames, close
```

### What was actually wrong before

The old `ws.py` was **dead code** - nothing imported it - and every public
channel name in it was wrong. The server accepts a bad channel name, replies
`{"op":"connect","data":{"result":true}}`, and then never pushes anything, so
the failure was silent.

| Old (wrong) | Correct | Live result |
|---|---|---|
| `market_price` | `price` | old: 0 frames, new: real `mp`/`ip`/`fr` |
| `market_ticker` | `ticker` | old: 0 frames, new: real `la`/`o`/`h`/`l` |
| `market_tickers` | `tickers` | old: 0 frames, new: real array |
| `market_trade` | `trade` | old: 0 frames, new: real trades |
| `market_kline_1hour` | `market_kline_60min` | old: `data={}`, new: real OHLC |

### Audit

| Item | Status |
|---|---|
| WS client exists | **VERIFIED** - `jrock/trader/ws.py`, live-connected |
| Public: `price` | **VERIFIED** - real frames captured |
| Public: `ticker` | **VERIFIED** - real frames captured |
| Public: `tickers` | **VERIFIED** - real frames captured |
| Public: `trade` | **VERIFIED** - real frames captured |
| Public: `depth_books` / `book1/5/15` | **VERIFIED** - real frames captured |
| Public: kline (all intervals) | **VERIFIED** - `1min`..`1month`; `1hour` is invalid |
| Private: `position` / `order` / `balance` | **UNTESTED** - no API keys in the build sandbox |
| Private login signature | **VERIFIED** against the official Go demo (`generateSign`) |
| Private live handshake | **UNTESTED** - cannot authenticate without keys |
| Heartbeat (ping/pong) | **VERIFIED** - 3 pings, 3 pongs, ~0.17s round trip |
| Heartbeat timeout -> reconnect | **VERIFIED** - no pong closes the socket |
| Reconnect + backoff | **VERIFIED** - 0.3s, 0.6s, 1.2s, 2.0s (capped) |
| Re-subscribe after reconnect | **VERIFIED** - resumed frames after recovery |
| Dispatcher / typed events | **VERIFIED** - `Event` objects, prefix + `*` handlers |
| Unknown channel ignored | **VERIFIED** - logged at debug, loop continues |
| Backpressure drop counter | **VERIFIED** - bounded queue, `dropped` counter |
| Clean shutdown | **VERIFIED** - zero orphaned asyncio tasks |
| Tests | **PARTIAL** - signing unit-tested; WS proven by real connections, not mocks |

**Why the default is `rest`:** the public layer is proven, but the private
login handshake has never been exercised against the real server because this
environment has no `BITUNIX_API_KEY`/`BITUNIX_API_SECRET`. Switching the engine
onto WS by default would mean trusting an unverified auth path with real
positions. Set `MARKET_DATA_SOURCE=ws` yourself once you have confirmed
`/ws test` and a private login on your own keys.

### Price cache - how `ws` actually replaces REST

`MARKET_DATA_SOURCE=ws` is not a label. Every price read in the engine goes
through one accessor, `TraderEngine.get_price(symbol)`, which consults a
`PriceCache` fed by the public `price` and `ticker` channels.

| Situation | What happens |
|---|---|
| cached quote younger than `PRICE_MAX_AGE_SEC` (default 5s) | returned, `source=ws`, **no REST call** |
| no cached quote | REST fallback, counted as a `miss` |
| cached quote older than the limit | **refused**, warning logged, REST fallback, counted as a `stale_rejection` |
| WS disconnected | REST fallback; the engine keeps running, never blocks on the socket |
| `MARKET_DATA_SOURCE=rest` | no cache consulted, **no socket opened at all** |

Every quote carries its source, the exchange timestamp and our local receive
time, so a stale price can never be used silently for a trading decision.

`/ws` prints the cache counters (`hits`, `misses`, `stale_rejections`,
`rest_fallbacks`, `ws_updates`, `rest_updates`) and the total REST price calls
made, which is how you prove REST is actually bypassed.

Live verification (`scripts/verify_ws_prices.py`, real feed):

```
Market data: ws (connected, 2 symbols)
BTCUSDT: 75921.8 src=ws age=0.06s exchange_ts=1794010712.343
20 get_price calls -> REST calls: 0
feed stopped + cache cleared -> get_price = 75921.8 in 181ms, REST calls: 1
injected stale price 1.0 -> refused (0.5s old, max 0.3s), REST fallback, price 75921.8
pending asyncio tasks after stop: 0
```

### Kline cache - how scans stop polling REST

Prices were only half the story. Every strategy scan still called
`GET /api/v1/futures/market/kline` for each timeframe. `get_candles()` now goes
through a `KlineCache` fed by the `market_kline_<interval>` channels.

**The frame has no time field.** Verified live on 2026-11-07, a
`market_kline_1min` push is:

```json
{"ch":"market_kline_1min","symbol":"BTCUSDT","ts":1794011946619,
 "data":{"o":"75915.9","c":"75915.9","h":"75915.9","l":"75915.8","b":"0.0378","q":"2869.62094"}}
```

`data` has exactly `o/h/l/c/b/q` - there is **no `time`**. The REST kline
endpoint *does* return `time`. So the candle's open time is derived by flooring
the frame `ts` to the interval boundary:

```
open_time = (ts_ms // interval_ms) * interval_ms
```

Get that wrong and every candle collapses onto one key and the series never
grows - silently, with no error.

**Replace vs append.** The channel re-pushes the *still-forming* candle every
500ms. The cache **replaces** the last candle when the open time matches and
**appends** when a newer open time arrives. Appending blindly would grow the
series by one every 500ms and corrupt every indicator.

**Staleness is a multiple of the interval, not flat seconds.** A 1m candle is
legitimately 60s old; a 1d candle is legitimately 24h old. A candle set is
stale when the newest candle's open time is older than
`KLINE_MAX_AGE_SEC * interval` (default 2x).

| Situation | What happens |
|---|---|
| fresh, long-enough, WS-fed series | returned, `source=ws`, **no REST call** |
| newest candle older than `KLINE_MAX_AGE_SEC * interval` | **refused**, warning, REST fallback, `stale_rejection` |
| fewer candles than the caller needs | **refused**, REST fallback, `short_series` - a short series produces a wrong signal, which is worse than a slow one |
| cache holds REST-seeded candles only | REST fallback (we cannot claim it is live) |
| WS disconnected | REST fallback; the engine keeps running |
| `MARKET_DATA_SOURCE=rest` | no cache consulted, **no socket opened at all** |

**Warmup.** The WS only gives candles from the moment you subscribe, so a
200-candle strategy would need 200 minutes of uptime. On startup the cache is
seeded from REST **once** per `(symbol, interval)`; the banner reports how many
calls that cost. The subscribed set is derived from the configured timeframes
(plus `5m`, which the trailing-stop ATR reads), not a hardcoded list.

Live verification (`scripts/verify_ws_klines.py`, real feed):

```
market data: ws (connected, 1 symbols)
kline timeframes subscribed: ['1m', '5m']
warmup REST calls: 2
RAW: {"ch": "market_kline_1min", "symbol": "BTCUSDT", "ts": 1794011946619, "data": {...}}
get_candles('BTCUSDT','1m',200) -> 200 candles, cache src=ws
closes seen for open=1794011940000: [75915.9, 75915.8, 75915.7, 75915.6]
same open time (replaced, not appended): True   count stable: True
open time advanced: True   count grew by exactly one: True (delta=1)
20 get_candles calls -> REST kline calls: 0
feed killed + cache cleared -> 200 candles in 212ms, REST kline calls: 1
frozen cache -> refused (62s old, max 0s), REST fallback
short series (50 candles, want 200) -> refused, REST fallback
MARKET_DATA_SOURCE=rest -> WS connect attempts: 0
pending asyncio tasks after stop: 0
```

### Heartbeat detail

The client sends `{"op":"ping","ping":<unix seconds>}` and the server answers
`{"op":"ping","ts":...,"pong":<echoed>,"ping":<server seconds>}`. The server
does **not** send its own ping (verified: 45s idle, zero frames), so the client
must drive it. A missing pong within `pong_timeout` closes the socket to force
a reconnect.

---

## Persistence

Settings and learned state survive a restart. One module owns it all:
`jrock/store.py`, backed by `data/state.json` (versioned) and
`data/memory.jsonl` (bounded).

**Precedence is explicit: persisted store > `.env` > built-in default.** A
value changed at runtime is written to disk *in the same call*, so `kill -9`
cannot lose it. `.env` is kept in sync as a convenience mirror only — it can
never win over the store.

What is persisted: every runtime-settable engine value (leverage, risk params,
symbols, timeframes, scan/report intervals, mid-management, reversal, …), the
agent's thinking level and market-data source, position management state
(`breakeven_done`, `trailing_stop`, `peak_roi`, `partial_done`), counters, and
the learned memory (feedback / episodes / facts).

**Autotrade and the engine loop are recorded but never auto-resumed.** After a
crash the banner says so out loud; you re-enable deliberately with
`start trading`. Silently restarting live order flow is the one failure mode
this engine must not have.

Safety properties:

- **Atomic writes** — temp file + `fsync` + `os.replace` + directory `fsync`.
  A crash mid-write leaves the old file or the new one, never a partial one.
- **Schema versioning** — a `version` field with a migration path. A v1 file is
  upgraded in place, not discarded.
- **Corruption recovery** — an unreadable file is backed up to
  `state.json.corrupt-<timestamp>`, a warning is logged, and the engine starts
  clean. It never crashes on startup and never silently wipes.
- **Secrets never touch disk** — secret key names are refused outright, and any
  registered secret *value* found in the payload aborts the write. API keys
  live in `.env` only.

```
/memory              entry count, size on disk, last write, recent entries
/memory <query>      search
/memory clear        asks first; /memory clear confirm wipes
/status              store path, schema version, last save, size
```

Verify it yourself:

```bash
python scripts/verify_store.py     # SIGKILL, precedence, corruption, migration, secret scan
python scripts/verify_restart.py   # the same through the real agent command path
```

---

## Tests

```bash
python -m pytest -q tests
```

772 tests covering indicators, risk math, all 16 strategies (including empty and
short-data inputs), aggregation rules, TP/SL planning and side validation
(30020-30033), the **running-engine** TP/SL path (breakeven + trailing, the
second 30029), the order-size pre-flight (20003, tier leverage caps, margin
bounds), API-reference/client drift, model probing, request signing, CLI
routing, the **natural-language intent layer** (sentence -> command mapping,
destructive-action confirmation, ambiguity clarification, and a regression
guard proving every existing command string still routes identically), the
thinking-level system (levels, aliases, persistence, and the exact request
parameter sent), the balance command, empty-model-response diagnosis, the order
builder's per-field validation, the price and kline caches, the WebSocket layer,
and the persistent store (round-trip, SIGKILL survival, precedence, atomicity,
corruption recovery, schema migration, memory eviction, and the secret guard).

---

## Order pre-flight: why 20003 and 30029 no longer reach the exchange

Two exchange rejections were traced to the engine sending requests it could
have refused locally.

### 30029 `SL price must be less than mark price`

This one had **two** independent causes. The first fix (58f901c) only closed
the first, which is why the error came back on a live AKEUSDT order.

**Cause 1 - the entry plan ignored the mark price.** The engine computed the
stop-loss from the **entry (last) price** and never fetched the **mark price**.
Bitunix validates the trigger against the mark price, so when the mark sat
below the last price the stop landed on the wrong side and the whole order was
rejected.

**Cause 2 - the running-engine path was never fixed at all.** `plan_tpsl` was
made mark-aware, but the management loop (`manage_positions`: breakeven and
trailing) built its own stop-loss with a hardcoded `round_price(..., 4)` and
never fetched the mark price. On AKEUSDT (`quotePrecision: 6`, price ~0.005)
rounding to 4 decimals turns `0.005051` into `0.005100` - above the mark - and
the exchange answers:

```
[30029] SL price must be less than mark price: 0.0051
```

The `0.0051` in that message is the fingerprint: **four decimals on a symbol
that trades in six.**

The fix:

- `risk.clamp_sl()` / `risk.clamp_tp()` are now the **single** place a TP/SL is
  made safe, and every path that sends one goes through them - the entry plan
  *and* the management loop. Rounding is direction-aware (a LONG stop is
  floored, a SHORT stop is ceiled) so rounding can only move a stop *further*
  from the mark, never back across it.
- `round_price()` is half-up, not Python's banker's rounding, which rounds the
  same distance in either direction.
- `manage_positions` fetches the mark price **fresh** for every position and
  re-clamps before sending; `try_enter` re-reads the mark immediately before
  the request and re-clamps, closing the window in which the mark can move
  during the pre-flight round-trips.
- `format_price()` no longer re-rounds with `round()`, which could undo a clamp
  after it had been validated.
- When the mark price cannot be fetched, the fallback is the last price widened
  by the symbol's price-protection scope in the safe direction - not the raw
  last price, which can sit on the wrong side of the mark.
- `validate_tpsl_side()` covers the documented rules 30020-30033 and
  30024/30025 (liquidation side) for both directions, and is re-run on the
  exact values about to be sent.
- `build_order_body` refuses locally with the symbol, direction, SL price,
  mark price and the rule violated.
- If no valid stop exists (high leverage + coarse tick), `TpSlSideError` names
  the liquidation price and the limit instead of emitting an invalid stop.

### 20003 `Insufficient balance`

Two independent causes, both now caught before sending:

1. **The tier caps the leverage.** `get_position_tiers` returns a `leverage`
   that is the *maximum* for that notional band (AKEUSDT: 50x under 10k, 25x
   under 20k, 10x under 80k). Asking for more makes the exchange require more
   margin than the engine computed — which surfaces as 20003, not a leverage
   error. The pre-flight now compares the engine's leverage against the tier.
2. **No balance check.** The pre-flight compares the required margin against
   the real available balance and refuses with both numbers.

Configurable bounds (all default `0` = disabled, so behaviour is unchanged):

| Variable | Meaning |
|---|---|
| `MIN_MARGIN_USDT` | refuse orders whose margin is below this |
| `MAX_MARGIN_USDT` | refuse orders whose margin is above this |
| `MAX_NOTIONAL_USDT` | refuse orders whose notional is above this |
| `TP_SL_MIN_STOP_TICKS` | minimum stop distance from mark/entry, in ticks |
| `TP_SL_TRIGGER_TYPE` | `MARK_PRICE` (default) or `LAST_PRICE` |
| `ORDER_UNIT` | `QUANTITY` (default), `NOTIONAL` or `COST` |

Verify against live data: `python scripts/verify_tpsl_side.py`

---

## API-doc audit (2026-11-07)

Every endpoint below was read against its official doc page and the engine's
implementation. Eight real defects were found and fixed.

| # | Endpoint | Defect | Fix |
|---|---|---|---|
| 1 | `GET /tpsl/get_pending_orders` | engine called `get_pending_order` (singular) → 404 | path corrected |
| 2 | `GET /tpsl/get_history_orders` | engine called `get_history_order` (singular) → 404 | path corrected |
| 3 | `POST /tpsl/cancel_order` | sent only `orderId`; `symbol` is **required** | `symbol` added |
| 4 | `GET /tpsl/get_*_orders` | sent `page`/`pageSize`; docs say `skip`/`limit` | params corrected |
| 5 | `POST /trade/batch_order` | sent only `orderList`; `symbol` is **required**, max 5 | `symbol` added + cap |
| 6 | `POST /account/adjust_position_margin` | always sent `positionId`, never `side`; docs say *either* | both supported |
| 7 | `GET /market/get_funding_rate_history` | engine called `funding_rate/history` → **404 (verified live)** | path corrected |
| 8 | `GET /market/funding_rate/batch` | `symbols` filter is **ignored by the server** (verified live: 931 rows for 2 symbols) | client-side filter added |

Also: the private **Tp Sl WebSocket channel was never subscribed** —
`PRIVATE_CHANNELS` existed only as help text. A `PrivateFeed` now subscribes
`balance`/`position`/`order`/`tpsl` and parses the documented push shape.

**Unverified:** the private login needs `BITUNIX_API_KEY`/`SECRET`, which are not
present in the build sandbox. The private handshake was **not** exercised against
the live server and is not claimed to work.

Verify: `python scripts/verify_four_tpsl_and_docs.py`

---

## The API contract — the root-cause fix

The defects above were not random. The engine was built from **partial knowledge
of the docs** rather than a full read of the official API reference, and that is
the root cause of every one of them: the 30029 SL-side error, the 404 paths, the
wrong parameter names, the missing endpoints. Fixing them one at a time does not
stop the next one.

So the docs are now read by a script, and the code is checked against them.

### `docs/bitunix_api_contract.json`

Fetched from the official reference by `scripts/fetch_api_contract.py`, which
enumerates every page in the API nav, fetches each one, and extracts the
machine-readable contract: HTTP method, exact path, every request parameter with
its name/type/required flag, and the documented response fields — each entry
carrying its `source_url` and `fetched_at`.

**54 pages: 39 REST endpoints, 10 WebSocket channels, 5 guides.**
A human-readable mirror is at `docs/bitunix_api_contract.md`.

```bash
python scripts/fetch_api_contract.py            # re-fetch from the live docs
python scripts/fetch_api_contract.py --offline  # rebuild from docs/_raw_api_docs/
```

### `scripts/audit_api_contract.py`

Parses `jrock/trader/client.py` with the AST (no network, no credentials) and
compares every `request(...)` call against the contract. Per endpoint it reports
implemented / missing, and for implemented ones every missing required param,
wrong param name and undocumented extra.

```bash
python scripts/audit_api_contract.py            # human report
python scripts/audit_api_contract.py --strict   # exit 1 on any finding
```

**It is wired into the test suite** (`tests/test_api_contract.py`), so a
mismatch fails the build. That is the guard that stops this class of bug coming
back — and there is a test that deliberately breaks a parameter name and asserts
the checker fails, so the guard itself cannot rot.

### What the full audit found

| # | Endpoint | Defect | Fix |
|---|---|---|---|
| 1 | `GET /account/get_leverage_margin_mode` | engine called `/account/leverage_margin_mode` — not a documented path | path corrected |
| 2 | `GET /account/position_mode` | **not implemented at all** | added |
| 3 | `GET /account/trading_settings` | **not implemented at all** | added |
| 4 | `POST /account/change_position_mode` | sent `marginCoin`; docs list `positionMode` only | extra param removed |
| 5 | `POST /trade/cancel_orders` | sent only `orderList`; `symbol` is **required** | `symbol` added |
| 6 | `GET /trade/get_pending_orders` | sent `page`/`pageSize`; docs say `skip`/`limit` | params corrected |
| 7 | `GET /trade/get_history_orders` | same | params corrected |
| 8 | `GET /trade/get_history_trades` | same | params corrected |
| 9 | `GET /position/get_history_positions` | same | params corrected |
| 10 | `POST /trade/place_order` | `**kwargs` passthrough with no validation | required set enforced |
| 11 | `POST /trade/modify_order` | `**kwargs` passthrough with no validation | required set enforced |

**Deliberately out of scope** (listed, not silently skipped): the three
`/api/v1/cp/asset/*` copy-trading endpoints — sub-account asset query and fund
transfers are not a trading-agent concern.

**Unverified:** every private endpoint needs `BITUNIX_API_KEY`/`SECRET`, which
are not present in the build sandbox. The private handshake was **not** exercised
against the live server and is not claimed to work.

Verify: `python scripts/verify_api_contract.py`

---

## The 4 TP/SL methods

These are the four from the official academy article *"Bitunix Futures
Positions: Guide to Four TP/SL Methods"* — a different axis from the four
order-type/trigger-type combinations above. `TPSL_METHOD` selects one.

| Method | What it does | Endpoint | Enforced by | Code |
|---|---|---|---|---|
| `POSITION` | one TP/SL closes the **entire** position | `POST /api/v1/futures/tpsl/position/place_order` | exchange | `client.py` `place_position_tp_sl_order` |
| `PARTIAL` | a defined **%** closes at each trigger, the rest stays open | `POST /api/v1/futures/tpsl/place_order` (`tpQty`/`slQty`) | exchange | `engine.py` `place_partial_tp_sl` |
| `TRAILING` | arms after a trend, then trails behind price | none — software | manage loop | `tpsl.py` `plan_tpsl` + `engine.py` `_manage_positions` |
| `ACCOUNT` | closes **all** positions when total account PnL crosses a threshold | none — software | manage loop | `account_tpsl.py` `account_tpsl_decision` |

**The honest caveat:** `POSITION` and `PARTIAL` are enforced by the exchange, so
they survive the engine being down. `TRAILING` and `ACCOUNT` have no native
endpoint — they are software and only fire while the manage loop is running.
`/status` says which is active and whether it needs the loop.

`PARTIAL` is the reason `place_tp_sl_order` takes `tpQty`/`slQty`: the position
endpoint has **no quantity field at all**, so it can only ever close the whole
position. A staged exit is only possible through the standalone endpoint.

```
tpsl method PARTIAL          # select the method
partial close pct 25         # PARTIAL: close 25% at the trigger
partial at roi 50            # PARTIAL: trigger at 50% ROI
account tp usdt 100          # ACCOUNT: close all at +100 USDT total
account sl usdt 50           # ACCOUNT: close all at -50 USDT total
account tp pct 5             # ACCOUNT: ...or at +5% of available equity
```

---

## The TP ladder — multi-level partial take-profit

`PARTIAL` closes one fixed percentage at one threshold. The **ladder** replaces
that with an ordered list of rungs, each closing a slice as the trade runs in
its favour. It is a superset of `PARTIAL`: when a ladder is configured it owns
the staged exits and the single-level partial is skipped, so the two can never
both fire on the same pass.

```
TP_LADDER=1R:25,2R:25,3R:25
```

| Rung | Trigger | Closes | Remaining |
|---|---|---|---|
| 1 | 1R | 25% of the **original** | 75% |
| 2 | 2R | 25% of the **original** | 50% |
| 3 | 3R | 25% of the **original** | 25% |
| runner | — | — | 25%, trails |

### The rule that is easy to get wrong

`close_pct` is a share of the **ORIGINAL** position size, never of the remaining
size. For a 1000-unit position the rungs close 250 / 250 / 250 and leave 250.
Compounding against the shrinking remainder would close 250 / 187.5 / 140.6 and
leave 421.9 — a different trade. The arithmetic lives in one function
(`ladder.level_qtys`) and is asserted directly by the tests.

### Config

| Variable | Default | Meaning |
|---|---|---|
| `TP_LADDER` | *(empty)* | the spec; empty disables and falls back to `PARTIAL` |
| `TP_LADDER_BREAKEVEN_AFTER` | `1` | which level moves the stop to breakeven; `0` disables |
| `TP_LADDER_RUNNER` | `trail` | `trail` hands the remainder to the trailing stop, `close` closes it |
| `TP_LADDER_TRIGGER_BASIS` | `R` | `R` (initial risk), `ROI` (percent), `PRICE` (absolute) |

### Rules enforced in code, not just documented

- **Sum ≤ 100.** A spec whose `close_pct` values exceed 100 is rejected with the
  total named. Nothing is silently rescaled.
- **Increasing triggers.** Out-of-order rungs are refused rather than sorted —
  silently reordering a spec hides a typo.
- **Each level fires exactly once.** `fired_levels` is persisted per position, so
  a restart cannot re-fire a level that has already closed its slice.
- **R uses the INITIAL risk.** Once the stop moves to breakeven, `|entry - sl|`
  collapses to ~0; measuring R against the live stop would put every remaining
  level on the entry price and fire them all at once. `initial_risk` is captured
  at entry and persisted.
- **Dust is closed, not orphaned.** If the remainder after a level would fall
  below the symbol's `minTradeVolume` (read from the trading-pairs endpoint), the
  remainder is closed with that level instead of leaving a position the exchange
  will refuse to flatten later.
- **One level per pass**, in order, so a fast move cannot skip a rung.

### Commands

```
/ladder                          # the spec, fired levels per position, remaining qty
ladder set 1R:25,2R:25,3R:25     # set it (validated; a bad spec changes nothing)
ladder off                       # disable, fall back to single-level PARTIAL
ladder breakeven 2               # move the stop after level 2
ladder runner close              # close the remainder instead of trailing it
ladder basis ROI                 # read triggers as ROI percent
```

### What each level sends

Two documented calls per level:

1. a reduce-only `MARKET` order for the level's slice —
   `POST /api/v1/futures/trade/place_order` with `reduceOnly: true`, and
2. `POST /api/v1/futures/tpsl/position/modify_order` for the **remaining**
   quantity, so the stop left on the exchange matches what is still open.

The level is only marked fired once the close has been accepted, so a rejected
order is retried on the next pass rather than skipped.

**Caveat, stated plainly:** the ladder is software. It only fires while the
manage loop is running — the exchange knows nothing about the rungs. If the
engine is down when a level's price is reached, that level fires on the next
pass at the then-current price, not at the trigger.

Verify: `python scripts/verify_tp_ladder.py`

---

## The 4 TP/SL variants and the 3 order units

### The 4 TP/SL variants

`place_order` takes two independent enums for each of TP and SL, giving four
documented combinations:

| Field | Values | Defined at |
|---|---|---|
| `tpOrderType` / `slOrderType` | `LIMIT`, `MARKET` | `order_builder.py:39` `ORDER_PRICE_TYPES` |
| `tpStopType` / `slStopType` | `MARK_PRICE`, `LAST_PRICE` | `order_builder.py:40` `STOP_TYPES` |

| # | `tpOrderType`/`slOrderType` | `tpStopType`/`slStopType` |
|---|---|---|
| 1 | `LIMIT` | `MARK_PRICE` |
| 2 | `LIMIT` | `LAST_PRICE` |
| 3 | `MARKET` | `MARK_PRICE` |
| 4 | `MARKET` | `LAST_PRICE` |

All four are validated in `build_order_body` (`order_builder.py:152-157`) and
placed into the body by `OrderSpec.to_body` (`order_builder.py:79-81`). The
engine's own entry path uses variant 3 (`MARKET`/`MARK_PRICE`) at
`engine.py:826-828`.

### The 3 order units

Per the official help article *"Explanation of the Order Units in Futures
Trading"*, Bitunix offers three units. All three are implemented in
`risk.py:171` `ORDER_UNITS` and converted by `qty_from_unit` (`risk.py:174`):

| Unit | Meaning | Conversion |
|---|---|---|
| `QUANTITY` | base-coin quantity | `qty = amount` |
| `NOTIONAL` | position value in USDT | `qty = amount / price` |
| `COST` | margin paid in USDT | `qty = amount * leverage / price` |

The wire format is **always** base-coin `qty` — the unit only changes how the
size is specified. `ORDER_UNIT` selects it; `QUANTITY` is the default and a
no-op, so historical behaviour is unchanged.

### Which price the trigger is compared against

`TP_SL_TRIGGER_TYPE` selects it, read in exactly one place —
`engine.py:375` `get_trigger_price()`. Both the local pre-flight validation and
the `tpStopType`/`slStopType` sent on the wire read that same setting, so they
cannot drift apart. Default `MARK_PRICE` (the documented Bitunix default and
the engine's historical behaviour).

Verify: `python scripts/verify_tpsl_variants.py`

---

## Safety

- Autotrade is **off** by default and never enables itself.
- Destructive shell commands are blocked by default and always logged.
- Every self-edit and every destructive action lands in `data/audit.jsonl`.
- Start on testnet or with a tiny `risk_pct` before going live.

---

**DISCLAIMER:** As an AI Agent, I cannot provide legally protected, personalized
investment advice. This analysis is for informational purposes only and does not
constitute investment advice or recommendations. Consult a licensed financial
advisor before making investment decisions. Trading futures with leverage can
lose more than your initial margin.
