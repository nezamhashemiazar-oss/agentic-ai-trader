<!-- source: https://www.bitunix.com/api-docs/futures/trade/place_order.html -->

[Skip to content](https://www.bitunix.com/api-docs/futures/trade/place_order.html#VPContent)

Return to top

# Place Order [​](https://www.bitunix.com/api-docs/futures/trade/place_order.html\#place-order)

Rate Limit: 20 req/sec/uid

### Description [​](https://www.bitunix.com/api-docs/futures/trade/place_order.html\#description)

Place order

### HTTP Request [​](https://www.bitunix.com/api-docs/futures/trade/place_order.html\#http-request)

- POST /api/v1/futures/trade/place\_order

### Request Parameters [​](https://www.bitunix.com/api-docs/futures/trade/place_order.html\#request-parameters)

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| symbol | string | true | Trading pair |
| qty | string | true | Amount (base coin) |
| price | string | false | Price of the order.<br>Required if the order type is **LIMIT** |
| side | string | true | Order direction<br>buy: **BUY**<br>sell: **SELL** |
| tradeSide | string | true | Direction<br>Only required in hedge-mode<br>Open and Close Notes:<br>For open long, side fill in"BUY"; tradeSide should be "OPEN"<br>For open short, side fill in "SELL"; tradeSide should be "OPEN"<br>For close long, side fill in "BUY"; tradeSide should be "CLOSE"<br>For close short, side fill in "SELL";tradeSide should be "CLOSE" |
| positionId | string | false | Position ID<br>Only required when "tradeSide" is "CLOSE" |
| orderType | string | true | Order type<br>**LIMIT**: limit orders<br>**MARKET**: market orders |
| effect | string | false | Order expiration date.<br>Required if the orderType is limit<br>**IOC**: Immediate or cancel<br>**FOK**: Fill or kill<br>**GTC**: Good till canceled(default value)<br>**POST\_ONLY**: POST only |
| clientId | string | false | Customize order ID |
| reduceOnly | boolean | false | Whether or not to just reduce the position |
| tpPrice | string | false | take profit trigger price |
| tpStopType | string | false | take profit trigger type <br>**MARK\_PRICE**<br>**LAST\_PRICE** |
| tpOrderType | string | false | take profit trigger place order type <br>**LIMIT**<br>**MARKET** |
| tpOrderPrice | string | false | take profit trigger place order price <br>**LIMIT**<br>**MARKET**<br> required if tpOrderType is **LIMIT** |
| slPrice | string | false | stop loss trigger price |
| slStopType | string | false | stop loss trigger type <br>**MARK\_PRICE**<br>**LAST\_PRICE** |
| slOrderType | string | false | stop loss trigger place order type <br>**LIMIT**<br>**MARKET** |
| slOrderPrice | string | false | stop loss trigger place order price <br>**LIMIT**<br>**MARKET**<br> required if slOrderType is **LIMIT** |

Request Example

bash

```
curl -X 'POST'  --location 'https://fapi.bitunix.com/api/v1/futures/trade/place_order' \
   -H "api-key:*******" \
   -H "sign:*" \
   -H "nonce:your-nonce" \
   -H "timestamp:1659076670000" \
   -H "language:en-US" \
   -H "Content-Type: application/json" \
 --data '{"symbol":"BTCUSDT","side":"BUY","price":"60000","qty":"0.5","positionId":"111","tradeSide":"CLOSE","orderType":"LIMIT","reduceOnly":false,"effect":"GTC","clientId":"1110000aaa","tpPrice":"61000","tpStopType":"MARK","tpOrderType":"LIMIT","tpOrderPrice":"61000.1"}'
```

### Response Parameters [​](https://www.bitunix.com/api-docs/futures/trade/place_order.html\#response-parameters)

| Parameter | Type | Description |
| --- | --- | --- |
| orderId | string | order id |
| clientId | string | client id |

Response Example

json

```
{"code":0,"data":{"orderId":"11111","clientId":"22222"},"msg":"Success"}
```