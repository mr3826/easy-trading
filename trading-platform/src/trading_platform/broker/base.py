"""BrokerAdapter contract for the OMS boundary.

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

from typing import Any, Dict, Optional, Protocol, runtime_checkable

from trading_platform.domain import Order
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
        ...
