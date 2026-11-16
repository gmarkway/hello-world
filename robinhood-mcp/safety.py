"""Safety rules that sit between Claude and real money.

- Orders must be previewed first, then confirmed with the preview's id.
- Each order and each day's total are capped in US dollars.
- Only symbols on your allow-list can be traded.
- Nothing is actually sent unless RH_LIVE_TRADING=true.
- Every preview, order and cancel is written to an audit log (one JSON per line).
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

PREVIEW_TTL_SECONDS = 120


@dataclass
class Config:
    live_trading: bool = False
    max_order_usd: Decimal = Decimal("50")
    max_daily_usd: Decimal = Decimal("200")
    allowed_symbols: set[str] = field(default_factory=lambda: {"BTC-USD", "ETH-USD"})
    audit_log: Path = Path("audit_log.jsonl")

    @classmethod
    def from_env(cls) -> "Config":
        symbols = os.environ.get("RH_ALLOWED_SYMBOLS", "BTC-USD,ETH-USD")
        return cls(
            live_trading=os.environ.get("RH_LIVE_TRADING", "false").lower() == "true",
            max_order_usd=Decimal(os.environ.get("RH_MAX_ORDER_USD", "50")),
            max_daily_usd=Decimal(os.environ.get("RH_MAX_DAILY_USD", "200")),
            allowed_symbols={s.strip().upper() for s in symbols.split(",") if s.strip()},
            audit_log=Path(os.environ.get(
                "RH_AUDIT_LOG", Path(__file__).resolve().parent / "audit_log.jsonl")),
        )


class OrderRejected(Exception):
    pass


class TradingGuard:
    def __init__(
        self,
        config: Config,
        price_lookup: Callable[[str, str], Decimal],
        place_fn: Callable[..., dict],
        clock: Callable[[], float] = time.time,
    ):
        """price_lookup(symbol, side) -> estimated USD price per unit.
        place_fn(**order) -> broker response (only called in live mode)."""
        self.config = config
        self.price_lookup = price_lookup
        self.place_fn = place_fn
        self.clock = clock
        self._previews: dict[str, dict] = {}

    # ---- audit log -----------------------------------------------------
    def log(self, event: str, **data) -> None:
        entry = {"time": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
                 "event": event, "live": self.config.live_trading, **data}
        with self.config.audit_log.open("a") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def spent_today_usd(self) -> Decimal:
        """Sum of live orders placed today (UTC), read from the audit log so it
        survives restarts."""
        today = datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()
        total = Decimal("0")
        if not self.config.audit_log.exists():
            return total
        for line in self.config.audit_log.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("event") == "order_placed" and e.get("live") and e["time"].startswith(today):
                total += Decimal(e["estimated_usd"])
        return total

    # ---- preview / confirm ----------------------------------------------
    def preview(self, symbol: str, side: str, order_type: str, quantity: str,
                limit_price: str | None = None) -> dict:
        symbol = symbol.upper().strip()
        side = side.lower().strip()
        order_type = order_type.lower().strip()

        if symbol not in self.config.allowed_symbols:
            raise OrderRejected(f"{symbol} is not in RH_ALLOWED_SYMBOLS "
                                f"({', '.join(sorted(self.config.allowed_symbols))}).")
        if side not in ("buy", "sell"):
            raise OrderRejected("side must be 'buy' or 'sell'.")
        if order_type not in ("market", "limit"):
            raise OrderRejected("order_type must be 'market' or 'limit'.")
        qty = _positive_decimal(quantity, "quantity")

        if order_type == "limit":
            if limit_price is None:
                raise OrderRejected("limit orders need a limit_price.")
            price = _positive_decimal(limit_price, "limit_price")
        else:
            limit_price = None
            price = self.price_lookup(symbol, side)

        estimated = (qty * price).quantize(Decimal("0.01"))
        self._check_limits(estimated)

        preview_id = str(uuid.uuid4())
        order = {"symbol": symbol, "side": side, "order_type": order_type,
                 "quantity": str(qty), "limit_price": limit_price}
        self._previews[preview_id] = {"order": order, "estimated_usd": estimated,
                                      "expires": self.clock() + PREVIEW_TTL_SECONDS}
        self.log("preview", preview_id=preview_id, estimated_usd=estimated, **order)
        return {
            "preview_id": preview_id,
            **order,
            "estimated_usd": str(estimated),
            "estimate_note": "Market orders use the current quote; the fill price can differ.",
            "live_trading": self.config.live_trading,
            "expires_in_seconds": PREVIEW_TTL_SECONDS,
            "next_step": "Show this to the user. Only after they explicitly approve, call "
                         "place_order with this preview_id.",
        }

    def confirm(self, preview_id: str) -> dict:
        p = self._previews.pop(preview_id, None)
        if p is None:
            raise OrderRejected("Unknown or already-used preview_id. Preview the order again.")
        if self.clock() > p["expires"]:
            raise OrderRejected("Preview expired. Preview the order again for a fresh quote.")
        self._check_limits(p["estimated_usd"])

        order = p["order"]
        if not self.config.live_trading:
            self.log("order_placed", preview_id=preview_id, estimated_usd=p["estimated_usd"],
                     dry_run=True, **order)
            return {"dry_run": True, "message": "RH_LIVE_TRADING is not 'true', so no real "
                    "order was sent.", "order": order, "estimated_usd": str(p["estimated_usd"])}

        try:
            # preview_id doubles as client_order_id so a retry can't create a duplicate.
            result = self.place_fn(client_order_id=preview_id, **order)
        except Exception as exc:
            self.log("order_failed", preview_id=preview_id, error=str(exc), **order)
            raise
        self.log("order_placed", preview_id=preview_id, estimated_usd=p["estimated_usd"],
                 response=result, **order)
        return {"dry_run": False, "order": order, "broker_response": result}

    def _check_limits(self, estimated: Decimal) -> None:
        if estimated > self.config.max_order_usd:
            raise OrderRejected(f"Estimated ${estimated} is over the per-order limit of "
                                f"${self.config.max_order_usd} (RH_MAX_ORDER_USD).")
        if self.config.live_trading:
            spent = self.spent_today_usd()
            if spent + estimated > self.config.max_daily_usd:
                raise OrderRejected(f"This would bring today's total to ${spent + estimated}, "
                                    f"over the daily limit of ${self.config.max_daily_usd} "
                                    "(RH_MAX_DAILY_USD).")


def _positive_decimal(value: str, name: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        raise OrderRejected(f"{name} must be a number, got {value!r}.")
    if not d.is_finite() or d <= 0:
        raise OrderRejected(f"{name} must be greater than zero.")
    return d
