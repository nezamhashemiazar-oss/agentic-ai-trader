# Bitunix API docs (local copy)

Fetched from the official Bitunix futures API documentation. These are the
contracts the engine is built against — when in doubt, this folder wins over
memory.

| File | Source |
|---|---|
| `get_tickers.md` | https://www.bitunix.com/api-docs/futures/market/get_tickers.html |
| `get_kline.md` | https://www.bitunix.com/api-docs/futures/market/get_kline.html |
| `get_depth.md` | https://www.bitunix.com/api-docs/futures/market/get_depth.html |
| `get_trading_pairs.md` | https://www.bitunix.com/api-docs/futures/market/get_trading_pairs.html |
| `funding_rate.md` | https://www.bitunix.com/api-docs/futures/market/get_funding_rate.html |
| `funding_rate_batch.md` | https://www.bitunix.com/api-docs/futures/market/get_funding_rate_batch.html |
| `funding_rate_history.md` | https://www.bitunix.com/api-docs/futures/market/get_funding_rate_history.html |
| `change_log.md` | https://www.bitunix.com/api-docs/futures/log/change_log.html |
| `get_single_account.md` | https://www.bitunix.com/api-docs/futures/account/get_single_account.html |
| `change_leverage.md` | https://www.bitunix.com/api-docs/futures/account/change_leverage.html |
| `change_margin_mode.md` | https://www.bitunix.com/api-docs/futures/account/change_margin_mode.html |
| `change_position_mode.md` | https://www.bitunix.com/api-docs/futures/account/change_position_mode.html |
| `get_leverage_and_margin_mode.md` | https://www.bitunix.com/api-docs/futures/account/get_leverage_and_margin_mode.html |
| `adjust_position_margin.md` | https://www.bitunix.com/api-docs/futures/account/adjust_position_margin.html |
| `asset_query.md` | https://www.bitunix.com/api-docs/futures/copyTrading/asset/asset_query.html |
| `get_pending_positions.md` | https://www.bitunix.com/api-docs/futures/position/get_pending_positions.html |
| `Position_history.md` | https://www.bitunix.com/api-docs/futures/position/get_history_positions.html |
| `get_position_tiers.md` | https://www.bitunix.com/api-docs/futures/position/get_position_tiers.html |
| `place_order.md` | https://www.bitunix.com/api-docs/futures/trade/place_order.html |
| `batch_order.md` | https://www.bitunix.com/api-docs/futures/trade/batch_order.html |
| `cancel_orders.md` | https://www.bitunix.com/api-docs/futures/trade/cancel_orders.html |
| `cancel_all_orders.md` | https://www.bitunix.com/api-docs/futures/trade/cancel_all_orders.html |
| `modify_order.md` | https://www.bitunix.com/api-docs/futures/trade/modify_order.html |
| `get_pending_orders.md` | https://www.bitunix.com/api-docs/futures/trade/get_pending_orders.html |
| `get_history_orders.md` | https://www.bitunix.com/api-docs/futures/trade/get_history_orders.html |
| `get_order_detail.md` | https://www.bitunix.com/api-docs/futures/trade/get_order_detail.html |
| `get_history_trades.md` | https://www.bitunix.com/api-docs/futures/trade/get_history_trades.html |
| `close_all_position.md` | https://www.bitunix.com/api-docs/futures/trade/close_all_position.html |
| `flash_close_position.md` | https://www.bitunix.com/api-docs/futures/trade/flash_close_position.html |
| `place_tp_sl_order.md` | https://www.bitunix.com/api-docs/futures/tp_sl/place_tp_sl_order.html |
| `place_position_tp_sl_order.md` | https://www.bitunix.com/api-docs/futures/tp_sl/place_position_tp_sl_order.html |
| `modify_tp_sl_order.md` | https://www.bitunix.com/api-docs/futures/tp_sl/modify_tp_sl_order.html |
| `modify_position_tp_sl_order.md` | https://www.bitunix.com/api-docs/futures/tp_sl/modify_position_tp_sl_order.html |
| `cancel_tp_sl_order.md` | https://www.bitunix.com/api-docs/futures/tp_sl/cancel_tp_sl_order.html |
| `get_pending_tp_sl_order.md` | https://www.bitunix.com/api-docs/futures/tp_sl/get_pending_tp_sl_order.html |
| `get_history_tp_sl_order.md` | https://www.bitunix.com/api-docs/futures/tp_sl/get_history_tp_sl_order.html |
| `WebSocket.md` | https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html |
| `error_code.md` | https://www.bitunix.com/api-docs/futures/ErrorCode/error_code.html |

## Related references

- Liquidation mechanism & tiered risk limits:
  https://support.bitunix.com/hc/en-us/articles/32152530856601
- Order units explained:
  https://www.bitunix.com/hub/helpcenter/article/explanation-of-the-order-units-in-futures-trading?id=170
- Official demo code: https://github.com/BitunixOfficial/open-api
  (mirror referenced in the task: https://github.com/qezawat-a/open-api/Demo)

## Note on coverage

The 12 files with full scraped content are the ones the engine depends on most
(`place_order`, `get_kline`, `get_tickers`, `get_trading_pairs`,
`get_single_account`, `get_pending_positions`, `get_position_tiers`,
`change_leverage`, `place_position_tp_sl_order`, `close_all_position`,
`WebSocket`, `error_code`). The remaining endpoints are fully specified in
`jrock/trader/api_reference.py` (method, path, auth, every parameter) and
implemented in `jrock/trader/client.py`; `tests/test_api_reference.py` keeps the
two in sync.
