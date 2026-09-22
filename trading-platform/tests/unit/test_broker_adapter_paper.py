"""Deterministic offline tests for the IBKR paper adapter.

The ib_async client library is never imported: a pure-Python fake module is
injected into sys.modules so the adapter's lazy import resolves against a mock.
No real connection is attempted and no order reaches a broker.
"""

from __future__ import annotations

import subprocess
import sys
import types
from collections import namedtuple
from datetime import datetime, timezone

import pytest
from trading_platform.broker_adapter import IBKRPaperBrokerAdapter
from trading_platform.domain import (
    Bar,
    BrokerSnapshot,
    Instrument,
    Order,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    RiskDecision,
    Signal,
    TimeInForce,
)

DONE_IB_STATUSES = frozenset({"Filled", "Cancelled", "ApiCancelled", "Inactive"})


class FakeEvent:
    def __init__(self) -> None:
        self._handlers: list = []

    def __iadd__(self, handler):
        self._handlers.append(handler)
        return self

    def __isub__(self, handler):
        self._handlers.remove(handler)
        return self

    def emit(self, *args) -> None:
        for handler in list(self._handlers):
            handler(*args)


class FakeIBOrder:
    def __init__(self, action, totalQuantity, orderType="LMT", lmtPrice=None, auxPrice=None, **kwargs):
        self.action = action
        self.totalQuantity = totalQuantity
        self.orderType = orderType
        self.lmtPrice = lmtPrice
        self.auxPrice = auxPrice
        self.orderId = kwargs.get("orderId", 0)
        self.parentId = kwargs.get("parentId", 0)
        self.transmit = kwargs.get("transmit", True)
        self.tif = kwargs.get("tif", "DAY")
        self.ocaGroup = ""
        self.ocaType = 0


class FakeContract:
    def __init__(self, symbol="", exchange="", currency="", secType="STK"):
        self.symbol = symbol
        self.exchange = exchange
        self.currency = currency
        self.secType = secType
        self.localSymbol = ""


class FakeExecution:
    def __init__(self, execId="", side="", shares=0.0, price=0.0, orderId=0):
        self.execId = execId
        self.side = side
        self.shares = shares
        self.price = price
        self.orderId = orderId
        self.time = datetime(2026, 9, 21, tzinfo=timezone.utc)


class FakeCommissionReport:
    def __init__(self, execId="", commission=0.0, currency="USD"):
        self.execId = execId
        self.commission = commission
        self.currency = currency


class FakeFill:
    def __init__(self, contract, execution, commissionReport):
        self.contract = contract
        self.execution = execution
        self.commissionReport = commissionReport


class FakeOrderStatusObj:
    def __init__(self, orderId=0, status="PendingSubmit"):
        self.orderId = orderId
        self.status = status
        self.completedStatus = ""


class FakeTrade:
    def __init__(self, contract, order):
        self.contract = contract
        self.order = order
        self.orderStatus = FakeOrderStatusObj(order.orderId, "PendingSubmit")
        self.fills = []
        self.log = []


FakeBracketOrder = namedtuple("FakeBracketOrder", ["parent", "takeProfit", "stopLoss"])


class FakeAccountValue:
    def __init__(self, account, tag, value, currency="USD"):
        self.account = account
        self.tag = tag
        self.value = value
        self.currency = currency


class FakePosition:
    def __init__(self, account, contract, position, avgCost):
        self.account = account
        self.contract = contract
        self.position = position
        self.avgCost = avgCost


class FakeClient:
    def __init__(self, ib):
        self._ib = ib
        self._seq = 100

    def getReqId(self):
        if not self._ib.isConnected():
            raise ConnectionError("Not connected")
        new_id = self._seq
        self._seq += 1
        return new_id

    def isConnected(self):
        return self._ib.isConnected()


class FakeIB:
    def __init__(self):
        self.connectedEvent = FakeEvent()
        self.disconnectedEvent = FakeEvent()
        self.newOrderEvent = FakeEvent()
        self.orderModifyEvent = FakeEvent()
        self.orderStatusEvent = FakeEvent()
        self.execDetailsEvent = FakeEvent()
        self.commissionReportEvent = FakeEvent()
        self.errorEvent = FakeEvent()
        self.cancelOrderEvent = FakeEvent()
        self.openOrderEvent = FakeEvent()
        self.positionEvent = FakeEvent()
        self.accountSummaryEvent = FakeEvent()
        self.client = FakeClient(self)
        self.host = ""
        self.port = 0
        self.account = ""
        self.trades_list = []
        self._trade_by_id = {}
        self._account_values = []
        self._positions = []
        self._completed_trades = []
        self.connect_calls = []
        self._connected = False

    def connect(self, host="127.0.0.1", port=7497, clientId=1, timeout=4, readonly=False, account=""):
        self.connect_calls.append(
            {"host": host, "port": port, "clientId": clientId, "readonly": readonly, "account": account}
        )
        self.host = host
        self.port = port
        self.account = account
        self._connected = True
        self.connectedEvent.emit()
        return self

    def disconnect(self):
        self._connected = False
        self.disconnectedEvent.emit()
        return None

    def isConnected(self):
        return self._connected

    def placeOrder(self, contract, order):
        trade = self._trade_by_id.get(order.orderId)
        if trade is None:
            if not order.orderId:
                order.orderId = self.client.getReqId()
            trade = FakeTrade(contract, order)
            self._trade_by_id[order.orderId] = trade
            self.trades_list.append(trade)
            self.newOrderEvent.emit(trade)
        else:
            self.orderModifyEvent.emit(trade)
        return trade

    def cancelOrder(self, order, manualCancelOrderTime=""):
        trade = self._trade_by_id.get(order.orderId)
        if trade is not None:
            self.cancelOrderEvent.emit(trade)
        return trade

    def accountSummary(self, account=""):
        return self._account_values

    def positions(self, account=""):
        return self._positions

    def openTrades(self):
        return [t for t in self.trades_list if t.orderStatus.status not in DONE_IB_STATUSES]

    def trades(self):
        return list(self.trades_list)

    def fills(self):
        return [f for t in self.trades_list for f in t.fills]

    def reqCompletedOrders(self, apiOnly):
        return list(self._completed_trades)

    def bracketOrder(self, action, quantity, limitPrice, takeProfitPrice, stopLossPrice, **kwargs):
        reverse_action = "BUY" if action == "SELL" else "SELL"
        parent = FakeIBOrder(
            action, quantity, "LMT", lmtPrice=limitPrice, orderId=self.client.getReqId(), transmit=False
        )
        take_profit = FakeIBOrder(
            reverse_action,
            quantity,
            "LMT",
            lmtPrice=takeProfitPrice,
            orderId=self.client.getReqId(),
            transmit=False,
            parentId=parent.orderId,
        )
        stop_loss = FakeIBOrder(
            reverse_action,
            quantity,
            "STP",
            auxPrice=stopLossPrice,
            orderId=self.client.getReqId(),
            transmit=True,
            parentId=parent.orderId,
        )
        return FakeBracketOrder(parent, take_profit, stop_loss)

    def oneCancelsAll(self, orders, ocaGroup, ocaType):
        for order in orders:
            order.ocaGroup = ocaGroup
            order.ocaType = ocaType
        return orders


class UnconnectableIB(FakeIB):
    def connect(self, host="127.0.0.1", port=7497, clientId=1, timeout=4, readonly=False, account=""):
        raise TimeoutError("simulated connection timeout")


def fake_limit_order(action, totalQuantity, lmtPrice, **kwargs):
    return FakeIBOrder(action, totalQuantity, "LMT", lmtPrice=lmtPrice, **kwargs)


def fake_market_order(action, totalQuantity, **kwargs):
    return FakeIBOrder(action, totalQuantity, "MKT", **kwargs)


def fake_stop_order(action, totalQuantity, stopPrice, **kwargs):
    return FakeIBOrder(action, totalQuantity, "STP", auxPrice=stopPrice, **kwargs)


def fake_stop_limit_order(action, totalQuantity, lmtPrice, stopPrice, **kwargs):
    return FakeIBOrder(action, totalQuantity, "STP LMT", lmtPrice=lmtPrice, auxPrice=stopPrice, **kwargs)


def fake_ib_async_module(ib_class=FakeIB):
    module = types.ModuleType("ib_async")
    module.IB = ib_class
    module.Stock = FakeContract
    module.Index = FakeContract
    module.LimitOrder = fake_limit_order
    module.MarketOrder = fake_market_order
    module.StopOrder = fake_stop_order
    module.StopLimitOrder = fake_stop_limit_order
    return module


@pytest.fixture
def ib_async_fake(monkeypatch):
    module = fake_ib_async_module()
    monkeypatch.setitem(sys.modules, "ib_async", module)
    return module


@pytest.fixture
def connected_adapter(ib_async_fake):
    ib = FakeIB()
    adapter = IBKRPaperBrokerAdapter(
        allow_connection=True, allow_paper_orders=True, account="DU1234567", ib_instance=ib
    )
    assert adapter.start(), adapter.get_last_error()
    return adapter, ib


def make_bar(symbol: str = "AAPL") -> Bar:
    instrument = Instrument(symbol)
    return Bar(instrument, datetime(2026, 9, 21, tzinfo=timezone.utc), 100.0, 102.0, 99.0, 101.0, 1000)


def make_order(
    order_id: str = "order-1",
    price: float | None = 100.0,
    quantity: int = 10,
    order_type: OrderType = OrderType.LIMIT,
    side: OrderSide = OrderSide.BUY,
) -> Order:
    instrument = Instrument("AAPL")
    signal = Signal(instrument, side, quantity, price, order_type, TimeInForce.DAY)
    intent = OrderIntent(signal=signal, order_id=order_id)
    return Order(
        order_id,
        instrument,
        side,
        quantity,
        price,
        order_type,
        TimeInForce.DAY,
        OrderStatus.SUBMITTED,
        signal,
        risk_decision=RiskDecision(intent, True, reason="test approval", policy_version="test-v1"),
    )


def test_two_flag_gate_blocks_submission_without_both_flags() -> None:
    with pytest.raises(RuntimeError):
        IBKRPaperBrokerAdapter(ib_instance=FakeIB()).execute_order(make_order(), make_bar())
    with pytest.raises(RuntimeError):
        IBKRPaperBrokerAdapter(allow_connection=True, ib_instance=FakeIB()).execute_order(make_order(), make_bar())
    disconnected = IBKRPaperBrokerAdapter(allow_connection=True, allow_paper_orders=True, ib_instance=FakeIB())
    with pytest.raises(RuntimeError):
        disconnected.execute_order(make_order(), make_bar())
    with pytest.raises(ValueError):
        IBKRPaperBrokerAdapter(allow_paper_orders=True)


def test_live_ports_rejected_in_adapter() -> None:
    with pytest.raises(ValueError) as tws_live:
        IBKRPaperBrokerAdapter(port=7496)
    assert "live" in str(tws_live.value)
    with pytest.raises(ValueError):
        IBKRPaperBrokerAdapter(port=4001)
    with pytest.raises(ValueError):
        IBKRPaperBrokerAdapter(port=7496, allow_connection=True, allow_paper_orders=True)


def test_live_account_configurations_rejected_in_adapter() -> None:
    with pytest.raises(ValueError) as marked_live:
        IBKRPaperBrokerAdapter(account="LIVE123")
    assert "live" in str(marked_live.value).lower()
    with pytest.raises(ValueError):
        IBKRPaperBrokerAdapter(account="U1234567")
    with pytest.raises(ValueError):
        IBKRPaperBrokerAdapter(account="DU12")


def test_paper_targets_accepted() -> None:
    adapter_tws = IBKRPaperBrokerAdapter(port=7497, account="DU123456")
    assert adapter_tws.broker_id == "IBKR"
    adapter_gateway = IBKRPaperBrokerAdapter(port=4002)
    assert not adapter_gateway.connected
    adapter_default = IBKRPaperBrokerAdapter()
    assert not adapter_default.connected


def test_connect_lifecycle_with_mocked_client(ib_async_fake) -> None:
    ib = FakeIB()
    adapter = IBKRPaperBrokerAdapter(
        allow_connection=True,
        host="127.0.0.1",
        port=7497,
        client_id=7,
        account="DU1234567",
        ib_instance=ib,
    )
    assert adapter.start()
    assert adapter.connected
    assert ib.connect_calls == [
        {"host": "127.0.0.1", "port": 7497, "clientId": 7, "readonly": False, "account": "DU1234567"}
    ]
    assert adapter.stop()
    assert not adapter.connected
    assert not ib.isConnected()
    assert adapter.start()
    assert adapter.connected


def test_connect_failure_records_error(monkeypatch) -> None:
    module = fake_ib_async_module(UnconnectableIB)
    monkeypatch.setitem(sys.modules, "ib_async", module)
    adapter = IBKRPaperBrokerAdapter(allow_connection=True, account="DU1234567")
    assert not adapter.start()
    assert adapter.get_last_error() is not None
    assert not adapter.connected


def test_missing_ib_async_library_surfaces_error(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "ib_async", None)
    adapter = IBKRPaperBrokerAdapter(allow_connection=True, account="DU1234567")
    assert not adapter.start()
    assert "ib_async" in adapter.get_last_error()


def test_connected_property_is_read_only() -> None:
    adapter = IBKRPaperBrokerAdapter(ib_instance=FakeIB())
    with pytest.raises(AttributeError):
        adapter.connected = True
    with pytest.raises(RuntimeError):
        adapter.execute_order(make_order(), make_bar())


def test_next_valid_order_id_tracking(connected_adapter) -> None:
    adapter, _ib = connected_adapter
    result_one = adapter.execute_order(make_order("order-1"), make_bar())
    result_two = adapter.execute_order(make_order("order-2"), make_bar())
    assert result_two["ib_order_id"] == result_one["ib_order_id"] + 1
    assert adapter.next_valid_order_id == result_two["ib_order_id"] + 1
    assert adapter.get_order_status("order-1") == "RECEIVED"
    assert adapter.get_order_status("order-2") == "RECEIVED"


def test_submission_result_and_acknowledgement(connected_adapter) -> None:
    adapter, ib = connected_adapter
    order = make_order("order-1", price=101.5)
    result = adapter.execute_order(order, make_bar())
    assert result["order_id"] == "order-1"
    assert result["symbol"] == "AAPL"
    assert result["side"] == "BUY"
    assert result["quantity"] == 10
    assert result["status"] == "RECEIVED"
    assert result["fill_price"] is None
    assert result["commission"] is None
    assert result["fill_quantity"] == 0
    trade = ib.trades_list[0]
    trade.orderStatus.status = "Submitted"
    ib.orderStatusEvent.emit(trade)
    assert adapter.get_order_status("order-1") == "SUBMITTED"
    ib.openOrderEvent.emit(trade)
    assert adapter.get_order_status("order-1") == "OPEN"
    trade.orderStatus.status = "Filled"
    ib.orderStatusEvent.emit(trade)
    assert adapter.get_order_status("order-1") == "FILLED"


def test_status_update_mapping(connected_adapter) -> None:
    adapter, ib = connected_adapter
    adapter.execute_order(make_order("order-1"), make_bar())
    trade = ib.trades_list[0]
    trade.orderStatus.status = "PreSubmitted"
    ib.orderStatusEvent.emit(trade)
    assert adapter.get_order_status("order-1") == "SUBMITTED"
    trade.orderStatus.status = "ApiPending"
    ib.orderStatusEvent.emit(trade)
    assert adapter.get_order_status("order-1") == "SUBMITTED"
    trade.orderStatus.status = "PendingCancel"
    ib.orderStatusEvent.emit(trade)
    assert adapter.get_order_status("order-1") == "SUBMITTED"
    trade.orderStatus.status = "Cancelled"
    ib.orderStatusEvent.emit(trade)
    assert adapter.get_order_status("order-1") == "CANCELLED"
    trade.orderStatus.status = "ApiCancelled"
    ib.orderStatusEvent.emit(trade)
    assert adapter.get_order_status("order-1") == "CANCELLED"
    trade.orderStatus.status = "Inactive"
    ib.orderStatusEvent.emit(trade)
    assert adapter.get_order_status("order-1") == "REJECTED"
    trade.orderStatus.status = "ValidationError"
    ib.orderStatusEvent.emit(trade)
    assert adapter.get_order_status("order-1") == "REJECTED"


def test_cancellation_flow(connected_adapter) -> None:
    adapter, ib = connected_adapter
    adapter.execute_order(make_order("order-1"), make_bar())
    trade = ib.trades_list[0]
    trade.orderStatus.status = "Submitted"
    ib.orderStatusEvent.emit(trade)
    assert adapter.get_order_status("order-1") == "SUBMITTED"
    assert adapter.cancel_order("order-1", "risk gate")
    trade.orderStatus.status = "PendingCancel"
    ib.orderStatusEvent.emit(trade)
    assert adapter.get_order_status("order-1") == "SUBMITTED"
    trade.orderStatus.status = "Cancelled"
    ib.orderStatusEvent.emit(trade)
    assert adapter.get_order_status("order-1") == "CANCELLED"
    assert not adapter.cancel_order("unknown-id")
    adapter.execute_order(make_order("order-2"), make_bar())
    assert adapter.stop()
    assert not adapter.cancel_order("order-2")
    assert adapter.get_last_error() is not None


def test_replacement_modifies_existing_ib_order(connected_adapter) -> None:
    adapter, ib = connected_adapter
    adapter.execute_order(make_order("order-1", price=100.0), make_bar())
    trade = ib.trades_list[0]
    original_id = trade.order.orderId
    result = adapter.execute_order(make_order("order-1", price=105.0), make_bar())
    assert ib.trades_list == [trade]
    assert trade.order.lmtPrice == 105.0
    assert result["ib_order_id"] == original_id
    assert adapter.list_orders()["order-1"]["price"] == 105.0


def test_partial_fills_and_commissions(connected_adapter) -> None:
    adapter, ib = connected_adapter
    adapter.execute_order(make_order("order-1", quantity=10), make_bar())
    trade = ib.trades_list[0]
    fill_one = FakeFill(
        trade.contract,
        FakeExecution(execId="exec-1", side="BOT", shares=4.0, price=100.25),
        FakeCommissionReport(execId="exec-1", commission=1.0),
    )
    ib.execDetailsEvent.emit(trade, fill_one)
    ib.commissionReportEvent.emit(trade, fill_one, fill_one.commissionReport)
    trade.orderStatus.status = "Submitted"
    ib.orderStatusEvent.emit(trade)
    assert adapter.get_order_status("order-1") == "SUBMITTED"
    fill_two = FakeFill(
        trade.contract,
        FakeExecution(execId="exec-2", side="BOT", shares=6.0, price=100.75),
        FakeCommissionReport(execId="exec-2", commission=0.75),
    )
    ib.execDetailsEvent.emit(trade, fill_two)
    ib.commissionReportEvent.emit(trade, fill_two, fill_two.commissionReport)
    trade.orderStatus.status = "Filled"
    ib.orderStatusEvent.emit(trade)
    assert adapter.get_order_status("order-1") == "FILLED"
    assert adapter.stop()
    completed = adapter.list_completed_orders()
    assert [entry["order_id"] for entry in completed] == ["order-1"]
    entry = completed[0]
    assert entry["filled_quantity"] == 10
    assert entry["avg_fill_price"] == pytest.approx((4.0 * 100.25 + 6.0 * 100.75) / 10.0)
    assert entry["commission"] == pytest.approx(1.75)
    assert entry["commission_currency"] == "USD"


def test_completed_orders_from_client_and_ledger(connected_adapter) -> None:
    adapter, ib = connected_adapter
    adapter.execute_order(make_order("order-1", quantity=5), make_bar())
    trade = ib.trades_list[0]
    trade.orderStatus.status = "Cancelled"
    ib.orderStatusEvent.emit(trade)
    completed_trade = FakeTrade(trade.contract, trade.order)
    completed_trade.orderStatus.status = "Cancelled"
    ib._completed_trades.append(completed_trade)
    client_view = adapter.list_completed_orders()
    assert [entry["order_id"] for entry in client_view] == ["order-1"]
    assert client_view[0]["status"] == "CANCELLED"
    assert adapter.stop()
    ledger_view = adapter.list_completed_orders()
    assert [entry["order_id"] for entry in ledger_view] == ["order-1"]
    assert ledger_view[0]["status"] == "CANCELLED"


def test_rejection_via_error_event(connected_adapter) -> None:
    adapter, ib = connected_adapter
    result = adapter.execute_order(make_order("order-1"), make_bar())
    ib.errorEvent.emit(result["ib_order_id"], 201, "order rejected: risk", None)
    assert adapter.get_order_status("order-1") == "REJECTED"
    assert "rejected" in adapter.get_last_error()
    result_two = adapter.execute_order(make_order("order-2"), make_bar())
    ib.errorEvent.emit(result_two["ib_order_id"], 2104, "market data farm connection is OK", None)
    assert adapter.get_order_status("order-2") != "REJECTED"
    ib.errorEvent.emit(-1, 2104, "market data farm connection is OK", None)
    assert adapter.get_order_status("order-2") != "REJECTED"


def test_bracket_order_parent_child_and_oca(connected_adapter) -> None:
    adapter, ib = connected_adapter
    order = make_order("order-1", order_type=OrderType.LIMIT, price=100.0)
    result = adapter.execute_bracket_order(order, make_bar(), take_profit_price=105.0, stop_loss_price=95.0)
    assert len(ib.trades_list) == 3
    parent_trade, tp_trade, sl_trade = ib.trades_list
    assert parent_trade.order.transmit is False
    assert tp_trade.order.parentId == parent_trade.order.orderId
    assert sl_trade.order.parentId == parent_trade.order.orderId
    assert sl_trade.order.transmit is True
    assert tp_trade.order.ocaGroup == "OCA-order-1"
    assert sl_trade.order.ocaGroup == "OCA-order-1"
    assert result["order_id"] == "order-1"
    assert result["children"] == ["order-1:TP", "order-1:SL"]
    assert adapter.get_order_status("order-1:TP") == "RECEIVED"
    assert adapter.get_order_status("order-1:SL") == "RECEIVED"


def test_account_snapshot_positions_and_order_retrieval(connected_adapter) -> None:
    adapter, ib = connected_adapter
    ib._account_values = [
        FakeAccountValue("DU123456", "CashBalance", "100000.00"),
        FakeAccountValue("DU123456", "BuyingPower", "200000.00"),
        FakeAccountValue("DU123456", "NetLiquidation", "100500.00"),
        FakeAccountValue("DU123456", "InitMarginReq", "2500.00"),
    ]
    ib._positions = [FakePosition("DU123456", FakeContract("AAPL"), 10.0, 100.0)]
    snapshot = adapter.get_account_snapshot()
    assert isinstance(snapshot, BrokerSnapshot)
    assert snapshot.cash == pytest.approx(100000.0)
    assert snapshot.buying_power == pytest.approx(200000.0)
    assert snapshot.margin_used == pytest.approx(2500.0)
    assert snapshot.status == "connected"
    assert snapshot.positions[Instrument("AAPL")] == pytest.approx(10.0)
    positions = adapter.list_positions()
    assert positions[0]["symbol"] == "AAPL"
    assert positions[0]["quantity"] == 10
    assert positions[0]["avg_cost"] == pytest.approx(100.0)
    adapter.execute_order(make_order("order-1", quantity=5), make_bar())
    open_orders = adapter.list_open_orders()
    assert [entry["order_id"] for entry in open_orders] == ["order-1"]
    assert open_orders[0]["symbol"] == "AAPL"
    assert open_orders[0]["ib_order_id"] == ib.trades_list[0].order.orderId


def test_offline_ledger_fallback_and_disconnected_retrieval(connected_adapter) -> None:
    adapter, _ib = connected_adapter
    adapter.execute_order(make_order("order-1", quantity=5), make_bar())
    assert adapter.stop()
    assert not adapter.connected
    open_orders = adapter.list_open_orders()
    assert [entry["order_id"] for entry in open_orders] == ["order-1"]
    with pytest.raises(RuntimeError):
        adapter.get_account_snapshot()
    with pytest.raises(RuntimeError):
        adapter.list_positions()


def test_reconnect_recovery_preserves_ledger(connected_adapter) -> None:
    adapter, _ib = connected_adapter
    adapter.execute_order(make_order("order-1"), make_bar())
    assert adapter.stop()
    assert not adapter.connected
    assert adapter.start()
    assert adapter.connected
    assert adapter.get_order_status("order-1") == "RECEIVED"
    assert adapter.reconnect()
    assert adapter.connected


def test_adapter_ledger_is_independent_from_oms_state(connected_adapter) -> None:
    from trading_platform.oms.oms import OMS

    adapter, ib = connected_adapter
    order = make_order("order-1", quantity=5)
    adapter.execute_order(order, make_bar())
    oms = OMS("paper-check")
    oms.orders["oms-only"] = make_order("oms-only", quantity=1)
    oms.orders["order-1"] = order
    ledger_keys_before = set(adapter._orders)
    adapter.sync_state(oms)
    assert set(adapter._orders) == ledger_keys_before
    assert "oms-only" not in adapter._orders
    trade = ib.trades_list[0]
    trade.orderStatus.status = "Cancelled"
    ib.orderStatusEvent.emit(trade)
    adapter.sync_state(oms)
    assert oms.orders["order-1"].status == OrderStatus.CANCELLED
    assert "oms-only" not in adapter._orders


def test_no_module_level_ib_async_import() -> None:
    code = (
        "import sys; "
        "import trading_platform.broker_adapter; "
        "import trading_platform.broker.fake; "
        "import trading_platform.broker.ibkr_paper; "
        "assert 'ib_async' not in sys.modules, 'ib_async imported at module level'"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
