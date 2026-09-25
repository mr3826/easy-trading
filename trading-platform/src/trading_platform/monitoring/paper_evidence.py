"""Durable paper-evidence tracking (Phase 10 preparation).

Records forward paper/sessions evidence as an append-only, schema-validated
event stream so the repository can ANSWER automatically what elapsed
forward evidence requires. It never fabricates outcomes: absence of events
means absence of evidence.

Answers (section 10 requirements):

- how many valid paper sessions / executed signals
- regime coverage
- any hard safety violation, stale-data trade, unresolved reconciliation,
  duplicate submission, unexpected sizing deviation
- realized vs modeled slippage/turnover/cost
- broker rejects, partial fills, restart/reconnect reconciliation results
- simulator/shadow/paper divergence counts
- whether the predeclared minimum sample is achieved

NOTE: minimum-sample achievement here is an ENGINEERING query. Phase 10
passage additionally requires the events to have actually happened in real
forward time — REQUIRES_FORWARD_EVIDENCE.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Set

PAPER_EVIDENCE_VERSION = "1.0.0"

EVENT_SESSION = "SESSION"
EVENT_SIGNAL = "SIGNAL_EXECUTED"
EVENT_FILL = "FILL"
EVENT_RISK_VIOLATION = "RISK_VIOLATION"
EVENT_STALE_DATA_TRADE = "STALE_DATA_TRADE"
EVENT_DUPLICATE_SUBMISSION = "DUPLICATE_SUBMISSION"
EVENT_UNRESOLVED_RECONCILIATION = "UNRESOLVED_RECONCILIATION"
EVENT_RECONNECT = "RECONNECT_RECONCILIATION"
EVENT_RESTART = "RESTART_RECONCILIATION"
EVENT_BROKER_REJECT = "BROKER_REJECT"
EVENT_PARTIAL_FILL = "PARTIAL_FILL"
EVENT_DIVERGENCE = "MODE_DIVERGENCE"

ALL_EVENT_TYPES = {
    EVENT_SESSION,
    EVENT_SIGNAL,
    EVENT_FILL,
    EVENT_RISK_VIOLATION,
    EVENT_STALE_DATA_TRADE,
    EVENT_DUPLICATE_SUBMISSION,
    EVENT_UNRESOLVED_RECONCILIATION,
    EVENT_RECONNECT,
    EVENT_RESTART,
    EVENT_BROKER_REJECT,
    EVENT_PARTIAL_FILL,
    EVENT_DIVERGENCE,
}

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class EvidenceRejected(ValueError):
    """A malformed or unknown evidence record; never stored."""


@dataclass(frozen=True)
class EvidencePolicy:
    """Predeclared thresholds for the forward-evidence sample. POLICY CHOICES."""

    required_trading_days: int = 60
    min_executed_signals: int = 20
    min_regime_coverage: int = 2
    max_avg_slippage_error_pct: float = 0.002  # realized vs modeled mean abs
    max_sizing_deviation_pct: float = 0.10
    version: str = PAPER_EVIDENCE_VERSION


_REQUIRED_FIELDS: Dict[str, Set[str]] = {
    EVENT_SESSION: {"trading_date", "outcome"},
    EVENT_SIGNAL: {"symbol", "regime", "strategy_id", "strategy_version"},
    EVENT_FILL: {
        "symbol",
        "quantity",
        "modeled_cost",
        "realized_cost",
        "modeled_quantity",
    },
    EVENT_RISK_VIOLATION: {"reason"},
    EVENT_STALE_DATA_TRADE: {"reason", "symbol"},
    EVENT_DUPLICATE_SUBMISSION: {"order_id"},
    EVENT_UNRESOLVED_RECONCILIATION: {"incident_id"},
    EVENT_RECONNECT: {"outcome"},
    EVENT_RESTART: {"outcome"},
    EVENT_BROKER_REJECT: {"order_id", "reason"},
    EVENT_PARTIAL_FILL: {"order_id", "filled", "requested"},
    EVENT_DIVERGENCE: {"source_a", "source_b", "detail"},
}

_NUMERIC_FIELDS = {"quantity", "modeled_cost", "realized_cost", "modeled_quantity", "filled", "requested"}


def _validate(event_type: str, fields: Mapping[str, Any]) -> None:
    if event_type not in ALL_EVENT_TYPES:
        raise EvidenceRejected(f"unknown event type {event_type!r}")
    missing = _REQUIRED_FIELDS[event_type] - set(fields)
    if missing:
        raise EvidenceRejected(f"missing fields for {event_type}: {sorted(missing)}")
    if event_type == EVENT_SESSION and not _DATE.match(str(fields["trading_date"])):
        raise EvidenceRejected("SESSION trading_date must be YYYY-MM-DD")
    if event_type == EVENT_SIGNAL and not str(fields["strategy_version"]):
        raise EvidenceRejected("strategy_version must be non-empty")
    for key in _NUMERIC_FIELDS & set(fields):
        value = fields[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise EvidenceRejected(f"{key} must be a non-negative number")
    if event_type == EVENT_FILL:
        if fields["quantity"] < 1:
            raise EvidenceRejected("FILL quantity must be >= 1")
        if fields["modeled_quantity"] < 1:
            raise EvidenceRejected("modeled_quantity must be >= 1")


@dataclass
class PaperEvidenceTracker:
    """Append-only JSONL evidence store with automatic aggregate queries."""

    path: Path
    policy: EvidencePolicy = field(default_factory=EvidencePolicy)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _events(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out: List[Dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue  # corrupt line is not evidence; ignore but never trust
            if isinstance(record, dict):
                out.append(record)
        return out

    def record(self, event_type: str, event_id: str, *, as_of: str, **fields: Any) -> bool:
        """Persist one evidence event. Returns False on duplicate event_id."""
        if not event_id:
            raise EvidenceRejected("event_id required")
        _validate(event_type, fields)
        seen = {str(e.get("event_id")) for e in self._events()}
        if event_id in seen:
            return False
        record = {
            "event_id": event_id,
            "event_type": event_type,
            "as_of": as_of,
            "version": PAPER_EVIDENCE_VERSION,
            "policy_version": self.policy.version,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return True

    # -- aggregate queries -------------------------------------------------

    def report(self) -> Dict[str, Any]:
        events = self._events()
        sessions = [e for e in events if e.get("event_type") == EVENT_SESSION]
        trading_days = sorted({str(e["trading_date"]) for e in sessions})
        signals = [e for e in events if e.get("event_type") == EVENT_SIGNAL]
        fills = [e for e in events if e.get("event_type") == EVENT_FILL]
        regimes = sorted({str(s.get("regime")) for s in signals})

        def _count(t: str) -> int:
            return sum(1 for e in events if e.get("event_type") == t)

        unresolved = [e for e in events if e.get("event_type") == EVENT_UNRESOLVED_RECONCILIATION]
        reconnect_fail = [e for e in events if e.get("event_type") == EVENT_RECONNECT and e.get("outcome") != "CLEAN"]
        restart_fail = [e for e in events if e.get("event_type") == EVENT_RESTART and e.get("outcome") != "CLEAN"]

        slippage_errors = [
            abs(float(f["realized_cost"]) - float(f["modeled_cost"])) / max(float(f["realized_cost"]), 1e-9)
            for f in fills
        ]
        sizing_deviations = [
            {"symbol": f.get("symbol"), "modeled": f["modeled_quantity"], "actual": f["quantity"]}
            for f in fills
            if f["modeled_quantity"] > 0
            and abs(f["quantity"] - f["modeled_quantity"]) / f["modeled_quantity"]
            > self.policy.max_sizing_deviation_pct
        ]

        hard_violations = {
            "risk_violations": _count(EVENT_RISK_VIOLATION),
            "stale_data_trades": _count(EVENT_STALE_DATA_TRADE),
            "duplicate_submissions": _count(EVENT_DUPLICATE_SUBMISSION),
            "unresolved_reconciliations": len(unresolved),
            "reconnect_failures": len(reconnect_fail),
            "restart_failures": len(restart_fail),
        }
        hard_safety_clean = all(v == 0 for v in hard_violations.values())

        p = self.policy
        avg_slip_error = float(sum(slippage_errors) / len(slippage_errors)) if slippage_errors else 0.0
        minimum_sample_achieved = (
            len(trading_days) >= p.required_trading_days
            and len(signals) >= p.min_executed_signals
            and len(regimes) >= p.min_regime_coverage
            and hard_safety_clean
            and (avg_slip_error <= p.max_avg_slippage_error_pct if fills else False)
        )

        return {
            "valid_sessions": len(sessions),
            "trading_days": len(trading_days),
            "executed_signals": len(signals),
            "fills": len(fills),
            "regime_coverage": regimes,
            "broker_rejects": _count(EVENT_BROKER_REJECT),
            "partial_fills": _count(EVENT_PARTIAL_FILL),
            "mode_divergences": _count(EVENT_DIVERGENCE),
            "hard_violations": hard_violations,
            "hard_safety_clean": hard_safety_clean,
            "avg_slippage_error": avg_slip_error,
            "sizing_deviations": sizing_deviations,
            "minimum_sample_achieved": bool(minimum_sample_achieved),
            "policy": {
                "required_trading_days": p.required_trading_days,
                "min_executed_signals": p.min_executed_signals,
                "min_regime_coverage": p.min_regime_coverage,
            },
            "status": "REQUIRES_FORWARD_EVIDENCE" if not minimum_sample_achieved else "SAMPLE_MET",
            "version": PAPER_EVIDENCE_VERSION,
        }
