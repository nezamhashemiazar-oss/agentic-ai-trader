# Bitunix Futures API contract

Source: https://www.bitunix.com/api-docs/futures/

Generated: 2026-11-07T07:09:11Z

**54 pages** — 39 REST endpoints, 10 WebSocket channels, 5 guides.

| # | Method | Path / channel | Required params | Optional |
|---|---|---|---|---|
| 1 | guide | `Change log` | — | — |
| 2 | POST | `/api/v1/futures/account/adjust_position_margin` | symbol, marginCoin, amount | side, positionId |
| 3 | POST | `/api/v1/futures/account/change_leverage` | marginCoin, symbol, leverage | — |
| 4 | POST | `/api/v1/futures/account/change_margin_mode` | marginMode, symbol, marginCoin | — |
| 5 | POST | `/api/v1/futures/account/change_position_mode` | positionMode | — |
| 6 | GET | `/api/v1/futures/account/get_leverage_margin_mode` | symbol, marginCoin | — |
| 7 | GET | `/api/v1/futures/account/position_mode` | — | — |
| 8 | GET | `/api/v1/futures/account` | marginCoin | — |
| 9 | GET | `/api/v1/futures/account/trading_settings` | — | symbols |
| 10 | guide | `Introduction` | — | — |
| 11 | guide | `Sign` | — | — |
| 12 | GET | `/api/v1/cp/asset/query` | — | — |
| 13 | POST | `/api/v1/cp/asset/transfer-to-sub-account` | amount, assetType | — |
| 14 | POST | `/api/v1/cp/asset/transfer-to-main-account` | amount, assetType | — |
| 15 | guide | `Error code` | — | — |
| 16 | GET | `/api/v1/futures/market/depth` | symbol | limit |
| 17 | GET | `/api/v1/futures/market/funding_rate/batch` | — | — |
| 18 | GET | `/api/v1/futures/market/get_funding_rate_history` | symbol | startTime, endTime, limit |
| 19 | GET | `/api/v1/futures/market/funding_rate` | symbol | — |
| 20 | GET | `/api/v1/futures/market/kline` | symbol, interval | endTime, limit, type |
| 21 | GET | `/api/v1/futures/market/tickers` | — | symbols |
| 22 | GET | `/api/v1/futures/market/trading_pairs` | — | symbols |
| 23 | GET | `/api/v1/futures/position/get_history_positions` | — | symbol, positionId, startTime, endTime, skip, limit, subAccountId |
| 24 | GET | `/api/v1/futures/position/get_pending_positions` | — | symbol, positionId, subAccountId, includeSubAccounts |
| 25 | GET | `/api/v1/futures/position/get_position_tiers` | symbol | — |
| 26 | POST | `/api/v1/futures/tpsl/cancel_order` | symbol, orderId | — |
| 27 | GET | `/api/v1/futures/tpsl/get_history_orders` | — | symbol, side, positionMode, startTime, endTime, skip, limit |
| 28 | GET | `/api/v1/futures/tpsl/get_pending_orders` | — | symbol, positionId, side, positionMode, skip, limit |
| 29 | POST | `/api/v1/futures/tpsl/position/modify_order` | symbol, positionId | tpPrice, tpStopType, slPrice, slStopType |
| 30 | POST | `/api/v1/futures/tpsl/modify_order` | orderId | tpPrice, tpStopType, slPrice, slStopType, tpOrderType, tpOrderPrice, slOrderType, slOrderPrice, tpQty, slQty |
| 31 | POST | `/api/v1/futures/tpsl/position/place_order` | symbol, positionId | tpPrice, tpStopType, slPrice, slStopType |
| 32 | POST | `/api/v1/futures/tpsl/place_order` | symbol, positionId | tpPrice, tpStopType, slPrice, slStopType, tpOrderType, tpOrderPrice, slOrderType, slOrderPrice, tpQty, slQty |
| 33 | POST | `/api/v1/futures/trade/batch_order` | symbol, orderList, >qty, >side, >tradeSide, >orderType | >price, >positionId, >effect, >clientId, >reduceOnly, >tpPrice, >tpStopType, >tpOrderType, >tpOrderPrice, >slPrice, >slStopType, >slOrderType, >slOrderPrice |
| 34 | POST | `/api/v1/futures/trade/cancel_all_orders` | — | symbol |
| 35 | POST | `/api/v1/futures/trade/cancel_orders` | symbol, orderList | orderId, clientId |
| 36 | POST | `/api/v1/futures/trade/close_all_position` | — | symbol |
| 37 | POST | `/api/v1/futures/trade/flash_close_position` | positionId | — |
| 38 | GET | `/api/v1/futures/trade/get_history_orders` | — | symbol, orderId, clientId, status, type, startTime, endTime, skip, limit, subAccountId, queryCanceled |
| 39 | GET | `/api/v1/futures/trade/get_history_trades` | — | symbol, orderId, positionId, startTime, endTime, skip, limit |
| 40 | GET | `/api/v1/futures/trade/get_order_detail` | — | orderId, clientId |
| 41 | GET | `/api/v1/futures/trade/get_pending_orders` | — | symbol, orderId, clientId, status, startTime, endTime, skip, limit |
| 42 | POST | `/api/v1/futures/trade/modify_order` | qty, price | orderId, clientId, tpPrice, tpStopType, tpOrderType, tpOrderPrice, slPrice, slStopType, slOrderType, slOrderPrice |
| 43 | POST | `/api/v1/futures/trade/place_order` | symbol, qty, side, tradeSide, orderType | price, positionId, effect, clientId, reduceOnly, tpPrice, tpStopType, tpOrderType, tpOrderPrice, slPrice, slStopType, slOrderType, slOrderPrice |
| 44 | guide | `WebSocket` | — | op, ping, op, args, > ch, > symbol, op, args, > ch, > symbol, op, args, > apiKey, > timestamp, > nonce, > sign |
| 45 | websocket | `balance` | — | — |
| 46 | websocket | `order` | — | — |
| 47 | websocket | `position` | — | — |
| 48 | websocket | `tpsl` | — | — |
| 49 | websocket | `depth_book1` | — | op, args, > ch, > symbol |
| 50 | websocket | `market_kline_1min` | — | op, args, > ch, > symbol |
| 51 | websocket | `price` | — | op, args, > symbol, > ch |
| 52 | websocket | `ticker` | — | op, args, > symbol, > ch |
| 53 | websocket | `tickers` | — | op, args, > symbol, > ch |
| 54 | websocket | `trade` | — | op, args, > symbol, > ch |

## Per-endpoint detail

### Change log

- Source: https://www.bitunix.com/api-docs/futures/log/change_log.html
- Fetched: 2026-11-07T07:09:11Z

### Adjust position margin

- Source: https://www.bitunix.com/api-docs/futures/account/adjust_position_margin.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/account/adjust_position_margin`
- rate limit: 5 req/sec/uid

| param | type | required |
|---|---|---|
| `symbol` | string | True |
| `marginCoin` | string | True |
| `amount` | string | True |
| `side` | string | False |
| `positionId` | string | False |

### Change leverage

- Source: https://www.bitunix.com/api-docs/futures/account/change_leverage.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/account/change_leverage`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `marginCoin` | string | True |
| `symbol` | string | True |
| `leverage` | int | True |

| response field | type |
|---|---|
| `marginCoin` | string |
| `symbol` | string |
| `leverage` | int |

### Change margin mode

- Source: https://www.bitunix.com/api-docs/futures/account/change_margin_mode.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/account/change_margin_mode`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `marginMode` | string | True |
| `symbol` | string | True |
| `marginCoin` | string | True |

| response field | type |
|---|---|
| `marginMode` | string |
| `symbol` | string |
| `marginCoin` | string |

### Change position mode

- Source: https://www.bitunix.com/api-docs/futures/account/change_position_mode.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/account/change_position_mode`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `positionMode` | string | True |

| response field | type |
|---|---|
| `positionMode` | string |

### Get leverage and margin mode

- Source: https://www.bitunix.com/api-docs/futures/account/get_leverage_and_margin_mode.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/account/get_leverage_margin_mode`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `symbol` | string | True |
| `marginCoin` | string | True |

| response field | type |
|---|---|
| `symbol` | string |
| `marginCoin` | string |
| `leverage` | int |
| `marginMode` | string |

### Get position mode

- Source: https://www.bitunix.com/api-docs/futures/account/get_position_mode.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/account/position_mode`
- rate limit: 20 req/sec/uid

| response field | type |
|---|---|
| `code` | integer |
| `msg` | string |
| `data` | object |
| `data.positionMode` | string |

### Get single account

- Source: https://www.bitunix.com/api-docs/futures/account/get_single_account.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/account`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `marginCoin` | string | True |

| response field | type |
|---|---|
| `marginCoin` | string |
| `available` | string |
| `frozen` | string |
| `margin` | string |
| `transfer` | string |
| `positionMode` | string |
| `crossUnrealizedPNL` | string |
| `isolationUnrealizedPNL` | string |
| `bonus` | string |

### Get trading settings

- Source: https://www.bitunix.com/api-docs/futures/account/get_trading_settings.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/account/trading_settings`
- rate limit: 20 req/sec/uid

| param | type | required |
|---|---|---|
| `symbols` | string | False |

| response field | type |
|---|---|
| `code` | integer |
| `msg` | string |
| `data` | list |
| `>symbol` | string |
| `>marginCoin` | string |
| `>marginMode` | string |
| `>leverage` | int |
| `>longLeverage` | int |
| `>shortLeverage` | int |

### Introduction

- Source: https://www.bitunix.com/api-docs/futures/common/introduction.html
- Fetched: 2026-11-07T07:09:11Z

### Sign

- Source: https://www.bitunix.com/api-docs/futures/common/sign.html
- Fetched: 2026-11-07T07:09:11Z

### Asset query

- Source: https://www.bitunix.com/api-docs/futures/copyTrading/asset/asset_query.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/cp/asset/query`
- rate limit: 10 req/sec/uid

| response field | type |
|---|---|
| `available` | string |
| `maxTransfer` | string |

### Transfer asset from main account to sub account

- Source: https://www.bitunix.com/api-docs/futures/copyTrading/asset/transfer_asset_from_main_account_to_sub_account.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/cp/asset/transfer-to-sub-account`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `amount` | string | True |
| `assetType` | string | True |

### Transfer asset from subaccount to main account

- Source: https://www.bitunix.com/api-docs/futures/copyTrading/asset/transfer_asset_from_subaccount_to_main_account.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/cp/asset/transfer-to-main-account`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `amount` | string | True |
| `assetType` | string | True |

### Error code

- Source: https://www.bitunix.com/api-docs/futures/ErrorCode/error_code.html
- Fetched: 2026-11-07T07:09:11Z

### Get depth

- Source: https://www.bitunix.com/api-docs/futures/market/get_depth.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/market/depth`
- rate limit: 10 req/sec/ip

| param | type | required |
|---|---|---|
| `symbol` | string | True |
| `limit` | string | False |

| response field | type |
|---|---|
| `asks.index[0]` | string |
| `asks.index[1]` | string |
| `bids.index[0]` | string |
| `bids.index[1]` | string |

### Get funding rate batch

- Source: https://www.bitunix.com/api-docs/futures/market/get_funding_rate_batch.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/market/funding_rate/batch`
- rate limit: 10 req/sec/ip

| response field | type |
|---|---|
| `symbol` | string |
| `markPrice` | decimal |
| `lastPrice` | decimal |
| `indexPrice` | decimal |
| `fundingRate` | decimal |
| `nextFundingTime` | int64 |
| `fundingInterval` | int32 |
| `maxFundingRate` | decimal |
| `minFundingRate` | decimal |

### Get funding rate history

- Source: https://www.bitunix.com/api-docs/futures/market/get_funding_rate_history.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/market/get_funding_rate_history`
- rate limit: 10 req/sec/ip

| param | type | required |
|---|---|---|
| `symbol` | string | True |
| `startTime` | int64 | False |
| `endTime` | int64 | False |
| `limit` | int32 | False |

| response field | type |
|---|---|
| `markPrice` | string |
| `fundingRate` | string |
| `fundingTime` | int64 |

### Get funding rate

- Source: https://www.bitunix.com/api-docs/futures/market/get_funding_rate.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/market/funding_rate`
- rate limit: 10 req/sec/ip

| param | type | required |
|---|---|---|
| `symbol` | string | True |

| response field | type |
|---|---|
| `symbol` | string |
| `markPrice` | decimal |
| `lastPrice` | decimal |
| `indexPrice` | decimal |
| `fundingRate` | decimal |
| `nextFundingTime` | int64 |
| `fundingInterval` | int32 |
| `maxFundingRate` | decimal |
| `minFundingRate` | decimal |

### Get kline

- Source: https://www.bitunix.com/api-docs/futures/market/get_kline.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/market/kline`
- rate limit: 10 req/sec/ip

| param | type | required |
|---|---|---|
| `symbol` | string | True |
| `endTime` | int64 | False |
| `interval` | string | True |
| `limit` | int | False |
| `type` | string | False |

| response field | type |
|---|---|
| `open` | string |
| `high` | string |
| `low` | string |
| `close` | string |
| `quoteVol` | string |
| `baseVol` | string |
| `time` | int64 |

### Get tickers

- Source: https://www.bitunix.com/api-docs/futures/market/get_tickers.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/market/tickers`
- rate limit: 10 req/sec/ip

| param | type | required |
|---|---|---|
| `symbols` | string | False |

| response field | type |
|---|---|
| `symbol` | string |
| `markPrice` | string |
| `lastPrice` | string |
| `open` | string |
| `last` | string |
| `quoteVol` | string |
| `baseVol` | string |
| `high` | string |
| `low` | string |

### Get trading pairs

- Source: https://www.bitunix.com/api-docs/futures/market/get_trading_pairs.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/market/trading_pairs`
- rate limit: 10 req/sec/ip

| param | type | required |
|---|---|---|
| `symbols` | string | False |

| response field | type |
|---|---|
| `symbol` | string |
| `base` | string |
| `quote` | string |
| `minTradeVolume` | string |
| `minBuyPriceOffset` | string |
| `maxSellPriceOffset` | string |
| `maxLimitOrderVolume` | string |
| `maxMarketOrderVolume` | string |
| `basePrecision` | int |
| `quotePrecision` | int |
| `maxLeverage` | int |
| `minLeverage` | int |
| `defaultLeverage` | int |
| `defaultMarginMode` | string |
| `priceProtectScope` | string |
| `symbolStatus` | string |
| `isApiSupported` | bool |
| `maxFundingRate` | decimal |
| `minFundingRate` | decimal |
| `launchTime` | long |
| `delistTime` | long |

### Get history positions

- Source: https://www.bitunix.com/api-docs/futures/position/get_history_positions.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/position/get_history_positions`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `symbol` | string | False |
| `positionId` | string | False |
| `startTime` | int64 | False |
| `endTime` | int64 | False |
| `skip` | int64 | False |
| `limit` | int64 | False |
| `subAccountId` | int64 | False |

| response field | type |
|---|---|
| `positionList` | list |
| `>positionId` | string |
| `>symbol` | string |
| `>maxQty` | string |
| `>entryPrice` | string |
| `>closePrice` | string |
| `>liqQty` | string |
| `>side` | string |
| `>marginMode` | string |
| `>positionMode` | string |
| `>leverage` | int32 |
| `>fee` | string |
| `>funding` | string |
| `>realizedPNL` | string |
| `>liqPrice` | string |
| `>ctime` | int64 |
| `>mtime` | int64 |
| `>subAccountId` | int64 |
| `total` | int64 |

### Get pending positions

- Source: https://www.bitunix.com/api-docs/futures/position/get_pending_positions.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/position/get_pending_positions`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `symbol` | string | False |
| `positionId` | string | False |
| `subAccountId` | int64 | False |
| `includeSubAccounts` | bool | False |

| response field | type |
|---|---|
| `positionId` | string |
| `symbol` | string |
| `qty` | string |
| `entryValue` | string |
| `side` | string |
| `marginMode` | string |
| `positionMode` | string |
| `leverage` | int32 |
| `fee` | string |
| `funding` | string |
| `realizedPNL` | string |
| `margin` | string |
| `unrealizedPNL` | string |
| `liqPrice` | string |
| `marginRate` | string |
| `avgOpenPrice` | string |
| `ctime` | int64 |
| `mtime` | int64 |
| `subAccountId` | int64 |

### Get position tiers

- Source: https://www.bitunix.com/api-docs/futures/position/get_position_tiers.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/position/get_position_tiers`
- rate limit: 10 req/sec/ip

| param | type | required |
|---|---|---|
| `symbol` | string | True |

| response field | type |
|---|---|
| `symbol` | string |
| `level` | int32 |
| `startValue` | string |
| `endValue` | string |
| `leverage` | int32 |
| `maintenanceMarginRate` | string |

### Cancel tp sl order

- Source: https://www.bitunix.com/api-docs/futures/tp_sl/cancel_tp_sl_order.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/tpsl/cancel_order`

| param | type | required |
|---|---|---|
| `symbol` | string | True |
| `orderId` | string | True |

| response field | type |
|---|---|
| `orderId` | string |

### Get history tp sl order

- Source: https://www.bitunix.com/api-docs/futures/tp_sl/get_history_tp_sl_order.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/tpsl/get_history_orders`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `symbol` | string | False |
| `side` | int32 | False |
| `positionMode` | int32 | False |
| `startTime` | int64 | False |
| `endTime` | int64 | False |
| `skip` | int64 | False |
| `limit` | int64 | False |

| response field | type |
|---|---|
| `orderList` | list |
| `>id` | string |
| `>positionId` | string |
| `>symbol` | string |
| `>base` | string |
| `>quote` | string |
| `>tpPrice` | string |
| `>tpStopType` | string |
| `>slPrice` | string |
| `>slStopType` | string |
| `>tpOrderType` | string |
| `>tpOrderPrice` | string |
| `>slOrderType` | string |
| `>slOrderPrice` | string |
| `>tpQty` | string |
| `>slQty` | string |
| `>status` | string |
| `>ctime` | int64 |
| `>triggerTime` | int64 |
| `total` | int64 |

### Get pending tp sl order

- Source: https://www.bitunix.com/api-docs/futures/tp_sl/get_pending_tp_sl_order.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/tpsl/get_pending_orders`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `symbol` | string | False |
| `positionId` | string | False |
| `side` | int32 | False |
| `positionMode` | int32 | False |
| `skip` | int64 | False |
| `limit` | int64 | False |

| response field | type |
|---|---|
| `id` | string |
| `positionId` | string |
| `symbol` | string |
| `base` | string |
| `quote` | string |
| `tpPrice` | string |
| `tpStopType` | string |
| `slPrice` | string |
| `slStopType` | string |
| `tpOrderType` | string |
| `tpOrderPrice` | string |
| `slOrderType` | string |
| `slOrderPrice` | string |
| `tpQty` | string |
| `slQty` | string |

### Modify position tp sl order

- Source: https://www.bitunix.com/api-docs/futures/tp_sl/modify_position_tp_sl_order.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/tpsl/position/modify_order`

| param | type | required |
|---|---|---|
| `symbol` | string | True |
| `positionId` | string | True |
| `tpPrice` | string | False |
| `tpStopType` | string | False |
| `slPrice` | string | False |
| `slStopType` | string | False |

| response field | type |
|---|---|
| `orderId` | string |

### Modify tp sl order

- Source: https://www.bitunix.com/api-docs/futures/tp_sl/modify_tp_sl_order.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/tpsl/modify_order`

| param | type | required |
|---|---|---|
| `orderId` | string | True |
| `tpPrice` | string | False |
| `tpStopType` | string | False |
| `slPrice` | string | False |
| `slStopType` | string | False |
| `tpOrderType` | string | False |
| `tpOrderPrice` | string | False |
| `slOrderType` | string | False |
| `slOrderPrice` | string | False |
| `tpQty` | string | False |
| `slQty` | string | False |

| response field | type |
|---|---|
| `orderId` | string |

### Place position tp sl order

- Source: https://www.bitunix.com/api-docs/futures/tp_sl/place_position_tp_sl_order.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/tpsl/position/place_order`

| param | type | required |
|---|---|---|
| `symbol` | string | True |
| `positionId` | string | True |
| `tpPrice` | string | False |
| `tpStopType` | string | False |
| `slPrice` | string | False |
| `slStopType` | string | False |

| response field | type |
|---|---|
| `orderId` | string |

### Place tp sl order

- Source: https://www.bitunix.com/api-docs/futures/tp_sl/place_tp_sl_order.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/tpsl/place_order`

| param | type | required |
|---|---|---|
| `symbol` | string | True |
| `positionId` | string | True |
| `tpPrice` | string | False |
| `tpStopType` | string | False |
| `slPrice` | string | False |
| `slStopType` | string | False |
| `tpOrderType` | string | False |
| `tpOrderPrice` | string | False |
| `slOrderType` | string | False |
| `slOrderPrice` | string | False |
| `tpQty` | string | False |
| `slQty` | string | False |

| response field | type |
|---|---|
| `orderId` | string |

### Batch order

- Source: https://www.bitunix.com/api-docs/futures/trade/batch_order.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/trade/batch_order`
- rate limit: 2 req/sec/uid

| param | type | required |
|---|---|---|
| `symbol` | string | True |
| `orderList` | list | True |
| `>qty` | string | True |
| `>price` | string | False |
| `>side` | string | True |
| `>tradeSide` | string | True |
| `>positionId` | string | False |
| `>orderType` | string | True |
| `>effect` | string | False |
| `>clientId` | string | False |
| `>reduceOnly` | boolean | False |
| `>tpPrice` | string | False |
| `>tpStopType` | string | False |
| `>tpOrderType` | string | False |
| `>tpOrderPrice` | string | False |
| `>slPrice` | string | False |
| `>slStopType` | string | False |
| `>slOrderType` | string | False |
| `>slOrderPrice` | string | False |

| response field | type |
|---|---|
| `successList` | list |
| `>id` | string |
| `>clientId` | string |
| `failureList` | list |
| `>clientId` | string |
| `>errorMsg` | string |
| `>errorCode` | string |

### Cancel all orders

- Source: https://www.bitunix.com/api-docs/futures/trade/cancel_all_orders.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/trade/cancel_all_orders`
- rate limit: 20 req/sec/uid

| param | type | required |
|---|---|---|
| `symbol` | string | False |

| response field | type |
|---|---|
| `successList` | list |
| `>id` | string |
| `>clientId` | string |
| `failureList` | list |
| `>id` | string |
| `>clientId` | string |
| `>errorMsg` | string |
| `>errorCode` | string |

### Cancel orders

- Source: https://www.bitunix.com/api-docs/futures/trade/cancel_orders.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/trade/cancel_orders`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `symbol` | string | True |
| `orderList` | list | True |
| `orderId` | string | False |
| `clientId` | string | False |

| response field | type |
|---|---|
| `successList` | list |
| `>id` | string |
| `>clientId` | string |
| `failureList` | list |
| `>id` | string |
| `>clientId` | string |
| `>errorMsg` | string |
| `>errorCode` | string |

### Close all position

- Source: https://www.bitunix.com/api-docs/futures/trade/close_all_position.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/trade/close_all_position`
- rate limit: 1 req/sec/uid

| param | type | required |
|---|---|---|
| `symbol` | string | False |

### Flash close position

- Source: https://www.bitunix.com/api-docs/futures/trade/flash_close_position.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/trade/flash_close_position`
- rate limit: 5 req/sec/uid

| param | type | required |
|---|---|---|
| `positionId` | String | True |

| response field | type |
|---|---|
| `positionId` | string |

### Get history orders

- Source: https://www.bitunix.com/api-docs/futures/trade/get_history_orders.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/trade/get_history_orders`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `symbol` | string | False |
| `orderId` | string | False |
| `clientId` | string | False |
| `status` | string | False |
| `type` | string | False |
| `startTime` | int64 | False |
| `endTime` | int64 | False |
| `skip` | int64 | False |
| `limit` | int64 | False |
| `subAccountId` | int64 | False |
| `queryCanceled` | boolean | False |

| response field | type |
|---|---|
| `orderList` | list |
| `>orderId` | string |
| `>symbol` | string |
| `>qty` | string |
| `>tradeQty` | string |
| `>positionMode` | string |
| `>marginMode` | string |
| `>leverage` | int |
| `>price` | string |
| `>side` | string |
| `>orderType` | string |
| `>effect` | string |
| `>clientId` | string |
| `>reduceOnly` | boolean |
| `>status` | string |
| `>fee` | string |
| `>realizedPNL` | string |
| `>tpPrice` | string |
| `>tpStopType` | string |
| `>tpOrderType` | string |
| `>tpOrderPrice` | string |
| `>slPrice` | string |
| `>slStopType` | string |
| `>slOrderType` | string |
| `>slOrderPrice` | string |
| `>ctime` | int64 |
| `>mtime` | int64 |
| `>subAccountId` | int64 |
| `total` | int64 |

### Get history trades

- Source: https://www.bitunix.com/api-docs/futures/trade/get_history_trades.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/trade/get_history_trades`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `symbol` | string | False |
| `orderId` | string | False |
| `positionId` | string | False |
| `startTime` | int64 | False |
| `endTime` | int64 | False |
| `skip` | int64 | False |
| `limit` | int64 | False |

| response field | type |
|---|---|
| `tradeList` | list |
| `>tradeId` | string |
| `>orderId` | string |
| `>symbol` | string |
| `>qty` | string |
| `>positionMode` | string |
| `>marginMode` | string |
| `>leverage` | int |
| `>price` | string |
| `>side` | string |
| `>orderType` | string |
| `>effect` | string |
| `>clientId` | string |
| `>reduceOnly` | boolean |
| `>fee` | string |
| `>realizedPNL` | string |
| `>ctime` | int64 |
| `>roleType` | string |
| `total` | int64 |

### Get order detail

- Source: https://www.bitunix.com/api-docs/futures/trade/get_order_detail.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/trade/get_order_detail`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `orderId` | string | False |
| `clientId` | string | False |

| response field | type |
|---|---|
| `orderId` | string |
| `symbol` | string |
| `qty` | string |
| `tradeQty` | string |
| `positionMode` | string |
| `marginMode` | string |
| `leverage` | int |
| `price` | string |
| `side` | string |
| `orderType` | string |
| `effect` | string |
| `clientId` | string |
| `reduceOnly` | boolean |
| `status` | string |
| `fee` | string |
| `realizedPNL` | string |
| `tpPrice` | string |
| `tpStopType` | string |
| `tpOrderType` | string |
| `tpOrderPrice` | string |
| `slPrice` | string |
| `slStopType` | string |
| `slOrderType` | string |
| `slOrderPrice` | string |
| `ctime` | int64 |
| `mtime` | int64 |

### Get pending orders

- Source: https://www.bitunix.com/api-docs/futures/trade/get_pending_orders.html
- Fetched: 2026-11-07T07:09:11Z
- `GET /api/v1/futures/trade/get_pending_orders`
- rate limit: 10 req/sec/uid

| param | type | required |
|---|---|---|
| `symbol` | string | False |
| `orderId` | string | False |
| `clientId` | string | False |
| `status` | string | False |
| `startTime` | int64 | False |
| `endTime` | int64 | False |
| `skip` | int64 | False |
| `limit` | int64 | False |

| response field | type |
|---|---|
| `orderList` | list |
| `>orderId` | string |
| `>symbol` | string |
| `>qty` | string |
| `>tradeQty` | string |
| `>positionMode` | string |
| `>marginMode` | string |
| `>leverage` | int |
| `>price` | string |
| `>side` | string |
| `>orderType` | string |
| `>effect` | string |
| `>clientId` | string |
| `>reduceOnly` | boolean |
| `>status` | string |
| `>fee` | string |
| `>realizedPNL` | string |
| `>tpPrice` | string |
| `>tpStopType` | string |
| `>tpOrderType` | string |
| `>tpOrderPrice` | string |
| `>slPrice` | string |
| `>slStopType` | string |
| `>slOrderType` | string |
| `>slOrderPrice` | string |
| `>ctime` | int64 |
| `>mtime` | int64 |
| `total` | int64 |

### Modify order

- Source: https://www.bitunix.com/api-docs/futures/trade/modify_order.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/trade/modify_order`
- rate limit: 20 req/sec/uid

| param | type | required |
|---|---|---|
| `orderId` | string | False |
| `clientId` | string | False |
| `qty` | string | True |
| `price` | string | True |
| `tpPrice` | string | False |
| `tpStopType` | string | False |
| `tpOrderType` | string | False |
| `tpOrderPrice` | string | False |
| `slPrice` | string | False |
| `slStopType` | string | False |
| `slOrderType` | string | False |
| `slOrderPrice` | string | False |

| response field | type |
|---|---|
| `orderId` | string |
| `clientId` | string |

### Place order

- Source: https://www.bitunix.com/api-docs/futures/trade/place_order.html
- Fetched: 2026-11-07T07:09:11Z
- `POST /api/v1/futures/trade/place_order`
- rate limit: 20 req/sec/uid

| param | type | required |
|---|---|---|
| `symbol` | string | True |
| `qty` | string | True |
| `price` | string | False |
| `side` | string | True |
| `tradeSide` | string | True |
| `positionId` | string | False |
| `orderType` | string | True |
| `effect` | string | False |
| `clientId` | string | False |
| `reduceOnly` | boolean | False |
| `tpPrice` | string | False |
| `tpStopType` | string | False |
| `tpOrderType` | string | False |
| `tpOrderPrice` | string | False |
| `slPrice` | string | False |
| `slStopType` | string | False |
| `slOrderType` | string | False |
| `slOrderPrice` | string | False |

| response field | type |
|---|---|
| `orderId` | string |
| `clientId` | string |

### WebSocket

- Source: https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html
- Fetched: 2026-11-07T07:09:11Z

| param | type | required |
|---|---|---|
| `op` | String | False |
| `ping` | int64 | False |
| `op` | String | False |
| `args` | Array | False |
| `> ch` | String | False |
| `> symbol` | String | False |
| `op` | String | False |
| `args` | Array | False |
| `> ch` | String | False |
| `> symbol` | String | False |
| `op` | String | False |
| `args` | Array | False |
| `> apiKey` | String | False |
| `> timestamp` | Int | False |
| `> nonce` | String | False |
| `> sign` | String | False |

### Balance Channel

- Source: https://www.bitunix.com/api-docs/futures/websocket/private/Balance%20Channel.html
- Fetched: 2026-11-07T07:09:11Z
- channel: `balance`

| response field | type |
|---|---|
| `ch` | String |
| `ts` | Int64 |
| `data` | <Object> |
| `> coin` | String |
| `> available` | String |
| `> frozen` | String |
| `> isolationFrozen` | String |
| `> crossFrozen` | String |
| `> margin` | String |
| `> isolationMargin` | String |
| `> crossMargin` | String |
| `> expMoney` | String |

### Order Channel

- Source: https://www.bitunix.com/api-docs/futures/websocket/private/Order%20Channel.html
- Fetched: 2026-11-07T07:09:11Z
- channel: `order`

| response field | type |
|---|---|
| `ch` | String |
| `ts` | Int64 |
| `data` | <Object> |
| `> event` | String |
| `> orderId` | String |
| `> symbol` | String |
| `> positionType` | String |
| `> positionMode` | String |
| `> side` | String |
| `> effect` | String |
| `> type` | String |
| `> qty` | String |
| `> price` | String |
| `> ctime` | String |
| `> mtime` | String |
| `> leverage` | String |
| `> orderStatus` | String |
| `> fee` | String |
| `> averagePrice` | String |
| `> dealAmount` | String |
| `> clientId` | String |
| `> tpStopType` | String |
| `> tpPrice` | String |
| `> tpOrderType` | String |
| `> tpOrderPrice` | String |
| `> slStopType` | String |
| `> slPrice` | String |
| `> slOrderType` | String |
| `> slOrderPrice` | String |

### Position Channel

- Source: https://www.bitunix.com/api-docs/futures/websocket/private/Position%20Channel.html
- Fetched: 2026-11-07T07:09:11Z
- channel: `position`

| response field | type |
|---|---|
| `ch` | String |
| `ts` | Int64 |
| `data` | <Object> |
| `> event` | String |
| `> positionId` | String |
| `> marginMode` | String |
| `> positionMode` | String |
| `> side` | String |
| `> leverage` | String |
| `> margin` | String |
| `> ctime` | String |
| `> qty` | String |
| `> symbol` | String |
| `> realizedPNL` | String |
| `> unrealizedPNL` | String |
| `> funding` | String |
| `> fee` | String |

### Tp Sl Channel

- Source: https://www.bitunix.com/api-docs/futures/websocket/private/Tp%20Sl%20Channel.html
- Fetched: 2026-11-07T07:09:11Z
- channel: `tpsl`

| response field | type |
|---|---|
| `ch` | String |
| `ts` | Int64 |
| `data` | <Object> |
| `> event` | String |
| `> positionId` | String |
| `> orderId` | String |
| `> symbol` | String |
| `> leverage` | String |
| `> side` | String |
| `> positionMode` | String |
| `> status` | String |
| `> ctime` | String |
| `> type` | String |
| `> tpQty` | String |
| `> slQty` | String |
| `> tpStopType` | String |
| `> tpPrice` | String |
| `> tpOrderType` | String |
| `> tpOrderPrice` | String |
| `> slStopType` | String |
| `> slPrice` | String |
| `> slOrderType` | String |
| `> slOrderPrice` | String |

### Depth channel

- Source: https://www.bitunix.com/api-docs/futures/websocket/public/depth%20channel.html
- Fetched: 2026-11-07T07:09:11Z
- channel: `depth_book1`

| param | type | required |
|---|---|---|
| `op` | String | False |
| `args` | List<Object> | False |
| `> ch` | String | False |
| `> symbol` | String | False |

| response field | type |
|---|---|
| `ch` | Object |
| `symbol` | String |
| `ts` | Int64 |
| `data` | String |
| `> a` | List<String> |
| `> b` | List<String> |

### Kline channel

- Source: https://www.bitunix.com/api-docs/futures/websocket/public/kline%20channel.html
- Fetched: 2026-11-07T07:09:11Z
- channel: `market_kline_1min`

| param | type | required |
|---|---|---|
| `op` | String | False |
| `args` | List<Object> | False |
| `> ch` | String | False |
| `> symbol` | String | False |

| response field | type |
|---|---|
| `ch` | String |
| `symbol` | String |
| `ts` | int64 |
| `data` | List<String> |
| `> o` | String |
| `> h` | String |
| `> l` | String |
| `> c` | String |
| `> b` | String |
| `> q` | String |

### MarketPrice Channel

- Source: https://www.bitunix.com/api-docs/futures/websocket/public/MarketPrice%20Channel.html
- Fetched: 2026-11-07T07:09:11Z
- channel: `price`

| param | type | required |
|---|---|---|
| `op` | String | False |
| `args` | List<Object> | False |
| `> symbol` | String | False |
| `> ch` | String | False |

| response field | type |
|---|---|
| `ch` | String |
| `symbol` | String |
| `ts` | int64 |
| `data` | List<String> |
| `> mp` | String |
| `> ip` | String |
| `> fr` | String |
| `> ft` | String |
| `> nft` | String |

### Ticker Channel

- Source: https://www.bitunix.com/api-docs/futures/websocket/public/Ticker%20Channel.html
- Fetched: 2026-11-07T07:09:11Z
- channel: `ticker`

| param | type | required |
|---|---|---|
| `op` | String | False |
| `args` | List<Object> | False |
| `> symbol` | String | False |
| `> ch` | String | False |

| response field | type |
|---|---|
| `ch` | String |
| `symbol` | String |
| `ts` | int64 |
| `data` | List<String> |
| `> s` | String |
| `> o` | String |
| `> h` | String |
| `> l` | String |
| `> la` | String |
| `> b` | String |
| `> q` | String |
| `> r` | String |

### Tickers Channel

- Source: https://www.bitunix.com/api-docs/futures/websocket/public/Tickers%20Channel.html
- Fetched: 2026-11-07T07:09:11Z
- channel: `tickers`

| param | type | required |
|---|---|---|
| `op` | String | False |
| `args` | List<Object> | False |
| `> symbol` | String | False |
| `> ch` | String | False |

| response field | type |
|---|---|
| `ch` | String |
| `ts` | int64 |
| `data` | List<String> |
| `> s` | String |
| `> o` | String |
| `> h` | String |
| `> l` | String |
| `> la` | String |
| `> b` | String |
| `> q` | String |
| `> r` | String |
| `> bd` | String |
| `> ak` | String |
| `> bv` | String |
| `> av` | String |

### Trade Channel

- Source: https://www.bitunix.com/api-docs/futures/websocket/public/Trade%20Channel.html
- Fetched: 2026-11-07T07:09:11Z
- channel: `trade`

| param | type | required |
|---|---|---|
| `op` | String | False |
| `args` | List<Object> | False |
| `> symbol` | String | False |
| `> ch` | String | False |

| response field | type |
|---|---|
| `ch` | String |
| `symbol` | String |
| `ts` | String |
| `data` | List<Object> |
| `> p` | String |
| `> v` | String |
| `> s` | String |
| `> t` | String |

