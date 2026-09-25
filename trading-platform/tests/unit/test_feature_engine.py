"""Feature-engine tests: correctness, warmup, and the no-lookahead guarantee."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from trading_platform.features.indicators import (
    _BENCHMARK_FEATURES,
    _REGISTRY,
    FeatureError,
    assert_point_in_time,
    atr,
    available_features,
    compute_features,
    donchian_upper,
    ema,
    gap_pct,
    make_spec,
    relative_strength,
    relative_volume,
    rolling_beta,
    rsi,
    sma,
    volume_zscore,
)


def _frame(n: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0004, 0.015, n))
    high = close * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n)))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


def test_sma_known_values() -> None:
    df = pd.DataFrame({"close": [1.0, 2.0, 3.0, 4.0, 5.0]})
    out = sma(df, 3)
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[4] == pytest.approx(4.0)


def test_ema_warmup_and_trailing() -> None:
    df = pd.DataFrame({"close": np.arange(1.0, 51.0)})
    out = ema(df, 10)
    assert out.iloc[:9].isna().all()
    assert np.isfinite(out.iloc[9:]).all()
    assert out.iloc[-1] < 50.0  # EMA trails the rising series


def test_rsi_bounds_and_all_gains() -> None:
    up = pd.DataFrame({"close": np.arange(1.0, 31.0)})
    out = rsi(up, 14)
    assert out.dropna().iloc[-1] == pytest.approx(100.0)
    df = _frame()
    out2 = rsi(df, 14)
    assert ((out2.dropna() >= 0) & (out2.dropna() <= 100)).all()


def test_atr_positive_after_warmup() -> None:
    df = _frame()
    out = atr(df, 14)
    assert out.dropna().gt(0).all()
    assert out.isna().sum() >= 13


def test_donchian_excludes_current_bar() -> None:
    df = pd.DataFrame({"high": [1.0, 5.0, 2.0, 2.0], "low": [1.0, 1.0, 1.0, 1.0]})
    out = donchian_upper(df, 3)
    # At index 3 the channel = max(high[0..2]) = 5, not including index 3.
    assert out.iloc[3] == pytest.approx(5.0)


def test_relative_volume_uses_prior_window() -> None:
    vols = np.concatenate([np.full(20, 1000.0), [4000.0]])
    df = pd.DataFrame({"volume": vols})
    out = relative_volume(df, 20)
    assert out.iloc[-1] == pytest.approx(4.0)


def test_volume_zscore_spike() -> None:
    vols = np.concatenate([np.full(60, 1000.0) * (1 + np.arange(60.0) * 0.001), [5000.0]])
    df = pd.DataFrame({"volume": vols})
    out = volume_zscore(df, 60)
    assert out.iloc[-1] > 3.0


def test_gap_pct() -> None:
    df = pd.DataFrame({"open": [100.0, 110.0], "close": [100.0, 110.0]})
    out = gap_pct(df)
    assert out.iloc[1] == pytest.approx(0.10)


def test_benchmark_features_alignment_check() -> None:
    df = _frame()
    short_bench = df["close"].iloc[:10]
    with pytest.raises(FeatureError):
        relative_strength(df, short_bench, 63)
    with pytest.raises(FeatureError):
        rolling_beta(df, short_bench, 63)


def test_make_spec_unknown_rejected() -> None:
    with pytest.raises(FeatureError):
        make_spec("not_a_feature")
    with pytest.raises(FeatureError):
        make_spec("sma", {"window": 0})
    with pytest.raises(FeatureError):
        make_spec("sma", {"window": -3})


def test_all_features_point_in_time() -> None:
    """Prefix-stability: values at cut must not change when the future is removed."""
    df = _frame(260)
    bench = _frame(260, seed=11)["close"]
    for name in _REGISTRY:
        fn = _REGISTRY[name][0]
        params = dict(_REGISTRY[name][1])
        assert_point_in_time(df, fn, **params)
    for name in _BENCHMARK_FEATURES:
        fn = _BENCHMARK_FEATURES[name][0]
        params = dict(_BENCHMARK_FEATURES[name][1])
        full = fn(df, bench, **params)
        cut = 200
        prefix = fn(df.iloc[: cut + 1], bench.iloc[: cut + 1], **params)
        a, b = full.iloc[cut], prefix.iloc[-1]
        assert (pd.isna(a) and pd.isna(b)) or a == pytest.approx(b, rel=0, abs=1e-10)


def test_revised_future_bars_do_not_leak() -> None:
    """Correcting future bars must not change earlier feature values."""
    df = _frame(120)
    mutated = df.copy()
    mutated.iloc[100:, mutated.columns.get_loc("close")] *= 2.0
    cut = 99
    a = sma(df, 20).iloc[cut]
    b = sma(mutated, 20).iloc[cut]
    assert a == pytest.approx(b)


def test_compute_features_and_snapshot_hash() -> None:
    df = _frame(120)
    specs = (make_spec("sma", {"window": 20}), make_spec("rsi", {"window": 14}))
    ff = compute_features(df, specs, symbol="TEST")
    assert list(ff.frame.columns) == ["sma", "rsi"]
    h = ff.snapshot_hash(ff.frame.index[-1])
    assert isinstance(h, str) and len(h) == 64
    with pytest.raises(FeatureError):
        ff.snapshot_hash("not-a-label")


def test_duplicate_columns_rejected() -> None:
    df = _frame(60)
    spec = make_spec("sma", {"window": 20})
    with pytest.raises(FeatureError):
        compute_features(df, (spec, spec))


def test_available_features_complete() -> None:
    names = set(available_features())
    expected = {
        "sma",
        "ema",
        "rsi",
        "atr",
        "normalized_atr",
        "realized_volatility",
        "return",
        "roc",
        "rolling_high",
        "rolling_low",
        "donchian_upper",
        "donchian_lower",
        "volume_sma",
        "relative_volume",
        "volume_zscore",
        "distance_from_ma",
        "rolling_drawdown",
        "relative_strength",
        "rolling_beta",
        "rolling_correlation",
        "gap_pct",
        "price_percentile",
        "volatility_percentile",
        "distance_from_52w_high",
    }
    assert expected <= names
