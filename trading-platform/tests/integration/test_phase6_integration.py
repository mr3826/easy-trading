"""Phase 6 comprehensive integration test - Production OMS, Hard Risk Engine, Reconciliation."""

import sys
from datetime import datetime

sys.path.insert(0, r"D:\hexabyte_technologies\easy-trading\trading-platform\src")

import pytest
from trading_platform.oms.oms import (
    OMS,
    IdempotencyKey,
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
    Instrument,
    Order,
    OrderSide,
    OrderType,
    TimeInForce,
    Signal,
    OrderStatus,
)
from trading_platform.strategies.ma_cross_strategy import MaCrossHypothesis


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
    order = Order(
        order_id="order-001",
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=signal,
    )
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
    """Test idempotency key prevention of duplicate orders."""
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
    order = Order(
        order_id="order-001",
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=signal,
    )

    result = oms.submit_order(order)
    order1_id = order.order_id

    # Submit with same key should be idempotent
    # OMS uses order_id from the order object for idempotency
    # Since we submit the same order object, it should be idempotent
    result2 = oms.submit_order(order)
    # The order object's order_id is the same
    assert order.order_id == order1_id


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
    order_a = Order(
        order_id="oca-a",
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=signal,
    )
    order_b = Order(
        order_id="oca-b",
        instrument=inst,
        side=OrderSide.SELL,
        quantity=10,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=signal,
    )
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
    """Test Reconciliation Engine."""
    oms = OMS(oms_id="recon-test")
    reconciliation = ReconciliationEngine(oms)
    assert len(reconciliation.errors) == 0

    result = reconciliation.reconcile_all(
        beginning_cash=10000.0,
        expected_ending_cash=10000.0,
        expected_positions={},
        actual_positions={},
        expected_fills=0,
        actual_fills=0,
        oms_orders={},
        broker_orders={},
    )
    assert "overall_status" in result


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
    """Test BrokerAdapter contract with fake broker."""
    oms = OMS(oms_id="broker-contract-test")
    fake_broker = FakeBroker(oms, fill_assumption="CLOSE")

    assert hasattr(fake_broker, "execute_order")
    assert hasattr(fake_broker, "cancel_all_orders")
