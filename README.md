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

Every line you type goes through four steps, in this order:

1. `/command` -> the agent command dispatcher
2. a bare word that names an agent command (`balance`, `positions`, `help`, ...) -> the agent
3. engine command grammar (`leverage 20`, `scan off`, `min agreeing strategies 3`) -> the engine
4. anything else -> the model, as natural language

A word that is both a query and a setter is disambiguated by its arguments:
`leverage` reads, `leverage 20` sets. `report` reads status, `report on` toggles
the engine report.

**Trader words are never sent to the model.** `balance`, `bal`, `wallet`,
`equity`, `funds`, `positions`, `orders`, `price`, `funding`, `leverage`,
`margin`, `pnl`, `status`, `symbols`, `pairs`, `markets` all have real handlers.

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

### Heartbeat detail

The client sends `{"op":"ping","ping":<unix seconds>}` and the server answers
`{"op":"ping","ts":...,"pong":<echoed>,"ping":<server seconds>}`. The server
does **not** send its own ping (verified: 45s idle, zero frames), so the client
must drive it. A missing pong within `pong_timeout` closes the socket to force
a reconnect.

---

## Tests

```bash
python -m pytest -q tests
```

249 tests covering indicators, risk math, all 16 strategies (including empty and
short-data inputs), aggregation rules, TP/SL planning, API-reference/client
drift, model probing, request signing, CLI routing, the thinking-level system
(levels, aliases, persistence, and the exact request parameter sent), the
balance command, empty-model-response diagnosis, and the order builder's
per-field validation.

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
