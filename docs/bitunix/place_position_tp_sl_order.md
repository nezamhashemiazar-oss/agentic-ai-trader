<!-- source: https://www.bitunix.com/api-docs/futures/tp_sl/place_position_tp_sl_order.html -->

[Skip to content](https://www.bitunix.com/api-docs/futures/tp_sl/place_position_tp_sl_order.html#VPContent)

Return to top

# Place Position TP/SL Order [​](https://www.bitunix.com/api-docs/futures/tp_sl/place_position_tp_sl_order.html\#place-position-tp-sl-order)

Rate Limit: 20 req/sec/UID

### Description [​](https://www.bitunix.com/api-docs/futures/tp_sl/place_position_tp_sl_order.html\#description)

Place Position TP/SL OrderWhen triggered, it will close the position at market price based on the position quantity at that time.Each position can only have one Position TP/SL Order

### HTTP Request [​](https://www.bitunix.com/api-docs/futures/tp_sl/place_position_tp_sl_order.html\#http-request)

- POST /api/v1/futures/tpsl/position/place\_order

### Request Parameters [​](https://www.bitunix.com/api-docs/futures/tp_sl/place_position_tp_sl_order.html\#request-parameters)

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| symbol | string | true | Trading pair |
| positionId | string | true | Position ID associated with take-profit and stop-loss |
| tpPrice | string | false | Take-profit trigger price<br>At least one of `tpPrice` or `slPrice` is required. |
| tpStopType | string | false | Take-profit trigger type<br>LAST\_PRICE<br>MARK\_PRICE<br> Default is market price. |
| slPrice | string | false | Stop-loss trigger price<br>At least one of `tpPrice` or `slPrice` is required. |
| slStopType | string | false | Stop-loss trigger type<br>LAST\_PRICE<br>MARK\_PRICE<br> Default is market price. |

Request Example

bash

```
curl -X 'POST'  --location 'https://fapi.bitunix.com/api/v1/futures/tpsl/position/place_order' \
   -H "api-key:*******" \
   -H "sign:*" \
   -H "nonce:your-nonce" \
   -H "timestamp:1659076670000" \
   -H "language:en-US" \
   -H "Content-Type: application/json" \
 --data '{"symbol":"BTCUSDT","positionId":"111","tpPrice":"12","tpStopType":"LAST_PRICE","slPrice":"9","slStopType":"LAST_PRICE"}'
```

### Response Parameters [​](https://www.bitunix.com/api-docs/futures/tp_sl/place_position_tp_sl_order.html\#response-parameters)

| Parameter | Type | Description |
| --- | --- | --- |
| orderId | string | TP/SL Order ID |

Response Example

json

```
{"code":0,"data":{"orderId":"11111"},"msg":"Success"}
```