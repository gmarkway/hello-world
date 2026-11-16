import base64
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from nacl.signing import SigningKey

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rh_client import RobinhoodClient  # noqa: E402
from safety import Config, OrderRejected, TradingGuard  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1_763_280_000.0  # fixed time

    def __call__(self):
        return self.now


def make_guard(tmp_path, live=False, max_order="50", max_daily="200", price="100000"):
    placed = []
    cfg = Config(live_trading=live, max_order_usd=Decimal(max_order),
                 max_daily_usd=Decimal(max_daily), audit_log=tmp_path / "audit.jsonl")
    clock = Clock()
    guard = TradingGuard(cfg, lambda s, side: Decimal(price),
                         lambda **o: placed.append(o) or {"id": "abc"}, clock)
    return guard, placed, clock


def test_dry_run_never_calls_broker(tmp_path):
    guard, placed, _ = make_guard(tmp_path)
    p = guard.preview("btc-usd", "buy", "market", "0.0001")
    assert p["estimated_usd"] == "10.00"
    result = guard.confirm(p["preview_id"])
    assert result["dry_run"] is True
    assert placed == []


def test_live_order_uses_preview_id_as_client_order_id(tmp_path):
    guard, placed, _ = make_guard(tmp_path, live=True)
    p = guard.preview("BTC-USD", "buy", "market", "0.0001")
    guard.confirm(p["preview_id"])
    assert placed[0]["client_order_id"] == p["preview_id"]
    assert placed[0]["symbol"] == "BTC-USD"


def test_preview_cannot_be_reused(tmp_path):
    guard, _, _ = make_guard(tmp_path, live=True)
    p = guard.preview("BTC-USD", "buy", "market", "0.0001")
    guard.confirm(p["preview_id"])
    with pytest.raises(OrderRejected):
        guard.confirm(p["preview_id"])


def test_preview_expires(tmp_path):
    guard, placed, clock = make_guard(tmp_path, live=True)
    p = guard.preview("BTC-USD", "buy", "market", "0.0001")
    clock.now += 121
    with pytest.raises(OrderRejected, match="expired"):
        guard.confirm(p["preview_id"])
    assert placed == []


def test_per_order_limit(tmp_path):
    guard, _, _ = make_guard(tmp_path)
    with pytest.raises(OrderRejected, match="per-order"):
        guard.preview("BTC-USD", "buy", "market", "0.001")  # $100


def test_daily_limit_counts_live_orders_and_survives_restart(tmp_path):
    guard, _, _ = make_guard(tmp_path, live=True, max_daily="25")
    guard.confirm(guard.preview("BTC-USD", "buy", "market", "0.0001")["preview_id"])  # $10
    guard.confirm(guard.preview("BTC-USD", "buy", "market", "0.0001")["preview_id"])  # $20
    fresh, _, _ = make_guard(tmp_path, live=True, max_daily="25")  # simulated restart
    assert fresh.spent_today_usd() == Decimal("20.00")
    with pytest.raises(OrderRejected, match="daily"):
        fresh.preview("BTC-USD", "buy", "market", "0.0001")


def test_symbol_allowlist_and_bad_input(tmp_path):
    guard, _, _ = make_guard(tmp_path)
    with pytest.raises(OrderRejected, match="not in"):
        guard.preview("DOGE-USD", "buy", "market", "1")
    for qty in ("0", "-1", "abc", "NaN"):
        with pytest.raises(OrderRejected):
            guard.preview("BTC-USD", "buy", "market", qty)
    with pytest.raises(OrderRejected, match="limit_price"):
        guard.preview("BTC-USD", "buy", "limit", "0.0001")


def test_limit_order_uses_limit_price(tmp_path):
    guard, _, _ = make_guard(tmp_path)
    p = guard.preview("ETH-USD", "sell", "limit", "0.01", "3000")
    assert p["estimated_usd"] == "30.00"


def test_audit_log_written(tmp_path):
    guard, _, _ = make_guard(tmp_path)
    guard.confirm(guard.preview("BTC-USD", "buy", "market", "0.0001")["preview_id"])
    events = [json.loads(l)["event"] for l in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert events == ["preview", "order_placed"]


def test_signature_verifies():
    key = SigningKey.generate()
    seed_b64 = base64.b64encode(bytes(key)).decode()
    c = RobinhoodClient("my-api-key", seed_b64)
    sig = c.sign(1700000000, "/api/v1/crypto/trading/orders/", "POST", '{"a": 1}')
    msg = b'my-api-key1700000000/api/v1/crypto/trading/orders/POST{"a": 1}'
    key.verify_key.verify(msg, base64.b64decode(sig))  # raises if wrong
