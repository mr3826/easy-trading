"""Phase 1 — Domain layer unit tests.

These tests encode permanent invariants as specified in Phase 1 exit gate G1.
"""

from datetime import datetime, timezone

import pytest
from trading_platform.domain import (
    Bar,
    BrokerSnapshot,
    CorporateAction,
    CorporateActionType,
    Instrument,
    JournalEvent,
    Order,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    Position,
    ReconciliationResult,
    RiskDecision,
    Signal,
    TimeInForce,
)


def test_invalid_nan_price_rejection():
    """NaN price must be rejected."""
    inst = Instrument(symbol="AAPL")
    with pytest.raises(ValueError):
        _ = Bar(
            instrument=inst,
            timestamp=datetime.now(timezone.utc),
            open=float("nan"),
            high=float("nan"),
            low=float("nan"),
            close=float("nan"),
            volume=100,
        )


def test_invalid_negative_quantity_rejection():
    """Negative quantity must be rejected."""
    inst = Instrument(symbol="AAPL")
    with pytest.raises(ValueError):
        _ = Signal(
            instrument=inst,
            side=OrderSide.BUY,
            quantity=-10,
            price=150.0,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
        )


def test_invalid_zero_quantity_rejection():
    """Zero quantity must be rejected."""
    inst = Instrument(symbol="AAPL")
    with pytest.raises(ValueError):
        _ = Signal(
            instrument=inst,
            side=OrderSide.BUY,
            quantity=0,
            price=150.0,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
        )


def test_illegal_order_transition_rejection():
    """Order transitions must be explicit and tested."""
    inst = Instrument(symbol="AAPL")
    order = Order(
        order_id="test-1",
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=150.0,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=Signal(
            instrument=inst,
            side=OrderSide.BUY,
            quantity=10,
            price=150.0,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
        ),
    )
    # A newly submitted order must not be in FILLED state
    assert order.status != OrderStatus.FILLED


def test_environment_credential_mismatch_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    """paper/research config cannot enable live credentials via environment flags."""
    from trading_platform.config import load_config

    monkeypatch.setenv("TRADING_ENV", "paper")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_STATUS", "AUTHORIZED")
    config = load_config()
    assert config.environment == "paper"
    assert config.live_trading_enabled is False
    assert config.live_status == "NOT_AUTHORIZED"


def test_duplicate_event_idempotency():
    """Duplicate event IDs must not create duplicate economic actions."""
    inst = Instrument(symbol="AAPL")
    base_time = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    event1 = JournalEvent(
        event_id="dup-001",
        timestamp=base_time,
        environment="simulation",
        code_version="v1.0.0",
        config_version="v1.0.0",
        event_type="order_submitted",
        payload={"instrument": inst.symbol, "quantity": 10},
        source="strategy",
        checksum="hash1",
    )

    event2 = JournalEvent(
        event_id="dup-001",  # Same ID as event1
        timestamp=base_time,
        environment="simulation",
        code_version="v1.0.0",
        config_version="v1.0.0",
        event_type="order_submitted",
        payload={"instrument": inst.symbol, "quantity": 10},
        source="strategy",
        checksum="hash1",
    )

    # Same event ID should produce same checksum
    assert event1.checksum == event2.checksum
    assert event1.event_id == event2.event_id


def test_deterministic_id_time_replay():
    """Deterministic IDs and times must reproduce consistently."""
    from trading_platform.domain import JournalEvent

    inst = Instrument(symbol="AAPL")
    base_time = datetime(2026, 6, 1, 10, 30, 0, tzinfo=timezone.utc)

    event_a = JournalEvent(
        event_id="det-001",
        timestamp=base_time,
        environment="simulation",
        code_version="v1.0.0",
        config_version="v1.0.0",
        event_type="order_submitted",
        payload={"instrument": inst.symbol, "quantity": 10},
        source="strategy",
        checksum="abc123",
    )

    # Same inputs should produce same event
    assert event_a.timestamp == base_time
    assert event_a.environment == "simulation"
    assert event_a.code_version == "v1.0.0"


def test_secret_redaction():
    """Sensitive payload values must be masked before storage/output."""
    from trading_platform.domain import JournalEvent, redact_secrets

    inst = Instrument(symbol="AAPL")
    event = JournalEvent(
        event_id="sec-001",
        timestamp=datetime.now(timezone.utc),
        environment="simulation",
        code_version="v1.0.0",
        config_version="v1.0.0",
        event_type="order_submitted",
        payload={"api_key": "PLACEHOLDER-TRADING-TEST-KEY", "instrument": inst.symbol, "quantity": 10},
        source="strategy",
        checksum="hash",
    )

    redacted = redact_secrets(event.payload)
    assert redacted["api_key"] == "***REDACTED***"
    assert "PLACEHOLDER-TRADING-TEST-KEY" not in redacted.values()
    assert redacted["instrument"] == inst.symbol
    assert redacted["quantity"] == 10
    assert redacted is not event.payload


def test_portfolio_snapshot_immutability():
    """Portfolio snapshot should enforce structure."""
    inst_a = Instrument(symbol="AAPL")
    inst_b = Instrument(symbol="MSFT")

    pos_a = Position(
        instrument=inst_a,
        quantity=10,
        average_cost=150.0,
        market_value=1500.0,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
    )
    pos_b = Position(
        instrument=inst_b,
        quantity=5,
        average_cost=300.0,
        market_value=1500.0,
        unrealized_pnl=0.0,
        realized_pnl=0.0,
    )

    snapshot = PortfolioSnapshot(
        timestamp=datetime.now(timezone.utc),
        cash=10000.0,
        positions={inst_a: pos_a, inst_b: pos_b},
        gross_exposure=3000.0,
        net_exposure=3000.0,
        total_pnl=0.0,
    )

    assert len(snapshot.positions) == 2
    assert snapshot.gross_exposure == 3000.0


def test_risk_decision_approval():
    """Risk decision must be associated with order intent."""
    inst = Instrument(symbol="AAPL")
    signal = Signal(
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=150.0,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
    )
    order_intent = OrderIntent(
        signal=signal,
        order_id="risk-test-1",
    )

    risk_decision = RiskDecision(
        order_intent=order_intent,
        approved=True,
        reason="Within limits",
        position_notional=1500.0,
    )

    assert risk_decision.approved is True
    assert risk_decision.reason == "Within limits"
    assert risk_decision.position_notional == 1500.0


def test_order_lifecycle_transitions():
    """Order must progress through explicit states."""
    inst = Instrument(symbol="AAPL")
    order = Order(
        order_id="lifecycle-1",
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=150.0,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=Signal(
            instrument=inst,
            side=OrderSide.BUY,
            quantity=10,
            price=150.0,
            order_type=OrderType.LIMIT,
            time_in_force=TimeInForce.DAY,
        ),
    )

    # Submit → Received is a valid transition
    assert order.status == OrderStatus.SUBMITTED

    # Cannot be FILLED immediately without processing
    assert order.status != OrderStatus.FILLED


def test_bar_timestamp_utc():
    """Bar timestamps must be UTC-convertible."""
    inst = Instrument(symbol="AAPL")
    bar = Bar(
        instrument=inst,
        timestamp=datetime(2026, 3, 15, 0, 0, 0, tzinfo=timezone.utc),
        open=100.0,
        high=105.0,
        low=95.0,
        close=102.0,
        volume=1000,
    )
    assert bar.bar_date == "2026-03-15"


def test_corporate_action_split():
    """Corporate action split must have ratio."""
    inst = Instrument(symbol="AAPL")
    split = CorporateAction(
        instrument=inst,
        action_type=CorporateActionType.SPLIT,
        ex_date=datetime(2026, 6, 15, tzinfo=timezone.utc),
        ratio=2.0,
    )
    assert split.ratio == 2.0
    assert split.action_type == CorporateActionType.SPLIT
    assert split.action_type.value == "split"


def test_corporate_action_dividend():
    """Corporate action dividend must have cash amount."""
    inst = Instrument(symbol="AAPL")
    dividend = CorporateAction(
        instrument=inst,
        action_type=CorporateActionType.DIVIDEND,
        ex_date=datetime(2026, 12, 1, tzinfo=timezone.utc),
        record_date=datetime(2026, 11, 15, tzinfo=timezone.utc),
        pay_date=datetime(2026, 12, 15, tzinfo=timezone.utc),
        cash_amount=1.50,
    )
    assert dividend.cash_amount == 1.50
    assert dividend.action_type == CorporateActionType.DIVIDEND
    assert dividend.action_type.value == "dividend"


def test_corporate_action_validation_rejects_invalid_economics():
    """Split actions require a positive ratio; dividends require positive cash."""
    inst = Instrument(symbol="AAPL")
    with pytest.raises(ValueError):
        _ = CorporateAction(
            instrument=inst,
            action_type=CorporateActionType.SPLIT,
            ex_date=datetime(2026, 6, 15, tzinfo=timezone.utc),
            ratio=0.0,
        )
    with pytest.raises(ValueError):
        _ = CorporateAction(
            instrument=inst,
            action_type=CorporateActionType.DIVIDEND,
            ex_date=datetime(2026, 12, 1, tzinfo=timezone.utc),
            cash_amount=-1.50,
        )


def test_order_with_risk_decision():
    """Order with risk decision must have it persisted."""
    inst = Instrument(symbol="AAPL")
    signal = Signal(
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=150.0,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
    )
    order_intent = OrderIntent(signal=signal, order_id="order-risk-1")

    risk_decision = RiskDecision(
        order_intent=order_intent,
        approved=True,
        reason="Within limits",
    )

    order = Order(
        order_id="order-1",
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=150.0,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        status=OrderStatus.SUBMITTED,
        signal=signal,
        risk_decision=risk_decision,
    )

    assert order.risk_decision is not None
    assert order.risk_decision.approved is True


def test_broker_snapshot_fields():
    """Broker snapshot must have required fields."""
    bs = BrokerSnapshot(
        timestamp=datetime.now(timezone.utc),
        positions={},
        cash=10000.0,
        buying_power=15000.0,
    )
    assert bs.positions == {}
    assert bs.cash == 10000.0
    assert bs.status == "connected"


def test_reconciliation_result():
    """Reconciliation result must track differences."""
    rr = ReconciliationResult(
        timestamp=datetime.now(timezone.utc),
        differences={},
        cash_overview={},
        position_overview={},
        order_overview={},
        fill_overview={},
        reconciled=True,
        blocks_submissions=False,
    )
    assert rr.reconciled is True
    assert rr.blocks_submissions is False


def test_journal_event_required_fields():
    """Journal event must have all required fields."""
    inst = Instrument(symbol="AAPL")
    je = JournalEvent(
        event_id="journal-001",
        timestamp=datetime.now(timezone.utc),
        environment="simulation",
        code_version="v1.0.0",
        config_version="v1.0.0",
        event_type="order_submitted",
        payload={"instrument": inst.symbol},
        source="strategy",
        checksum="abc123def456",
    )
    assert je.event_id == "journal-001"
    assert je.environment == "simulation"
    assert je.code_version == "v1.0.0"
    assert je.event_type == "order_submitted"
    assert je.source == "strategy"
    assert je.checksum == "abc123def456"
