"""OMS-level reconciliation, fail-closed chain, ledger replay, and protective-order tests (R8-R12)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import pytest
from trading_platform.domain import (
    BrokerSnapshot,
    Instrument,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Signal,
    TimeInForce,
)
from trading_platform.oms.oms import OMS, FakeBroker, IdempotencyKey, OCAGroup
from trading_platform.reconciliation import QueuedIncidentSink
from trading_platform.risk.risk_engine import (
    HardRiskEngine,
    ReconciliationEngine,
    RiskPolicyVersion,
    SessionScheduler,
)

UTC = timezone.utc


def make_order(
    order_id: str,
    symbol: str = "AAPL",
    side: OrderSide = OrderSide.BUY,
    quantity: int = 10,
    price: float | None = 100.0,
    order_type: OrderType = OrderType.LIMIT,
) -> Order:
    instrument = Instrument(symbol)
    signal = Signal(instrument, side, quantity, price, order_type, TimeInForce.DAY)
    return Order(
        order_id, instrument, side, quantity, price, order_type, TimeInForce.DAY, OrderStatus.SUBMITTED, signal
    )


def open_order_in_oms(oms: OMS, order_id: str) -> None:
    assert oms.accept_order(order_id)
    assert oms.open_order(order_id)


def broker_snapshot(positions: dict[str, float], cash: float) -> BrokerSnapshot:
    return BrokerSnapshot(
        timestamp=datetime.now(UTC),
        positions={Instrument(symbol): quantity for symbol, quantity in positions.items()},
        cash=cash,
        buying_power=cash,
    )


def test_duplicate_submission_is_rejected_while_order_pending() -> None:
    oms = OMS("idem")
    order = make_order("o-1")
    accepted, _reason = oms.submit_order(order, "idem-1")
    assert accepted

    duplicate, reason = oms.submit_order(make_order("o-1-dup"), "idem-1")
    assert not duplicate
    assert "Duplicate submission" in reason
    assert oms.get_order_status("o-1") == "SUBMITTED"
    assert oms.get_order("o-1-dup") is None

    same_object, same_reason = oms.submit_order(order)
    assert not same_object
    assert "Duplicate submission" in same_reason
    assert len(oms.orders) == 1

    open_order_in_oms(oms, "o-1")
    assert oms.fill_order("o-1", 10, 101.0, execution_id="exec-1")
    terminal, terminal_reason = oms.submit_order(make_order("o-2"), "idem-1")
    assert not terminal
    assert "FILLED" in terminal_reason


def test_fill_events_carry_derived_fees_and_average_price() -> None:
    oms = OMS("fees")
    order = make_order("o-fee", quantity=10)
    oms.submit_order(order)
    open_order_in_oms(oms, "o-fee")

    assert oms.fill_order("o-fee", 4, 100.0, execution_id="e-1", commission=1.0, slippage=0.1)
    assert oms.get_order_status("o-fee") == "PARTIALLY_FILLED"
    assert oms.fill_order("o-fee", 6, 104.0, execution_id="e-2", commission=1.0, slippage=0.1)
    assert oms.get_order_status("o-fee") == "FILLED"

    expected_average = (4 * 100.0 + 6 * 104.0) / 10
    assert order.average_fill_price == pytest.approx(expected_average)
    assert order.filled_price == pytest.approx(expected_average)
    assert order.filled_quantity == 10
    assert order.filled_at is not None

    fill_events = [
        event for event in oms.get_event_ledger() if event["event"] in ("ORDER_FILLED", "ORDER_PARTIAL_FILL")
    ]
    assert len(fill_events) == 2
    assert all(event["commission"] == 1.0 for event in fill_events)
    assert all(event["slippage"] == 0.1 for event in fill_events)
    assert all(event["instrument"] == "AAPL" for event in fill_events)
    assert all(event["side"] == "BUY" for event in fill_events)
    assert [event["execution_id"] for event in fill_events] == ["e-1", "e-2"]
    assert [event["quantity"] for event in fill_events] == [4, 6]
    assert [event["status"] for event in fill_events] == ["PARTIALLY_FILLED", "FILLED"]


def make_bar(symbol: str, opening: float) -> Any:
    return type("BarStub", (), {"symbol": symbol, "open": opening, "close": opening + 1.0})()


def test_fake_broker_execution_records_fees_in_ledger() -> None:
    oms = OMS("broker-fees")
    order = make_order("o-broker", quantity=1)
    oms.submit_order(order)
    execution = FakeBroker(oms, "NEXT_OPEN").execute_order(order, make_bar("AAPL", opening=102.0))
    assert execution["status"] == "FILLED"
    fill_events = [event for event in oms.get_event_ledger() if event["event"] == "ORDER_FILLED"]
    assert len(fill_events) == 1
    assert fill_events[0]["commission"] == 1.0
    assert fill_events[0]["execution_id"] == "fake-1"
    assert fill_events[0]["slippage"] == 2.0


def test_duplicate_fill_is_deduped_by_execution_id() -> None:
    oms = OMS("dedupe")
    order = make_order("o-dup", quantity=10)
    oms.submit_order(order)
    open_order_in_oms(oms, "o-dup")

    assert oms.fill_order("o-dup", 5, 100.0, execution_id="same-exec")
    assert not oms.fill_order("o-dup", 5, 100.0, execution_id="same-exec")
    assert order.filled_quantity == 5
    events = [event for event in oms.get_event_ledger() if event["event"] == "DUPLICATE_FILL_DETECTED"]
    assert len(events) == 1
    assert events[0]["execution_id"] == "same-exec"


def test_late_fill_after_cancellation_is_recorded_not_dropped() -> None:
    oms = OMS("late")
    order = make_order("o-late", quantity=10)
    oms.submit_order(order)
    open_order_in_oms(oms, "o-late")
    assert oms.cancel_order("o-late")

    assert not oms.fill_order("o-late", 5, 100.0, execution_id="late-1")
    assert not order.filled_quantity
    events = [event for event in oms.get_event_ledger() if event["event"] == "LATE_FILL_DETECTED"]
    assert len(events) == 1
    assert events[0]["fill_quantity"] == 5
    assert events[0]["execution_id"] == "late-1"
    assert events[0]["severity"] == "CRITICAL"


def test_overfill_is_rejected_and_recorded() -> None:
    oms = OMS("over")
    order = make_order("o-over", quantity=10)
    oms.submit_order(order)
    open_order_in_oms(oms, "o-over")

    assert not oms.fill_order("o-over", 11, 100.0, execution_id="over-1")
    assert not order.filled_quantity
    events = [event for event in oms.get_event_ledger() if event["event"] == "OVERFILL_REJECTED"]
    assert len(events) == 1
    assert events[0]["remaining"] == 10


def test_replace_order_is_compensated_on_midway_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    oms = OMS("replace")
    old = make_order("o-old", quantity=10)
    oms.submit_order(old, "replace-key")
    open_order_in_oms(oms, "o-old")
    replacement = make_order("o-new", quantity=10, price=101.0)

    def failing_commit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(oms, "_commit_replacement", failing_commit)
    ok, reason = oms.replace_order("o-old", replacement, "replace-key")
    assert not ok
    assert "compensated" in reason.lower()
    assert oms.get_order_status("o-old") == "OPEN"
    assert oms.get_order("o-new") is None
    assert oms.idempotency_keys["replace-key"] == "o-old"
    events = [event for event in oms.get_event_ledger() if event["event"] == "ORDER_REPLACE_COMPENSATED"]
    assert len(events) == 1
    assert events[0]["status"] == "OPERATOR_RESOLUTION_REQUIRED"

    monkeypatch.undo()
    ok_after, _reason_after = oms.replace_order("o-old", replacement, "replace-key")
    assert ok_after
    assert oms.get_order_status("o-old") == "CANCELLED"
    assert oms.get_order_status("o-new") == "SUBMITTED"
    assert oms.idempotency_keys["replace-key"] == "o-new"


def test_replace_order_rejects_colliding_replacement_id_without_cancel() -> None:
    oms = OMS("replace-collide")
    old = make_order("o-old", quantity=10)
    oms.submit_order(old)
    open_order_in_oms(oms, "o-old")

    ok, reason = oms.replace_order("o-old", make_order("o-old"))
    assert not ok
    assert "already exists" in reason
    assert oms.get_order_status("o-old") == "OPEN"
    assert not [event for event in oms.get_event_ledger() if event["event"] == "ORDER_REPLACE_COMPENSATED"]


def test_rebuild_from_ledger_restores_state_and_dedup() -> None:
    oms = OMS("ledger")
    order = make_order("o-1", quantity=10)
    oms.submit_order(order, "idem-1")
    open_order_in_oms(oms, "o-1")
    oms.fill_order("o-1", 4, 100.0, execution_id="e-1", commission=1.0, slippage=0.1)
    ledger = [dict(event) for event in oms.get_event_ledger()]

    rebuilt = OMS("rebuilt")
    rebuilt.rebuild_from_ledger(ledger)
    assert rebuilt.get_order_status("o-1") == "PARTIALLY_FILLED"
    restored = rebuilt.get_order("o-1")
    assert restored is not None
    assert restored.filled_quantity == 4
    assert restored.average_fill_price == pytest.approx(100.0)
    assert rebuilt.get_event_ledger() == ledger

    duplicate, _reason = rebuilt.submit_order(make_order("o-1-dup"), "idem-1")
    assert not duplicate
    assert not rebuilt.fill_order("o-1", 4, 100.0, execution_id="e-1")
    deduped = [event for event in rebuilt.get_event_ledger() if event["event"] == "DUPLICATE_FILL_DETECTED"]
    assert len(deduped) == 1

    gapped = OMS("gap")
    gapped.rebuild_from_ledger(
        [
            {
                "event": "ORDER_FILLED",
                "order_id": "ghost",
                "instrument": "AAPL",
                "side": "BUY",
                "quantity": 1,
                "fill_quantity": 1,
                "fill_price": 100.0,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        ]
    )
    assert len([event for event in gapped.get_event_ledger() if event["event"] == "REPLAY_GAP"]) == 1


def test_reconcile_cash_derives_fees_from_durable_ledger() -> None:
    oms = OMS("cash")
    recon = ReconciliationEngine(oms)
    order = make_order("o-cash", quantity=5, price=None)
    oms.submit_order(order)
    open_order_in_oms(oms, "o-cash")
    oms.fill_order("o-cash", 5, 100.0, execution_id="e-1", commission=2.0, slippage=1.0)

    ok, _message = recon.reconcile_cash(1000.0, 1000.0 - 5 * 100.0 - 2.0 - 1.0)
    assert ok
    bad, _message = recon.reconcile_cash(1000.0, 1000.0)
    assert not bad


def test_internal_snapshot_matches_independent_broker_snapshot() -> None:
    oms = OMS("indep")
    recon = ReconciliationEngine(oms)
    broker = FakeBroker(oms, "NEXT_OPEN")
    beginning_cash = 10_000.0
    executions: list[dict[str, Any]] = []
    for index in range(2):
        order = make_order(f"o-{index}", quantity=5, price=None)
        oms.submit_order(order)
        executions.append(broker.execute_order(order, make_bar("AAPL", opening=100.0 + index)))

    independent_positions: dict[str, float] = {}
    independent_cash = beginning_cash
    for execution in executions:
        quantity = float(execution["quantity"])
        delta = -quantity if execution["side"] == "SELL" else quantity
        independent_positions[execution["symbol"]] = independent_positions.get(execution["symbol"], 0.0) + delta
        independent_cash -= delta * float(execution["fill_price"])
        independent_cash -= float(execution["commission"]) + float(execution["slippage"])

    snapshot = broker_snapshot(independent_positions, independent_cash)
    result = recon.reconcile_against_snapshot(snapshot, beginning_cash=beginning_cash)
    assert result["overall_status"] == "PASS"
    assert not result["blocks_new_orders"]

    mismatched_positions = dict(independent_positions)
    mismatched_positions["AAPL"] += 1.0
    failed = recon.reconcile_against_snapshot(
        broker_snapshot(mismatched_positions, independent_cash), beginning_cash=beginning_cash
    )
    assert failed["overall_status"] == "MISMATCH"
    assert failed["blocks_new_orders"]
    assert failed["operator_resolution_required"]
    assert "AAPL" in failed["differences"][0]
    assert recon.blocks_new_orders


def test_fail_closed_chain_wires_incident_alert_and_resolution() -> None:
    sink = QueuedIncidentSink()
    alerts: list[str] = []
    oms = OMS("chain")
    recon = ReconciliationEngine(oms, incident_sink=sink, alert_callback=alerts.append)
    engine = HardRiskEngine([RiskPolicyVersion(1, min_cash_reserve_pct=0)], reconciliation=recon)
    order = make_order("o-chain")

    result = recon.reconcile_against_snapshot(broker_snapshot({"AAPL": 5.0}, 1000.0))
    assert result["overall_status"] == "MISMATCH"
    assert recon.blocks_new_orders
    assert len(sink.recorded()) == 1
    incident = sink.recorded()[0]
    assert incident["severity"] == "CRITICAL"
    assert incident["status"] == "OPERATOR_RESOLUTION_REQUIRED"
    assert incident["details"]["operator_resolution_required"] is True
    assert incident["details"]["incident_id"] == result["incident_id"]
    assert len(alerts) == 1
    assert "OPERATOR_RESOLUTION_REQUIRED" in alerts[0]

    approved, reason, _policy = engine.check_order(order, {}, 1000.0)
    assert not approved
    assert "OPERATOR_RESOLUTION_REQUIRED" in reason

    assert not recon.resolve("")
    assert recon.blocks_new_orders
    assert recon.resolve("operator", "acknowledged")
    assert not recon.blocks_new_orders
    approved_again, _reason_again, _policy_again = engine.check_order(order, {}, 1000.0)
    assert approved_again


def test_incident_sink_failure_keeps_new_orders_blocked() -> None:
    class FailingSink:
        def record_incident(self, incident: Mapping[str, Any]) -> None:
            raise RuntimeError("sink unavailable")

    oms = OMS("sinkfail")
    recon = ReconciliationEngine(oms, incident_sink=FailingSink(), alert_callback=None)
    result = recon.reconcile_against_snapshot(broker_snapshot({"AAPL": 5.0}, 1000.0))
    assert result["overall_status"] == "MISMATCH"
    assert recon.blocks_new_orders
    assert recon.reconciliation_status == "OPERATOR_RESOLUTION_REQUIRED"


def test_startup_uses_real_durable_inputs_and_fails_closed() -> None:
    oms = OMS("startup")
    recon = ReconciliationEngine(oms)
    scheduler = SessionScheduler(HardRiskEngine(), recon)

    plain = scheduler.startup()
    assert plain["status"] == "STARTUP_OK"
    assert plain["warnings"]

    matched = scheduler.startup(
        beginning_cash=10_000.0, expected_ending_cash=10_000.0, expected_fills=0, broker_orders={}
    )
    assert matched["status"] == "STARTUP_OK"

    order = make_order("o-start", quantity=5)
    oms.submit_order(order)
    open_order_in_oms(oms, "o-start")
    halted = scheduler.startup(
        beginning_cash=10_000.0, expected_ending_cash=10_000.0, expected_fills=0, broker_orders={}
    )
    assert halted["status"] == "TRADING_HALTED"
    assert halted["operator_resolution_required"]
    assert halted["incident_id"] is not None
    assert recon.blocks_new_orders


def test_startup_cash_mismatch_halts_trading() -> None:
    oms = OMS("startup-cash")
    recon = ReconciliationEngine(oms)
    scheduler = SessionScheduler(HardRiskEngine(), recon)
    order = make_order("o-startup-cash", quantity=5, price=None)
    oms.submit_order(order)
    open_order_in_oms(oms, "o-startup-cash")
    oms.fill_order("o-startup-cash", 5, 100.0, execution_id="e-1", commission=2.0, slippage=1.0)
    halted = scheduler.startup(
        beginning_cash=10_000.0,
        expected_ending_cash=10_000.0,
        expected_fills=1,
        broker_snapshot=broker_snapshot({"AAPL": 5.0}, 10_000.0 - 5 * 100.0 - 3.0),
        broker_orders={},
    )
    assert halted["status"] == "TRADING_HALTED"
    assert any("Cash reconciliation" in detail for detail in halted["details"])

    resolved = scheduler.startup(
        beginning_cash=10_000.0,
        expected_ending_cash=10_000.0 - 5 * 100.0 - 3.0,
        expected_fills=1,
        broker_snapshot=broker_snapshot({"AAPL": 5.0}, 10_000.0 - 5 * 100.0 - 3.0),
        broker_orders={},
    )
    assert resolved["status"] == "TRADING_HALTED"
    assert recon.blocks_new_orders
    assert recon.resolve("operator", "corrected expected cash")


def test_on_reconnect_reconciles_against_fresh_broker_snapshot() -> None:
    oms = OMS("reconnect")
    recon = ReconciliationEngine(oms)
    scheduler = SessionScheduler(HardRiskEngine(), recon)

    no_snapshot = scheduler.on_reconnect()
    assert no_snapshot["status"] == "TRADING_HALTED"

    matched = scheduler.on_reconnect(broker_snapshot({}, 1000.0))
    assert matched["status"] == "RECONNECT_OK"
    assert scheduler.should_trade()

    mismatched = scheduler.on_reconnect(broker_snapshot({"AAPL": 3.0}, 1000.0))
    assert mismatched["status"] == "TRADING_HALTED"
    assert recon.blocks_new_orders


def test_fill_triggered_reconciliation_runs_on_oms_callback() -> None:
    sink = QueuedIncidentSink()
    oms = OMS("wire")
    recon = ReconciliationEngine(oms, incident_sink=sink)
    scheduler = SessionScheduler(HardRiskEngine(), recon)
    oms.on_fill_reconcile = scheduler.on_fill_event

    order = make_order("o-wire", quantity=5)
    oms.submit_order(order)
    open_order_in_oms(oms, "o-wire")
    assert oms.fill_order("o-wire", 5, 100.0, execution_id="e-1")

    last = scheduler.last_fill_reconciliation
    assert last["status"] == "RECONCILIATION_RUN"
    assert last["reconciled"] is True
    assert "note" not in last
    assert last["orders_checked"] >= 1

    wire_order = oms.get_order("o-wire")
    assert wire_order is not None
    wire_order.filled_quantity = 99
    mismatched = scheduler.on_fill_event()
    assert mismatched["reconciled"] is False
    assert mismatched["operator_resolution_required"] is True
    assert len(sink.recorded()) == 1
    assert recon.blocks_new_orders


def test_periodic_and_session_end_hooks_reconcile() -> None:
    oms = OMS("hooks")
    recon = ReconciliationEngine(oms)
    scheduler = SessionScheduler(HardRiskEngine(), recon)

    periodic = scheduler.periodic()
    assert periodic["status"] == "RECONCILIATION_RUN"
    assert periodic["reconciled"] is True

    order = make_order("o-hooks", quantity=5)
    oms.submit_order(order)
    open_order_in_oms(oms, "o-hooks")
    end = scheduler.session_end(broker_orders={})
    assert end["status"] == "TRADING_HALTED"
    assert recon.blocks_new_orders


def test_oms_protective_order_invariant_and_risk_check() -> None:
    oms = OMS("protective", require_protective_orders=True)
    entry = make_order("o-entry", quantity=10)
    stop = make_order("o-stop", side=OrderSide.SELL, quantity=10, price=95.0, order_type=OrderType.STOP)
    oms.submit_order(entry)
    oms.submit_order(stop)
    open_order_in_oms(oms, "o-entry")
    open_order_in_oms(oms, "o-stop")

    assert not oms.fill_order("o-entry", 10, 100.0, execution_id="e-1")
    events = [event for event in oms.get_event_ledger() if event["event"] == "PROTECTIVE_ORDER_MISSING"]
    assert len(events) == 1
    assert events[0]["status"] == "OPERATOR_RESOLUTION_REQUIRED"
    assert not oms.link_protective_order("AAPL", "missing-stop")

    assert oms.link_protective_order("AAPL", "o-stop")
    assert oms.fill_order("o-entry", 10, 100.0, execution_id="e-1")
    assert oms.get_order_status("o-entry") == "FILLED"
    assert oms.check_protective_invariant(["AAPL"])[0]
    assert not oms.check_protective_invariant(["AAPL", "MSFT"])[0]

    engine = HardRiskEngine([RiskPolicyVersion(1, min_cash_reserve_pct=0)], oms=oms)
    oms.unlink_protective_order("AAPL")
    buy = make_order("o-buy", quantity=1, price=100.0)
    rejected, reason, _policy = engine.check_order(buy, {}, 1000.0)
    assert not rejected
    assert "Protective" in reason
    oms.link_protective_order("AAPL", "o-stop")
    approved_again, _reason_again, _policy_again = engine.check_order(buy, {}, 1000.0)
    assert approved_again

    coverage_ok, _message = engine.check_protective_order_coverage(
        {"AAPL": Position(Instrument("AAPL"), 10, 100.0, 1000.0, 0.0, 0.0)}, {"AAPL": "o-stop"}
    )
    assert coverage_ok
    coverage_bad, message = engine.check_protective_order_coverage(
        {"MSFT": Position(Instrument("MSFT"), 10, 100.0, 1000.0, 0.0, 0.0)}, {"AAPL": "o-stop"}
    )
    assert not coverage_bad
    assert "MSFT" in message


def test_risk_engine_with_reconciliation_blocks_orders() -> None:
    oms = OMS("blocked")
    recon = ReconciliationEngine(oms)
    engine = HardRiskEngine([RiskPolicyVersion(1, min_cash_reserve_pct=0)], reconciliation=recon)
    recon.reconcile_against_snapshot(broker_snapshot({"AAPL": 5.0}, 1000.0))
    approved, reason, _policy = engine.check_order(make_order("o-blocked"), {}, 1000.0)
    assert not approved
    assert "OPERATOR_RESOLUTION_REQUIRED" in reason


def test_oms_edge_paths_covered() -> None:
    oms = OMS("edges")
    order = make_order("o-edge", quantity=2)
    assert oms.submit_order(order, "idem-edge")
    open_order_in_oms(oms, "o-edge")
    assert oms.fill_order("o-edge", 2, 100.0, execution_id="e-full")

    filled_order = oms.get_order("o-edge")
    assert filled_order is not None
    oms.set_timeout("o-edge", datetime.now(UTC) - timedelta(seconds=1))
    assert oms.check_timeout("o-edge", datetime.now(UTC) + timedelta(seconds=2))
    oms.timeouts.pop("o-edge")
    assert not oms.check_timeout("o-edge", datetime.now(UTC))

    not_found_ok, not_found_reason = oms.replace_order("missing", make_order("o-x"))
    assert not not_found_ok
    assert "not found" in not_found_reason
    filled_replace_ok, filled_replace_reason = oms.replace_order("o-edge", make_order("o-y"))
    assert not filled_replace_ok
    assert "cannot replace" in filled_replace_reason

    assert oms.get_oca_group("o-edge") is not None
    assert oms.get_oca_group("missing-group") is None

    key = IdempotencyKey(order)
    assert key != "not-a-key"


def test_fill_rejected_directly_from_submitted_state() -> None:
    oms = OMS("direct-fill")
    order = make_order("o-direct", quantity=1)
    oms.submit_order(order)
    assert not oms.fill_order("o-direct", 1, 100.0, execution_id="e-direct")
    assert oms.get_order_status("o-direct") == "SUBMITTED"


def test_oca_group_cascade_cancels_members() -> None:
    oms = OMS("oca-cascade")
    first = make_order("o-g1", quantity=1)
    second = make_order("o-g2", quantity=1)
    oms.submit_order(first)
    oms.submit_order(second)
    open_order_in_oms(oms, "o-g1")
    open_order_in_oms(oms, "o-g2")
    group = OCAGroup("shared-group")
    group.add(first)
    group.add(second)
    oms.oca_groups.clear()
    oms.oca_groups["shared-group"] = group

    assert oms.cancel_order("o-g1")
    assert oms.get_order_status("o-g2") == "CANCELLED"


def test_replace_commits_without_group_when_group_missing() -> None:
    oms = OMS("replace-no-group")
    old = make_order("o-old", quantity=1)
    oms.submit_order(old)
    open_order_in_oms(oms, "o-old")
    assert oms.oca_groups["o-old"].orders.clear() is None
    ok, _reason = oms.replace_order("o-old", make_order("o-new"))
    assert ok
    assert oms.get_oca_group("o-new") is not None
    assert oms.get_order_status("o-new") == "SUBMITTED"


def test_protective_link_rejects_buy_side_order() -> None:
    oms = OMS("link-buy")
    buy = make_order("o-buy", quantity=1)
    oms.submit_order(buy)
    assert not oms.link_protective_order("AAPL", "o-buy")
    assert "AAPL" not in oms.protective_orders


def test_replay_covers_cancel_replacement_and_protective_events() -> None:
    oms = OMS("replay-full")
    order = make_order("o-r1", quantity=5)
    oms.submit_order(order, "idem-r1")
    open_order_in_oms(oms, "o-r1")
    oms.cancel_order("o-r1", reason="TEST")
    stop = make_order("o-stop", side=OrderSide.SELL, quantity=5, price=95.0, order_type=OrderType.STOP)
    oms.submit_order(stop)
    assert oms.link_protective_order("AAPL", "o-stop")
    ledger = [dict(event) for event in oms.get_event_ledger()]

    rebuilt = OMS("rebuilt-full")
    rebuilt.rebuild_from_ledger(ledger)
    assert rebuilt.get_order_status("o-r1") == "CANCELLED"
    assert rebuilt.protective_orders == {"AAPL": "o-stop"}

    gapped = OMS("gap-open")
    gapped.rebuild_from_ledger(
        [{"event": "ORDER_OPEN", "order_id": "ghost", "timestamp": datetime.now(UTC).isoformat()}]
    )
    assert len([event for event in gapped.get_event_ledger() if event["event"] == "REPLAY_GAP"]) == 1


def test_fake_broker_next_open_requires_open_price() -> None:
    oms = OMS("no-open")
    order = make_order("o-no-open", quantity=1)
    with pytest.raises(ValueError):
        FakeBroker(oms, "NEXT_OPEN").execute_order(order, type("BarStub", (), {"close": 100.0})())


def test_postgres_incident_sink_bridges_sync_and_async() -> None:
    from trading_platform.reconciliation import PostgresIncidentSink

    class FakeAsyncStore:
        def __init__(self) -> None:
            self.recorded: list[tuple[str, str, dict[str, Any], datetime]] = []

        async def record_incident(
            self, incident_id: str, severity: str, details: Mapping[str, Any], created_at: datetime
        ) -> None:
            self.recorded.append((incident_id, severity, dict(details), created_at))

    sink_store = FakeAsyncStore()
    sink = PostgresIncidentSink(sink_store)
    incident = {
        "incident_id": "incident-sync",
        "severity": "CRITICAL",
        "created_at": datetime.now(UTC),
        "details": {"status": "OPERATOR_RESOLUTION_REQUIRED"},
    }
    sink.record_incident(incident)
    assert len(sink_store.recorded) == 1
    assert sink_store.recorded[0][0] == "incident-sync"

    async def running_loop_scenario() -> None:
        sink.record_incident({**incident, "incident_id": "incident-loop"})
        await asyncio.sleep(0.01)

    asyncio.run(running_loop_scenario())
    recorded_ids = [record[0] for record in sink_store.recorded]
    assert "incident-loop" in recorded_ids


def test_fail_closed_chain_alert_failure_and_empty_resolution() -> None:
    from trading_platform.reconciliation import FailClosedChain

    def failing_alert(_message: str) -> None:
        raise RuntimeError("alert transport down")

    fresh_chain = FailClosedChain(alert_callback=failing_alert)
    assert not fresh_chain.resolve("operator", "nothing to resolve")
    assert fresh_chain.unresolved_incidents() == ()

    chain = FailClosedChain(alert_callback=failing_alert)
    incident = chain.trigger(["diff"], source="test")
    assert chain.blocks_new_orders
    assert incident.resolved_at is None
    assert chain.unresolved_incidents() == (incident,)
