"""Strategy reverification engine.

Consumes out-of-sample results for every tested configuration of a strategy
family and produces a complete ``ValidationReport`` per configuration plus
family-level overfitting diagnostics. Every tried configuration contributes
to the trial count used by Deflated Sharpe / reality-check corrections —
failed experiments are data, and are never silently dropped.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from trading_platform.validation.reverification import (
    benchmark_comparison,
    concentration,
    cost_stress_summary,
    parameter_stability_surface,
    regime_decomposition,
)
from trading_platform.validation.statistics import (
    VALIDATION_STATS_VERSION,
    ArrayLike,
    BootstrapConfig,
    annualized_return,
    bootstrap_distribution,
    calmar_ratio,
    cscv_pbo,
    deflated_sharpe_ratio,
    effective_pbo_blocks,
    longest_drawdown_days,
    max_consecutive_losses,
    max_drawdown,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
    sortino_ratio,
    trade_metrics,
    white_reality_check,
)

REVERIFICATION_VERSION = "1.0.0"


@dataclass(frozen=True)
class ConfigResult:
    """Out-of-sample evidence for one tested parameter configuration."""

    config_id: str
    params: Mapping[str, Any]
    daily_returns: ArrayLike  # OOS daily portfolio returns
    trade_pnls: Sequence[float] = ()
    symbol_pnls: Mapping[str, float] = field(default_factory=dict)
    benchmark_returns: Optional[ArrayLike] = None  # aligned
    regime_labels: Optional[pd.Series] = None  # aligned, indexed like daily returns
    cost_stress: Optional[Mapping[float, Mapping[str, float]]] = None
    fold_sharpes: Sequence[float] = ()  # per-fold OOS Sharpe


@dataclass(frozen=True)
class FamilyEvidence:
    """Family-level multiple-testing record."""

    family_id: str
    trial_count: int
    config_ids: Sequence[str]


def _dataset_hash(daily_returns: ArrayLike) -> str:
    arr = np.asarray(daily_returns, dtype=float)
    return hashlib.sha256(arr.tobytes()).hexdigest()


def validate_configuration(
    result: ConfigResult,
    family: FamilyEvidence,
    *,
    bootstrap: Optional[BootstrapConfig] = None,
    pbo_returns_matrix: Optional[np.ndarray] = None,
    pbo_blocks: int = 16,
) -> Dict[str, Any]:
    """Full reverification of one configuration within its family.

    Returns a JSON-serializable report dict suitable for promotion-gate
    input and archival.
    """
    r = np.asarray(result.daily_returns, dtype=float)
    report: Dict[str, Any] = {
        "config_id": result.config_id,
        "family_id": family.family_id,
        "params": dict(result.params),
        "trial_count": family.trial_count,
        "dataset_hash": _dataset_hash(r),
        "version": REVERIFICATION_VERSION,
        "stats_version": VALIDATION_STATS_VERSION,
    }
    if r[np.isfinite(r)].size < 2:
        report.update({"status": "INSUFFICIENT_DATA", "rejection_reasons": ["<2 finite OOS returns"]})
        return report

    metrics = trade_metrics(result.trade_pnls)
    report["metrics"] = {
        "annualized_return": annualized_return(r),
        "sharpe": sharpe_ratio(r),
        "sortino": sortino_ratio(r),
        "max_drawdown": max_drawdown(r),
        "calmar": calmar_ratio(r),
        "longest_drawdown_days": longest_drawdown_days(r),
        "max_consecutive_losses": max_consecutive_losses(r),
        **metrics,
    }

    psr = probabilistic_sharpe_ratio(r, benchmark_sharpe=0.0)
    trial_sharpes: List[float] = []
    dsr = deflated_sharpe_ratio(r, family.trial_count, trial_sharpes or None)
    report["significance"] = {"psr": psr, "dsr": dsr}

    if bootstrap is not None:
        report["bootstrap"] = bootstrap_distribution(r, bootstrap)

    if pbo_returns_matrix is not None:
        blocks = effective_pbo_blocks(len(pbo_returns_matrix), pbo_blocks)
        if blocks is None:
            report["pbo"] = {"pbo": 1.0, "status": "INSUFFICIENT_DATA", "n_configs": float(len(pbo_returns_matrix[0]))}
        else:
            report["pbo"] = cscv_pbo(np.asarray(pbo_returns_matrix, dtype=float), n_blocks=blocks)

    if result.benchmark_returns is not None:
        report["benchmark"] = benchmark_comparison(r, np.asarray(result.benchmark_returns, dtype=float))

    if result.regime_labels is not None:
        series = pd.Series(r, index=result.regime_labels.index)
        report["regime_decomposition"] = regime_decomposition(series, result.regime_labels)

    if result.trade_pnls:
        trade_map = {str(i): float(v) for i, v in enumerate(result.trade_pnls)}
    else:
        trade_map = {}
    report["concentration"] = {
        "trades": concentration(trade_map).to_dict() if trade_map else None,
        "symbols": concentration(dict(result.symbol_pnls)).to_dict() if result.symbol_pnls else None,
    }

    if result.cost_stress:
        report["cost_stress"] = cost_stress_summary(result.cost_stress)
        report["cost_stress"]["expectancy_stress"] = cost_stress_summary(result.cost_stress, metric="expectancy")

    if result.fold_sharpes:
        folds = np.asarray(result.fold_sharpes, dtype=float)
        report["walk_forward_consistency"] = {
            "fold_sharpes": [float(v) for v in folds],
            "positive_fold_fraction": float(np.mean(folds > 0)) if folds.size else 0.0,
            "fold_sharpe_std": float(np.std(folds, ddof=1)) if folds.size > 1 else 0.0,
        }

    report["status"] = "VALIDATED"
    return report


def family_level_diagnostics(
    results: Sequence[ConfigResult],
    family: FamilyEvidence,
    *,
    benchmark_returns: Optional[ArrayLike] = None,
    bootstrap: Optional[BootstrapConfig] = None,
) -> Dict[str, Any]:
    """Family-level multiple-testing evidence across all configurations."""
    if len(results) != family.trial_count:
        raise ValueError("family.trial_count must include every tested configuration")
    sharpes = []
    for res in results:
        r = np.asarray(res.daily_returns, dtype=float)
        r = r[np.isfinite(r)]
        if r.size >= 2:
            sharpes.append(sharpe_ratio(r))
    out: Dict[str, Any] = {
        "family_id": family.family_id,
        "trial_count": family.trial_count,
        "sharpe_cross_section": sharpes,
        "parameter_stability_sharpe": parameter_stability_surface(
            {res.config_id: {"params": res.params, "metrics": {"sharpe": s}} for res, s in zip(results, sharpes)}
        )
        if len(sharpes) >= 2
        else None,
    }
    if benchmark_returns is not None and bootstrap is not None and len(results) >= 2:
        matrix = np.column_stack([np.asarray(res.daily_returns, dtype=float) for res in results])
        # Align benchmark to OOS length (use the trailing overlap).
        bench = np.asarray(benchmark_returns, dtype=float)
        if bench.size >= matrix.shape[0]:
            bench = bench[bench.size - matrix.shape[0] :]
        else:
            matrix = matrix[matrix.shape[0] - bench.size :, :]
        out["white_reality_check"] = white_reality_check(matrix, bench, bootstrap)
    return out
