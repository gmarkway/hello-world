"""Small client for Robinhood's official Crypto Trading API.

Every request is signed with your Ed25519 private key. Robinhood checks the
signature, so your private key itself is never sent over the network.
"""

import base64
import json
import time
import uuid

import httpx
from nacl.signing import SigningKey

BASE_URL = "https://trading.robinhood.com"


class RobinhoodClient:
    def __init__(self, api_key: str, private_key_base64: str, base_url: str = BASE_URL):
        if not api_key or not private_key_base64:
            raise ValueError(
                "Missing credentials. Set RH_API_KEY and RH_PRIVATE_KEY (see README)."
            )
        self.api_key = api_key
        seed = base64.b64decode(private_key_base64)
        if len(seed) != 32:
            raise ValueError(
                "RH_PRIVATE_KEY should be the base64 of a 32-byte Ed25519 private key."
            )
        self._signing_key = SigningKey(seed)
        self._http = httpx.Client(base_url=base_url, timeout=15)

    # ---- signing -------------------------------------------------------
    def sign(self, timestamp: int, path: str, method: str, body: str) -> str:
        message = f"{self.api_key}{timestamp}{path}{method}{body}"
        signed = self._signing_key.sign(message.encode("utf-8"))
        return base64.b64encode(signed.signature).decode("utf-8")

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        body_str = json.dumps(body) if body is not None else ""
        timestamp = int(time.time())
        headers = {
            "x-api-key": self.api_key,
            "x-timestamp": str(timestamp),
            "x-signature": self.sign(timestamp, path, method, body_str),
            "Content-Type": "application/json",
        }
        resp = self._http.request(method, path, headers=headers, content=body_str or None)
        if resp.status_code >= 400:
            raise RuntimeError(f"Robinhood API error {resp.status_code}: {resp.text}")
        return resp.json() if resp.content else {}

    # ---- read-only -----------------------------------------------------
    def get_account(self) -> dict:
        return self._request("GET", "/api/v1/crypto/trading/accounts/")

    def get_holdings(self, asset_codes: list[str] | None = None) -> dict:
        query = "".join(f"&asset_code={c}" for c in (asset_codes or []))
        path = "/api/v1/crypto/trading/holdings/"
        return self._request("GET", path + ("?" + query[1:] if query else ""))

    def get_best_bid_ask(self, symbol: str) -> dict:
        return self._request("GET", f"/api/v1/crypto/marketdata/best_bid_ask/?symbol={symbol}")

    def get_orders(self) -> dict:
        return self._request("GET", "/api/v1/crypto/trading/orders/")

    def get_order(self, order_id: str) -> dict:
        return self._request("GET", f"/api/v1/crypto/trading/orders/{order_id}/")

    # ---- trading -------------------------------------------------------
    def place_order(
        self,
        client_order_id: str,
        side: str,
        symbol: str,
        order_type: str,
        quantity: str,
        limit_price: str | None = None,
    ) -> dict:
        body: dict = {
            "client_order_id": client_order_id or str(uuid.uuid4()),
            "side": side,
            "symbol": symbol,
            "type": order_type,
        }
        if order_type == "market":
            body["market_order_config"] = {"asset_quantity": quantity}
        elif order_type == "limit":
            body["limit_order_config"] = {
                "asset_quantity": quantity,
                "limit_price": limit_price,
                "time_in_force": "gtc",
            }
        else:
            raise ValueError("Only 'market' and 'limit' orders are supported.")
        return self._request("POST", "/api/v1/crypto/trading/orders/", body)

    def cancel_order(self, order_id: str) -> dict:
        return self._request("POST", f"/api/v1/crypto/trading/orders/{order_id}/cancel/")
