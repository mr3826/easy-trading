"""IBKR paper trading client adapter backed by ib_async (lazy import).

The client library is imported only inside methods, so importing this module
never requires ib_async and default offline tests need no IBKR library.
Connection state comes exclusively from the client handshake (``isConnected``)
exposed via a read-only property; a simulated ``connected=True`` assignment
from outside raises AttributeError and can never enable submission. Submission
requires both the ``allow_connection`` and ``allow_paper_orders`` flags and an
established handshake. Order state lives in an adapter-owned ledger fed only by
submission and IBKR callbacks, never from OMS state.
"""

from __future__ import annotations

import importlib
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_platform.domain import (
    BrokerSnapshot,
    Instrument,
    InstrumentType,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)
from trading_platform.oms.oms import OMS

logger = logging.getLogger("trading_platform.broker.ibkr_paper")

LIVE_PORTS: frozenset[int] = frozenset({7496, 4001})
PAPER_PORTS: frozenset[int] = frozenset({7497, 4002})

_IB_TO_PLATFORM_STATUS: Dict[str, str] = {
    "PendingSubmit": "RECEIVED",
    "PreSubmitted": "SUBMITTED",
    "Submitted": "SUBMITTED",
    "ApiPending": "SUBMITTED",
    "ApiUpdate": "SUBMITTED",
    "Filled": "FILLED",
    "Cancelled": "CANCELLED",
    "ApiCancelled": "CANCELLED",
    "Inactive": "REJECTED",
    "ValidationError": "REJECTED",
}

_OPEN_PLATFORM_STATUSES: frozenset[str] = frozenset({"RECEIVED", "SUBMITTED", "ACCEPTED", "OPEN"})
_DONE_PLATFORM_STATUSES: frozenset[str] = frozenset({"FILLED", "CANCELLED", "REJECTED", "EXPIRED"})
_TERMINAL_PLATFORM_STATUSES: frozenset[str] = frozenset({"FILLED", "CANCELLED", "REJECTED", "EXPIRED"})
_REJECTION_ERROR_CODES: frozenset[int] = frozenset({201})

_IB_ORDER_TYPES: Dict[OrderType, str] = {
    OrderType.MARKET: "MKT",
    OrderType.LIMIT: "LMT",
    OrderType.STOP: "STP",
    OrderType.STOP_LIMIT: "STP LMT",
}


def validate_paper_target(host: str, port: int, account: str = "") -> None:
    """Raise ValueError unless the target is a verified paper connection."""
    if port in LIVE_PORTS:
        raise ValueError(f"live IBKR port {port} is forbidden; paper ports are 7497 (TWS) or 4002 (Gateway)")
    if not account:
        return
    normalized = account.strip().upper()
    if "LIVE" in normalized:
        raise ValueError(f"live IBKR account configuration {account!r} is forbidden")
    if not (normalized.startswith("DU") and normalized[2:].isdigit() and len(normalized) >= 8):
        raise ValueError(
            f"account {account!r} is not verified as paper; paper accounts follow the DUxxxxxxx convention"
        )


def _import_ib_async() -> Any:
    try:
        return importlib.import_module("ib_async")
    except ImportError as exc:
        raise RuntimeError("ib_async is not installed; install the 'ibkr' extra") from exc


def _parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


class IBKRPaperBrokerAdapter:
    """Interactive Brokers paper adapter backed by ib_async.

    Implements the BrokerAdapter protocol for TWS/Gateway paper connections:
    connect/disconnect/reconnect lifecycle, next-valid-order-ID tracking,
    submission, acknowledgement, status updates, cancellation, replacement,
    partial fills, commissions, rejections, bracket orders with parent/child
    protective legs and OCA behavior, and retrieval of account, cash,
    positions, open orders, and completed orders.
    """

    def __init__(
        self,
        paper: bool = True,
        allow_connection: bool = False,
        allow_paper_orders: bool = False,
        host: str = "127.0.0.1",
        port: int = 7497,
        client_id: int = 1,
        connect_timeout: float = 4.0,
        account: str = "",
        ib_instance: Any | None = None,
    ) -> None:
        if not paper:
            raise ValueError("IBKR adapter accepts paper configuration only")
        if allow_paper_orders and not allow_connection:
            raise ValueError("allow_paper_orders requires allow_connection")
        validate_paper_target(host, port, account)
        self._allow_connection = allow_connection
        self._allow_paper_orders = allow_paper_orders
        self._host = host
        self._port = port
        self._client_id = client_id
        self._connect_timeout = connect_timeout
        self._account = account
        self._ib: Any | None = ib_instance
        self._handlers_bound = False
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._by_ib_id: Dict[int, Dict[str, Any]] = {}
        self._account_cache: Dict[str, str] = {}
        self._position_cache: Dict[str, Dict[str, float]] = {}
        self._account_refreshed = False
        self._error: Optional[str] = None
        self._last_order_id: Optional[int] = None

    # --- Broker identity ---

    @property
    def broker_id(self) -> str:
        return "IBKR"

    @property
    def connected(self) -> bool:
        if self._ib is None:
            return False
        return bool(self._ib.isConnected())

    @property
    def next_valid_order_id(self) -> Optional[int]:
        """The order id the next allocation via ib.client.getReqId() will return."""
        if self._last_order_id is None:
            return None
        return self._last_order_id + 1

    # --- Order execution ---

    def execute_order(self, order: Order, bar: Any, stop_price: float | None = None) -> Dict[str, Any]:
        """Submit an order, or replace the IBKR order previously submitted
        under the same order id. Raises RuntimeError unless both gate flags
        are set and the client handshake is established."""
        self._require_submission_allowed()
        existing = self._orders.get(order.order_id)
        if existing is not None:
            return self._replace(order, existing, stop_price)
        ib = self._ensure_client()
        contract = self._build_contract(order.instrument)
        ib_order = self._build_ib_order(order, stop_price)
        ib_order.orderId = self._allocate_order_id()
        trade = ib.placeOrder(contract, ib_order)
        entry = self._new_entry(order, ib_order, trade)
        self._orders[order.order_id] = entry
        self._by_ib_id[int(ib_order.orderId)] = entry
        return self._submission_result(order, entry)

    def execute_bracket_order(
        self, order: Order, bar: Any, take_profit_price: float, stop_loss_price: float
    ) -> Dict[str, Any]:
        """Submit a bracket order: entry limit order with take-profit and
        stop-loss protective legs sharing an OCA group. Raises RuntimeError
        under the same conditions as execute_order."""
        self._require_submission_allowed()
        if order.price is None:
            raise ValueError(f"bracket entry {order.order_id} requires a limit price")
        ib = self._ensure_client()
        contract = self._build_contract(order.instrument)
        action = order.side.name
        reverse_side = OrderSide.SELL if action == "BUY" else OrderSide.BUY
        bracket = ib.bracketOrder(action, order.quantity, order.price, take_profit_price, stop_loss_price)
        oca_group = f"OCA-{order.order_id}"
        ib.oneCancelsAll([bracket.takeProfit, bracket.stopLoss], oca_group, 1)
        parent_trade = ib.placeOrder(contract, bracket.parent)
        tp_trade = ib.placeOrder(contract, bracket.takeProfit)
        sl_trade = ib.placeOrder(contract, bracket.stopLoss)
        leg_ids = [int(bracket.parent.orderId), int(bracket.takeProfit.orderId), int(bracket.stopLoss.orderId)]
        self._last_order_id = max(leg_ids)
        parent_entry = self._new_entry(order, bracket.parent, parent_trade)
        parent_entry["role"] = "parent"
        parent_entry["children"] = [f"{order.order_id}:TP", f"{order.order_id}:SL"]
        self._orders[order.order_id] = parent_entry
        self._by_ib_id[int(bracket.parent.orderId)] = parent_entry
        tp_order = Order(
            f"{order.order_id}:TP",
            order.instrument,
            reverse_side,
            order.quantity,
            take_profit_price,
            OrderType.LIMIT,
            order.time_in_force,
            OrderStatus.SUBMITTED,
            None,
        )
        tp_entry = self._new_entry(tp_order, bracket.takeProfit, tp_trade)
        tp_entry["role"] = "child"
        tp_entry["parent"] = order.order_id
        tp_entry["oca_group"] = oca_group
        self._orders[tp_order.order_id] = tp_entry
        self._by_ib_id[int(bracket.takeProfit.orderId)] = tp_entry
        sl_order = Order(
            f"{order.order_id}:SL",
            order.instrument,
            reverse_side,
            order.quantity,
            stop_loss_price,
            OrderType.STOP,
            order.time_in_force,
            OrderStatus.SUBMITTED,
            None,
        )
        sl_entry = self._new_entry(sl_order, bracket.stopLoss, sl_trade)
        sl_entry["role"] = "child"
        sl_entry["parent"] = order.order_id
        sl_entry["oca_group"] = oca_group
        self._orders[sl_order.order_id] = sl_entry
        self._by_ib_id[int(bracket.stopLoss.orderId)] = sl_entry
        return self._submission_result(order, parent_entry)

    def cancel_order(self, order_id: str, reason: str = "") -> bool:
        """Cancel a specific order via IBKR. Returns True if the cancellation
        was initiated; the status change arrives through the order status
        callback."""
        entry = self._orders.get(order_id)
        if entry is None:
            return False
        if not self.connected:
            self._error = f"cancellation of {order_id} not initiated: IBKR client is not connected"
            return False
        trade = entry.get("trade")
        if trade is None:
            return False
        ib = self._ib
        if ib is None:
            self._error = f"cancellation of {order_id} not initiated: IBKR client is not connected"
            return False
        ib.cancelOrder(trade.order)
        entry["pending_cancel"] = reason or "requested"
        return True

    def list_orders(self) -> Dict[str, Any]:
        """List open orders from the adapter-owned ledger."""
        return {
            order_id: {
                "status": entry["status"],
                "symbol": entry["symbol"],
                "side": entry["side"],
                "quantity": entry["quantity"],
                "price": entry["price"],
                "ib_order_id": entry["ib_order_id"],
            }
            for order_id, entry in self._orders.items()
            if entry["status"] in _OPEN_PLATFORM_STATUSES
        }

    def get_order_status(self, order_id: str) -> Optional[str]:
        """Get the platform-mapped status of a specific order, or None."""
        entry = self._orders.get(order_id)
        if entry is None:
            return None
        return str(entry["status"])

    # --- Independent state retrieval ---

    def get_account_snapshot(self) -> BrokerSnapshot:
        """Build a BrokerSnapshot from broker-side data only: account summary
        and position values received from IBKR (event callbacks or the client
        request), never from OMS state. Requires a connected client."""
        if not self.connected:
            raise RuntimeError("IBKR account snapshot requires a connected client")
        if not self._account_refreshed:
            self._refresh_account_cache()
            self._account_refreshed = True
        cash = _parse_float(self._account_cache.get("CashBalance")) or 0.0
        buying_power = _parse_float(self._account_cache.get("BuyingPower")) or 0.0
        margin_used = _parse_float(self._account_cache.get("InitMarginReq"))
        positions = {Instrument(symbol): data["quantity"] for symbol, data in self._position_cache.items()}
        return BrokerSnapshot(
            timestamp=datetime.now(timezone.utc),
            positions=positions,
            cash=cash,
            buying_power=buying_power,
            margin_used=margin_used,
            status="connected",
        )

    def list_positions(self) -> List[Dict[str, Any]]:
        """List positions from broker-side data received from IBKR."""
        if not self.connected:
            raise RuntimeError("IBKR positions require a connected client")
        if not self._account_refreshed:
            self._refresh_account_cache()
            self._account_refreshed = True
        return [
            {
                "symbol": symbol,
                "quantity": int(data["quantity"]),
                "avg_cost": data["avg_cost"],
                "account": self._account,
            }
            for symbol, data in self._position_cache.items()
        ]

    def list_open_orders(self) -> List[Dict[str, Any]]:
        """List open orders: from IBKR openTrades() when connected, otherwise
        from the adapter-owned ledger."""
        if self.connected:
            ib = self._ensure_client()
            views: List[Dict[str, Any]] = []
            for trade in ib.openTrades():
                entry = self._entry_for_trade(trade)
                if entry is not None:
                    views.append(self._order_view(entry))
                else:
                    views.append(self._trade_view(trade))
            return views
        return [
            self._order_view(entry) for entry in self._orders.values() if entry["status"] in _OPEN_PLATFORM_STATUSES
        ]

    def list_completed_orders(self) -> List[Dict[str, Any]]:
        """List completed orders: from IBKR reqCompletedOrders(apiOnly=True)
        when connected, otherwise from the adapter-owned ledger."""
        if self.connected:
            ib = self._ensure_client()
            views: List[Dict[str, Any]] = []
            for trade in ib.reqCompletedOrders(True):
                entry = self._entry_for_trade(trade)
                if entry is not None:
                    views.append(self._completed_view(entry))
                else:
                    views.append(self._completed_trade_view(trade))
            return views
        return [
            self._completed_view(entry) for entry in self._orders.values() if entry["status"] in _DONE_PLATFORM_STATUSES
        ]

    # --- Session lifecycle ---

    def start(self) -> bool:
        """Connect to the IBKR paper endpoint. Returns False (with the error
        recorded) unless allow_connection is set, the target is verified
        paper, and the client handshake succeeds."""
        if self.connected:
            return True
        if not self._allow_connection:
            self._error = "connection is not allowed; set allow_connection to enable IBKR paper connectivity"
            return False
        try:
            validate_paper_target(self._host, self._port, self._account)
            ib = self._ensure_client()
            self._register_handlers_once(ib)
            ib.connect(self._host, self._port, self._client_id, self._connect_timeout, False, self._account)
        except Exception as exc:
            self._error = f"IBKR paper connection failed: {exc}"
            return False
        if not self.connected:
            self._error = "IBKR paper connection failed: client not connected after handshake"
            return False
        self._error = None
        return True

    def stop(self) -> bool:
        """Disconnect from IBKR. Idempotent; returns True when the session is
        closed or was never open."""
        if self._ib is not None and self._ib.isConnected():
            try:
                self._ib.disconnect()
            except Exception as exc:
                self._error = f"IBKR paper disconnection failed: {exc}"
                return False
        return True

    def reconnect(self) -> bool:
        """Reconnect the IBKR session after an interruption."""
        if self.connected:
            return True
        return self.start()

    # --- Reconciliation sync ---

    def sync_state(self, oms: OMS) -> None:
        """Update OMS order statuses from the adapter-owned ledger.

        The ledger is never seeded from OMS state: only the broker-to-OMS
        direction is applied, and ledger-only orders are flagged, not copied.
        """
        if not self.connected:
            return
        for order_id, entry in self._orders.items():
            if order_id in oms.orders:
                try:
                    oms.orders[order_id].status = OrderStatus[entry["status"]]
                except KeyError:
                    self._error = f"unknown broker status: {entry['status']}"
            else:
                logger.warning("broker ledger has order %s but OMS does not", order_id)

    def get_last_error(self) -> Optional[str]:
        """Get the last error string observed from the broker."""
        return self._error

    # --- Client wiring ---

    def _ensure_client(self) -> Any:
        if self._ib is None:
            ib_async = _import_ib_async()
            self._ib = ib_async.IB()
        return self._ib

    def _register_handlers_once(self, ib: Any) -> None:
        if self._handlers_bound:
            return
        ib.connectedEvent += self._on_connected
        ib.disconnectedEvent += self._on_disconnected
        ib.orderStatusEvent += self._on_order_status
        ib.execDetailsEvent += self._on_exec_details
        ib.commissionReportEvent += self._on_commission_report
        ib.errorEvent += self._on_error
        ib.cancelOrderEvent += self._on_cancel_order
        ib.openOrderEvent += self._on_open_order
        ib.positionEvent += self._on_position
        ib.accountSummaryEvent += self._on_account_summary
        self._handlers_bound = True

    def _allocate_order_id(self) -> int:
        ib = self._ensure_client()
        order_id = int(ib.client.getReqId())
        self._last_order_id = order_id
        return order_id

    def _require_submission_allowed(self) -> None:
        if not (self._allow_connection and self._allow_paper_orders):
            raise RuntimeError(
                "IBKR paper submission blocked: both allow_connection and allow_paper_orders must be set"
            )
        if not self.connected:
            raise RuntimeError("IBKR paper submission blocked: IBKR client is not connected")

    # --- Order mapping ---

    def _build_contract(self, instrument: Instrument) -> Any:
        ib_async = _import_ib_async()
        if instrument.instrument_type == InstrumentType.INDEX:
            return ib_async.Index(instrument.symbol, instrument.exchange, instrument.currency)
        return ib_async.Stock(instrument.symbol, instrument.exchange, instrument.currency)

    def _build_ib_order(self, order: Order, stop_price: float | None) -> Any:
        ib_async = _import_ib_async()
        action = order.side.name
        if order.order_type == OrderType.MARKET:
            return ib_async.MarketOrder(action, order.quantity)
        if order.order_type == OrderType.LIMIT:
            if order.price is None:
                raise ValueError(f"limit order {order.order_id} requires a price")
            return ib_async.LimitOrder(action, order.quantity, order.price)
        if order.order_type == OrderType.STOP:
            stop = stop_price if stop_price is not None else order.price
            if stop is None:
                raise ValueError(f"stop order {order.order_id} requires a stop price")
            return ib_async.StopOrder(action, order.quantity, stop)
        if order.order_type == OrderType.STOP_LIMIT:
            stop = stop_price if stop_price is not None else order.price
            if order.price is None or stop is None:
                raise ValueError(f"stop-limit order {order.order_id} requires limit and stop prices")
            return ib_async.StopLimitOrder(action, order.quantity, order.price, stop)
        raise ValueError(f"unsupported order type: {order.order_type}")

    # --- Ledger entries ---

    def _new_entry(self, order: Order, ib_order: Any, trade: Any) -> Dict[str, Any]:
        status = "PendingSubmit"
        order_status = getattr(trade, "orderStatus", None)
        if order_status is not None:
            status = str(getattr(order_status, "status", "PendingSubmit"))
        return {
            "order_id": order.order_id,
            "symbol": order.instrument.symbol,
            "side": order.side.name,
            "quantity": order.quantity,
            "price": order.price,
            "order_type": str(getattr(ib_order, "orderType", _IB_ORDER_TYPES[order.order_type])),
            "ib_order_id": int(ib_order.orderId),
            "ib_status": status,
            "status": _IB_TO_PLATFORM_STATUS.get(status, "RECEIVED"),
            "filled_quantity": 0,
            "avg_fill_price": None,
            "commission": None,
            "commission_currency": "",
            "fills": [],
            "log": [],
            "parent": None,
            "children": [],
            "oca_group": None,
            "role": "single",
            "trade": trade,
        }

    def _submission_result(self, order: Order, entry: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "order_id": entry["order_id"],
            "symbol": entry["symbol"],
            "side": entry["side"],
            "quantity": entry["quantity"],
            "fill_price": entry["avg_fill_price"],
            "commission": entry["commission"],
            "slippage": 0.0,
            "status": entry["status"],
            "fill_quantity": entry["filled_quantity"],
            "ib_order_id": entry["ib_order_id"],
            "children": list(entry["children"]),
            "oca_group": entry["oca_group"],
        }

    def _replace(self, order: Order, entry: Dict[str, Any], stop_price: float | None) -> Dict[str, Any]:
        ib = self._ensure_client()
        trade = entry.get("trade")
        if trade is None:
            raise RuntimeError(f"cannot replace {order.order_id} without an IBKR trade reference")
        ib_order = trade.order
        ib_order.orderType = _IB_ORDER_TYPES[order.order_type]
        ib_order.totalQuantity = order.quantity
        if order.order_type == OrderType.LIMIT:
            ib_order.lmtPrice = order.price
        elif order.order_type == OrderType.STOP:
            ib_order.auxPrice = stop_price if stop_price is not None else order.price
        elif order.order_type == OrderType.STOP_LIMIT:
            ib_order.lmtPrice = order.price
            ib_order.auxPrice = stop_price if stop_price is not None else order.price
        ib.placeOrder(trade.contract, ib_order)
        entry["quantity"] = order.quantity
        entry["price"] = order.price
        entry["ib_status"] = str(trade.orderStatus.status)
        entry["status"] = _IB_TO_PLATFORM_STATUS.get(entry["ib_status"], "RECEIVED")
        entry["log"].append(f"replaced: quantity={order.quantity}, price={order.price}")
        return self._submission_result(order, entry)

    def _entry_for_trade(self, trade: Any) -> Optional[Dict[str, Any]]:
        order = getattr(trade, "order", None)
        ib_order_id = getattr(order, "orderId", None)
        if ib_order_id is not None:
            try:
                key = int(ib_order_id)
            except (TypeError, ValueError):
                return None
            if key in self._by_ib_id:
                return self._by_ib_id[key]
        for entry in self._orders.values():
            if entry.get("trade") is trade:
                return entry
        return None

    # --- IBKR event handlers ---

    def _on_connected(self) -> None:
        logger.info("IBKR paper connection established")

    def _on_disconnected(self) -> None:
        logger.info("IBKR paper connection closed")

    def _on_order_status(self, trade: Any) -> None:
        entry = self._entry_for_trade(trade)
        if entry is None:
            return
        ib_status = str(trade.orderStatus.status)
        entry["ib_status"] = ib_status
        if ib_status == "PendingCancel":
            return
        entry["status"] = _IB_TO_PLATFORM_STATUS.get(ib_status, "RECEIVED")
        self._sync_fill_state(entry)

    def _on_exec_details(self, trade: Any, fill: Any) -> None:
        entry = self._entry_for_trade(trade)
        if entry is None:
            return
        execution = getattr(fill, "execution", None)
        entry["fills"].append(
            {
                "exec_id": str(getattr(execution, "execId", "") or ""),
                "side": str(getattr(execution, "side", "") or ""),
                "shares": float(getattr(execution, "shares", 0.0) or 0.0),
                "price": float(getattr(execution, "price", 0.0) or 0.0),
            }
        )
        self._sync_fill_state(entry)

    def _on_commission_report(self, trade: Any, fill: Any, report: Any) -> None:
        entry = self._entry_for_trade(trade)
        if entry is None:
            return
        commission = float(getattr(report, "commission", 0.0) or 0.0)
        currency = str(getattr(report, "currency", "") or "")
        entry["commission"] = (entry["commission"] or 0.0) + commission
        if currency:
            entry["commission_currency"] = currency

    def _on_error(self, req_id: int, error_code: int, error_string: str, contract: Any) -> None:
        self._error = f"IBKR error {error_code}: {error_string}"
        entry: Optional[Dict[str, Any]] = None
        if req_id is not None and int(req_id) >= 0:
            entry = self._by_ib_id.get(int(req_id))
        if entry is None:
            logger.warning("IBKR error %s: %s", error_code, error_string)
            return
        if error_code in _REJECTION_ERROR_CODES:
            entry["status"] = "REJECTED"
            entry["ib_status"] = "Inactive"
            entry["rejection_reason"] = error_string
            self._error = f"order {entry['order_id']} rejected: {error_string}"
        else:
            entry["warning_text"] = error_string

    def _on_cancel_order(self, trade: Any) -> None:
        entry = self._entry_for_trade(trade)
        if entry is not None:
            entry["pending_cancel"] = "confirmed"

    def _on_open_order(self, trade: Any) -> None:
        entry = self._entry_for_trade(trade)
        if entry is None or entry["status"] in _TERMINAL_PLATFORM_STATUSES:
            return
        entry["status"] = "OPEN"

    def _on_position(self, position: Any) -> None:
        contract = getattr(position, "contract", None)
        symbol = str(getattr(contract, "localSymbol", "") or getattr(contract, "symbol", "") or "").upper()
        if not symbol:
            return
        self._position_cache[symbol] = {
            "quantity": float(getattr(position, "position", 0.0) or 0.0),
            "avg_cost": float(getattr(position, "avgCost", 0.0) or 0.0),
        }

    def _on_account_summary(self, value: Any) -> None:
        tag = str(getattr(value, "tag", "") or "")
        if tag:
            self._account_cache[tag] = str(getattr(value, "value", "") or "")

    # --- Ledger and client views ---

    def _refresh_account_cache(self) -> None:
        ib = self._ensure_client()
        for value in ib.accountSummary(self._account):
            self._on_account_summary(value)
        for position in ib.positions(self._account):
            self._on_position(position)

    def _sync_fill_state(self, entry: Dict[str, Any]) -> None:
        total = 0.0
        notional = 0.0
        for fill in entry["fills"]:
            shares = float(fill["shares"])
            price = float(fill["price"])
            total += shares
            notional += shares * price
        if total > 0:
            entry["filled_quantity"] = int(total)
            entry["avg_fill_price"] = notional / total
        elif entry["status"] == "FILLED":
            entry["filled_quantity"] = int(entry["quantity"])

    def _order_view(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "order_id": entry["order_id"],
            "symbol": entry["symbol"],
            "side": entry["side"],
            "quantity": entry["quantity"],
            "price": entry["price"],
            "status": entry["status"],
            "ib_order_id": entry["ib_order_id"],
            "parent": entry["parent"],
            "children": list(entry["children"]),
            "oca_group": entry["oca_group"],
        }

    def _completed_view(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "order_id": entry["order_id"],
            "symbol": entry["symbol"],
            "side": entry["side"],
            "quantity": entry["quantity"],
            "status": entry["status"],
            "filled_quantity": entry["filled_quantity"],
            "avg_fill_price": entry["avg_fill_price"],
            "commission": entry["commission"],
            "commission_currency": entry["commission_currency"],
            "ib_order_id": entry["ib_order_id"],
        }

    def _trade_view(self, trade: Any) -> Dict[str, Any]:
        order = getattr(trade, "order", None)
        order_status = getattr(trade, "orderStatus", None)
        contract = getattr(trade, "contract", None)
        return {
            "order_id": str(getattr(order, "orderRef", "") or getattr(order, "orderId", 0)),
            "symbol": str(getattr(contract, "localSymbol", "") or getattr(contract, "symbol", "") or "").upper(),
            "side": str(getattr(order, "action", "")),
            "quantity": int(_parse_float(getattr(order, "totalQuantity", 0.0)) or 0.0),
            "price": _parse_float(getattr(order, "lmtPrice", None)) or _parse_float(getattr(order, "auxPrice", None)),
            "status": _IB_TO_PLATFORM_STATUS.get(str(getattr(order_status, "status", "")), "RECEIVED"),
            "ib_order_id": int(getattr(order, "orderId", 0) or 0),
            "parent": None,
            "children": [],
            "oca_group": str(getattr(order, "ocaGroup", "") or "") or None,
        }

    def _completed_trade_view(self, trade: Any) -> Dict[str, Any]:
        order = getattr(trade, "order", None)
        order_status = getattr(trade, "orderStatus", None)
        contract = getattr(trade, "contract", None)
        fills = list(getattr(trade, "fills", []) or [])
        total = 0.0
        notional = 0.0
        commission = 0.0
        currency = ""
        for fill in fills:
            execution = getattr(fill, "execution", None)
            shares = _parse_float(getattr(execution, "shares", None)) or 0.0
            price = _parse_float(getattr(execution, "price", None)) or 0.0
            total += shares
            notional += shares * price
            report = getattr(fill, "commissionReport", None)
            if report is not None:
                commission += _parse_float(getattr(report, "commission", None)) or 0.0
                currency = str(getattr(report, "currency", "") or "") or currency
        return {
            "order_id": str(getattr(order, "orderRef", "") or getattr(order, "orderId", 0)),
            "symbol": str(getattr(contract, "localSymbol", "") or getattr(contract, "symbol", "") or "").upper(),
            "side": str(getattr(order, "action", "")),
            "quantity": int(_parse_float(getattr(order, "totalQuantity", 0.0)) or 0.0),
            "status": _IB_TO_PLATFORM_STATUS.get(str(getattr(order_status, "status", "")), "RECEIVED"),
            "filled_quantity": int(total),
            "avg_fill_price": (notional / total) if total > 0 else None,
            "commission": commission if fills else None,
            "commission_currency": currency,
            "ib_order_id": int(getattr(order, "orderId", 0) or 0),
        }
