"""Statistics for strategy reverification.

Includes risk-adjusted performance metrics, probabilistic/deflated Sharpe
ratios (Bailey & Lopez de Prado), CSCV probability of backtest overfitting,
block-bootstrap resampling suitable for serially dependent returns, and
White's reality-check style data-snooping test.

All functions are deterministic given explicit seeds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Dict, List, Optional, Sequence, Union

import numpy as np
from numpy import ndarray

ArrayLike = Union[Sequence[float], ndarray]

VALIDATION_STATS_VERSION = "1.0.0"

TRADING_DAYS = 252


class ValidationError(ValueError):
    pass


def _clean(returns: ArrayLike) -> ndarray:
    arr = np.asarray(returns, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        raise ValidationError("need at least 2 finite returns")
    return arr


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Acklam's approximation of the standard normal quantile."""
    if not 0.0 < p < 1.0:
        raise ValidationError(f"p must be in (0,1), got {p}")
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


# ---------------------------------------------------------------------------
# Basic performance metrics


def annualized_return(returns: ArrayLike) -> float:
    r = _clean(returns)
    total = float(np.prod(1.0 + r))
    years = r.size / TRADING_DAYS
    if years <= 0 or total <= 0:
        return 0.0
    return total ** (1.0 / years) - 1.0


def sharpe_ratio(returns: ArrayLike, *, periods: int = TRADING_DAYS) -> float:
    r = _clean(returns)
    sd = float(np.std(r, ddof=1))
    if sd == 0.0:
        return 0.0
    return float(np.mean(r)) / sd * math.sqrt(periods)


def sortino_ratio(returns: ArrayLike, *, periods: int = TRADING_DAYS) -> float:
    r = _clean(returns)
    downside = r[r < 0]
    if downside.size == 0:
        return 0.0
    dd = float(np.std(downside, ddof=1)) if downside.size > 1 else abs(float(downside[0]))
    if dd == 0.0:
        return 0.0
    return float(np.mean(r)) / dd * math.sqrt(periods)


def max_drawdown(returns: ArrayLike) -> float:
    r = _clean(returns)
    equity = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    return float(np.min(dd))


def calmar_ratio(returns: ArrayLike) -> float:
    mdd = abs(max_drawdown(returns))
    if mdd == 0.0:
        return 0.0
    return annualized_return(returns) / mdd


def drawdown_series(returns: ArrayLike) -> ndarray:
    r = _clean(returns)
    equity = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(equity)
    return equity / peak - 1.0


def longest_drawdown_days(returns: ArrayLike) -> int:
    dd = drawdown_series(returns)
    longest = current = 0
    for v in dd:
        current = current + 1 if v < 0 else 0
        longest = max(longest, current)
    return longest


def max_consecutive_losses(returns: ArrayLike) -> int:
    r = _clean(returns)
    longest = current = 0
    for v in r:
        current = current + 1 if v < 0 else 0
        longest = max(longest, current)
    return longest


def trade_metrics(trade_pnls: Sequence[float]) -> Dict[str, float]:
    """Expectancy/win-rate/payoff/profit-factor from per-trade net P&L."""
    trades = np.asarray(trade_pnls, dtype=float)
    trades = trades[np.isfinite(trades)]
    if trades.size == 0:
        return {
            "trade_count": 0.0,
            "expectancy": 0.0,
            "win_rate": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "payoff_ratio": 0.0,
            "profit_factor": 0.0,
        }
    wins = trades[trades > 0]
    losses = trades[trades < 0]
    avg_win = float(wins.mean()) if wins.size else 0.0
    avg_loss = float(losses.mean()) if losses.size else 0.0
    gross_win = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    return {
        "trade_count": float(trades.size),
        "expectancy": float(trades.mean()),
        "win_rate": float(wins.size / trades.size),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": (avg_win / abs(avg_loss)) if avg_loss else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# Sharpe significance


def probabilistic_sharpe_ratio(
    returns: ArrayLike,
    benchmark_sharpe: float = 0.0,
    *,
    periods: int = TRADING_DAYS,
) -> Dict[str, float]:
    """PSR: P(true Sharpe > benchmark), correcting for non-normality.

    Bailey & Lopez de Prado (2012). Uses observed skew/kurtosis of returns.
    """
    r = _clean(returns)
    n = r.size
    sr = sharpe_ratio(r, periods=periods)
    sd = float(np.std(r, ddof=1))
    if sd == 0.0 or n < 3:
        return {"psr": 0.0, "sharpe": sr, "benchmark_sharpe": benchmark_sharpe, "n": float(n)}
    m = float(np.mean(r))
    m3 = float(np.mean((r - m) ** 3))
    m4 = float(np.mean((r - m) ** 4))
    skew = m3 / sd**3
    kurt = m4 / sd**4  # non-excess
    sr_daily = sr / math.sqrt(periods)
    bench_daily = benchmark_sharpe / math.sqrt(periods)
    denom = math.sqrt(max(1e-12, (1 - skew * sr_daily + (kurt - 1) / 4.0 * sr_daily**2) / (n - 1)))
    psr = _norm_cdf((sr_daily - bench_daily) / denom)
    return {"psr": psr, "sharpe": sr, "benchmark_sharpe": benchmark_sharpe, "n": float(n)}


def expected_max_sharpe(n_trials: int, sr_std: float, skew: float = 0.0, kurt: float = 3.0, n: int = 1) -> float:
    """E[max Sharpe] across N independent trials under the null (SR*=0)."""
    if n_trials < 1:
        raise ValidationError("n_trials must be >= 1")
    if n_trials == 1:
        return 0.0
    gamma = 0.5772156649015329  # Euler-Mascheroni
    z1 = _norm_ppf(1.0 - 1.0 / n_trials)
    z2 = _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return float(sr_std * ((1 - gamma) * z1 + gamma * z2))


def deflated_sharpe_ratio(
    returns: ArrayLike,
    n_trials: int,
    trial_sharpes: Optional[Sequence[float]] = None,
    *,
    periods: int = TRADING_DAYS,
) -> Dict[str, float]:
    """DSR: PSR against E[max SR] across all tested configurations.

    ``n_trials`` is the total number of strategy/parameter configurations
    tried in the experiment family (including failures). ``trial_sharpes``,
    when provided, gives the cross-sectional Sharpe estimates used to
    estimate the selection bias scale; otherwise a conservative unit scale
    annualized from the returns is used.
    """
    if n_trials < 1:
        raise ValidationError("n_trials must be >= 1")
    r = _clean(returns)
    sr = sharpe_ratio(r, periods=periods)
    if trial_sharpes and len(trial_sharpes) >= 2:
        sr_std = float(np.std(np.asarray(trial_sharpes, dtype=float), ddof=1))
    else:
        # Conservative scale: sampling std of the Sharpe itself.
        n = r.size
        sr_std = math.sqrt(max(1e-12, (1.0 + 0.5 * sr**2) / (n / periods)))
    emax = expected_max_sharpe(n_trials, sr_std)
    psr = probabilistic_sharpe_ratio(r, benchmark_sharpe=emax, periods=periods)
    return {
        "dsr": psr["psr"],
        "sharpe": sr,
        "expected_max_sharpe": emax,
        "n_trials": float(n_trials),
        "sharpe_std": sr_std,
    }


# ---------------------------------------------------------------------------
# Probability of backtest overfitting (CSCV)


def cscv_pbo(returns_matrix: ndarray, n_blocks: int = 16) -> Dict[str, float]:
    """Combinatorially-symmetric cross-validation PBO.

    ``returns_matrix`` rows = time steps, columns = strategy configurations.
    Splits rows into ``n_blocks`` contiguous blocks; for every combination of
    S/2 in-sample blocks, ranks configs in-sample and checks where the IS-best
    config ranks out-of-sample. PBO = fraction where the IS-best config's OOS
    rank is at or below the median (logit < 0).
    """
    m = np.asarray(returns_matrix, dtype=float)
    if m.ndim != 2 or m.shape[1] < 2:
        raise ValidationError("need a 2D matrix with >= 2 configurations")
    t, n_configs = m.shape
    if n_blocks < 4 or n_blocks % 2 != 0 or t < n_blocks:
        raise ValidationError("n_blocks must be even, >= 4, and <= number of rows")
    block_size = t // n_blocks
    blocks = [m[i * block_size : (i + 1) * block_size, :] for i in range(n_blocks)]
    half = n_blocks // 2
    logits: List[float] = []
    for combo in combinations(range(n_blocks), half):
        is_idx = set(combo)
        oos_idx = [i for i in range(n_blocks) if i not in is_idx]
        is_mat = np.concatenate([blocks[i] for i in sorted(is_idx)], axis=0)
        oos_mat = np.concatenate([blocks[i] for i in sorted(oos_idx)], axis=0)

        def _sr(col: ndarray) -> float:
            sd = float(np.std(col, ddof=1)) if col.size > 1 else 0.0
            return float(np.mean(col)) / sd if sd > 0 else 0.0

        is_sr = np.array([_sr(is_mat[:, j]) for j in range(n_configs)])
        oos_sr = np.array([_sr(oos_mat[:, j]) for j in range(n_configs)])
        best = int(np.argmax(is_sr))
        # OOS rank of the IS-best config (descending, 1 = best).
        rank = 1 + int(np.sum(oos_sr > oos_sr[best]))
        rel = (rank - 1) / (n_configs - 1) if n_configs > 1 else 0.0
        rel = min(max(rel, 1e-6), 1 - 1e-6)
        # logit of OOS rank position: negative => IS-best is below median OOS
        logits.append(math.log((1 - rel) / rel))
    pbo = float(np.mean([1.0 if lg < 0 else 0.0 for lg in logits]))
    return {
        "pbo": pbo,
        "n_configs": float(n_configs),
        "n_blocks": float(n_blocks),
        "n_combinations": float(len(logits)),
        "mean_logit": float(np.mean(logits)) if logits else 0.0,
    }


# ---------------------------------------------------------------------------
# Block bootstrap / Monte Carlo


@dataclass(frozen=True)
class BootstrapConfig:
    n_resamples: int = 1000
    block_length: int = 10
    seed: int = 42


def stationary_bootstrap(returns: ArrayLike, config: BootstrapConfig) -> List[ndarray]:
    """Politis-Romano stationary bootstrap (random block lengths, mean = block_length).

    Preserves serial dependence better than IID resampling; deterministic
    under ``config.seed``.
    """
    r = _clean(returns)
    n = r.size
    if config.block_length < 1:
        raise ValidationError("block_length must be >= 1")
    rng = np.random.default_rng(config.seed)
    p = 1.0 / config.block_length
    samples: List[ndarray] = []
    for _ in range(config.n_resamples):
        idx = np.empty(n, dtype=int)
        pos = int(rng.integers(0, n))
        for i in range(n):
            if rng.random() < p:
                pos = int(rng.integers(0, n))
            idx[i] = pos
            pos = (pos + 1) % n
        samples.append(r[idx])
    return samples


def bootstrap_distribution(returns: ArrayLike, config: BootstrapConfig) -> Dict[str, Any]:
    """Distribution of key metrics over stationary-bootstrap resamples."""
    r = _clean(returns)
    base_sharpe = sharpe_ratio(r)
    resamples = stationary_bootstrap(r, config)
    totals, sharpes, mdds, streaks, terminals = [], [], [], [], []
    for sample in resamples:
        totals.append(float(np.prod(1.0 + sample) - 1.0))
        sharpes.append(sharpe_ratio(sample))
        mdds.append(max_drawdown(sample))
        streaks.append(max_consecutive_losses(sample))
        terminals.append(float(np.prod(1.0 + sample)))

    def _pct(values: List[float], q: float) -> float:
        return float(np.percentile(np.asarray(values), q))

    return {
        "observed_total_return": float(np.prod(1.0 + r) - 1.0),
        "observed_sharpe": base_sharpe,
        "observed_max_drawdown": max_drawdown(r),
        "observed_expectancy": float(np.mean(r)),
        "total_return_p5": _pct(totals, 5),
        "total_return_p50": _pct(totals, 50),
        "total_return_p95": _pct(totals, 95),
        "sharpe_p5": _pct(sharpes, 5),
        "sharpe_p50": _pct(sharpes, 50),
        "sharpe_p95": _pct(sharpes, 95),
        "max_drawdown_p5": _pct(mdds, 5),
        "max_drawdown_p50": _pct(mdds, 50),
        "max_drawdown_p95": _pct(mdds, 95),
        "max_loss_streak_p95": _pct([float(s) for s in streaks], 95),
        "terminal_equity_p5": _pct(terminals, 5),
        "terminal_equity_p95": _pct(terminals, 95),
        "prob_total_return_positive": float(np.mean([1.0 if t > 0 else 0.0 for t in totals])),
        "n_resamples": float(config.n_resamples),
        "block_length": float(config.block_length),
    }


def white_reality_check(
    config_returns: ndarray,
    benchmark_returns: ArrayLike,
    config: BootstrapConfig,
) -> Dict[str, float]:
    """White (2000) reality check via stationary bootstrap.

    Tests whether the BEST of N configurations beats the benchmark after
    accounting for data snooping. ``config_returns`` columns = configurations
    of net-of-benchmark excess daily returns... (excess = config - benchmark).
    Returns the bootstrap p-value for H0: best config has no true edge.
    """
    m = np.asarray(config_returns, dtype=float)
    bench = _clean(benchmark_returns)
    if m.ndim != 2 or m.shape[0] != bench.size:
        raise ValidationError("config matrix rows must match benchmark length")
    excess = m - bench[:, None]
    observed = np.max(np.mean(excess, axis=0))
    centered = excess - np.mean(excess, axis=0, keepdims=True)
    t = bench.size
    hits = 0
    rng = np.random.default_rng(config.seed)
    p = 1.0 / config.block_length
    for _ in range(config.n_resamples):
        idx = np.empty(t, dtype=int)
        pos = int(rng.integers(0, t))
        for i in range(t):
            if rng.random() < p:
                pos = int(rng.integers(0, t))
            idx[i] = pos
            pos = (pos + 1) % t
        stat = float(np.max(np.mean(centered[idx], axis=0)))
        if stat >= observed:
            hits += 1
    return {
        "p_value": float((hits + 1) / (config.n_resamples + 1)),
        "observed_best_mean_excess": float(observed),
        "n_configs": float(m.shape[1]),
        "n_resamples": float(config.n_resamples),
    }
