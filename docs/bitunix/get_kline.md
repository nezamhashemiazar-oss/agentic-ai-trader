<!-- source: https://www.bitunix.com/api-docs/futures/market/get_kline.html -->

[Skip to content](https://www.bitunix.com/api-docs/futures/market/get_kline.html#VPContent)

Return to top

# Get Kline [​](https://www.bitunix.com/api-docs/futures/market/get_kline.html\#get-kline)

Rate Limit: 10 req/sec/ip

### Description [​](https://www.bitunix.com/api-docs/futures/market/get_kline.html\#description)

Interface is used to get future kline history.

### HTTP Request [​](https://www.bitunix.com/api-docs/futures/market/get_kline.html\#http-request)

- GET /api/v1/futures/market/kline

### Request Parameters [​](https://www.bitunix.com/api-docs/futures/market/get_kline.html\#request-parameters)

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| symbol | string | true | Trading pair, based on the symbolName, i.e. BTCUSDT |
| endTime | int64 | false | Exclusive end bound. Query `limit` klines before this time. Unix timestamp in milliseconds, e.g. 1672410000000. Best practice: align to the interval open time. If omitted, the current forming candle open time is used |
| interval | string | true | kline interval such as 1m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 3d 1w 1M |
| limit | int | false | Number of klines to return, counting backward from `endTime`. Default: 100, maximum: 200 |
| type | string | false | Kline type, values: LAST\_PRICE, MARK\_PRICE; default: LAST\_PRICE |

Request Example

Query the 2 closed 15m candles before `2022-12-30 16:00:00 UTC`. `endTime` is the 16:00 candle open time, so that candle is not included; the response is 15:30 and 15:45.

bash

```
curl -X 'GET' --location 'https://fapi.bitunix.com/api/v1/futures/market/kline?symbol=BTCUSDT&interval=15m&endTime=1672410000000&limit=2'
```

Omit `endTime` to take the latest closed candles from the current interval open time:

bash

```
curl -X 'GET' --location 'https://fapi.bitunix.com/api/v1/futures/market/kline?symbol=BTCUSDT&interval=15m&limit=100'
```

### Response Parameters [​](https://www.bitunix.com/api-docs/futures/market/get_kline.html\#response-parameters)

| Parameter | Type | Description |
| --- | --- | --- |
| open | string | open price |
| high | string | high price |
| low | string | low price |
| close | string | close price |
| quoteVol | string | Trading amount (quote currency turnover) for the kline period |
| baseVol | string | Trading volume (base coin) for the kline period |
| time | int64 | Candle open time, Unix timestamp in milliseconds. This is the open time, not the close time. Only closed candles are returned; the candle that is still forming is not included. |

Response Example

json

```
{"code":0,"data":[{"open":"60000","high":"60001","close":"60000","low":"59989.2","time":111111,"quoteVol":"1","baseVol":"60000","type":"LAST_PRICE"}],"msg":"Success"}
```