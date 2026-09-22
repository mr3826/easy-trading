"""Production OMS state machine for Phase 6.

V1 Order lifecycle: SUBMITTED -> ACCEPTED -> OPEN -> PARTIALLY_FILLED -> FILLED -> CANCELED/REJECTED

Key features:
- Idempotency keys for duplicate detection (duplicates are rejected while the
  original order is pending; terminal-state resubmission is rejected too)
- OCA (Order Cancel Replace) groups
- Cancellation and replacement handling (transactional with compensation)
- Timeout handling
- Order state persistence
- Order submission/replacement/cancellation API
- Append-only event ledger: history is never destroyed
- Fill events carry execution id, commission, and slippage for reconciliation
- Late/duplicate/over fills are recorded as incidents, never silently dropped
- Protective (stop-loss) order invariant: position-opening fills require a
  linked protective order when ``require_protective_orders`` is enabled
- ``rebuild_from_ledger`` replays durable events to recover state
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

from trading_platform.domain import (
    Instrument,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
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

TERMINAL_STATUSES = (
    OrderLifecycle.FILLED,
    OrderLifecycle.CANCELED,
    OrderLifecycle.REJECTED,
    OrderLifecycle.EXPIRED,
)
FILLABLE_STATUSES = (OrderLifecycle.OPEN, OrderLifecycle.PARTIALLY_FILLED)
CANCELLABLE_STATUSES = (
    OrderLifecycle.SUBMITTED,
    OrderLifecycle.ACCEPTED,
    OrderLifecycle.OPEN,
    OrderLifecycle.PARTIALLY_FILLED,
)


class OMS:
    """Production Order State Machine.

    V1 lifecycle: SUBMITTED -> ACCEPTED -> OPEN -> (PARTIALLY_FILLED | FILLED | CANCELED | REJECTED | EXPIRED)

    Features:
    - Idempotency key tracking (duplicate submissions are rejected)
    - OCA group management
    - Cancellation and replacement (transactional with compensation)
    - Timeout tracking
    - Order lifecycle event reporting (append-only ledger)
    - Optional fill-reconciliation callback seam (the OMS never imports risk)
    - Protective (stop-loss) order invariant
    - Ledger replay for restart recovery
    """

    def __init__(
        self,
        oms_id: str = "default_oms",
        on_fill_reconcile: Callable[[Mapping[str, Any]], object] | None = None,
        require_protective_orders: bool = False,
    ) -> None:
        self.oms_id = oms_id
        # Order state: order_id -> Order
        self.orders: Dict[str, Order] = {}
        # Idempotency key tracking: key -> order_id
        self.idempotency_keys: Dict[str, str] = {}
        # OCA groups: group_id -> OCAGroup
        self.oca_groups: Dict[str, OCAGroup] = {}
        # Order lifecycle events (append-only)
        self.event_ledger: list[Dict[str, Any]] = []
        # Timeout tracking: order_id -> deadline
        self.timeouts: Dict[str, datetime] = {}
        # Fill deduplication: execution/trade ids already applied
        self.fill_execution_ids: set[str] = set()
        # Protective (stop-loss) order links: position symbol -> protective order id
        self.protective_orders: Dict[str, str] = {}
        self.require_protective_orders = require_protective_orders
        self.on_fill_reconcile = on_fill_reconcile

    # ---- Order submission ----

    def submit_order(self, order: Order, idempotency_key: Optional[str] = None) -> tuple[bool, str]:
        """Submit an order through the OMS state machine.

        V1 lifecycle transition: -> SUBMITTED

        Duplicate detection: an existing order with the same order id is a
        duplicate in any state; an existing order with the same idempotency key
        is rejected while pending or terminal, and replaced only when OPEN.

        Args:
            order: The order to submit
            idempotency_key: Optional key for duplicate detection

        Returns (accepted, reason):
            - accepted=True: order accepted into state machine
            - accepted=False: order rejected, reason string
        """
        existing = self.orders.get(order.order_id)
        if existing is not None:
            return (
                False,
                f"Duplicate submission: order {order.order_id} already {existing.status.name}",
            )

        if idempotency_key and idempotency_key in self.idempotency_keys:
            existing_order_id = self.idempotency_keys[idempotency_key]
            existing_order = self.orders.get(existing_order_id)
            if existing_order and existing_order.status in TERMINAL_STATUSES:
                return (
                    False,
                    f"Order already {existing_order.status.name} with same idempotency key",
                )
            if existing_order and existing_order.status == OrderLifecycle.OPEN:
                return self.replace_order(existing_order.order_id, order, idempotency_key)
            return (
                False,
                f"Duplicate submission: order {existing_order_id} already "
                f"{existing_order.status.name if existing_order else 'UNKNOWN'} with same idempotency key",
            )

        if idempotency_key:
            self.idempotency_keys[idempotency_key] = order.order_id

        # ---- OCA group: each submission owns a group keyed by its order id.
        # Group reuse is impossible here because membership is keyed by
        # order ids that the duplicate check above already rejects.
        oca_group = OCAGroup(group_id=order.order_id)
        self.oca_groups[oca_group.group_id] = oca_group
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
                "order_type": order.order_type.name,
                "time_in_force": order.time_in_force.name,
                "idempotency_key": idempotency_key,
                "oca_group": oca_group.group_id if oca_group else None,
            }
        )

        return True, "Order submitted"

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

    def fill_order(
        self,
        order_id: str,
        fill_quantity: int,
        fill_price: float,
        execution_id: str | None = None,
        commission: float = 0.0,
        slippage: float = 0.0,
    ) -> bool:
        """Apply a fill (partially or fully) with durable fill-event semantics.

        Duplicate fills (same execution id) are deduplicated; late fills after
        a terminal state and overfills are recorded as incidents and never
        silently dropped. Partial fills set PARTIALLY_FILLED and average the
        fill price across fills. Commission and slippage come from the
        executing broker and are recorded in the ledger for reconciliation.
        """
        order = self.orders.get(order_id)
        if not order:
            return False

        if execution_id and execution_id in self.fill_execution_ids:
            self.event_ledger.append(
                {
                    "event": "DUPLICATE_FILL_DETECTED",
                    "order_id": order_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "execution_id": execution_id,
                    "fill_quantity": fill_quantity,
                    "fill_price": fill_price,
                }
            )
            return False

        if order.status in TERMINAL_STATUSES:
            self.event_ledger.append(
                {
                    "event": "LATE_FILL_DETECTED",
                    "order_id": order_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": order.status.name,
                    "severity": "CRITICAL",
                    "execution_id": execution_id,
                    "fill_quantity": fill_quantity,
                    "fill_price": fill_price,
                }
            )
            return False

        if order.status not in FILLABLE_STATUSES:
            return False

        remaining = order.quantity - (order.filled_quantity or 0)
        if fill_quantity > remaining:
            self.event_ledger.append(
                {
                    "event": "OVERFILL_REJECTED",
                    "order_id": order_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "status": order.status.name,
                    "requested": fill_quantity,
                    "remaining": remaining,
                    "execution_id": execution_id,
                }
            )
            return False

        if (
            order.side == OrderSide.BUY
            and self.require_protective_orders
            and order.instrument.symbol not in self.protective_orders
        ):
            self.event_ledger.append(
                {
                    "event": "PROTECTIVE_ORDER_MISSING",
                    "order_id": order_id,
                    "symbol": order.instrument.symbol,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "severity": "CRITICAL",
                    "status": "OPERATOR_RESOLUTION_REQUIRED",
                    "execution_id": execution_id,
                }
            )
            return False

        previous_filled = order.filled_quantity or 0
        previous_price = order.filled_price
        order.filled_quantity = previous_filled + fill_quantity
        average_price = (
            (previous_filled * previous_price + fill_quantity * fill_price) / order.filled_quantity
            if previous_price is not None
            else fill_price
        )
        order.filled_price = average_price
        order.average_fill_price = average_price

        if order.filled_quantity >= order.quantity:
            order.status = OrderLifecycle.FILLED
            order.filled_at = datetime.now(timezone.utc)
            event_name = "ORDER_FILLED"
        else:
            order.status = OrderLifecycle.PARTIALLY_FILLED
            event_name = "ORDER_PARTIAL_FILL"

        event: Dict[str, Any] = {
            "event": event_name,
            "order_id": order_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": order.status.name,
            "instrument": order.instrument.symbol,
            "side": order.side.name,
            "quantity": fill_quantity,
            "fill_quantity": order.filled_quantity,
            "fill_price": order.filled_price,
            "fill_average_price": order.average_fill_price,
            "commission": commission,
            "slippage": slippage,
            "execution_id": execution_id,
        }
        self.event_ledger.append(event)
        if execution_id:
            self.fill_execution_ids.add(execution_id)
        if self.on_fill_reconcile is not None:
            self.on_fill_reconcile(event)
        return True

    # ---- Order cancellation ----

    def cancel_order(self, order_id: str, reason: str = "") -> bool:
        """Cancel an order.

        V1: SUBMITTED/ACCEPTED/OPEN/PARTIALLY_FILLED -> CANCELED.
        Returns True if cancellation succeeded.
        """
        order = self.orders.get(order_id)
        if not order:
            return False

        if order.status not in CANCELLABLE_STATUSES:
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
                        if order and order.status in FILLABLE_STATUSES:
                            order.status = OrderLifecycle.CANCELED
                group.cancel_all()
                break

    # ---- Order replacement ----

    def replace_order(
        self, old_order_id: str, new_order: Order, idempotency_key: Optional[str] = None
    ) -> tuple[bool, str]:
        """Replace an existing order with a new one.

        The replacement is transactional: preconditions are validated before
        anything changes, and a mid-way failure is compensated by restoring the
        prior state and recording an incident event — the old order is never
        left canceled with no replacement.
        """
        old_order = self.orders.get(old_order_id)
        if old_order is None:
            return False, "Order to replace not found"
        if new_order.order_id in self.orders:
            return False, f"Replacement order id {new_order.order_id} already exists in OMS"

        group_id, previous_statuses = self._capture_replace_state(old_order_id)
        cancel_ok = self.cancel_order(old_order_id, reason="REPLACE")
        if not cancel_ok:
            return False, f"Order {old_order_id} is {old_order.status.name}; cannot replace"

        try:
            self._commit_replacement(old_order_id, new_order, idempotency_key, group_id)
        except Exception as exc:
            self._restore_replace_state(group_id, previous_statuses)
            self.orders.pop(new_order.order_id, None)
            if idempotency_key:
                self.idempotency_keys[idempotency_key] = old_order_id
            self.event_ledger.append(
                {
                    "event": "ORDER_REPLACE_COMPENSATED",
                    "order_id": old_order_id,
                    "replacement_order_id": new_order.order_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "reason": str(exc),
                    "severity": "CRITICAL",
                    "status": "OPERATOR_RESOLUTION_REQUIRED",
                }
            )
            return False, f"Replacement failed and was compensated: {exc}"

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

    def _capture_replace_state(self, order_id: str) -> tuple[Optional[str], Dict[str, OrderStatus]]:
        """Capture the statuses of the order and its OCA group before a cancel."""
        group_id: Optional[str] = None
        statuses: Dict[str, OrderStatus] = {}
        order = self.orders.get(order_id)
        if order is not None:
            statuses[order_id] = order.status
        for gid, group in self.oca_groups.items():
            if order_id in group.orders:
                group_id = gid
                for oid, member in group.orders.items():
                    statuses.setdefault(oid, member.status)
                break
        return group_id, statuses

    def _restore_replace_state(self, group_id: Optional[str], statuses: Dict[str, OrderStatus]) -> None:
        """Restore captured statuses (compensation for a failed replacement)."""
        for oid, status in statuses.items():
            order = self.orders.get(oid)
            if order is not None:
                order.status = status
                if group_id is not None:
                    group = self.oca_groups.get(group_id)
                    if group is not None:
                        group.add(order)

    def _commit_replacement(
        self, old_order_id: str, new_order: Order, idempotency_key: Optional[str], group_id: Optional[str]
    ) -> None:
        """Commit the replacement order into the state machine."""
        new_order.status = OrderLifecycle.SUBMITTED
        self.orders[new_order.order_id] = new_order
        if idempotency_key:
            self.idempotency_keys[idempotency_key] = new_order.order_id
        if group_id is not None:
            group = self.oca_groups.get(group_id)
            if group is not None:
                group.add(new_order)
                return
        new_oca = OCAGroup(group_id=new_order.order_id)
        new_oca.add(new_order)
        self.oca_groups[new_oca.group_id] = new_oca

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
            if order and order.status in FILLABLE_STATUSES:
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

    # ---- Event ledger (append-only) ----

    def get_event_ledger(self) -> list[Dict[str, Any]]:
        """Get the complete order event ledger.

        The ledger is append-only: events are never removed or wiped.
        """
        return list(self.event_ledger)

    # ---- Protective (stop-loss) order invariant ----

    def link_protective_order(self, symbol: str, protective_order_id: str) -> bool:
        """Link a protective (stop-loss) order to a position symbol.

        The protective order must be a known OMS order on the exit side.
        Returns True if the link was recorded.
        """
        protective = self.orders.get(protective_order_id)
        if protective is None:
            return False
        if protective.side != OrderSide.SELL or protective.order_type not in (
            OrderType.STOP,
            OrderType.STOP_LIMIT,
        ):
            return False
        self.protective_orders[symbol] = protective_order_id
        self.event_ledger.append(
            {
                "event": "PROTECTIVE_ORDER_LINKED",
                "symbol": symbol,
                "protective_order_id": protective_order_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        return True

    def unlink_protective_order(self, symbol: str) -> None:
        """Remove the protective link when the position is closed."""
        self.protective_orders.pop(symbol, None)

    def check_protective_invariant(self, held_positions: Iterable[str]) -> tuple[bool, str]:
        """Every held position must have a linked protective (stop-loss) order.

        Returns (satisfied, reason).
        """
        missing = [symbol for symbol in held_positions if symbol not in self.protective_orders]
        if missing:
            return (
                False,
                f"Protective order invariant violated: no stop-loss linked for {', '.join(sorted(missing))}",
            )
        return True, "Protective order invariant satisfied"

    # ---- Restart recovery (journal replay) ----

    def rebuild_from_ledger(self, events: Iterable[Mapping[str, Any]]) -> None:
        """Rebuild OMS state from durable ledger events.

        Replays events in order through the same semantics the production
        methods apply, restoring orders, statuses, fills, dedup state, and
        protective links. Events referencing unknown orders are recorded as
        REPLAY_GAP entries — never silently dropped.
        """
        self.orders = {}
        self.idempotency_keys = {}
        self.oca_groups = {}
        self.timeouts = {}
        self.fill_execution_ids = set()
        self.protective_orders = {}
        self.event_ledger = []
        for event in events:
            self._replay_event(dict(event))

    def _replay_event(self, event: Dict[str, Any]) -> None:
        kind = event.get("event")
        order_id = event.get("order_id")
        if kind == "ORDER_SUBMITTED":
            submitted = self._order_from_event(event, str(order_id), OrderStatus.SUBMITTED)
            self.orders[str(order_id)] = submitted
            key = event.get("idempotency_key")
            if key:
                self.idempotency_keys[str(key)] = str(order_id)
            gid = event.get("oca_group")
            if gid:
                group = self.oca_groups.setdefault(str(gid), OCAGroup(str(gid)))
                group.add(submitted)
        elif kind in ("ORDER_ACCEPTED", "ORDER_OPEN"):
            replayed = self.orders.get(str(order_id)) if order_id else None
            if replayed is None:
                self._append_replay_gap(event)
                return
            replayed.status = OrderStatus.ACCEPTED if kind == "ORDER_ACCEPTED" else OrderStatus.OPEN
        elif kind in ("ORDER_FILLED", "ORDER_PARTIAL_FILL"):
            filled = self.orders.get(str(order_id)) if order_id else None
            if filled is None:
                self._append_replay_gap(event)
                return
            execution_id = event.get("execution_id")
            if execution_id:
                self.fill_execution_ids.add(str(execution_id))
            filled.filled_quantity = int(event.get("fill_quantity", 0) or 0)
            fill_price = event.get("fill_price")
            filled.filled_price = float(fill_price) if fill_price is not None else None
            average = event.get("fill_average_price")
            filled.average_fill_price = float(average) if average is not None else filled.filled_price
            filled.status = OrderStatus.FILLED if kind == "ORDER_FILLED" else OrderStatus.PARTIALLY_FILLED
        elif kind == "ORDER_CANCELED":
            canceled = self.orders.get(str(order_id)) if order_id else None
            if canceled is None:
                self._append_replay_gap(event)
                return
            canceled.status = OrderStatus.CANCELED
        elif kind == "ORDER_REPLACED":
            replacement_id = event.get("order_id")
            if replacement_id and replacement_id not in self.orders:
                replacement = self._order_from_event(event, str(replacement_id), OrderStatus.SUBMITTED)
                self.orders[str(replacement_id)] = replacement
                gid = event.get("oca_group")
                if gid:
                    group = self.oca_groups.setdefault(str(gid), OCAGroup(str(gid)))
                    group.add(replacement)
        elif kind == "PROTECTIVE_ORDER_LINKED":
            symbol = str(event.get("symbol", ""))
            protective_id = event.get("protective_order_id")
            if symbol and protective_id:
                self.protective_orders[symbol] = str(protective_id)
        self.event_ledger.append(event)

    def _append_replay_gap(self, event: Mapping[str, Any]) -> None:
        self.event_ledger.append(
            {
                "event": "REPLAY_GAP",
                "order_id": event.get("order_id"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "severity": "CRITICAL",
                "unresolved_event": event.get("event"),
            }
        )

    def _order_from_event(self, event: Mapping[str, Any], order_id: str, status: OrderStatus) -> Order:
        return Order(
            order_id=order_id,
            instrument=Instrument(str(event.get("instrument", "UNKNOWN"))),
            side=OrderSide[str(event.get("side", "BUY"))],
            quantity=int(event.get("quantity", 1) or 1),
            price=event.get("price"),
            order_type=OrderType[str(event.get("order_type", "LIMIT"))],
            time_in_force=TimeInForce[str(event.get("time_in_force", "DAY"))],
            status=status,
            signal=None,
        )


# ---------------------------------------------------------------------------
# Deterministic fake broker for testing


class FakeBroker:
    """Deterministic fake broker for testing OMS without external dependencies.

    V1: Models order execution realistically but deterministically.
    - Orders fill at the next eligible open (configurable for safe tests)
    - Commission: FIXED $1.00 per order (recorded in the OMS ledger)
    - Slippage: 0.1% default, configurable
    - No network latency, no rejections beyond risk limits
    - Full order state synchronization with OMS
    - The event ledger is append-only: cancels never wipe history
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
        - Sync order state to OMS with execution id and fee data
        """
        self.order_counter += 1
        order_id = order.order_id
        execution_id = f"fake-{self.order_counter}"

        # Determine fill price based on assumption
        fill_price = self._calculate_fill_price(order, bar)

        # Determine actual fill quantity (whole-share quantization)
        actual_qty = min(order.quantity, order.quantity)  # full fill in V1

        # Commission
        commission = self.commission_rate
        slippage = round(fill_price - (order.price or 0), 2) if order.price else 0.0

        # Transition through OMS lifecycle before accepting any fill.
        oms_order = self.oms.get_order(order_id)
        if oms_order and oms_order.status == OrderLifecycle.SUBMITTED:
            self.oms.accept_order(order_id)
            oms_order = self.oms.get_order(order_id)
        if oms_order and oms_order.status == OrderLifecycle.ACCEPTED:
            self.oms.open_order(order_id)
            oms_order = self.oms.get_order(order_id)
        if oms_order and oms_order.status in FILLABLE_STATUSES:
            self.oms.fill_order(
                order_id,
                actual_qty,
                fill_price,
                execution_id=execution_id,
                commission=commission,
                slippage=slippage,
            )
            oms_order = self.oms.get_order(order_id)

        return {
            "order_id": order_id,
            "symbol": order.instrument.symbol,
            "side": order.side.name,
            "quantity": actual_qty,
            "fill_price": fill_price,
            "commission": commission,
            "slippage": slippage,
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
        """Cancel all open orders and sync with OMS; the ledger is append-only."""
        for oid, order in self.oms.orders.items():
            if order.status in FILLABLE_STATUSES:
                self.oms.cancel_order(oid, reason="BROKER_CANCEL")
