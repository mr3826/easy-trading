"""Reverification statistics tests: PSR/DSR/PBO, bootstrap, concentration."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from trading_platform.validation.reverification import (
    benchmark_comparison,
    concentration,
    cost_stress_summary,
    parameter_stability_surface,
    purged_walk_forward_splits,
    regime_decomposition,
    verify_no_leakage,
)
from trading_platform.validation.statistics import (
    BootstrapConfig,
    ValidationError,
    cscv_pbo,
    deflated_sharpe_ratio,
    max_consecutive_losses,
    max_drawdown,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
    white_reality_check,
)


def test_purged_splits_no_overlap() -> None:
    folds = purged_walk_forward_splits(1000, n_folds=4, min_train=252, embargo=30)
    assert verify_no_leakage(folds, embargo=30)
    for f in folds:
        assert f.train_end <= f.test_start - 30
        assert f.test_end - f.test_start >= 1
    # final fold consumes the tail
    assert folds[-1].test_end == 1000


def test_anchored_vs_rolling() -> None:
    anchored = purged_walk_forward_splits(1000, n_folds=4, min_train=252, anchored=True)
    rolling = purged_walk_forward_splits(1000, n_folds=4, min_train=252, anchored=False)
    assert all(f.train_start == 0 for f in anchored)
    assert any(f.train_start > 0 for f in rolling)


def test_splits_reject_impossible() -> None:
    with pytest.raises(ValidationError):
        purged_walk_forward_splits(100, n_folds=4, min_train=252)
    with pytest.raises(ValidationError):
        purged_walk_forward_splits(1000, n_folds=0, min_train=10)


def test_sharpe_and_max_drawdown() -> None:
    rng = np.random.default_rng(1)
    assert sharpe_ratio(rng.normal(0.001, 0.01, 500)) != 0.0
    assert max_drawdown([0.05, -0.1, 0.02, -0.2]) == pytest.approx(-0.2656, abs=1e-3)
    assert max_consecutive_losses([0.01, -0.01, -0.02, 0.03, -0.01, -0.01, -0.01]) == 3


def test_psr_high_for_strong_positive() -> None:
    rng = np.random.default_rng(2)
    good = rng.normal(0.002, 0.01, 500)
    bad = rng.normal(-0.002, 0.01, 500)
    assert probabilistic_sharpe_ratio(good)["psr"] > 0.95
    assert probabilistic_sharpe_ratio(bad)["psr"] < 0.05


def test_dsr_penalizes_many_trials() -> None:
    rng = np.random.default_rng(4)
    returns = rng.normal(0.0005, 0.01, 500)
    few = deflated_sharpe_ratio(returns, 2)
    many = deflated_sharpe_ratio(returns, 500)
    assert many["expected_max_sharpe"] > few["expected_max_sharpe"]
    assert many["dsr"] <= few["dsr"] + 1e-9


def test_dsr_fallback_scale_shrinks_with_sample_size() -> None:
    """The null selection-bias scale must shrink with more data, in correct
    annualized-Sharpe units (regression test for a units bug)."""
    rng = np.random.default_rng(24)
    small = rng.normal(0.0005, 0.01, 252)
    large = rng.normal(0.0005, 0.01, 2520)
    d_small = deflated_sharpe_ratio(small, 100)
    d_large = deflated_sharpe_ratio(large, 100)
    assert d_large["sharpe_std"] < d_small["sharpe_std"]
    assert d_small["expected_max_sharpe"] < 10.0  # sane annualized units


def test_effective_pbo_blocks_guards_thin_data() -> None:
    from trading_platform.validation.statistics import effective_pbo_blocks

    assert effective_pbo_blocks(1600, 16) == 16
    assert effective_pbo_blocks(80, 16) == 16
    assert effective_pbo_blocks(50, 16) == 10
    assert effective_pbo_blocks(20, 16) == 4
    assert effective_pbo_blocks(10, 16) is None
    assert effective_pbo_blocks(7, 16) is None


def test_engine_reports_insufficient_pbo_fail_closed() -> None:
    """Thin OOS data must produce pbo=1.0 INSUFFICIENT_DATA, never a crash
    and never a silently-missing (optimistic) field."""
    from trading_platform.validation.engine import ConfigResult, FamilyEvidence, validate_configuration

    rng = np.random.default_rng(25)
    result = ConfigResult(
        config_id="c1",
        params={"w": 10},
        daily_returns=rng.normal(0.0005, 0.01, 10),
    )
    report = validate_configuration(
        result,
        FamilyEvidence("fam", 1, ["c1"]),
        pbo_returns_matrix=rng.normal(0, 0.01, (10, 2)),
    )
    assert report["pbo"]["status"] == "INSUFFICIENT_DATA"
    assert report["pbo"]["pbo"] == 1.0


def test_pbo_low_when_ranking_persists_oos() -> None:
    n, t = 6, 1600
    rng = np.random.default_rng(5)
    edges = np.array([0.003, 0.002, 0.001, -0.001, -0.002, -0.003])
    m = rng.normal(0, 0.01, (t, n)) + edges[None, :]
    pbo = cscv_pbo(m, n_blocks=16)
    # strong persistent edges -> IS ranking matches OOS ranking -> PBO ~ 0
    assert pbo["pbo"] < 0.2
    assert pbo["n_configs"] == 6


def test_pbo_high_when_pure_noise() -> None:
    n, t = 8, 1600
    rng = np.random.default_rng(6)
    pbo = cscv_pbo(rng.normal(0, 0.01, (t, n)), n_blocks=16)
    assert pbo["pbo"] > 0.3  # pure noise: IS-best is a coin flip OOS


def test_bootstrap_reproducible_and_reasonable() -> None:
    rng = np.random.default_rng(7)
    r = rng.normal(0.001, 0.01, 300)
    cfg = BootstrapConfig(n_resamples=200, block_length=10, seed=123)
    from trading_platform.validation.statistics import bootstrap_distribution

    a = bootstrap_distribution(r, cfg)
    b = bootstrap_distribution(r, cfg)
    assert a == b  # deterministic under seed
    assert a["total_return_p5"] <= a["total_return_p50"] <= a["total_return_p95"]


def test_white_reality_check_noise_not_significant() -> None:
    rng = np.random.default_rng(8)
    n, t = 10, 500
    m = rng.normal(0, 0.01, (t, n))
    wrc = white_reality_check(m, np.zeros(t), BootstrapConfig(n_resamples=100, block_length=10, seed=9))
    assert 0.0 <= wrc["p_value"] <= 1.0
    # true persistent edge should be detected
    edge = m + 0.004
    wrc2 = white_reality_check(edge, np.zeros(t), BootstrapConfig(n_resamples=100, block_length=10, seed=9))
    assert wrc2["p_value"] < 0.1


def test_concentration_flags_outliers() -> None:
    concentrated = {"a": 100.0, "b": 5.0, "c": 5.0}
    spread = {f"s{i}": 10.0 for i in range(20)}
    assert concentration(concentrated).flagged
    assert not concentration(spread).flagged
    assert concentration({"a": -5.0}).flagged  # no positive contributions


def test_regime_decomposition() -> None:
    rng = np.random.default_rng(21)
    idx = pd.date_range("2024-01-01", periods=100, freq="B")
    bull = rng.normal(0.002, 0.008, 50)
    bear = rng.normal(-0.002, 0.008, 50)
    returns = pd.Series(np.concatenate([bull, bear]), index=idx)
    regimes = pd.Series(["bull"] * 50 + ["bear"] * 50, index=idx)
    out = regime_decomposition(returns, regimes)
    assert out["bull"]["sharpe"] > 0
    assert out["bear"]["sharpe"] < 0


def test_benchmark_comparison_beats_and_lags() -> None:
    rng = np.random.default_rng(10)
    bench = rng.normal(0.0005, 0.01, 300)
    strat = bench + 0.001
    out = benchmark_comparison(strat, bench)
    assert out["excess_return"] > 0
    assert out["beta"] == pytest.approx(1.0, abs=0.05)
    lag = benchmark_comparison(bench - 0.001, bench)
    assert lag["excess_return"] < 0


def test_parameter_stability_surface() -> None:
    results = {
        "c1": {"params": {"w": 10}, "metrics": {"sharpe": 1.0}},
        "c2": {"params": {"w": 11}, "metrics": {"sharpe": 0.9}},
        "c3": {"params": {"w": 9}, "metrics": {"sharpe": 0.8}},
        "c4": {"params": {"w": 50}, "metrics": {"sharpe": 0.1}},
    }
    out = parameter_stability_surface(results)
    assert out["best_config"] == "c1"
    assert out["stable"]
    spike = dict(results)
    spike["c1"] = {"params": {"w": 10}, "metrics": {"sharpe": 5.0}}
    unstable = parameter_stability_surface(spike)
    assert unstable["best_config"] == "c1"
    assert not unstable["stable"]


def test_cost_stress_summary() -> None:
    out = cost_stress_summary({1.0: {"expectancy": 100.0}, 1.5: {"expectancy": 60.0}, 2.0: {"expectancy": 20.0}})
    assert out["survives_2x"]
    failing = cost_stress_summary({1.0: {"expectancy": 100.0}, 1.5: {"expectancy": 30.0}, 2.0: {"expectancy": -10.0}})
    assert not failing["survives_2x"]
