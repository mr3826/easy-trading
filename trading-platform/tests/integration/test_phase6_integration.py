"""Phase 6 comprehensive integration test - Production OMS, Hard Risk Engine, Reconciliation."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from trading_platform.oms.oms import (
    OMS,
    OCAGroup,
    FakeBroker,
    OrderLifecycle,
)
from trading_platform.risk.risk_engine import (
    HardRiskEngine,
    RiskPolicyVersion,
    ReconciliationEngine,
    SessionScheduler,
)
from trading_platform.domain import (
    BrokerSnapshot,
    Instrument,
    Order,
    OrderIntent,
    OrderSide,
    OrderType,
    TimeInForce,
    Signal,
    OrderStatus,
    RiskDecision,
)


def approve(order: Order) -> Order:
    signal = order.signal or Signal(
        order.instrument,
        order.side,
        order.quantity,
        order.price,
        order.order_type,
        order.time_in_force,
    )
    order.risk_decision = RiskDecision(
        OrderIntent(signal=signal, order_id=order.order_id),
        approved=True,
        reason="test approval",
        policy_version="test-v1",
    )
    return order


@pytest.mark.integration
def test_oms_state_machine_lifecycle():
    """Test OMS state machine lifecycle."""
    oms = OMS(oms_id="phase6_integration")
    inst = Instrument(symbol="AAPL")
    signal = Signal(
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )
    order = approve(Order(
        order_id="order-001",
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=signal,
    ))
    # Submit order
    result = oms.submit_order(order)
    order1_id = order.order_id  # Use the order's own ID
    assert result[0] is True
    assert oms.get_order_status(order1_id) == "SUBMITTED"

    # Accept order
    accepted = oms.accept_order(order1_id)
    assert accepted is True
    assert oms.get_order_status(order1_id) == "ACCEPTED"

    # Open order
    opened = oms.open_order(order1_id)
    assert opened is True
    assert oms.get_order_status(order1_id) == "OPEN"

    # Fill order (directly, not through execute_order which internally calls fill_order)
    filled = oms.fill_order(order1_id, 10, 102.0)
    assert filled is True
    assert oms.get_order_status(order1_id) == "FILLED"


@pytest.mark.integration
def test_oms_idempotency():
    """Duplicate submissions are rejected while the original order is pending."""
    oms = OMS(oms_id="idem_test")
    inst = Instrument(symbol="AAPL")
    signal = Signal(
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )
    order = approve(Order(
        order_id="order-001",
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=signal,
    ))

    result = oms.submit_order(order)
    order1_id = order.order_id
    assert result[0] is True
    assert oms.get_order_status(order1_id) == "SUBMITTED"

    duplicate = oms.submit_order(order)
    assert duplicate[0] is False
    assert "Duplicate submission" in duplicate[1]
    assert len(oms.orders) == 1
    assert oms.get_order_status(order1_id) == "SUBMITTED"

    keyed = approve(Order(
        order_id="order-002",
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=signal,
    ))
    keyed_result = oms.submit_order(keyed, "idem-key-1")
    assert keyed_result[0] is True

    conflicting = approve(Order(
        order_id="order-003",
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=signal,
    ))
    conflicting_result = oms.submit_order(conflicting, "idem-key-1")
    assert conflicting_result[0] is False
    assert "Duplicate submission" in conflicting_result[1]
    assert oms.get_order_status("order-002") == "SUBMITTED"
    assert oms.get_order("order-003") is None


@pytest.mark.integration
def test_oca_group():
    """Test OCA group for atomic cancel/replace."""
    oms = OMS(oms_id="oca-demo-001")
    inst = Instrument(symbol="AAPL")
    signal = Signal(
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )
    order_a = approve(Order(
        order_id="oca-a",
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=signal,
    ))
    order_b = approve(Order(
        order_id="oca-b",
        instrument=inst,
        side=OrderSide.SELL,
        quantity=10,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=signal,
    ))
    oca = OCAGroup(group_id="oca-demo-001")
    oca.add(order_a)
    oca.add(order_b)

    assert len(oca.orders) == 2
    oca.cancel_all()
    assert len(oca.orders) == 0


@pytest.mark.integration
def test_hard_risk_engine():
    """Test Hard Risk Engine with versioned policies."""
    risk_engine = HardRiskEngine()
    policy = RiskPolicyVersion(
        version=1,
        max_positions=3,
        max_gross_exposure=1_000_000.0,
        max_drawdown_pct=10.0,
        max_turnover_pct=20.0,
    )
    risk_engine.add_policy(policy)
    assert risk_engine.active_policy is not None
    assert risk_engine.active_policy.version == 1


@pytest.mark.integration
def test_reconciliation_engine():
    """Reconciliation derives from the durable ledger and fails closed on real mismatches."""
    oms = OMS(oms_id="recon-test")
    reconciliation = ReconciliationEngine(oms)
    assert len(reconciliation.errors) == 0

    order = approve(Order(
        order_id="recon-order-001",
        instrument=Instrument(symbol="AAPL"),
        side=OrderSide.BUY,
        quantity=5,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=None,
    ))
    oms.submit_order(order)
    oms.accept_order("recon-order-001")
    oms.open_order("recon-order-001")
    oms.fill_order("recon-order-001", 5, 100.0, execution_id="exec-001", commission=1.0, slippage=0.0)

    internal = reconciliation.build_internal_snapshot()
    assert internal["fill_count"] == 1
    assert internal["total_commission"] == 1.0
    assert internal["positions"] == {"AAPL": 5.0}

    snapshot = BrokerSnapshot(
        timestamp=datetime.now(timezone.utc),
        positions={Instrument(symbol="AAPL"): 5.0},
        cash=10_000.0 - 500.0 - 1.0,
        buying_power=9_499.0,
    )
    matched = reconciliation.reconcile_against_snapshot(snapshot, beginning_cash=10_000.0)
    assert matched["overall_status"] == "PASS"
    assert matched["blocks_new_orders"] is False

    mismatched = BrokerSnapshot(
        timestamp=datetime.now(timezone.utc),
        positions={Instrument(symbol="AAPL"): 4.0},
        cash=10_000.0 - 500.0 - 1.0,
        buying_power=9_499.0,
    )
    failed = reconciliation.reconcile_against_snapshot(mismatched, beginning_cash=10_000.0)
    assert failed["overall_status"] == "MISMATCH"
    assert failed["blocks_new_orders"] is True
    assert failed["operator_resolution_required"] is True
    assert reconciliation.blocks_new_orders


@pytest.mark.integration
def test_session_scheduler():
    """Test Session Scheduler."""
    oms = OMS(oms_id="sched-test")
    reconcile = ReconciliationEngine(oms)
    risk_engine = HardRiskEngine()
    scheduler = SessionScheduler(risk_engine, reconcile)
    startup_result = scheduler.startup()
    assert "status" in startup_result


@pytest.mark.integration
def test_broker_contract():
    """Fake broker executes and cancels orders without destroying ledger history."""
    oms = OMS(oms_id="broker-contract-test")
    fake_broker = FakeBroker(oms, fill_assumption="NEXT_OPEN")

    assert hasattr(fake_broker, "execute_order")
    assert hasattr(fake_broker, "cancel_all_orders")

    inst = Instrument(symbol="AAPL")
    filled = approve(Order(
        order_id="broker-order-001",
        instrument=inst,
        side=OrderSide.BUY,
        quantity=1,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=None,
    ))
    oms.submit_order(filled)
    execution = fake_broker.execute_order(filled, SimpleNamespace(open=102.0, close=103.0))
    assert execution["status"] == "FILLED"
    assert oms.get_order_status("broker-order-001") == "FILLED"

    opened = approve(Order(
        order_id="broker-order-002",
        instrument=inst,
        side=OrderSide.BUY,
        quantity=1,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=None,
    ))
    oms.submit_order(opened)
    oms.accept_order("broker-order-002")
    oms.open_order("broker-order-002")

    events_before_cancel = len(oms.get_event_ledger())
    assert events_before_cancel > 0
    fake_broker.cancel_all_orders()
    assert oms.get_order_status("broker-order-002") == "CANCELLED"
    assert len(oms.get_event_ledger()) == events_before_cancel + 1
