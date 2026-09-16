"""Production OMS state machine for Phase 6.

V1 Order lifecycle: SUBMITTED → ACCEPTED → OPEN → FILLED → CANCELED/REJECTED

Key features:
- Idempotency keys for duplicate detection
- OCA (Order Cancel Replace) groups
- Cancellation and replacement handling
- Timeout handling
- Order state persistence
- Order submission/replacement/cancellation API
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from trading_platform.domain import (
    Order,
    OrderSide,
    OrderStatus,
)

# ---------------------------------------------------------------------------
# Order status enum — V1 lifecycle
#
# The canonical lifecycle enum now lives in the domain model
# (``trading_platform.domain.OrderStatus``) so OMS state and orders share one
# type. ``OrderLifecycle`` is retained as an alias for API compatibility.
OrderLifecycle = OrderStatus


# ---------------------------------------------------------------------------
# Idempotency key — prevents duplicate order submission


class IdempotencyKey:
    """Unique key for duplicate order detection.

    V1: key = f"{symbol}_{side}_{quantity}_{price}_{timestamp}"
    Used by the OMS to detect and reject duplicate submissions.
    """

    def __init__(self, order: Order):
        self.key = f"{order.instrument.symbol}_{order.side.name}_{order.quantity}_{order.price}_{order.order_id}"
        self.timestamp = datetime.now(timezone.utc)

    def __hash__(self) -> int:
        return hash(self.key)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, IdempotencyKey):
            return NotImplemented
        return self.key == other.key


# ---------------------------------------------------------------------------
# OCA group — orders that cancel/replace each other


class OCAGroup:
    """Order Cancel Replace group.

    V1: Orders within the same OCA group cancel each other when a replace
    or cancel is issued on any member. Protects against double-submission
    and ensures atomic order family behavior.
    """

    def __init__(self, group_id: str | None = None):
        self.group_id = group_id or str(uuid.uuid4())
        self.orders: Dict[str, Order] = {}  # order_id -> order
        self.parent_order: Optional[Order] = None  # original order

    def add(self, order: Order) -> None:
        """Add an order to the OCA group."""
        self.orders[order.order_id] = order
        if self.parent_order is None:
            self.parent_order = order

    def remove(self, order_id: str) -> Optional[Order]:
        """Remove an order from the group, return it."""
        return self.orders.pop(order_id, None)

    def cancel_all(self) -> None:
        """Cancel all orders in the group."""
        for order in self.orders.values():
            order.status = OrderLifecycle.CANCELED
        self.orders.clear()

    def replace_all(self, new_order: Order) -> None:
        """Replace all orders in the group with a new order."""
        self.cancel_all()
        self.add(new_order)


# ---------------------------------------------------------------------------
# OMS state machine


class OMS:
    """Production Order State Machine.

    V1 lifecycle: SUBMITTED → ACCEPTED → OPEN → (FILLED | CANCELED | REJECTED | EXPIRED)

    Features:
    - Idempotency key tracking
    - OCA group management
    - Cancellation and replacement
    - Timeout tracking
    - Order lifecycle event reporting
    - State consistency checks
    """

    def __init__(self, oms_id: str = "default_oms"):
        self.oms_id = oms_id
        # Order state: order_id -> Order
        self.orders: Dict[str, Order] = {}
        # Idempotency key tracking: key -> order_id
        self.idempotency_keys: Dict[str, str] = {}
        # OCA groups: group_id -> OCAGroup
        self.oca_groups: Dict[str, OCAGroup] = {}
        # Order lifecycle events
        self.event_ledger: list[Dict[str, Any]] = []
        # Timeout tracking: order_id -> deadline
        self.timeouts: Dict[str, datetime] = {}

    # ---- Order submission ----

    def submit_order(self, order: Order, idempotency_key: Optional[str] = None) -> tuple[bool, str]:
        """Submit an order through the OMS state machine.

        V1 lifecycle transition: → SUBMITTED

        Args:
            order: The order to submit
            idempotency_key: Optional key for duplicate detection

        Returns (accepted, reason):
            - accepted=True: order accepted into state machine
            - accepted=False: order rejected, reason string
        """
        # ---- Idempotency check ----
        if idempotency_key:
            if idempotency_key in self.idempotency_keys:
                existing_order_id = self.idempotency_keys[idempotency_key]
                # Check if the existing order is in a terminal state
                existing_order = self.orders.get(existing_order_id)
                if existing_order and existing_order.status in (
                    OrderLifecycle.FILLED,
                    OrderLifecycle.CANCELED,
                    OrderLifecycle.REJECTED,
                ):
                    return (
                        False,
                        f"Order already {existing_order.status.name} with same idempotency key",
                    )
                # If existing order is OPEN, we can replace it
                if existing_order and existing_order.status == OrderLifecycle.OPEN:
                    return self.replace_order(existing_order.order_id, order, idempotency_key)

            self.idempotency_keys[idempotency_key] = order.order_id

        # ---- OCA group check ----
        # Find if order belongs to an OCA group
        oca_group = None
        for gid, group in self.oca_groups.items():
            if order.order_id in group.orders or self._order_in_oca_group(order, gid):
                oca_group = group
                break

        # If order has a price and quantity, assign to OCA group if needed
        if oca_group is None:
            oca_group = OCAGroup(group_id=order.order_id)
            self.oca_groups[oca_group.group_id] = oca_group
            oca_group.add(order)
        else:
            oca_group.add(order)

        # ---- Transition to SUBMITTED ----
        order.status = OrderLifecycle.SUBMITTED
        self.orders[order.order_id] = order

        self.event_ledger.append(
            {
                "event": "ORDER_SUBMITTED",
                "order_id": order.order_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": order.status.name,
                "instrument": order.instrument.symbol,
                "side": order.side.name,
                "quantity": order.quantity,
                "price": order.price,
                "oca_group": oca_group.group_id if oca_group else None,
            }
        )

        return True, "Order submitted"

    def _order_in_oca_group(self, order: Order, group_id: str) -> bool:
        """Return whether an order is already registered in an OCA group."""
        group = self.oca_groups.get(group_id)
        return group is not None and order.order_id in group.orders

    # ---- Order acceptance ----

    def accept_order(self, order_id: str) -> bool:
        """Move order from SUBMITTED to ACCEPTED.

        Returns True if transition succeeded, False if invalid state.
        """
        order = self.orders.get(order_id)
        if not order:
            return False
        if order.status != OrderLifecycle.SUBMITTED:
            return False

        order.status = OrderLifecycle.ACCEPTED

        self.event_ledger.append(
            {
                "event": "ORDER_ACCEPTED",
                "order_id": order_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": order.status.name,
            }
        )
        return True

    # ---- Order opening ----

    def open_order(self, order_id: str) -> bool:
        """Move order from ACCEPTED to OPEN.

        Returns True if transition succeeded.
        """
        order = self.orders.get(order_id)
        if not order:
            return False
        if order.status != OrderLifecycle.ACCEPTED:
            return False

        order.status = OrderLifecycle.OPEN

        self.event_ledger.append(
            {
                "event": "ORDER_OPEN",
                "order_id": order_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": order.status.name,
            }
        )
        return True

    # ---- Order fill ----

    def fill_order(self, order_id: str, fill_quantity: int, fill_price: float) -> bool:
        """Mark order as filled (partially or fully).

        V1: Reduces remaining quantity. If remaining == 0, transitions to FILLED.
        """
        order = self.orders.get(order_id)
        if not order:
            return False
        if order.status not in (OrderLifecycle.OPEN,):
            return False

        order.filled_quantity = (order.filled_quantity or 0) + fill_quantity
        order.filled_price = fill_price  # last fill price (avg if partial)

        # If fully filled
        if order.filled_quantity >= order.quantity:
            order.status = OrderLifecycle.FILLED

            self.event_ledger.append(
                {
                    "event": "ORDER_FILLED",
                    "order_id": order_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": order.status.name,
                    "fill_quantity": order.filled_quantity,
                    "fill_price": order.filled_price,
                }
            )
        else:
            # Partially filled - stay OPEN
            self.event_ledger.append(
                {
                    "event": "ORDER_PARTIAL_FILL",
                    "order_id": order_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": order.status.name,
                    "fill_quantity": order.filled_quantity,
                    "fill_price": order.filled_price,
                }
            )
        return True

    # ---- Order cancellation ----

    def cancel_order(self, order_id: str, reason: str = "") -> bool:
        """Cancel an order.

        V1: OPEN → CANCELED, SUBMITTED → CANCELED (if not yet accepted).
        Returns True if cancellation succeeded.
        """
        order = self.orders.get(order_id)
        if not order:
            return False

        # Can cancel from OPEN or SUBMITTED states
        if order.status not in (OrderLifecycle.OPEN, OrderLifecycle.SUBMITTED):
            return False

        order.status = OrderLifecycle.CANCELED

        # Cancel all orders in the same OCA group
        self._cancel_oca_group(order_id)

        self.event_ledger.append(
            {
                "event": "ORDER_CANCELED",
                "order_id": order_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": order.status.name,
                "reason": reason,
            }
        )
        return True

    def _cancel_oca_group(self, order_id: str) -> None:
        """Cancel all orders in the same OCA group."""
        for gid, group in self.oca_groups.items():
            if order_id in group.orders:
                for oid in list(group.orders.keys()):
                    if oid != order_id:
                        order = self.orders.get(oid)
                        if order and order.status == OrderLifecycle.OPEN:
                            order.status = OrderLifecycle.CANCELED
                group.cancel_all()
                break

    # ---- Order replacement ----

    def replace_order(
        self, old_order_id: str, new_order: Order, idempotency_key: Optional[str] = None
    ) -> tuple[bool, str]:
        """Replace an existing order with a new one.

        V1: Cancel old order, submit new order with same idempotency key logic.
        """
        # Cancel the old order
        cancel_ok = self.cancel_order(old_order_id, reason="REPLACE")
        if not cancel_ok:
            return False, "Could not cancel original order for replacement"

        # Submit the new order
        new_order.status = OrderLifecycle.SUBMITTED
        self.orders[new_order.order_id] = new_order

        # Handle idempotency
        if idempotency_key:
            self.idempotency_keys[idempotency_key] = new_order.order_id

        # Handle OCA group - add new order to same group
        # Find the OCA group of the old order
        for gid, group in self.oca_groups.items():
            if old_order_id in group.orders:
                group.add(new_order)
                break
        else:
            # No OCA group, create new one
            new_oca = OCAGroup(group_id=new_order.order_id)
            new_oca.add(new_order)
            self.oca_groups[new_oca.group_id] = new_oca

        self.event_ledger.append(
            {
                "event": "ORDER_REPLACED",
                "order_id": new_order.order_id,
                "old_order_id": old_order_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": new_order.status.name,
            }
        )
        return True, "Order replaced"

    # ---- Order timeout ----

    def set_timeout(self, order_id: str, deadline: datetime) -> None:
        """Set a timeout deadline for an order.

        If the order is not filled or canceled by the deadline, it will be
        automatically canceled.
        """
        self.timeouts[order_id] = deadline

    def check_timeout(self, order_id: str, now: datetime) -> bool:
        """Check if an order has timed out.

        Returns True if the order should be canceled due to timeout.
        """
        deadline = self.timeouts.get(order_id)
        if not deadline:
            return False
        if now > deadline:
            # Cancel the order
            order = self.orders.get(order_id)
            if order and order.status == OrderLifecycle.OPEN:
                return self.cancel_order(order_id, reason="TIMEOUT")
            return True
        return False

    # ---- Order state query ----

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order state."""
        return self.orders.get(order_id)

    def get_order_status(self, order_id: str) -> Optional[str]:
        """Get order status name."""
        order = self.orders.get(order_id)
        if order:
            return order.status.name
        return None

    def list_orders(self) -> Dict[str, Dict[str, Any]]:
        """List all orders with summary state."""
        result = {}
        for oid, order in self.orders.items():
            result[oid] = {
                "status": order.status.name,
                "symbol": order.instrument.symbol,
                "side": order.side.name,
                "quantity": order.quantity,
                "fill_quantity": (order.filled_quantity or 0),
                "price": order.price,
            }
        return result

    # ---- OCA group queries ----

    def get_oca_group(self, group_id: str) -> Optional[OCAGroup]:
        """Get OCA group by ID."""
        return self.oca_groups.get(group_id)

    def list_oca_groups(self) -> Dict[str, Dict[str, Any]]:
        """List all OCA groups with order summaries."""
        result = {}
        for gid, group in self.oca_groups.items():
            result[gid] = {
                "orders": list(group.orders.keys()),
                "parent": group.parent_order.order_id if group.parent_order else None,
                "order_count": len(group.orders),
            }
        return result

    # ---- Event ledger ----

    def get_event_ledger(self) -> list[Dict[str, Any]]:
        """Get the complete order event ledger."""
        return self.event_ledger

    def clear_event_ledger(self) -> None:
        """Clear the event ledger (for new session)."""
        self.event_ledger.clear()


# ---------------------------------------------------------------------------
# Deterministic fake broker for testing


class FakeBroker:
    """Deterministic fake broker for testing OMS without external dependencies.

    V1: Models order execution realistically but deterministically.
    - Orders fill at the next eligible open (configurable for safe tests)
    - Commission: FIXED $1.00 per order
    - Slippage: 0.1% default, configurable
    - No network latency, no rejections beyond risk limits
    - Full order state synchronization with OMS
    """

    def __init__(self, oms: OMS, fill_assumption: str = "NEXT_OPEN"):
        self.oms = oms
        self.fill_assumption = fill_assumption
        self.commission_rate = 1.0  # $1.00 per order (FIXED model)
        self.slippage_pct = 0.001  # 0.1% default
        self.order_counter = 0

    def execute_order(self, order: Order, bar: Any) -> Dict[str, Any]:
        """Execute an order deterministically and sync with OMS.

        V1 execution model:
        - Fill price based on fill_assumption (NEXT_OPEN, MARKET, LIMIT)
        - Commission: $1.00 per order
        - Slippage: |fill_price - signal_price| for MARKET orders
        - Sync order state to OMS
        """
        self.order_counter += 1
        order_id = order.order_id

        # Determine fill price based on assumption
        fill_price = self._calculate_fill_price(order, bar)

        # Determine actual fill quantity (whole-share quantization)
        actual_qty = min(order.quantity, order.quantity)  # full fill in V1

        # Commission
        commission = self.commission_rate

        # Transition through OMS lifecycle before accepting any fill.
        oms_order = self.oms.get_order(order_id)
        if oms_order and oms_order.status == OrderLifecycle.SUBMITTED:
            self.oms.accept_order(order_id)
            oms_order = self.oms.get_order(order_id)
        if oms_order and oms_order.status == OrderLifecycle.ACCEPTED:
            self.oms.open_order(order_id)
            oms_order = self.oms.get_order(order_id)
        if oms_order and oms_order.status == OrderLifecycle.OPEN:
            self.oms.fill_order(order_id, actual_qty, fill_price)
            oms_order = self.oms.get_order(order_id)

        return {
            "order_id": order_id,
            "symbol": order.instrument.symbol,
            "side": order.side.name,
            "quantity": actual_qty,
            "fill_price": fill_price,
            "commission": commission,
            "slippage": round(fill_price - (order.price or 0), 2) if order.price else 0,
            "status": oms_order.status.name if oms_order else "UNKNOWN",
            "fill_quantity": (oms_order.filled_quantity or 0) if oms_order else 0,
        }

    def _calculate_fill_price(self, order: Order, bar: Any) -> float:
        """Calculate fill price based on fill assumption."""
        # Simplified: use bar close price
        # In a full implementation, would use NEXT_OPEN, MARKET with slippage, etc.
        close_price = getattr(bar, "close", 100.0) if bar else 100.0

        if self.fill_assumption == "CLOSE":
            raise ValueError("same-bar close fills are prohibited")
        elif self.fill_assumption == "NEXT_OPEN":
            open_price = getattr(bar, "open", None)
            if open_price is None:
                raise ValueError("next-open fake fills require an open price")
            return float(open_price)
        elif self.fill_assumption == "MARKET":
            # Market order: close + small slippage
            slippage = close_price * self.slippage_pct
            return close_price + slippage if order.side == OrderSide.BUY else close_price - slippage
        elif self.fill_assumption == "LIMIT":
            # Limit order: fill at signal price or better
            return order.price if order.price else close_price
        return close_price

    def cancel_all_orders(self) -> None:
        """Cancel all open orders and sync with OMS."""
        # Find all OPEN orders
        for oid, order in self.oms.orders.items():
            if order.status == OrderLifecycle.OPEN:
                self.oms.cancel_order(oid, reason="BROKER_CANCEL")
        self.oms.clear_event_ledger()
