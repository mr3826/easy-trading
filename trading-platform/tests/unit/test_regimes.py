"""Market-regime engine tests."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from trading_platform.regimes import (
    LiquidityRegime,
    RegimeEngine,
    RegimePolicy,
    TrendRegime,
    VolatilityRegime,
    classify_trend,
)


def _uptrend(n: int = 400) -> pd.Series:
    idx = pd.date_range("2019-01-01", periods=n, freq="B")
    return pd.Series(100 * np.cumprod(1 + np.full(n, 0.002)), index=idx)


def _downtrend(n: int = 400) -> pd.Series:
    idx = pd.date_range("2019-01-01", periods=n, freq="B")
    return pd.Series(100 * np.cumprod(1 - np.full(n, 0.002)), index=idx)


def test_bullish_trend() -> None:
    close = _uptrend()
    policy = RegimePolicy()
    trend, distance, slope = classify_trend(close, policy)
    assert trend.iloc[-1] == TrendRegime.BULLISH.value
    assert distance.iloc[-1] > policy.neutral_band
    assert slope.iloc[-1] > 0


def test_bearish_trend() -> None:
    close = _downtrend()
    policy = RegimePolicy()
    trend, distance, slope = classify_trend(close, policy)
    assert trend.iloc[-1] == TrendRegime.BEARISH.value


def test_neutral_when_within_band() -> None:
    idx = pd.date_range("2019-01-01", periods=400, freq="B")
    close = pd.Series(100 + np.sin(np.arange(400) / 20.0), index=idx)
    trend, distance, _ = classify_trend(close, RegimePolicy())
    assert abs(distance.iloc[-1]) <= RegimePolicy().neutral_band
    assert trend.iloc[-1] == TrendRegime.NEUTRAL.value


def test_engine_classification_point_in_time() -> None:
    close = _uptrend()
    frame = pd.DataFrame({"close": close, "volume": np.full(len(close), 1e7)})
    engine = RegimeEngine()
    series = engine.classify_series(frame)
    # Warmup rows must not carry fabricated trend information.
    assert series["trend"].iloc[100] == TrendRegime.NEUTRAL.value
    cut = 300
    later = engine.classify_at(frame, close.index[cut])
    assert later.trend == TrendRegime.BULLISH
    assert later.liquidity == LiquidityRegime.ACCEPTABLE


def test_volatility_extreme_detection() -> None:
    rng = np.random.default_rng(3)
    n = 600
    rets = np.concatenate([rng.normal(0, 0.005, 500), rng.normal(0, 0.08, 100)])
    close = pd.Series(100 * np.cumprod(1 + rets), index=pd.date_range("2018-01-01", periods=n, freq="B"))
    frame = pd.DataFrame({"close": close, "volume": np.full(n, 1e7)})
    engine = RegimeEngine()
    series = engine.classify_series(frame)
    assert series["volatility"].iloc[-1] in (
        VolatilityRegime.HIGH.value,
        VolatilityRegime.EXTREME.value,
    )


def test_low_liquidity_flagged() -> None:
    close = _uptrend()
    frame = pd.DataFrame({"close": close, "volume": np.full(len(close), 100.0)})
    engine = RegimeEngine()
    series = engine.classify_series(frame)
    assert series["liquidity"].iloc[-1] == LiquidityRegime.UNACCEPTABLE.value


def test_policy_validation() -> None:
    with pytest.raises(ValueError):
        RegimeEngine(RegimePolicy(low_vol_percentile=0.9, high_vol_percentile=0.5))
