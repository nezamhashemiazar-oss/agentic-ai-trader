"""Bitunix error codes (from the official ErrorCode page) + helpers."""
from __future__ import annotations

ERROR_CODES: dict[int, str] = {
    0: "Success",
    403: "This API key does not support this operation",
    10001: "Network Error",
    10002: "Parameter Error",
    10003: "api-key can't be empty",
    10004: "The current ip is not in the apikey ip whitelist",
    10005: "Too many requests, please try again later",
    10006: "Request too frequently",
    10007: "Sign signature error",
    10008: "{value} does not comply with the rule, optional [correctValue]",
    10009: "Another order operation is in progress, please try again shortly",
    10010: "Nonce's length must be less than 64",
    10011: "The request timestamp must be within 60,000 ms of the server time.",
    20001: "Market not exists",
    20002: "The current positions amount has exceeded the maximum open limit, please adjust the risk limit",
    20003: "Insufficient balance",
    20004: "Insufficient Trader",
    20005: "Invalid leverage",
    20006: "You can't change leverage or margin mode as there are open orders",
    20007: "Order not found, please try it later",
    20008: "Insufficient amount",
    20009: "Position exists, so positions mode cannot be updated",
    20010: "Activation failed, the available balance in the futures account does not meet the conditions for activation of the coupon",
    20011: "Account not allowed to trade",
    20012: "This futures does not allow trading",
    20013: "Function disabled due tp pending account deletion request",
    20014: "Account deleted",
    20015: "This futures is not supported",
    20016: "Batch order exceeded the max size",
    30001: "Failed to order. Please adjust the order price or the leverage as the order price dealt may immediately liquidate.",
    30002: "Price below liquidated price",
    30003: "Price above liquidated price",
    30004: "Position not exist",
    30005: "The trigger price is closer to the current price and may be triggered immediately",
    30006: "Please select TP or SL",
    30007: "TP trigger price is greater than average entry price",
    30008: "TP trigger price is less than average entry price",
    30009: "SL trigger price is less than average entry price",
    30010: "SL trigger price is greater than average entry price",
    30011: "Abnormal order status",
    30012: "Already added to favorite",
    30013: "Exceeded the maximum order quantity",
    30014: "Max Buy Order Price",
    30015: "Mini Sell Order Price",
    30016: "The qty should be larger than",
    30017: "The qty cannot be less than the minimum qty",
    30018: "Order failed. No position opened. Cancel [Reduce-only] settings and retry later",
    30019: "Order failed. A [Reduce-only] order can not be in the same direction as the open position",
    30020: "Trigger price for TP should be higher than mark price",
    30021: "Trigger price for TP should be lower than mark price",
    30022: "Trigger price for SL should be higher than mark price",
    30023: "Trigger price for SL should be lower than mark price",
    30024: "Trigger price for SL should be lower than liq price",
    30025: "Trigger price for SL should be higher than liq price",
    30026: "TP price must be greater than last price",
    30027: "TP price must be greater than mark price",
    30028: "SL price must be less than last price",
    30029: "SL price must be less than mark price",
    30030: "SL price must be greater than last price",
    30031: "SL price must be greater than mark price",
    30032: "TP price must be less than last price",
    30033: "TP price must be less than mark price",
    30034: "TP price must be less than mark price",
    30035: "SL price must be greater than trigger price",
    30036: "TP price must be greater than trigger price",
    30037: "TP price must be greater than trigger price",
    30038: "TP/SL amount must be less than the size of the position.",
    30039: "The order qty can't be greater than the max order qty",
    30040: "Futures trading is prohibited, please contact customer service.",
    30041: "Trigger price must be greater than 0",
    30042: "Client ID duplicate",
    40001: "Please cancel open orders and close all positions before canceling lead trading",
    40002: "Lead amount hast to be over the limits",
    40003: "Lead order amount exceeds the limits.",
    40004: "Please do not repeat the operation",
    40005: "Action is not available for the current user type.",
    40006: "Sub-account reaches the limit.",
    40007: "Share settlement is being processed, lease try again later",
    40008: "After the transfer, the account balance will be less than the order amount, please enter again.",
}


def describe(code: int) -> str:
    return ERROR_CODES.get(code, f"Unknown error code {code}")


class BitunixAPIError(Exception):
    def __init__(self, code: int, msg: str = "", data=None):
        self.code = code
        self.msg = msg
        self.data = data
        super().__init__(f"[{code}] {msg or describe(code)}")

    @property
    def hint(self) -> str:
        return describe(self.code)
