"""Strategy promotion gate.

A strategy becomes paper-execution eligible only when a configurable,
versioned evidence policy is satisfied across MULTIPLE independent forms of
evidence. Thresholds below are conservative research POLICY CHOICES — not
mathematical truths — and are pinned by ``POLICY_VERSION``.

Outcomes: APPROVED / REJECTED / RESEARCH_ONLY, always with explicit reasons.
Artifacts are persisted to versioned JSON so the paper orchestrator can
verify approval before any submission.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

PROMOTION_POLICY_VERSION = "1.0.0"

STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_RESEARCH_ONLY = "RESEARCH_ONLY"


@dataclass(frozen=True)
class PromotionPolicy:
    """Evidence thresholds for strategy promotion. POLICY CHOICES.

    Rationale of defaults: modest but positive expectancy with deflated
    significance, bounded drawdown, enough trades to be statistically
    meaningful, robustness to 2x costs, and no extreme concentration.
    """

    min_trade_count: int = 30
    min_expectancy: float = 0.0  # net expectancy per trade > 0
    min_sharpe: float = 0.0
    max_drawdown_floor: float = -0.35  # max_dd must be > -35%
    min_profit_factor: float = 1.0
    min_psr: float = 0.95  # P(true Sharpe > 0) >= 95%
    min_dsr: float = 0.90  # significance after deflation for N trials
    max_pbo: float = 0.5  # PBO below coin-flip
    min_positive_fold_fraction: float = 0.5
    cost_stress_must_survive_2x: bool = True
    allow_top1_concentration: float = 0.5
    require_parameter_stability: bool = True
    min_benchmark_excess: float = 0.0  # must beat buy-and-hold excess of 0
    version: str = PROMOTION_POLICY_VERSION

    def policy_hash(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PromotionDecision:
    status: str
    strategy_id: str
    config_id: str
    reasons: Tuple[str, ...]
    policy_hash: str
    report_hash: str
    decided_at: str
    version: str = PROMOTION_POLICY_VERSION

    def is_approved(self) -> bool:
        return self.status == STATUS_APPROVED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "strategy_id": self.strategy_id,
            "config_id": self.config_id,
            "reasons": list(self.reasons),
            "policy_hash": self.policy_hash,
            "report_hash": self.report_hash,
            "decided_at": self.decided_at,
            "version": self.version,
        }


def _fail(reasons: List[str], condition: bool, reason: str) -> None:
    if not condition:
        reasons.append(reason)


def evaluate_promotion(
    strategy_id: str,
    report: Mapping[str, Any],
    policy: Optional[PromotionPolicy] = None,
    *,
    family_diagnostics: Optional[Mapping[str, Any]] = None,
) -> PromotionDecision:
    """Evaluate a reverification report against the promotion policy.

    No single metric can promote a strategy; multiple independent evidence
    classes (performance, significance, robustness, consistency,
    concentration) must all pass. Unknown/missing evidence fails closed.
    """
    policy = policy or PromotionPolicy()
    reasons: List[str] = []

    if report.get("status") != "VALIDATED":
        return _decision(
            strategy_id, report, STATUS_REJECTED, (f"report not validated: {report.get('status')}",), policy
        )

    m = report.get("metrics", {})
    sig = report.get("significance", {})

    # 1. Performance evidence.
    _fail(
        reasons,
        m.get("trade_count", 0) >= policy.min_trade_count,
        f"trade_count {m.get('trade_count', 0)} < {policy.min_trade_count}",
    )
    _fail(
        reasons, m.get("expectancy", 0.0) > policy.min_expectancy, f"expectancy {m.get('expectancy', 0.0)} not positive"
    )
    _fail(reasons, m.get("sharpe", 0.0) > policy.min_sharpe, f"sharpe {m.get('sharpe', 0.0)} <= {policy.min_sharpe}")
    _fail(
        reasons,
        m.get("max_drawdown", -1.0) > policy.max_drawdown_floor,
        f"max_drawdown {m.get('max_drawdown')} breaches floor {policy.max_drawdown_floor}",
    )
    _fail(
        reasons,
        m.get("profit_factor", 0.0) > policy.min_profit_factor,
        f"profit_factor {m.get('profit_factor', 0.0)} <= {policy.min_profit_factor}",
    )

    # 2. Statistical significance after multiple-testing correction.
    _fail(
        reasons,
        sig.get("psr", {}).get("psr", 0.0) >= policy.min_psr,
        f"PSR {sig.get('psr', {}).get('psr', 0.0):.3f} < {policy.min_psr}",
    )
    _fail(
        reasons,
        sig.get("dsr", {}).get("dsr", 0.0) >= policy.min_dsr,
        f"DSR {sig.get('dsr', {}).get('dsr', 0.0):.3f} < {policy.min_dsr}",
    )
    if "pbo" in report:
        _fail(
            reasons,
            report["pbo"].get("pbo", 1.0) < policy.max_pbo,
            f"PBO {report['pbo'].get('pbo', 1.0):.3f} >= {policy.max_pbo}",
        )
    else:
        reasons.append("PBO evidence missing")

    # 3. Walk-forward consistency.
    wf = report.get("walk_forward_consistency")
    if wf is not None:
        _fail(
            reasons,
            wf.get("positive_fold_fraction", 0.0) >= policy.min_positive_fold_fraction,
            f"positive_fold_fraction {wf.get('positive_fold_fraction', 0.0)} < {policy.min_positive_fold_fraction}",
        )
    else:
        reasons.append("walk-forward fold evidence missing")

    # 4. Cost-stress robustness.
    cs = report.get("cost_stress")
    if policy.cost_stress_must_survive_2x:
        if cs is None:
            reasons.append("cost-stress evidence missing")
        else:
            _fail(reasons, cs.get("survives_2x", False), "does not survive 2x transaction costs")

    # 5. Concentration.
    conc = report.get("concentration", {})
    for scope in ("trades", "symbols"):
        c = conc.get(scope)
        if c is not None:
            _fail(
                reasons,
                c.get("top1_share", 1.0) <= policy.allow_top1_concentration,
                f"{scope} concentration flagged: {c.get('reason', '')}",
            )

    # 6. Benchmark comparison (when provided).
    bench = report.get("benchmark")
    if bench is not None:
        _fail(
            reasons,
            bench.get("excess_return", -1.0) > policy.min_benchmark_excess,
            f"excess return {bench.get('excess_return')} <= {policy.min_benchmark_excess}",
        )

    # 7. Parameter stability (family level).
    if policy.require_parameter_stability:
        stab = (family_diagnostics or {}).get("parameter_stability_sharpe")
        if stab is None:
            reasons.append("parameter stability evidence missing")
        else:
            _fail(reasons, stab.get("stable", False), f"parameter instability: {stab.get('reason', '')}")

    status = STATUS_APPROVED if not reasons else STATUS_REJECTED
    return _decision(strategy_id, report, status, tuple(reasons), policy)


def mark_research_only(strategy_id: str, report: Mapping[str, Any], reason: str) -> PromotionDecision:
    return _decision(strategy_id, report, STATUS_RESEARCH_ONLY, (reason,), PromotionPolicy())


def _decision(
    strategy_id: str,
    report: Mapping[str, Any],
    status: str,
    reasons: Tuple[str, ...],
    policy: PromotionPolicy,
) -> PromotionDecision:
    report_hash = hashlib.sha256(json.dumps(report, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return PromotionDecision(
        status=status,
        strategy_id=strategy_id,
        config_id=str(report.get("config_id", "")),
        reasons=reasons,
        policy_hash=policy.policy_hash(),
        report_hash=report_hash,
        decided_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Persistence


def promotion_root() -> Path:
    return Path(
        os.environ.get(
            "TRADING_PROMOTIONS_ROOT",
            str(Path.cwd() / "artifacts" / "promotions"),
        )
    )


def persist_decision(decision: PromotionDecision, root: Optional[Path] = None) -> Path:
    """Persist a decision artifact. Immutable: existing files are not overwritten."""
    root = root or promotion_root()
    directory = root / decision.strategy_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{decision.config_id}--{decision.report_hash[:12]}.json"
    if path.exists():
        return path
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(decision.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return path


def load_approvals(strategy_id: Optional[str] = None, root: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Load APPROVED decision artifacts, keyed by strategy_id.

    Only artifacts whose policy hash matches the CURRENT policy version are
    honored — a strategy approved under an old policy must be revalidated.
    """
    root = root or promotion_root()
    out: Dict[str, Dict[str, Any]] = {}
    if not root.exists():
        return out
    current_policy_hash = PromotionPolicy().policy_hash()
    for path in sorted(root.glob(("*/" if strategy_id is None else f"{strategy_id}/") + "*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if payload.get("status") != STATUS_APPROVED:
            continue
        if payload.get("version") != PROMOTION_POLICY_VERSION:
            continue
        if payload.get("policy_hash") != current_policy_hash:
            continue
        out[str(payload.get("strategy_id"))] = payload
    return out
