"""MCP server that lets Claude use your Robinhood crypto account.

Run it from Claude Code / Claude Desktop (see README). Credentials and limits
come from environment variables; nothing secret lives in this file.
"""

from decimal import Decimal

from mcp.server.mcpserver import MCPServer

from rh_client import RobinhoodClient
from safety import Config, OrderRejected, TradingGuard

mcp = MCPServer("robinhood-crypto")
config = Config.from_env()
_client: RobinhoodClient | None = None


def client() -> RobinhoodClient:
    global _client
    if _client is None:
        import os
        _client = RobinhoodClient(os.environ.get("RH_API_KEY", ""),
                                  os.environ.get("RH_PRIVATE_KEY", ""))
    return _client


def _price_for(symbol: str, side: str) -> Decimal:
    """Buying pays the ask, selling receives the bid."""
    results = client().get_best_bid_ask(symbol).get("results", [])
    if not results:
        raise OrderRejected(f"No quote returned for {symbol}.")
    q = results[0]
    key = "ask_inclusive_of_buy_spread" if side == "buy" else "bid_inclusive_of_sell_spread"
    value = q.get(key) or q.get("price")
    if value is None:
        raise OrderRejected(f"Quote for {symbol} had no usable price: {q}")
    return Decimal(str(value))


guard = TradingGuard(config, _price_for, lambda **o: client().place_order(**o))


def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except OrderRejected as exc:
        return {"rejected": True, "reason": str(exc)}


# ---- read-only tools -------------------------------------------------------
@mcp.tool()
def get_account() -> dict:
    """Your Robinhood crypto account: status and buying power."""
    return client().get_account()


@mcp.tool()
def get_holdings(asset_codes: list[str] | None = None) -> dict:
    """Crypto you hold. Optionally filter, e.g. ["BTC", "ETH"]."""
    return client().get_holdings(asset_codes)


@mcp.tool()
def get_quote(symbol: str) -> dict:
    """Current best bid/ask for a trading pair such as "BTC-USD"."""
    return client().get_best_bid_ask(symbol.upper())


@mcp.tool()
def get_orders() -> dict:
    """Your recent crypto orders."""
    return client().get_orders()


@mcp.tool()
def get_order(order_id: str) -> dict:
    """One order's details and status."""
    return client().get_order(order_id)


@mcp.tool()
def get_limits() -> dict:
    """The safety limits in force and how much has been traded live today."""
    return {
        "live_trading": config.live_trading,
        "max_order_usd": str(config.max_order_usd),
        "max_daily_usd": str(config.max_daily_usd),
        "spent_today_usd": str(guard.spent_today_usd()),
        "allowed_symbols": sorted(config.allowed_symbols),
    }


# ---- trading tools ---------------------------------------------------------
@mcp.tool()
def preview_order(symbol: str, side: str, quantity: str, order_type: str = "market",
                  limit_price: str | None = None) -> dict:
    """Step 1 of placing an order. Checks the limits and returns a preview_id.
    Does NOT trade. Show the preview to the user and wait for an explicit "yes".

    symbol: e.g. "BTC-USD". side: "buy" or "sell".
    quantity: amount of the crypto asset (e.g. "0.0005" BTC), as a string.
    order_type: "market" or "limit". limit_price: required for limit orders.
    """
    return _safe(guard.preview, symbol, side, order_type, quantity, limit_price)


@mcp.tool()
def place_order(preview_id: str) -> dict:
    """Step 2: place a previously previewed order. Only call this after the user
    has explicitly approved that exact preview in this conversation."""
    return _safe(guard.confirm, preview_id)


@mcp.tool()
def cancel_order(order_id: str) -> dict:
    """Cancel an open order."""
    if not config.live_trading:
        return {"dry_run": True, "message": "RH_LIVE_TRADING is not 'true'; nothing cancelled."}
    result = client().cancel_order(order_id)
    guard.log("order_cancelled", order_id=order_id, response=result)
    return result


if __name__ == "__main__":
    mcp.run()
