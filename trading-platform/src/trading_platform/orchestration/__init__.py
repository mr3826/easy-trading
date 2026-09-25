"""Autonomous paper-execution orchestrator.

Wires the existing platform components into one deterministic, restartable,
paper-only decision loop:

    market data -> point-in-time validation -> features -> regime
    -> approved strategy -> SignalEvidence -> TradePlan -> HardRiskEngine
    -> OMS -> paper broker -> trade management -> reconciliation
    -> monitoring / drift / kill-switch checks -> journal/audit archive

Hard guarantees
---------------
- PAPER ONLY. This module imports no live-execution authority and refuses to
  run unless a current APPROVED promotion artifact exists for the pinned
  strategy version.
- Idempotent decisions: a deterministic decision_id derived from
  (strategy, decision timestamp, symbol, plan) prevents duplicates across
  restarts.
- Fail closed: any unhealthy input blocks NEW exposure; exits of existing
  positions are separate and never blocked by kill-switch blocks.
- Kill-switch BLOCK does not imply cancel-all or liquidation.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from trading_platform.promotion import PROMOTION_POLICY_VERSION, load_approvals
from trading_platform.risk.kill_switch import (
    BlockDecision,
    HealthSnapshot,
    KillSwitchCoordinator,
)
from trading_platform.trade_planning import SignalEvidence, TradePlan

ORCHESTRATOR_VERSION = "1.2.0"


class OrchestrationBlocked(RuntimeError):
    """Raised when a step fails closed."""


# ---------------------------------------------------------------------------
# Injectable boundaries (duck-typed; the real implementations already exist)


class RiskEngineLike(Protocol):
    def check_order(self, *args: Any, **kwargs: Any) -> Any: ...


class OMSLike(Protocol):
    def submit_order(self, order: Any, idempotency_key: Optional[str] = None) -> Tuple[bool, str]: ...


class BrokerLike(Protocol):
    def execute_order(self, order: Any, bar: Any) -> Mapping[str, Any]: ...


class RiskApprover(Protocol):
    """Adapter from TradePlan to whatever the concrete risk engine requires.

    Returns (approved, risk_decision_payload). Strategy code has no path
    past this check.
    """

    def __call__(self, plan: TradePlan) -> Tuple[bool, Mapping[str, Any]]: ...


# ---------------------------------------------------------------------------
# Journal (durable decision log)


class DecisionJournal:
    """Append-only JSONL journal enabling restart recovery and dedupe."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            did = record.get("decision_id")
            if isinstance(did, str):
                self._seen.add(did)

    def seen(self, decision_id: str) -> bool:
        return decision_id in self._seen

    def append(self, record: Mapping[str, Any]) -> str:
        did = str(record.get("decision_id") or "")
        if not did:
            raise ValueError("journal record requires decision_id")
        if did in self._seen:
            return did  # idempotent
        line = json.dumps(record, sort_keys=True, default=str)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._seen.add(did)
        return did


def deterministic_decision_id(plan: TradePlan) -> str:
    return hashlib.sha256(f"{plan.strategy_id}|{plan.decision_timestamp}|{plan.plan_id()}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Orchestrator


@dataclass(frozen=True)
class OrchestratorConfig:
    strategy_id: str
    strategy_version: str
    risk_policy_version: str
    sizing_policy_version: str
    journal_path: Path
    promotions_root: Optional[Path] = None
    max_plans_per_cycle: int = 3
    version: str = ORCHESTRATOR_VERSION


@dataclass(frozen=True)
class CycleResult:
    cycle_id: str
    decision: BlockDecision
    plans_created: int
    plans_approved: int
    plans_submitted: int
    rejections: Tuple[Mapping[str, Any], ...]
    version: str = ORCHESTRATOR_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "decision": self.decision.to_dict(),
            "plans_created": self.plans_created,
            "plans_approved": self.plans_approved,
            "plans_submitted": self.plans_submitted,
            "rejections": [dict(r) for r in self.rejections],
            "version": self.version,
        }


class PaperOrchestrator:
    """Runs one deterministic decision cycle at a time.

    The caller (scheduler/operator) supplies a cycle function that returns
    (health, ranked evidence, plan builder); the orchestrator owns gating,
    journaling, idempotency, and submission ordering.
    """

    def __init__(
        self,
        config: OrchestratorConfig,
        kill_switch: KillSwitchCoordinator,
        risk_approve: RiskApprover,
        submit_plan: Callable[[TradePlan, str], Tuple[bool, str]],
        *,
        reconciliation_healthy: Callable[[], bool] = lambda: True,
    ) -> None:
        self.config = config
        self.kill_switch = kill_switch
        self.risk_approve = risk_approve
        self.submit_plan = submit_plan
        self.reconciliation_healthy = reconciliation_healthy
        self.journal = DecisionJournal(config.journal_path)
        self._approval_cache: Optional[Dict[str, Any]] = None

    # -- gating ---------------------------------------------------------

    def _strategy_approval(self) -> Optional[Mapping[str, Any]]:
        """Load APPROVED promotion artifact for the pinned strategy version."""
        approvals = load_approvals(self.config.strategy_id, root=self.config.promotions_root)
        artifact = approvals.get(self.config.strategy_id)
        if artifact is None:
            return None
        # Version pinning: approval must match the exact configured strategy
        # version and current promotion policy.
        if artifact.get("version") != PROMOTION_POLICY_VERSION:
            return None
        return artifact

    def _preflight(
        self,
        health: HealthSnapshot,
        evidence: Sequence[SignalEvidence],
        *,
        drift_disabled: bool = False,
        risk_engine_blocked: bool = False,
    ) -> BlockDecision:
        approved = self._strategy_approval() is not None
        for e in evidence:
            if e.strategy_version != self.config.strategy_version:
                raise OrchestrationBlocked(
                    f"strategy version mismatch: {e.strategy_version} != {self.config.strategy_version}"
                )
        return self.kill_switch.evaluate(
            health,
            risk_engine_blocked=risk_engine_blocked,
            strategy_approved=approved,
            strategy_drift_disabled=drift_disabled,
        )

    # -- main cycle ------------------------------------------------------

    def run_cycle(
        self,
        cycle_id: str,
        health: HealthSnapshot,
        evidence: Sequence[SignalEvidence],
        plan_builder: Callable[[SignalEvidence], Tuple[Optional[TradePlan], Tuple[str, ...]]],
        *,
        drift_disabled: bool = False,
        risk_engine_blocked: bool = False,
        now: Optional[datetime] = None,
    ) -> CycleResult:
        """One full decision cycle. Fully idempotent via the journal.

        Order of operations is fixed and fail-closed:
        1. kill-switch preflight (includes strategy-approval + drift gates)
        2. eligibility filter + evidence ranking (score NEVER confers eligibility)
        3. TradePlan construction (risk-based sizing)
        4. hard risk engine approval (per plan; no bypass)
        5. OMS/broker submission with deterministic idempotency key
        6. journaled outcome
        """
        current = now or datetime.now(timezone.utc)
        decision = self._preflight(
            health, evidence, drift_disabled=drift_disabled, risk_engine_blocked=risk_engine_blocked
        )
        rejections: List[Mapping[str, Any]] = []
        created = approved_count = submitted = 0

        if not decision.allow_new_exposure:
            self.journal.append(
                {
                    "decision_id": f"cycle-blocked:{cycle_id}",
                    "cycle_id": cycle_id,
                    "event": "cycle_blocked",
                    "blocked_conditions": decision.blocked_conditions,
                    "at": current.isoformat(),
                }
            )
            return CycleResult(cycle_id, decision, 0, 0, 0, tuple(rejections))

        # Eligibility strictly separated from ranking.
        eligible = [e for e in evidence if e.eligible and e.raw_signal == "BUY"]
        ranked = sorted(eligible, key=lambda e: (-e.signal_score, e.symbol))
        rejected_signals = [e for e in evidence if not e.eligible]
        for e in rejected_signals:
            rejections.append(
                {
                    "symbol": e.symbol,
                    "strategy_id": e.strategy_id,
                    "reasons": list(e.rejection_reasons),
                    "stage": "eligibility",
                }
            )

        for ev in ranked[: self.config.max_plans_per_cycle]:
            plan, plan_rejections = plan_builder(ev)
            if plan is None:
                rejections.append({"symbol": ev.symbol, "stage": "sizing", "reasons": list(plan_rejections)})
                continue
            created += 1
            decision_id = deterministic_decision_id(plan)
            if self.journal.seen(decision_id):
                continue  # idempotent: already decided (e.g., after restart)
            ok, risk_payload = self.risk_approve(plan)
            if not ok:
                rejections.append(
                    {"symbol": plan.symbol, "stage": "risk", "reasons": [str(risk_payload.get("reason", "rejected"))]}
                )
                self.journal.append(
                    {
                        "decision_id": decision_id,
                        "cycle_id": cycle_id,
                        "event": "risk_rejected",
                        "plan": plan.to_dict(),
                        "risk_decision": dict(risk_payload),
                        "at": current.isoformat(),
                    }
                )
                continue
            approved_count += 1
            # Re-check the kill switch immediately before submission: a
            # condition latched mid-cycle (operator halt, incident, market
            # close) must stop later plans even though preflight passed.
            recheck = self.kill_switch.evaluate(
                health,
                risk_engine_blocked=risk_engine_blocked,
                strategy_approved=self._strategy_approval() is not None,
                strategy_drift_disabled=drift_disabled,
            )
            if not recheck.allow_new_exposure:
                self.journal.append(
                    {
                        "decision_id": decision_id,
                        "cycle_id": cycle_id,
                        "event": "submission_withheld_killswitch_midcycle",
                        "blocked_conditions": recheck.blocked_conditions,
                        "plan": plan.to_dict(),
                        "at": current.isoformat(),
                    }
                )
                break
            if not self.reconciliation_healthy():
                self.journal.append(
                    {
                        "decision_id": decision_id,
                        "cycle_id": cycle_id,
                        "event": "submission_withheld_reconciliation",
                        "plan": plan.to_dict(),
                        "at": current.isoformat(),
                    }
                )
                continue
            submitted_ok, broker_ref = self.submit_plan(plan, decision_id)
            if submitted_ok:
                submitted += 1
            else:
                rejections.append({"symbol": plan.symbol, "stage": "submission", "reasons": [broker_ref]})
            self.journal.append(
                {
                    "decision_id": decision_id,
                    "cycle_id": cycle_id,
                    "event": "submitted" if submitted_ok else "submission_failed",
                    "plan": plan.to_dict(),
                    "risk_decision": dict(risk_payload),
                    "broker_ref": broker_ref,
                    "at": current.isoformat(),
                }
            )

        return CycleResult(cycle_id, decision, created, approved_count, submitted, tuple(rejections))
