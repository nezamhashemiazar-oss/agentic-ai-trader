# State audit — what survived a restart, before and after

Every row was read from the code, not guessed. Line numbers are from the
pre-change tree (commit `dd647b2`); the "after" column reflects the current
tree.

Legend: **PERSISTED** = on disk and restored · **IN-MEMORY-ONLY** = lost on
restart · **MISSING** = not tracked at all.

## 1. Agent settings

| State | Where it lived | Before | After |
|---|---|---|---|
| `thinking_level` | `agent.py:292-304` → `data/settings.json` | PERSISTED | PERSISTED (now in `state.json`, versioned) |
| `market_data_source` | `agent.py:822` → `data/settings.json` | PERSISTED | PERSISTED |
| `model` (pinned) | `agent.py:697` → memory + `.env` | PERSISTED (via `.env`) | PERSISTED (store > `.env`) |
| `model_meta` (reasoning capability cache) | `agent.py:350-354` | PERSISTED | PERSISTED |
| `mcp_servers` / `skills` registry | `agent.py:333-345` → `data/registry.json` | PERSISTED | PERSISTED (unchanged) |
| `auto_accept_edit` | `config.py:44` ← `.env` only | PERSISTED (`.env`) | PERSISTED (`.env`) |
| `full_device_access` | `config.py:45` ← `.env` only | PERSISTED (`.env`) | PERSISTED (`.env`) |
| `block_destructive` | `config.py:46` ← `.env` only | PERSISTED (`.env`) | PERSISTED (`.env`) |

## 2. Engine settings — the main gap

All of these were `EngineState` dataclass fields (`engine.py:40-66`) seeded
once from `.env` in `__init__` (`engine.py:74-90`) and **never written back**.
A runtime change was lost on every restart.

| State | Where it lived | Before | After |
|---|---|---|---|
| `leverage` | `engine.py:51`, set at `engine.py:1000` | IN-MEMORY-ONLY | PERSISTED |
| `margin_mode` | `engine.py:52`, set at `engine.py:985` | IN-MEMORY-ONLY | PERSISTED |
| `position_mode` | `engine.py:53`, set at `engine.py:993` | IN-MEMORY-ONLY | PERSISTED |
| `max_open_positions` | `engine.py:54`, set at `engine.py:1010` | IN-MEMORY-ONLY | PERSISTED |
| `risk_pct` | `engine.py:55`, set at `engine.py:1016` | IN-MEMORY-ONLY | PERSISTED |
| `breakeven_pct` | `engine.py:56`, set at `engine.py:960` | IN-MEMORY-ONLY | PERSISTED |
| `trailing_trigger_roi_pct` | `engine.py:57`, set at `engine.py:966` | IN-MEMORY-ONLY | PERSISTED |
| `liq_sl_distance_pct` | `engine.py:58`, set at `engine.py:972` | IN-MEMORY-ONLY | PERSISTED |
| `min_agreeing` | `engine.py:59`, set at `engine.py:978` | IN-MEMORY-ONLY | PERSISTED |
| `timeframes` | `engine.py:60`, set at `engine.py:984` | IN-MEMORY-ONLY | PERSISTED |
| `min_confidence` | `engine.py:61`, set at `engine.py:990` | IN-MEMORY-ONLY | PERSISTED |
| `tf_min_confidence` | `engine.py:62`, set at `engine.py:996` | IN-MEMORY-ONLY | PERSISTED |
| `symbols` (watchlist) | `engine.py:50`, set at `engine.py:1004` | IN-MEMORY-ONLY | PERSISTED |
| `scan_interval` | `engine.py:46`, set at `engine.py:954` | IN-MEMORY-ONLY | PERSISTED |
| `report_interval` | `engine.py:45`, set at `engine.py:876` | IN-MEMORY-ONLY | PERSISTED |
| `scan_on` | `engine.py:42`, set at `engine.py:890` | IN-MEMORY-ONLY | PERSISTED |
| `report_on` | `engine.py:44`, set at `engine.py:870` | IN-MEMORY-ONLY | PERSISTED |
| `mid_management` | `engine.py:49`, set at `engine.py:896` | IN-MEMORY-ONLY | PERSISTED |
| `reversal` | `engine.py:47`, set at `engine.py:906` | IN-MEMORY-ONLY | PERSISTED |
| `reversal_confidence` | `engine.py:48`, set at `engine.py:900` | IN-MEMORY-ONLY | PERSISTED |
| `autotrade` | `engine.py:43`, set at `engine.py:857` | IN-MEMORY-ONLY | PERSISTED as *desired* state — **never auto-resumed** (see note) |
| `engine_on` | `engine.py:41`, set at `engine.py:250` | IN-MEMORY-ONLY | PERSISTED as *desired* state — **never auto-resumed** |

> **Why autotrade is not auto-resumed.** Silently restarting live order flow
> after a crash is the single most dangerous thing this engine could do. The
> value is recorded so the startup banner can say *"autotrade was ON before the
> restart — it is NOT auto-resumed"*, and the operator re-enables it
> deliberately with `start trading`.

## 3. Position / trade state

| State | Where it lived | Before | After |
|---|---|---|---|
| `positions` dict (management state) | `engine.py:99`, written at `engine.py:561` and `engine.py:629` | IN-MEMORY-ONLY | PERSISTED |
| `breakeven_done` | `tpsl.py:106` | IN-MEMORY-ONLY | PERSISTED |
| `trailing_active` | `tpsl.py:107` | IN-MEMORY-ONLY | PERSISTED |
| `trailing_stop` | `tpsl.py:108` | IN-MEMORY-ONLY | PERSISTED |
| `partial_done` | `tpsl.py:109` | IN-MEMORY-ONLY | PERSISTED |
| `peak_roi` | `tpsl.py:111` | IN-MEMORY-ONLY | PERSISTED |
| `opened_ts` | `tpsl.py:110` | IN-MEMORY-ONLY | PERSISTED |
| trade log | `engine.py:782` → `data/trades.jsonl` | PERSISTED | PERSISTED (unchanged) |
| TP/SL log | `engine.py:785` → `data/tpsl.jsonl` | PERSISTED | PERSISTED (unchanged) |
| signals log | `data/signals.jsonl` | PERSISTED | PERSISTED (unchanged) |
| audit log | `permissions.py:29` → `data/audit.jsonl` | PERSISTED | PERSISTED (unchanged) |

> **Why this mattered.** Losing `breakeven_done` / `trailing_stop` / `peak_roi`
> meant a restart re-armed breakeven and re-tightened a trailing stop it had
> already tightened — acting on a trade it believed was fresh. The exchange
> remains the source of truth for the position itself; this is the management
> state layered on top.

## 4. Counters and learned state

| State | Where it lived | Before | After |
|---|---|---|---|
| `scans` | `engine.py:65` | IN-MEMORY-ONLY | PERSISTED |
| `errors` (list) | `engine.py:66` | IN-MEMORY-ONLY | PERSISTED as count + last message |
| `last_scan_ts` / `last_report_ts` | `engine.py:63-64` | IN-MEMORY-ONLY | deliberately **not** persisted (wall-clock, meaningless after a restart) |
| WS reconnect count | `ws.py` feed object | IN-MEMORY-ONLY | IN-MEMORY-ONLY (per-connection by design) |
| price/kline cache counters | `price_cache.py`, `kline_cache.py` | IN-MEMORY-ONLY | IN-MEMORY-ONLY (per-session by design) |
| learned memory (feedback/episode/fact) | `memory.py` → `data/memory.jsonl` | PERSISTED but **unbounded** | PERSISTED, bounded at `MAX_MEMORY_ITEMS` (5000) with weighted eviction |
| memory durability | `memory.py:add` | append, no fsync | append + `fsync` (survives `kill -9`) |

## 5. What was MISSING entirely

| Item | Before | After |
|---|---|---|
| Schema version on the settings file | MISSING — a bare dict, no `version` | `version: 2` + migration path |
| Atomic write | MISSING — `write_text` could truncate on crash | temp file + `fsync` + `os.replace` + dir `fsync` |
| Corrupt-file handling | MISSING — `except: return`, silently wiped | backed up to `*.corrupt-<ts>`, loud warning, starts clean |
| Secret guard | MISSING — any key could be written | secret keys refused; registered secret *values* abort the write |
| Precedence rule | implicit and undocumented | explicit: store > `.env` > default, with `shadowed()` warning |
| `/memory clear` | MISSING | present, requires `confirm` |
| Store visibility in `/status` | MISSING | path, schema version, last save, size, migration/corruption flags |
| Startup state banner | MISSING | reports what was loaded and from where |

## 6. Files

| File | Role |
|---|---|
| `jrock/store.py` | **new** — the single owner of persisted state |
| `jrock/memory.py` | bounded + fsync + atomic compaction |
| `jrock/settings_store.py` | now a thin compat shim over `store.py` |
| `jrock/trader/engine.py` | persists engine state, positions, counters |
| `jrock/agent.py` | shared store, secret registration, precedence, `/memory`, banner |
| `jrock/config.py` | `MAX_MEMORY_ITEMS` |
| `tests/test_store.py` | **new** — 36 regression tests |
| `scripts/verify_store.py` | **new** — live SIGKILL/corruption/migration verification |
