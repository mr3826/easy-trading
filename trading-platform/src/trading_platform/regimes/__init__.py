"""Deterministic market-regime classification.

Regimes are computed from point-in-time benchmark data only. No ML, no LLM,
no forward-looking information. Regime output may FILTER or SELECT strategy
families; it must never loosen hard-risk constraints.

Axes
----
- trend: BULLISH / BEARISH / NEUTRAL (benchmark vs its 200-day SMA + slope)
- volatility: LOW / NORMAL / HIGH / EXTREME (realized-vol percentile)
- liquidity: ACCEPTABLE / UNACCEPTABLE (median dollar volume vs floor)
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd

REGIME_ENGINE_VERSION = "1.0.0"


class TrendRegime(str, enum.Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class VolatilityRegime(str, enum.Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


class LiquidityRegime(str, enum.Enum):
    ACCEPTABLE = "acceptable"
    UNACCEPTABLE = "unacceptable"


@dataclass(frozen=True)
class RegimePolicy:
    """Configurable thresholds for regime classification.

    These are POLICY CHOICES (research defaults), not mathematical truths.
    """

    trend_window: int = 200
    slope_window: int = 20
    neutral_band: float = 0.02  # +/-2% around the trend MA is "neutral"
    vol_window: int = 20
    vol_rank_window: int = 252
    low_vol_percentile: float = 0.25
    high_vol_percentile: float = 0.75
    extreme_vol_percentile: float = 0.95
    liquidity_window: int = 20
    min_median_dollar_volume: float = 1_000_000.0
    version: str = REGIME_ENGINE_VERSION


@dataclass(frozen=True)
class RegimeClassification:
    """Regime state valid for decisions made at/after ``as_of``'s next open."""

    as_of: Any
    trend: TrendRegime
    volatility: VolatilityRegime
    liquidity: LiquidityRegime
    trend_distance: float  # benchmark close / trend MA - 1
    trend_slope: float  # per-bar slope of the trend MA
    vol_percentile: float
    median_dollar_volume: float
    policy_version: str = REGIME_ENGINE_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "as_of": str(self.as_of),
            "trend": self.trend.value,
            "volatility": self.volatility.value,
            "liquidity": self.liquidity.value,
            "trend_distance": self.trend_distance,
            "trend_slope": self.trend_slope,
            "vol_percentile": self.vol_percentile,
            "median_dollar_volume": self.median_dollar_volume,
            "policy_version": self.policy_version,
        }


def classify_trend(benchmark_close: pd.Series, policy: RegimePolicy) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Return (trend label, distance, slope) series for a benchmark close series."""
    close = benchmark_close.astype(float)
    ma = close.rolling(policy.trend_window, min_periods=policy.trend_window).mean()
    distance = close / ma.replace(0.0, np.nan) - 1.0
    slope = ma.diff(policy.slope_window) / policy.slope_window
    trend = pd.Series(TrendRegime.NEUTRAL.value, index=close.index, dtype=object)
    valid = distance.notna() & slope.notna()
    bullish = valid & (distance > policy.neutral_band) & (slope > 0)
    bearish = valid & (distance < -policy.neutral_band) & (slope < 0)
    trend = trend.mask(bullish, TrendRegime.BULLISH.value).mask(bearish, TrendRegime.BEARISH.value)
    return trend, distance, slope


def classify_volatility(benchmark_close: pd.Series, policy: RegimePolicy) -> Tuple[pd.Series, pd.Series]:
    """Return (volatility label, vol percentile) from benchmark daily returns."""
    close = benchmark_close.astype(float)
    rets = close.pct_change()
    rv = rets.rolling(policy.vol_window, min_periods=policy.vol_window).std(ddof=1)
    pct = rv.rolling(policy.vol_rank_window, min_periods=policy.vol_rank_window).apply(
        lambda x: (x <= x[-1]).mean(), raw=True
    )
    label = pd.Series(VolatilityRegime.NORMAL.value, index=close.index, dtype=object)
    valid = pct.notna()
    label = label.mask(valid & (pct < policy.low_vol_percentile), VolatilityRegime.LOW.value)
    label = label.mask(valid & (pct >= policy.high_vol_percentile), VolatilityRegime.HIGH.value)
    label = label.mask(valid & (pct >= policy.extreme_vol_percentile), VolatilityRegime.EXTREME.value)
    return label, pct


def classify_liquidity(dollar_volume: pd.Series, policy: RegimePolicy) -> Tuple[pd.Series, pd.Series]:
    """Return (liquidity label, median dollar volume) from a dollar-volume series."""
    dv = dollar_volume.astype(float)
    median = dv.rolling(policy.liquidity_window, min_periods=policy.liquidity_window).median()
    label = pd.Series(LiquidityRegime.UNACCEPTABLE.value, index=dv.index, dtype=object)
    label = label.mask(median >= policy.min_median_dollar_volume, LiquidityRegime.ACCEPTABLE.value)
    return label, median


class RegimeEngine:
    """Computes point-in-time regime series from benchmark daily bars."""

    def __init__(self, policy: RegimePolicy | None = None) -> None:
        self.policy = policy or RegimePolicy()
        if policy is not None and not isinstance(policy, RegimePolicy):
            raise TypeError("policy must be a RegimePolicy")
        p = self.policy
        if not (0.0 < p.low_vol_percentile < p.high_vol_percentile < p.extreme_vol_percentile <= 1.0):
            raise ValueError("volatility percentiles must be strictly ordered in (0, 1]")
        if p.trend_window < 1 or p.slope_window < 1 or p.vol_window < 1:
            raise ValueError("windows must be positive")

    def classify_series(self, benchmark: pd.DataFrame) -> pd.DataFrame:
        """Classify regimes for every row of a benchmark OHLCV frame.

        Output columns: trend, volatility, liquidity, trend_distance,
        trend_slope, vol_percentile, median_dollar_volume. Rows are NaN/
        UNACCEPTABLE-safe during warmup.
        """
        for col in ("close", "volume"):
            if col not in benchmark.columns:
                raise ValueError(f"benchmark frame missing column {col!r}")
        trend, distance, slope = classify_trend(benchmark["close"], self.policy)
        vol, vol_pct = classify_volatility(benchmark["close"], self.policy)
        liq, med_dv = classify_liquidity(
            (benchmark["close"].astype(float) * benchmark["volume"].astype(float)), self.policy
        )
        return pd.DataFrame(
            {
                "trend": trend,
                "volatility": vol,
                "liquidity": liq,
                "trend_distance": distance,
                "trend_slope": slope,
                "vol_percentile": vol_pct,
                "median_dollar_volume": med_dv,
            },
            index=benchmark.index,
        )

    def classify_at(self, benchmark: pd.DataFrame, as_of: Any) -> RegimeClassification:
        """Point-in-time classification as of a single row label."""
        series = self.classify_series(benchmark)
        if as_of not in series.index:
            raise KeyError(f"as_of {as_of!r} not present in benchmark index")
        row = series.loc[as_of]
        return RegimeClassification(
            as_of=as_of,
            trend=TrendRegime(row["trend"]),
            volatility=VolatilityRegime(row["volatility"]),
            liquidity=LiquidityRegime(row["liquidity"]),
            trend_distance=float(row["trend_distance"]) if pd.notna(row["trend_distance"]) else float("nan"),
            trend_slope=float(row["trend_slope"]) if pd.notna(row["trend_slope"]) else float("nan"),
            vol_percentile=float(row["vol_percentile"]) if pd.notna(row["vol_percentile"]) else float("nan"),
            median_dollar_volume=(float(row["median_dollar_volume"]) if pd.notna(row["median_dollar_volume"]) else 0.0),
            policy_version=self.policy.version,
        )
