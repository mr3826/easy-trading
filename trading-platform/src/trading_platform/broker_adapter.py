"""BrokerAdapter contract for Phase 6.

Defines the interface that both deterministic fake brokers and real broker
integrations (e.g., Alpaca, Interactive Brokers) must implement. The OMS
depends on this contract, not on concrete broker implementations.

V1 contract methods:
- execute_order: Submit and track an order
- cancel_order: Cancel an existing order
- list_orders: List all open orders
- get_order_status: Get status of a specific order
- reconcile: Sync broker state with OMS
- Session lifecycle: start/stop/reconnect
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from trading_platform.domain import Order, OrderStatus
from trading_platform.oms.oms import OMS


@runtime_checkable
class BrokerAdapter(Protocol):
    """Protocol defining the broker adapter contract.

    Concrete implementations (FakeBroker, AlpacaBroker, IBKRBroker) must
    satisfy this protocol so the OMS can work with any broker.
    """

    # Broker identity
    broker_id: str
    connected: bool

    # Order execution
    def execute_order(self, order: Order, bar: Any) -> Dict[str, Any]:
        """Execute an order and return fill information.

        Returns dict with: order_id, symbol, side, quantity,
        fill_price, commission, slippage, status, fill_quantity
        """

    def cancel_order(self, order_id: str, reason: str = "") -> bool:
        """Cancel a specific order.

        Returns True if cancellation succeeded.
        """

    def list_orders(self) -> Dict[str, Any]:
        """List all open orders with their current state.

        Returns dict mapping order_id -> {status, symbol, side, quantity, price}
        """

    def get_order_status(self, order_id: str) -> Optional[str]:
        """Get the status of a specific order.

        Returns status name string or None if order not found.
        """

    # Session lifecycle
    def start(self) -> bool:
        """Start the broker connection.

        Returns True if connection succeeded.
        """

    def stop(self) -> bool:
        """Stop the broker connection.

        Returns True if disconnection succeeded.
        """

    def reconnect(self) -> bool:
        """Reconnect the broker session.

        Useful after network interruption. Returns True if reconnection succeeded.
        """

    # Reconciliation sync
    def sync_state(self, oms: OMS) -> None:
        """Sync broker state with OMS.

        V1: Update OMS orders dict from broker state, reconcile order statuses.
        """

    # Error handling
    def get_last_error(self) -> Optional[str]:
        """Get the last error string from the broker."""
        pass


# ---------------------------------------------------------------------------
# Real broker adapter boundary


class IBKRPaperBrokerAdapter:
    """IBKR broker adapter for paper trading integration.

    Implements the BrokerAdapter protocol for Interactive Brokers TWS/Gateway API.
    V1 supports: market orders, limit orders, cancel/replace, OCA groups,
    order status tracking, and basic reconciliation.

    This boundary deliberately has no fake-fill behavior. An implementation
    backed by an approved IBKR paper client must be supplied by deployment
    configuration and tested only under the external gate.
    """

    def __init__(self, paper: bool = True):
        if not paper:
            raise ValueError("IBKR adapter accepts paper configuration only")
        self._paper = paper
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._error: Optional[str] = None
        self._connected = False

    # --- Broker identity ---

    @property
    def broker_id(self) -> str:
        return "IBKR"

    @property
    def connected(self) -> bool:
        return self._connected if hasattr(self, "_connected") else False

    @connected.setter
    def connected(self, value: bool) -> None:
        self._connected = value

    # --- Order execution ---

    def execute_order(self, order: Order, bar: Any) -> Dict[str, Any]:
        """Reject until a real, paper-only client is explicitly configured."""
        raise RuntimeError("IBKR paper client is not configured; submission blocked")

    def cancel_order(self, order_id: str, reason: str = "") -> bool:
        """Cancel a specific order via IBKR.

        V1: Removes order from open orders dict.
        Returns True if order was found and cancelled.
        """
        if order_id in self._orders:
            self._orders[order_id]["status"] = "CANCELED"
            return True
        return False

    def list_orders(self) -> Dict[str, Any]:
        """List all open orders from IBKR perspective.

        V1: Returns dict mapping order_id -> {status, symbol, side, quantity, price}
        """
        # Return only orders that are not yet filled or cancelled
        return {oid: info for oid, info in self._orders.items() if info["status"] in ("SUBMITTED", "ACCEPTED", "OPEN")}

    def get_order_status(self, order_id: str) -> Optional[str]:
        """Get the status of a specific order from IBKR.

        V1: Returns status name string or None if order not found.
        """
        if order_id in self._orders:
            return self._orders[order_id]["status"]
        return None

    # --- Session lifecycle ---

    def start(self) -> bool:
        """Start the IBKR TWS/Gateway connection.

        V1: Paper mode connects to TWS paper trader.
        Returns True if connection succeeded.
        """
        self._error = "external IBKR paper setup required"
        return False

    def stop(self) -> bool:
        """Stop the IBKR connection.

        V1: Graceful disconnection.
        Returns True if disconnection succeeded.
        """
        self.connected = False
        return True

    def reconnect(self) -> bool:
        """Reconnect the IBKR session.

        V1: Useful after network interruption.
        Returns True if reconnection succeeded.
        """
        return self.start()

    # --- Reconciliation sync ---

    def sync_state(self, oms: "OMS") -> None:
        """Sync broker state with OMS.

        V1: Update OMS orders dict from broker state,
        reconcile order statuses.
        """
        # Sync orders from broker to OMS
        for order_id, broker_info in self.list_orders().items():
            # Check if OMS has this order
            if order_id in oms.orders:
                # Update OMS order status to match broker
                try:
                    oms.orders[order_id].status = OrderStatus[broker_info["status"]]
                except KeyError:
                    self._error = f"unknown broker status: {broker_info['status']}"
            else:
                # OMS doesn't know about this order - this is an inconsistency
                # that should be flagged
                logger = logging.getLogger("trading_platform.broker_adapter")
                logger.warning(f"Broker has order {order_id} but OMS does not")

        # Also sync OMS orders back to broker state (subset)
        for order_id, oms_order in oms.orders.items():
            if order_id not in self._orders:
                # Mark as SUBMITTED in broker if not already there
                self._orders[order_id] = {
                    "status": oms_order.status.name,
                    "symbol": oms_order.instrument.symbol,
                    "side": oms_order.side.name,
                    "quantity": oms_order.quantity,
                }

    def get_last_error(self) -> Optional[str]:
        """Get the last error string from the broker."""
        return self._error


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


# Example: AlpacaBroker skeleton (placeholder for future V2)
# class AlpacaBroker(BrokerAdapter):
#     broker_id = "ALPACA"
#     connected = False
#
#     def execute_order(self, order: Order, bar: Any) -> Dict[str, Any]: ...
#     def cancel_order(self, order_id: str, reason: str = "") -> bool: ...
#     def list_orders(self) -> Dict[str, Any]: ...
#     def get_order_status(self, order_id: str) -> Optional[str]: ...
#     def start(self) -> bool: ...
#     def stop(self) -> bool: ...
#     def reconnect(self) -> bool: ...
#     def sync_state(self, oms: OMS): ...
#     def get_last_error(self) -> Optional[str]: ...
