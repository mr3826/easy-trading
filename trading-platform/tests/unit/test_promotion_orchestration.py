"""Promotion gate, orchestrator gating/idempotency, and drift monitor tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from trading_platform.monitoring import (
    STATE_DEGRADED,
    STATE_DISABLED,
    STATE_HEALTHY,
    DriftExpectations,
    DriftMonitor,
    DriftPolicy,
)
from trading_platform.orchestration import (
    DecisionJournal,
    OrchestrationBlocked,
    OrchestratorConfig,
    PaperOrchestrator,
    deterministic_decision_id,
)
from trading_platform.promotion import (
    STATUS_APPROVED,
    STATUS_REJECTED,
    PromotionPolicy,
    evaluate_promotion,
    load_approvals,
    persist_decision,
)
from trading_platform.risk.kill_switch import (
    HealthSnapshot,
    KillSwitchCoordinator,
    KillSwitchPolicy,
)
from trading_platform.trade_planning import SignalEvidence


def _strong_report() -> dict:
    return {
        "config_id": "trend_rs_a",
        "status": "VALIDATED",
        "metrics": {
            "trade_count": 120.0,
            "expectancy": 45.0,
            "sharpe": 1.2,
            "max_drawdown": -0.12,
            "profit_factor": 1.8,
        },
        "significance": {
            "psr": {"psr": 0.99},
            "dsr": {"dsr": 0.95, "n_trials": 8.0},
        },
        "pbo": {"pbo": 0.2},
        "walk_forward_consistency": {"positive_fold_fraction": 0.75},
        "cost_stress": {"survives_2x": True},
        "concentration": {
            "trades": {"top1_share": 0.2, "flagged": False},
            "symbols": {"top1_share": 0.3, "flagged": False},
        },
        "benchmark": {"excess_return": 0.05},
    }


def _stable_family() -> dict:
    return {"parameter_stability_sharpe": {"stable": True, "reason": ""}}


def test_promotion_approval_requires_all_evidence() -> None:
    decision = evaluate_promotion("trend_rs", _strong_report(), family_diagnostics=_stable_family())
    assert decision.status == STATUS_APPROVED
    assert decision.is_approved()


def test_promotion_rejects_missing_evidence() -> None:
    thin = {
        "config_id": "x",
        "status": "VALIDATED",
        "metrics": {
            "trade_count": 100.0,
            "expectancy": 10.0,
            "sharpe": 1.0,
            "max_drawdown": -0.1,
            "profit_factor": 2.0,
        },
        "significance": {"psr": {"psr": 0.99}, "dsr": {"dsr": 0.99}},
    }
    decision = evaluate_promotion("trend_rs", thin, family_diagnostics=_stable_family())
    assert decision.status == STATUS_REJECTED
    reasons = " | ".join(decision.reasons)
    assert "PBO evidence missing" in reasons
    assert "walk-forward fold evidence missing" in reasons
    assert "cost-stress evidence missing" in reasons


def test_promotion_rejects_single_metric_success() -> None:
    report = _strong_report()
    report["significance"]["dsr"] = {"dsr": 0.10, "n_trials": 500.0}
    report["metrics"]["sharpe"] = 3.0
    report["métricas_extra"] = None
    decision = evaluate_promotion("trend_rs", report, family_diagnostics=_stable_family())
    assert decision.status == STATUS_REJECTED
    assert any("DSR" in r for r in decision.reasons)


def test_promotion_rejects_concentration() -> None:
    report = _strong_report()
    report["concentration"]["trades"] = {"top1_share": 0.8, "flagged": True, "reason": "top item contributes 80%"}
    decision = evaluate_promotion("trend_rs", report, family_diagnostics=_stable_family())
    assert decision.status == STATUS_REJECTED
    assert any("concentration" in r for r in decision.reasons)


def test_unvalidated_report_rejected_fail_closed() -> None:
    decision = evaluate_promotion("trend_rs", {"status": "INSUFFICIENT_DATA"})
    assert decision.status == STATUS_REJECTED


def test_persisted_approval_and_policy_pinning(tmp_path: Path) -> None:
    policy = PromotionPolicy()
    decision = evaluate_promotion("trend_rs", _strong_report(), policy, family_diagnostics=_stable_family())
    path = persist_decision(decision, root=tmp_path)
    assert path.exists()
    approvals = load_approvals("trend_rs", root=tmp_path)
    assert "trend_rs" in approvals
    # Tampered/stale-policy artifact must not authorize.
    payload = json.loads(path.read_text())
    payload["policy_hash"] = "0" * 64
    path.write_text(json.dumps(payload))
    approvals = load_approvals("trend_rs", root=tmp_path)
    assert "trend_rs" not in approvals


# ---------------------------------------------------------------------------
# Orchestrator


def _healthy(now: datetime) -> HealthSnapshot:
    return HealthSnapshot(
        last_bar_timestamp=now - timedelta(hours=1),
        expected_bar_complete=True,
        clock_skew_seconds=0.0,
        database_available=True,
        broker_available=True,
        reconciliation_mismatch=False,
        unknown_broker_positions=False,
        duplicate_order_uncertainty=False,
        daily_pnl=0.0,
        portfolio_drawdown=0.0,
        gross_exposure_pct=0.2,
        broker_reject_count=0,
        heartbeat_fresh=True,
        market_open=True,
    )


def _evidence(
    symbol: str = "AAPL", version: str = "1.0.0", score: float = 1.0, eligible: bool = True
) -> SignalEvidence:
    return SignalEvidence(
        symbol=symbol,
        decision_timestamp="2026-01-05T14:30:00+00:00",
        strategy_id="trend_rs",
        strategy_version=version,
        market_regime={"trend": "bullish"},
        raw_signal="BUY",
        trend_confirmation=True,
        relative_strength=0.05,
        volatility_state="normal",
        liquidity_state="acceptable",
        volume_confirmation=True,
        feature_snapshot_hash="ab" * 32,
        expected_entry=100.0,
        initial_stop=92.0,
        planned_exit="atr_trailing",
        estimated_transaction_cost=3.0,
        signal_score=score,
        rejection_reasons=() if eligible else ("no breakout",),
    )


def _orchestrator(tmp_path: Path, submissions: list):
    config = OrchestratorConfig(
        strategy_id="trend_rs",
        strategy_version="1.0.0",
        risk_policy_version="risk-1.0.0",
        sizing_policy_version="sizing-1.0.0",
        journal_path=tmp_path / "journal.jsonl",
        promotions_root=tmp_path / "promotions",
    )
    return PaperOrchestrator(
        config,
        KillSwitchCoordinator(),
        risk_approve=lambda plan: (True, {"approved": True}),
        submit_plan=lambda plan, did: submissions.append((plan.plan_id(), did)) or (True, "PAPER-1"),
    )


def _plan_builder(ev: SignalEvidence):
    from trading_platform.trade_planning import PortfolioState, SizingPolicy, build_trade_plan

    return build_trade_plan(
        ev,
        PortfolioState(equity=100_000, settled_cash=100_000, gross_exposure=0.0, open_positions=0),
        SizingPolicy(),
    )


def _approval(tmp_path: Path) -> None:
    decision = evaluate_promotion("trend_rs", _strong_report(), family_diagnostics=_stable_family())
    persist_decision(decision, root=tmp_path / "promotions")


def test_orchestrator_blocks_unapproved_strategy(tmp_path: Path) -> None:
    submissions: list = []
    orch = _orchestrator(tmp_path, submissions)
    now = datetime.now(timezone.utc)
    result = orch.run_cycle("cycle-1", _healthy(now), [_evidence()], _plan_builder, now=now)
    assert not result.decision.allow_new_exposure
    assert result.plans_submitted == 0
    assert submissions == []
    assert "strategy_unapproved" in result.decision.blocked_conditions


def test_orchestrator_submits_approved_signal(tmp_path: Path) -> None:
    _approval(tmp_path)
    submissions: list = []
    orch = _orchestrator(tmp_path, submissions)
    now = datetime.now(timezone.utc)
    result = orch.run_cycle("cycle-1", _healthy(now), [_evidence()], _plan_builder, now=now)
    assert result.decision.allow_new_exposure
    assert result.plans_submitted == 1


def test_orchestrator_idempotent_across_replay(tmp_path: Path) -> None:
    _approval(tmp_path)
    submissions: list = []
    orch = _orchestrator(tmp_path, submissions)
    now = datetime.now(timezone.utc)
    orch.run_cycle("cycle-1", _healthy(now), [_evidence()], _plan_builder, now=now)
    # Replay the same cycle (restart): no duplicate submissions.
    orch2 = _orchestrator(tmp_path, submissions)
    orch2.run_cycle("cycle-1", _healthy(now), [_evidence()], _plan_builder, now=now)
    assert len(submissions) == 1


def test_orchestrator_version_mismatch_blocks() -> None:
    with pytest.raises(OrchestrationBlocked):
        config = OrchestratorConfig(
            strategy_id="trend_rs",
            strategy_version="2.0.0",
            risk_policy_version="r",
            sizing_policy_version="s",
            journal_path=Path("unused"),
        )
        orch = PaperOrchestrator(
            config, KillSwitchCoordinator(), risk_approve=lambda p: (True, {}), submit_plan=lambda p, d: (True, "x")
        )
        orch.run_cycle("c", _healthy(datetime.now(timezone.utc)), [_evidence(version="1.0.0")], _plan_builder)


def test_killswitch_midcycle_blocks_second_submission(tmp_path: Path) -> None:
    _approval(tmp_path)
    submissions: list = []
    orch = _orchestrator(tmp_path, submissions)
    now = datetime.now(timezone.utc)

    def submit(plan, did):
        submissions.append((plan.symbol, did))
        if len(submissions) == 1:
            orch.kill_switch.latch("incident_recon", "mid-cycle reconciliation failure")
        return True, "PAPER-1"

    orch.submit_plan = submit
    ev1 = _evidence(symbol="AAA", score=2.0)
    ev2 = _evidence(symbol="BBB", score=1.0)
    result = orch.run_cycle("cycle-1", _healthy(now), [ev1, ev2], _plan_builder, now=now)
    # First submission happened; the latch must stop the second even though
    # preflight passed before the cycle began.
    assert len(submissions) == 1
    assert result.plans_submitted == 1
    journal_text = orch.journal.path.read_text()
    assert "submission_withheld_killswitch_midcycle" in journal_text


def test_orchestrator_stops_when_approval_vanishes_midcycle(tmp_path: Path) -> None:
    _approval(tmp_path)
    submissions: list = []
    orch = _orchestrator(tmp_path, submissions)
    now = datetime.now(timezone.utc)
    approvals_dir = tmp_path / "promotions" / "trend_rs"

    def submit(plan, did):
        submissions.append(plan.symbol)
        for f in approvals_dir.glob("*.json"):
            f.unlink()
        return True, "PAPER-1"

    orch.submit_plan = submit
    ev1 = _evidence(symbol="AAA", score=2.0)
    ev2 = _evidence(symbol="BBB", score=1.0)
    result = orch.run_cycle("cycle-1", _healthy(now), [ev1, ev2], _plan_builder, now=now)
    assert len(submissions) == 1
    assert result.plans_submitted == 1
    assert "submission_withheld_killswitch_midcycle" in orch.journal.path.read_text()


def test_drift_disabled_blocks_new_positions(tmp_path: Path) -> None:
    _approval(tmp_path)
    submissions: list = []
    orch = _orchestrator(tmp_path, submissions)
    now = datetime.now(timezone.utc)
    result = orch.run_cycle("cycle-1", _healthy(now), [_evidence()], _plan_builder, drift_disabled=True, now=now)
    assert not result.decision.allow_new_exposure
    assert "strategy_drift_disabled" in result.decision.blocked_conditions


def test_unhealthy_inputs_fail_closed(tmp_path: Path) -> None:
    _approval(tmp_path)
    submissions: list = []
    orch = _orchestrator(tmp_path, submissions)
    now = datetime.now(timezone.utc)
    unhealthy = HealthSnapshot()  # everything unknown
    result = orch.run_cycle("cycle-2", unhealthy, [_evidence()], _plan_builder, now=now)
    assert not result.decision.allow_new_exposure
    assert set(result.decision.blocked_conditions) >= {
        "stale_market_data",
        "missing_completed_bar",
        "clock_uncertainty",
        "database_unavailable",
        "broker_unavailable",
        "reconciliation_mismatch",
    }


# ---------------------------------------------------------------------------
# Kill switch semantics


def test_kill_switch_distinct_block_vs_liquidation() -> None:
    coord = KillSwitchCoordinator(KillSwitchPolicy(require_market_open=False))
    decision = coord.evaluate(
        HealthSnapshot(
            last_bar_timestamp=datetime.now(timezone.utc),
            expected_bar_complete=True,
            clock_skew_seconds=0.0,
            database_available=True,
            broker_available=True,
            reconciliation_mismatch=False,
            unknown_broker_positions=False,
            duplicate_order_uncertainty=False,
            daily_pnl=-5000.0,
            portfolio_drawdown=0.0,
            gross_exposure_pct=0.5,
            broker_reject_count=0,
            heartbeat_fresh=True,
            market_open=True,
        )
    )
    assert not decision.allow_new_exposure
    assert "daily_loss_breach" in decision.blocked_conditions
    # A block never implies liquidation: the decision only covers NEW exposure.
    assert decision.allow_new_exposure is False


def test_kill_switch_latch_and_release() -> None:
    coord = KillSwitchCoordinator(KillSwitchPolicy(require_heartbeat=False, require_market_open=False))
    coord.latch("operator_halt", "manual halt")
    decision = coord.evaluate(
        HealthSnapshot(
            last_bar_timestamp=datetime.now(timezone.utc),
            expected_bar_complete=True,
            clock_skew_seconds=0.0,
            database_available=True,
            broker_available=True,
            reconciliation_mismatch=False,
            unknown_broker_positions=False,
            duplicate_order_uncertainty=False,
            daily_pnl=0.0,
            portfolio_drawdown=0.0,
            gross_exposure_pct=0.1,
            broker_reject_count=0,
        )
    )
    assert "latched:operator_halt" in decision.blocked_conditions
    coord.release("operator_halt")
    assert coord.latched() == ()


# ---------------------------------------------------------------------------
# Drift monitor


def _expectations() -> DriftExpectations:
    return DriftExpectations(
        expectancy=50.0,
        win_rate=0.55,
        sharpe=1.2,
        profit_factor=1.8,
        max_drawdown=-0.10,
        avg_trades_per_month=8.0,
    )


def test_drift_healthy_then_degraded() -> None:
    mon = DriftMonitor(DriftPolicy(min_forward_trades=5))
    mon.register("trend_rs", _expectations())
    for pnl in [60, 70, 40, 55, 65]:
        mon.record_trade("trend_rs", float(pnl))
    assert mon.status("trend_rs").state == STATE_HEALTHY
    for pnl in [-60, -70, -40, -55, -65]:
        mon.record_trade("trend_rs", float(pnl))
    status = mon.status("trend_rs")
    assert status.state == STATE_DEGRADED
    assert not status.allows_new_positions()


def test_drift_unregistered_is_disabled() -> None:
    mon = DriftMonitor()
    assert mon.status("ghost").state == STATE_DISABLED


def test_drift_manual_disable() -> None:
    mon = DriftMonitor()
    mon.register("trend_rs", _expectations())
    mon.disable("trend_rs", "operator disable")
    assert mon.status("trend_rs").state == STATE_DISABLED


def test_journal_roundtrip(tmp_path: Path) -> None:
    journal = DecisionJournal(tmp_path / "j.jsonl")
    journal.append({"decision_id": "a", "event": "submitted"})
    journal.append({"decision_id": "a", "event": "submitted"})  # idempotent
    assert journal.seen("a")
    journal2 = DecisionJournal(tmp_path / "j.jsonl")
    assert journal2.seen("a")
    assert not journal2.seen("b")


def test_deterministic_decision_id(tmp_path: Path) -> None:
    from trading_platform.trade_planning import PortfolioState, SizingPolicy, build_trade_plan

    plan, _ = build_trade_plan(_evidence(), PortfolioState(100_000, 100_000, 0.0, 0), SizingPolicy())
    assert plan is not None
    assert deterministic_decision_id(plan) == deterministic_decision_id(plan)
    plan2, _ = build_trade_plan(_evidence(symbol="MSFT"), PortfolioState(100_000, 100_000, 0.0, 0), SizingPolicy())
    assert deterministic_decision_id(plan) != deterministic_decision_id(plan2)
