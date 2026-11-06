<!-- source: https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html -->

[Skip to content](https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html#VPContent)

Return to top

### Websocket API [​](https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html\#websocket-api)

WebSocket is a new HTML5 protocol that achieves full-duplex data transmission between the client and server, allowing data to be transferred effectively in both directions. A connection between the client and server can be established with just one handshake. The server will then be able to push data to the client according to preset rules. Its advantages include:

- The WebSocket request header size for data transmission between client and server is only 2 bytes.
- Either the client or server can initiate data transmission.
- There's no need to repeatedly create and delete TCP connections, saving resources on bandwidth and server.

#### WebSocket Connection Limits [​](https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html\#websocket-connection-limits)

The WebSocket server accepts a maximum of 5 messages per second. Messages include:

- PING frames
- PONG frames
- JSON-formatted messages, such as subscribe and unsubscribe requests

If a user sends messages exceeding this limit, the connection will be disconnected. IPs that are repeatedly disconnected may be blocked by the server.

### OpenAPI Demo [​](https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html\#openapi-demo)

You can fill in your API key and API secret to try running the official demo provided.

[Github Demo](https://github.com/BitunixOfficial/open-api)

It is strongly recommended that developers use WebSocket API to obtain market information and transaction depth.

| Domain | WebSocket API | Recommended to use |
| --- | --- | --- |
| Websocket Domain | wss://fapi.bitunix.com/public/ | Main Domain, Public channel |
| Websocket Domain | wss://fapi.bitunix.com/private/ | Main Domain, Private channel |

### Ping [​](https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html\#ping)

##### Request Parameters [​](https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html\#request-parameters)

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation,ping |
| ping | int64 | Yes | Seconds format of Unix timestamp |

request example:

json

```
{
   "op":"ping",
   "ping":1732519687
}
```

response example:

json

```
{
   "op":"ping",
   "pong":1732519687,
   "ping":1732519690
}
```

### connect [​](https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html\#connect)

**Subscription limit**: Max 300 channel subscription/connection

### subscribe [​](https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html\#subscribe)

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation,subscribe |
| args | Array | Yes | List of subscribe channels |
| \> ch | String | Yes | Channel name |
| \> symbol | String | No | Instrument ID |

request example:

json

```
{
    "op":"subscribe",
    "args":[\
        {\
            "symbol":"BTCUSDT",\
            "ch":"market_kline_1min"\
        },\
        {\
            "symbol":"BTCUSDT",\
            "ch":"depth_books"\
        }\
    ]
}
```

### unsubscribe [​](https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html\#unsubscribe)

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation, unsubscribe |
| args | Array | Yes | List of subscribe channels |
| \> ch | String | Yes | Channel name |
| \> symbol | String | No | Instrument ID |

request example:

json

```
{
    "op":"unsubscribe",
    "args":[\
        {\
            "symbol":"BTCUSDT",\
            "channel":"market_kline_1min"\
        }\
    ]
}
```

### Login [​](https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html\#login)

##### Request Parameters [​](https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html\#request-parameters-1)

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| op | String | Yes | Operation,login |
| args | Array | Yes |  |
| \> apiKey | String | Yes | API Key |
| \> timestamp | Int | Yes | Seconds format of Unix timestamp |
| \> nonce | String | Yes | Random string |
| \> sign | String | Yes | Signature string |

request example:

json

```
{
   "op":"login",
   "args":[\
         {\
             "apiKey":"a91ma19akoo5kjihgvnkllohs61cvdf19v8a65a1a5s61cv6a81va65sdf19v8a65a1",\
             "timestamp": 1747402389,\
             "nonce":"o9jnhu8ijko2nbhy36fgt0mnjuyhgtsh",\
             "sign":"kkogbwoehuoenlbgagogheooeggehn939uh5gelqq33"\
         }\
   ]
}
```

##### Preparing for Access [​](https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html\#preparing-for-access)

If you need to use the API, please [login](https://www.bitunix.com/login) to complete the application of the API key and the configuration, and then develop and trade according to the details of this document.

You can click [here](https://www.bitunix.com/account/apiManagement) to create an API key.

Please make sure to remember the following information after you have successfully created the API Key:

- APIKey: Identity for API transactions, generated by random algorithm.
- SecretKey: private key, randomly generated by the system, used for signature generation.

> Risks: These two keys are closely related to the security of your account, please keep in mind that do not disclose them to others at any time. Any leakage of these two keys may cause loss of your assets. If you find any leakage of APIKey, please delete the APIKey as soon as possible.

###### Interface Type [​](https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html\#interface-type)

- Public
- Private Interface

###### Public Interface [​](https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html\#public-interface)

> Public interface can be used to obtain configuration information and market data. Public requests can be invoked without authentication.

###### private interface [​](https://www.bitunix.com/api-docs/futures/websocket/prepare/WebSocket.html\#private-interface)

> Private interface can be used for order management and account management. Each private request must be \[signed\] ( sign.md) using a canonical form of authentication. The private interface requires authentication using your APIKey.

Go

go

```
func Sign() string {
	apiKey := "your-apiKey"
	secretKey := "your-secretKey"
	nonce := "your-nonce"
	timestamp := time.Now().Unix()
	sign := sha256Hash(fmt.Sprintf("%s%d%s", nonce, timestamp, apiKey))
	sign = sha256Hash(fmt.Sprintf("%s%s", sign, secretKey))

	return sign
}

func sha256Hash(input string) string {
	hash := sha256.New()
	hash.Write([]byte(input))
	hashInBytes := hash.Sum(nil)
	hashInHex := hex.EncodeToString(hashInBytes)

	return hashInHex
}
```

Python

python

```
import hashlib
import time

def sign():
    api_key = "your-apiKey"
    secret_key = "your-secretKey"
    nonce = "your-nonce"
    timestamp = int(time.time())

    # First SHA-256 hash
    sign = hashlib.sha256((nonce + str(timestamp) + api_key).encode()).hexdigest()

    # Second SHA-256 hash
    sign = hashlib.sha256((sign + secret_key).encode()).hexdigest()

    return sign
```