"""Deterministic in-process fake broker adapter.

It never communicates externally and wraps the OMS fake fill engine, so
default tests remain offline.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from trading_platform.domain import Order
from trading_platform.oms.oms import OMS


class FakeBrokerAdapter:
    """Deterministic in-process adapter; it never communicates externally."""

    broker_id = "FAKE"

    def __init__(self, oms: OMS) -> None:
        from trading_platform.oms.oms import FakeBroker

        self.oms = oms
        self._broker = FakeBroker(oms, fill_assumption="NEXT_OPEN")
        self.connected = False

    def start(self) -> bool:
        self.connected = True
        return True

    def stop(self) -> bool:
        self.connected = False
        return True

    def reconnect(self) -> bool:
        return self.start()

    def execute_order(self, order: Order, bar: Any) -> Dict[str, Any]:
        if not self.connected:
            raise RuntimeError("fake broker is disconnected")
        return self._broker.execute_order(order, bar)

    def cancel_order(self, order_id: str, reason: str = "") -> bool:
        return self.oms.cancel_order(order_id, reason)

    def list_orders(self) -> Dict[str, Any]:
        return self.oms.list_orders()

    def get_order_status(self, order_id: str) -> Optional[str]:
        return self.oms.get_order_status(order_id)

    def sync_state(self, oms: OMS) -> None:
        if not self.connected:
            raise RuntimeError("fake broker is disconnected")

    def get_last_error(self) -> Optional[str]:
        return None
