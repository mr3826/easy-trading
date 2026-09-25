"""Purged/embargoed walk-forward splits, parameter stability, concentration
analysis, regime decomposition, and benchmarking helpers.

These utilities complement walk_forward/walk_forward.py (which executes
folds through the simulator) with the statistical hygiene required before a
strategy can be considered for promotion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from trading_platform.validation.statistics import ArrayLike, ValidationError, max_drawdown

# ---------------------------------------------------------------------------
# Purged / embargoed walk-forward splits


@dataclass(frozen=True)
class PurgedFold:
    fold: int
    train_start: int
    train_end: int  # exclusive
    test_start: int
    test_end: int  # exclusive

    def to_dict(self) -> Dict[str, int]:
        return {
            "fold": self.fold,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
        }


def purged_walk_forward_splits(
    n_observations: int,
    *,
    n_folds: int,
    min_train: int,
    embargo: int = 0,
    anchored: bool = False,
) -> List[PurgedFold]:
    """Contiguous time-series walk-forward splits with purge/embargo.

    - No shuffling. Time order is always preserved.
    - ``embargo`` bars are dropped between train and test so that any label /
      holding-period window that would overlap the test period is excluded
      from training (purge). Callers should set embargo >= max holding period.
    - anchored: train always starts at 0; rolling: train window slides.
    """
    if n_folds < 1:
        raise ValidationError("n_folds must be >= 1")
    if embargo < 0 or min_train < 1:
        raise ValidationError("invalid embargo/min_train")
    test_size = (n_observations - min_train - embargo) // n_folds
    if test_size < 1:
        raise ValidationError("not enough observations for requested folds")
    folds: List[PurgedFold] = []
    for fold in range(n_folds):
        test_start = min_train + embargo + fold * test_size
        test_end = test_start + test_size if fold < n_folds - 1 else n_observations
        if test_end > n_observations:
            break
        train_end = test_start - embargo  # purge window removed
        train_start = 0 if anchored else max(0, train_end - min_train)
        if train_end - train_start < 1:
            raise ValidationError("fold produced empty training window")
        folds.append(PurgedFold(fold, train_start, train_end, test_start, test_end))
    return folds


def verify_no_leakage(folds: Sequence[PurgedFold], embargo: int = 0) -> bool:
    """Assert test windows never precede/overlap their train windows."""
    for f in folds:
        # Train must end at least `embargo` bars before the test window.
        if f.train_end > f.test_start - embargo:
            return False
        if f.train_start < 0 or f.test_end <= f.test_start:
            return False
    return True


# ---------------------------------------------------------------------------
# Parameter stability


def parameter_stability_surface(
    results: Mapping[str, Mapping[str, Any]],
    *,
    metric: str = "sharpe",
    stability_tolerance: float = 0.5,
) -> Dict[str, Any]:
    """Assess whether the best config sits in a stable parameter neighborhood.

    ``results`` maps config_id -> {"params": {...}, "metrics": {...}}.
    A neighborhood (L1 distance 1 in sorted-parameter grid space) median
    must be within ``stability_tolerance`` (relative) of the best value.
    """
    entries = []
    for cfg_id, payload in results.items():
        params = payload.get("params", {})
        value = payload.get("metrics", {}).get(metric)
        if value is None or not math.isfinite(float(value)):
            continue
        entries.append((cfg_id, dict(params), float(value)))
    if not entries:
        raise ValidationError("no finite metric values in results")
    numeric_keys = sorted({k for _, p, _ in entries for k, v in p.items() if isinstance(v, (int, float))})
    grids: Dict[str, List[float]] = {k: sorted({float(p[k]) for _, p, _ in entries if k in p}) for k in numeric_keys}

    def _grid_coord(params: Mapping[str, Any]) -> Optional[Tuple[int, ...]]:
        coord = []
        for k in numeric_keys:
            if k not in params:
                return None
            coord.append(grids[k].index(float(params[k])))
        return tuple(coord)

    best_id, best_params, best_value = max(entries, key=lambda e: e[2])
    best_coord = _grid_coord(best_params)
    neighbors: List[float] = []
    if best_coord is not None:
        for cfg_id, params, value in entries:
            if cfg_id == best_id:
                continue
            coord = _grid_coord(params)
            if coord is not None and sum(abs(a - b) for a, b in zip(coord, best_coord)) == 1:
                neighbors.append(value)
    if not neighbors:
        return {
            "metric": metric,
            "best_config": best_id,
            "best_value": best_value,
            "neighbor_count": 0,
            "stability_ratio": 0.0,
            "stable": False,
            "reason": "no adjacent configurations evaluated",
        }
    neighbor_median = float(np.median(np.asarray(neighbors)))
    scale = abs(best_value) if abs(best_value) > 1e-12 else 1.0
    stability_ratio = neighbor_median / scale if best_value > 0 else 0.0
    stable = best_value > 0 and neighbor_median > 0 and stability_ratio >= stability_tolerance
    return {
        "metric": metric,
        "best_config": best_id,
        "best_value": best_value,
        "neighbor_count": len(neighbors),
        "neighbor_median": neighbor_median,
        "stability_ratio": stability_ratio,
        "stable": bool(stable),
        "reason": "" if stable else "best config is an isolated optimum or neighbors are non-positive",
    }


# ---------------------------------------------------------------------------
# Concentration analysis


@dataclass(frozen=True)
class ConcentrationResult:
    top1_share: float
    top5_share: float
    n_items: int
    flagged: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "top1_share": self.top1_share,
            "top5_share": self.top5_share,
            "n_items": self.n_items,
            "flagged": self.flagged,
            "reason": self.reason,
        }


def concentration(
    values: Mapping[str, float],
    *,
    max_top1_share: float = 0.5,
    max_top5_share: float = 0.9,
) -> ConcentrationResult:
    """Share of total positive P&L contributed by the top-1 / top-5 items.

    Applies to per-trade P&L, per-symbol P&L, or per-period P&L. Flags
    deceptive performance concentrated in very few outcomes.
    """
    positives = sorted((v for v in values.values() if v > 0), reverse=True)
    total = sum(positives)
    if total <= 0:
        return ConcentrationResult(0.0, 0.0, 0, True, "no positive contributions")
    top1 = positives[0] / total
    top5 = sum(positives[:5]) / total
    flagged = top1 > max_top1_share or top5 > max_top5_share
    reason = ""
    if top1 > max_top1_share:
        reason = f"top item contributes {top1:.1%} of positive P&L"
    elif top5 > max_top5_share:
        reason = f"top 5 items contribute {top5:.1%} of positive P&L"
    return ConcentrationResult(top1, top5, len(positives), flagged, reason)


# ---------------------------------------------------------------------------
# Regime decomposition


def regime_decomposition(
    returns: pd.Series,
    regimes: pd.Series,
) -> Dict[str, Dict[str, float]]:
    """Performance metrics computed separately per regime label."""
    if not returns.index.equals(regimes.index):
        regimes = regimes.reindex(returns.index)
    out: Dict[str, Dict[str, float]] = {}
    for label in sorted(regimes.dropna().unique()):
        sub = returns[regimes == label]
        sub = sub[np.isfinite(sub)]
        if sub.size < 2:
            out[str(label)] = {"n": float(sub.size), "sharpe": 0.0, "total_return": 0.0}
            continue
        sd = float(sub.std(ddof=1))
        out[str(label)] = {
            "n": float(sub.size),
            "sharpe": (float(sub.mean()) / sd * math.sqrt(252)) if sd > 0 else 0.0,
            "total_return": float(np.prod(1.0 + sub.to_numpy()) - 1.0),
            "max_drawdown": max_drawdown(sub.to_numpy()),
        }
    return out


# ---------------------------------------------------------------------------
# Benchmark comparison


def benchmark_comparison(
    strategy_returns: ArrayLike,
    benchmark_returns: ArrayLike,
) -> Dict[str, float]:
    """Excess/beta comparison of a strategy against a benchmark series."""
    s = np.asarray(strategy_returns, dtype=float)
    b = np.asarray(benchmark_returns, dtype=float)
    n = min(s.size, b.size)
    if n < 2:
        raise ValidationError("insufficient overlap with benchmark")
    s, b = s[:n], b[:n]
    mask = np.isfinite(s) & np.isfinite(b)
    s, b = s[mask], b[mask]
    strat_total = float(np.prod(1.0 + s) - 1.0)
    bench_total = float(np.prod(1.0 + b) - 1.0)
    var_b = float(np.var(b, ddof=1))
    beta = float(np.cov(s, b, ddof=1)[0, 1] / var_b) if var_b > 0 else 0.0
    return {
        "strategy_return": strat_total,
        "benchmark_return": bench_total,
        "excess_return": strat_total - bench_total,
        "beta": beta,
        "max_drawdown_diff": max_drawdown(b) - max_drawdown(s),
        "sharpe_diff": _safe_sharpe(s) - _safe_sharpe(b),
    }


def _safe_sharpe(r: np.ndarray) -> float:
    sd = float(np.std(r, ddof=1)) if r.size > 1 else 0.0
    return float(np.mean(r)) / sd * math.sqrt(252) if sd > 0 else 0.0


# ---------------------------------------------------------------------------
# Transaction-cost stress helper


COST_STRESS_MULTIPLIERS: Tuple[float, ...] = (1.0, 1.5, 2.0)


def cost_stress_summary(
    results_by_multiplier: Mapping[float, Mapping[str, float]],
    *,
    metric: str = "expectancy",
) -> Dict[str, Any]:
    """Summarize metric degradation across cost multipliers (1x, 1.5x, 2x)."""
    series: List[Tuple[float, float]] = []
    for mult, metrics in sorted(results_by_multiplier.items()):
        value = metrics.get(metric)
        if value is not None and math.isfinite(float(value)):
            series.append((float(mult), float(value)))
    if not series:
        raise ValidationError("no finite cost-stress results")
    base = series[0][1]
    degraded = {str(m): v for m, v in series}
    worst = series[-1][1]
    survives = all(v > 0 for _, v in series) if base > 0 else False
    return {
        "metric": metric,
        "by_multiplier": degraded,
        "base": base,
        "worst": worst,
        "survives_2x": bool(survives),
    }
