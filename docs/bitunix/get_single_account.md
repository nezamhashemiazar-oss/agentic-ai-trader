<!-- source: https://www.bitunix.com/api-docs/futures/account/get_single_account.html -->

[Skip to content](https://www.bitunix.com/api-docs/futures/account/get_single_account.html#VPContent)

Return to top

# Get Single Account [​](https://www.bitunix.com/api-docs/futures/account/get_single_account.html\#get-single-account)

Rate Limit: 10 req/sec/uid

### Description [​](https://www.bitunix.com/api-docs/futures/account/get_single_account.html\#description)

Get account details with the given 'marginCoin'

### HTTP Request [​](https://www.bitunix.com/api-docs/futures/account/get_single_account.html\#http-request)

- GET /api/v1/futures/account

### Request Parameters [​](https://www.bitunix.com/api-docs/futures/account/get_single_account.html\#request-parameters)

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| marginCoin | string | true | Margin coin |

Request Example

bash

```
curl -X 'GET'  --location 'https://fapi.bitunix.com/api/v1/futures/account?marginCoin=USDT' \
   -H "api-key:*******" \
   -H "sign:*" \
   -H "nonce:your-nonce" \
   -H "timestamp:1659076670000" \
   -H "language:en-US" \
   -H "Content-Type: application/json"
```

### Response Parameters [​](https://www.bitunix.com/api-docs/futures/account/get_single_account.html\#response-parameters)

| Parameter | Type | Description |
| --- | --- | --- |
| marginCoin | string | Margin Coin |
| available | string | Available quantity in the account.<br> This field + crossUnrealizedPNL = Actual maximum open amount |
| frozen | string | locked quantity of orders |
| margin | string | locked quantity of positions |
| transfer | string | Maximum transferable amount |
| positionMode | string | Position mode <br>**ONE\_WAY**<br>**HEDGE** |
| crossUnrealizedPNL | string | unrealizedPNL for cross positions |
| isolationUnrealizedPNL | string | unrealizedPNL for isolation positions |
| bonus | string | Futures Bonus |

Response Example

json

```
{"code":0,"data":[{"marginCoin":"USDT","available":"1000","frozen":"0","margin":"10","transfer":"1000","positionMode":"HEDGE","crossUnrealizedPNL":"2","isolationUnrealizedPNL":"0","bonus":"0"}],"msg":"Success"}
```