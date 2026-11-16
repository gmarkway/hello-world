# Robinhood Crypto ↔ Claude (MCP server)

This lets Claude check your Robinhood **crypto** account and place crypto orders, with safety limits.

> **Stocks are not supported.** Robinhood offers an official API only for crypto. Tools that trade stocks log in as you through Robinhood's private app API. They break often and may violate Robinhood's terms, so this project doesn't use them.

## What Claude can do

| Tool | What it does | Moves money? |
|---|---|---|
| `get_account`, `get_holdings`, `get_quote`, `get_orders`, `get_order`, `get_limits` | Look things up | No |
| `preview_order` | Check an order against your limits and return a `preview_id` | No |
| `place_order` | Send a previewed order, using its `preview_id` | **Yes**, only if live trading is on |
| `cancel_order` | Cancel an open order | Only if live trading is on |

## Safety rules (built in)

1. **Preview, then confirm.** Claude must preview an order first and show it to you. It can only place the order with that preview's id, which expires after 2 minutes and works once.
2. **Dollar limits.** Each order is capped (`RH_MAX_ORDER_USD`, default $50), and so is each day's total (`RH_MAX_DAILY_USD`, default $200).
3. **Allow-list.** Only the symbols in `RH_ALLOWED_SYMBOLS` can be traded (default `BTC-USD,ETH-USD`).
4. **Dry run by default.** Nothing is sent to Robinhood until you set `RH_LIVE_TRADING=true`.
5. **Audit log.** Every preview, order and cancel is recorded in `audit_log.jsonl`, in this folder.

Also, keep Claude's tool permission prompts **on** for `place_order`, so you approve each trade yourself.

## Setup (about 15 minutes)

You need Python 3.10 or newer.

**1. Install.** In a terminal, from this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

**2. Make your keys.**

```bash
python generate_keys.py
```

This prints a **public** key and a **private** key. Save the private key somewhere safe, such as a password manager. Never paste it into a chat, an email or a git commit.

**3. Create the API credential on Robinhood.** On the Robinhood **website**, open the crypto API settings (search their help center for "Crypto Trading API"). Create a credential, paste in your **public** key, and choose the permissions you want. Robinhood then gives you an **API key**, which looks like `rh-api-...`.

**4. Connect it to Claude.**

*Claude Code:* run this from this folder. Use your real values and the full path to this folder:

```bash
claude mcp add robinhood \
  -e RH_API_KEY=rh-api-xxxx \
  -e RH_PRIVATE_KEY=your-private-key \
  -e RH_LIVE_TRADING=false \
  -- "$PWD/.venv/bin/python" "$PWD/server.py"
```

*Claude Desktop:* open Settings → Developer → Edit Config and add:

```json
{
  "mcpServers": {
    "robinhood": {
      "command": "/full/path/to/robinhood-mcp/.venv/bin/python",
      "args": ["/full/path/to/robinhood-mcp/server.py"],
      "env": {
        "RH_API_KEY": "rh-api-xxxx",
        "RH_PRIVATE_KEY": "your-private-key",
        "RH_LIVE_TRADING": "false",
        "RH_MAX_ORDER_USD": "50",
        "RH_MAX_DAILY_USD": "200",
        "RH_ALLOWED_SYMBOLS": "BTC-USD,ETH-USD"
      }
    }
  }
}
```

**5. Try it in dry-run mode.** Ask Claude: *"What crypto do I hold?"* and then *"Preview buying $5 of BTC"*. When you confirm, you should get `dry_run: true` back, which means no real order was placed.

**6. Turn on live trading** only when you're ready. Change `RH_LIVE_TRADING` to `true` and restart Claude. Start with a tiny order.

## Before going live: check against Robinhood's docs

This code was written from Robinhood's Crypto Trading API (docs.robinhood.com/crypto/trading) without access to the docs at the time. Check these against the current docs:

- The base URL `https://trading.robinhood.com` and the endpoint paths in `rh_client.py`
- The signed message format: `api_key + timestamp + path + method + body`
- The quote field names `ask_inclusive_of_buy_spread` and `bid_inclusive_of_sell_spread` in `server.py`

If something is wrong, the read-only tools will fail with an error first, which is another reason to start in dry-run mode.

## Run the tests

```bash
pytest tests
```
