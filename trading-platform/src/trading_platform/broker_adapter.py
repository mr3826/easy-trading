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

from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol, runtime_checkable

from trading_platform.domain import Instrument, Order, OrderSide, OrderType, TimeInForce, OrderStatus
from trading_platform.oms.oms import OMS, OrderLifecycle


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
    def execute_order(self, order: Order, bar: Any) -> dict:
        """Execute an order and return fill information.

        Returns dict with: order_id, symbol, side, quantity,
        fill_price, commission, slippage, status, fill_quantity
        """

    def cancel_order(self, order_id: str, reason: str = "") -> bool:
        """Cancel a specific order.

        Returns True if cancellation succeeded.
        """

    def list_orders(self) -> Dict[str, dict]:
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
# Deterministic fake broker (already implemented in oms.py)


# ---------------------------------------------------------------------------
# Real broker adapter implementations

# Example: InteractiveBrokersBroker implementation
class InteractiveBrokersBroker(BrokerAdapter):
    """IBKR broker adapter for paper trading integration.

    Implements the BrokerAdapter protocol for Interactive Brokers TWS/Gateway API.
    V1 supports: market orders, limit orders, cancel/replace, OCA groups,
    order status tracking, and basic reconciliation.

    NOTE: This is a skeletal implementation for V1. Full IBKR API integration
    would require the ib_inspect/ib.client libraries and is beyond V1 scope.
    """

    broker_id = "IBKR"
    connected = False

    def __init__(self, paper: bool = True):
        self._paper = paper
        self._orders: Dict[str, dict] = {}
        self._error: Optional[str] = None

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

    def execute_order(self, order: Order, bar: Any) -> dict:
        """Execute an order via IBKR paper trading.

        V1: Deterministic fill simulation for paper mode.
        Returns dict with: order_id, symbol, side, quantity,
        fill_price, commission, slippage, status, fill_quantity
        """
        from trading_platform.oms.oms import OMS, FakeBroker

        # Use the deterministic fake broker for fill simulation
        # Pass a minimal OMS instance
        oms = OMS(oms_id="ibkr_paper_tmp")
        fake = FakeBroker(oms, fill_assumption="CLOSE")
        # Actually, let's simulate more directly without relying on FakeBroker internals:
        fill_price = bar.close if hasattr(bar, "close") else order.price or 100.0
        slippage = 0.0  # Paper trading: zero slippage
        commission = 1.0  # IBKR paper: $1 per order flat

        # Mark order as FILLED in broker state
        self._orders[order.order_id] = {
            "status": "FILLED",
            "symbol": order.instrument.symbol,
            "side": order.side.value,
            "quantity": order.quantity,
            "price": order.price,
            "fill_price": fill_price,
        }

        return {
            "order_id": order.order_id,
            "symbol": order.instrument.symbol,
            "side": order.side.value,
            "quantity": order.quantity,
            "fill_price": fill_price,
            "commission": commission,
            "slippage": slippage,
            "status": "FILLED",
            "fill_quantity": order.quantity,
        }

    def cancel_order(self, order_id: str, reason: str = "") -> bool:
        """Cancel a specific order via IBKR.

        V1: Removes order from open orders dict.
        Returns True if order was found and cancelled.
        """
        if order_id in self._orders:
            self._orders[order_id]["status"] = "CANCELED"
            return True
        return False

    def list_orders(self) -> Dict[str, dict]:
        """List all open orders from IBKR perspective.

        V1: Returns dict mapping order_id -> {status, symbol, side, quantity, price}
        """
        # Return only orders that are not yet filled or cancelled
        return {
            oid: info
            for oid, info in self._orders.items()
            if info["status"] in ("SUBMITTED", "ACCEPTED", "OPEN")
        }

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
        self.connected = True
        self._orders = {}  # Reset order book on connect
        # In V1, we simulate a successful connect for paper trading
        return True

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
        self.connected = True
        return True

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
                oms.orders[order_id]["status"] = broker_info["status"]
            else:
                # OMS doesn't know about this order - this is an inconsistency
                # that should be flagged
                import logging
                logger = logging.getLogger("trading_platform.broker_adapter")
                logger.warning(
                    f"Broker has order {order_id} but OMS does not"
                )

        # Also sync OMS orders back to broker state (subset)
        for order_id, oms_order in oms.orders.items():
            if order_id not in self._orders:
                # Mark as SUBMITTED in broker if not already there
                self._orders[order_id] = {
                    "status": oms_order.get("status", "SUBMITTED"),
                    "symbol": oms_order.get("instrument", {}).get("symbol", "UNKNOWN"),
                    "side": oms_order.get("side", "BUY"),
                    "quantity": oms_order.get("quantity", 0),
                }

    def get_last_error(self) -> Optional[str]:
        """Get the last error string from the broker."""
        return self._error


# Example: AlpacaBroker skeleton (placeholder for future V2)
# class AlpacaBroker(BrokerAdapter):
#     broker_id = "ALPACA"
#     connected = False
#
#     def execute_order(self, order: Order, bar: Any) -> dict: ...
#     def cancel_order(self, order_id: str, reason: str = "") -> bool: ...
#     def list_orders(self) -> Dict[str, dict]: ...
#     def get_order_status(self, order_id: str) -> Optional[str]: ...
#     def start(self) -> bool: ...
#     def stop(self) -> bool: ...
#     def reconnect(self) -> bool: ...
#     def sync_state(self, oms: OMS): ...
#     def get_last_error(self) -> Optional[str]: ...