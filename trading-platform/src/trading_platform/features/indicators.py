"""Point-in-time technical feature engine.

Deterministic NumPy/pandas indicators computed strictly from trailing data.

Point-in-time guarantee
-----------------------
Every indicator at row ``t`` is a function of bars with index ``<= t`` only.
All rolling/ewm operations are trailing; no centered windows, no forward
fills, no revised-history leakage. For daily-bar decision workflows the
feature value at row ``t`` is available after the close of bar ``t`` and is
eligible for decisions taken at/after the next bar's open.

Warmup behavior
---------------
Rows before the required lookback contain NaN. Callers must treat NaN as
"feature unavailable" and must not forward-fill across the warmup boundary.

Each registered feature carries a :class:`FeatureSpec` describing name,
parameters, lookback, source columns, and a content version so research
artifacts can pin the exact feature definition used.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Tuple

import numpy as np
import pandas as pd

FEATURE_ENGINE_VERSION = "1.0.0"


class FeatureError(ValueError):
    """Raised for invalid feature parameters or inputs."""


@dataclass(frozen=True)
class FeatureSpec:
    """Declarative description of a point-in-time feature."""

    name: str
    params: Mapping[str, Any]
    lookback: int
    source_columns: Tuple[str, ...]
    version: str = FEATURE_ENGINE_VERSION
    description: str = ""

    def fingerprint(self) -> str:
        """Stable hash of the full feature definition."""
        canonical = json.dumps(
            {
                "name": self.name,
                "params": dict(sorted(self.params.items())),
                "lookback": self.lookback,
                "source_columns": list(self.source_columns),
                "version": self.version,
            },
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Helpers


def _require_columns(df: pd.DataFrame, columns: Tuple[str, ...]) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise FeatureError(f"missing source columns: {missing}")


def _window(value: int, name: str = "window") -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise FeatureError(f"{name} must be a positive integer, got {value!r}")
    return value


def _true_range(df: pd.DataFrame) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev_close = df["close"].astype(float).shift(1)
    ranges = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1)


# ---------------------------------------------------------------------------
# Indicators. Every function returns a pd.Series aligned to the input index,
# using strictly trailing computations.


def sma(df: pd.DataFrame, window: int) -> pd.Series:
    """Simple moving average of close."""
    _require_columns(df, ("close",))
    w = _window(window)
    return df["close"].astype(float).rolling(w, min_periods=w).mean()


def ema(df: pd.DataFrame, window: int) -> pd.Series:
    """Exponential moving average of close (adjust=False, warmup NaN)."""
    _require_columns(df, ("close",))
    w = _window(window)
    result = df["close"].astype(float).ewm(span=w, adjust=False).mean()
    # EWM has no natural NaN warmup; mask the first w-1 observations.
    result.iloc[: w - 1] = np.nan
    return result


def rsi(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder smoothing) of close."""
    _require_columns(df, ("close",))
    w = _window(window)
    delta = df["close"].astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / w, adjust=False, min_periods=w).mean()
    avg_loss = loss.ewm(alpha=1.0 / w, adjust=False, min_periods=w).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # When avg_loss == 0 and avg_gain > 0, RSI is 100 by definition.
    out = out.where(~((avg_loss == 0.0) & (avg_gain > 0.0)), 100.0)
    return out


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average True Range (Wilder smoothing)."""
    _require_columns(df, ("high", "low", "close"))
    w = _window(window)
    tr = _true_range(df)
    out = tr.ewm(alpha=1.0 / w, adjust=False, min_periods=w).mean()
    return out


def normalized_atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """ATR divided by close (unit-free volatility)."""
    _require_columns(df, ("high", "low", "close"))
    a = atr(df, window)
    close = df["close"].astype(float)
    return a / close.replace(0.0, np.nan)


def realized_volatility(df: pd.DataFrame, window: int = 20, periods_per_year: int = 252) -> pd.Series:
    """Annualized rolling std of daily simple returns."""
    _require_columns(df, ("close",))
    w = _window(window)
    rets = df["close"].astype(float).pct_change()
    return rets.rolling(w, min_periods=w).std(ddof=1) * math.sqrt(periods_per_year)


def rolling_return(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Simple return over the trailing window."""
    _require_columns(df, ("close",))
    w = _window(window)
    return df["close"].astype(float).pct_change(periods=w)


def roc(df: pd.DataFrame, window: int = 12) -> pd.Series:
    """Rate of change (momentum) of close over the trailing window."""
    return rolling_return(df, window)


def rolling_high(df: pd.DataFrame, window: int = 20) -> pd.Series:
    _require_columns(df, ("high",))
    w = _window(window)
    return df["high"].astype(float).rolling(w, min_periods=w).max()


def rolling_low(df: pd.DataFrame, window: int = 20) -> pd.Series:
    _require_columns(df, ("low",))
    w = _window(window)
    return df["low"].astype(float).rolling(w, min_periods=w).min()


def donchian_upper(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Upper Donchian channel: highest high of the PRIOR window (excludes current bar).

    Exclusion of the current bar makes breakout detection well-defined:
    ``close > donchian_upper`` means today's close exceeded the prior N-day high.
    """
    return rolling_high(df, window).shift(1)


def donchian_lower(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Lower Donchian channel: lowest low of the PRIOR window (excludes current bar)."""
    return rolling_low(df, window).shift(1)


def volume_sma(df: pd.DataFrame, window: int = 20) -> pd.Series:
    _require_columns(df, ("volume",))
    w = _window(window)
    return df["volume"].astype(float).rolling(w, min_periods=w).mean()


def relative_volume(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Current volume divided by its trailing average (prior window, excludes current bar)."""
    _require_columns(df, ("volume",))
    w = _window(window)
    avg = volume_sma(df, w).shift(1)
    return df["volume"].astype(float) / avg.replace(0.0, np.nan)


def volume_zscore(df: pd.DataFrame, window: int = 60) -> pd.Series:
    """Z-score of volume against the trailing window (prior bars, excludes current)."""
    _require_columns(df, ("volume",))
    w = _window(window)
    vol = df["volume"].astype(float)
    mean = vol.rolling(w, min_periods=w).mean().shift(1)
    std = vol.rolling(w, min_periods=w).std(ddof=1).shift(1)
    return (vol - mean) / std.replace(0.0, np.nan)


def distance_from_ma(df: pd.DataFrame, window: int = 50) -> pd.Series:
    """(close - SMA) / SMA — signed distance from the moving average."""
    m = sma(df, window)
    close = df["close"].astype(float)
    return (close - m) / m.replace(0.0, np.nan)


def rolling_drawdown(df: pd.DataFrame, window: int = 252) -> pd.Series:
    """Trailing drawdown from the rolling-window high of close (<= 0)."""
    _require_columns(df, ("close",))
    w = _window(window)
    close = df["close"].astype(float)
    peak = close.rolling(w, min_periods=1).max()
    return close / peak.replace(0.0, np.nan) - 1.0


def relative_strength(df: pd.DataFrame, benchmark: pd.Series, window: int = 63) -> pd.Series:
    """Symbol return minus benchmark return over the trailing window.

    ``benchmark`` must be a point-in-time close series aligned to ``df``.
    """
    _require_columns(df, ("close",))
    w = _window(window)
    if len(benchmark) != len(df):
        raise FeatureError("benchmark series must align with frame length")
    sym = df["close"].astype(float).pct_change(periods=w)
    bench = benchmark.astype(float).pct_change(periods=w)
    return sym - bench


def rolling_beta(df: pd.DataFrame, benchmark: pd.Series, window: int = 63) -> pd.Series:
    """Rolling OLS beta of daily symbol returns against benchmark returns."""
    _require_columns(df, ("close",))
    w = _window(window)
    if len(benchmark) != len(df):
        raise FeatureError("benchmark series must align with frame length")
    sym = df["close"].astype(float).pct_change()
    bench = benchmark.astype(float).pct_change()
    cov = sym.rolling(w, min_periods=w).cov(bench)
    var = bench.rolling(w, min_periods=w).var(ddof=1)
    return cov / var.replace(0.0, np.nan)


def rolling_correlation(df: pd.DataFrame, benchmark: pd.Series, window: int = 63) -> pd.Series:
    """Rolling correlation of daily returns against the benchmark."""
    _require_columns(df, ("close",))
    w = _window(window)
    if len(benchmark) != len(df):
        raise FeatureError("benchmark series must align with frame length")
    sym = df["close"].astype(float).pct_change()
    bench = benchmark.astype(float).pct_change()
    return sym.rolling(w, min_periods=w).corr(bench)


def gap_pct(df: pd.DataFrame) -> pd.Series:
    """Overnight gap: open / prior close - 1."""
    _require_columns(df, ("open", "close"))
    return df["open"].astype(float) / df["close"].astype(float).shift(1).replace(0.0, np.nan) - 1.0


def price_percentile(df: pd.DataFrame, window: int = 252) -> pd.Series:
    """Percentile rank of the current close within the trailing window (0..1)."""
    _require_columns(df, ("close",))
    w = _window(window)
    close = df["close"].astype(float)
    return close.rolling(w, min_periods=w).apply(lambda x: (x <= x[-1]).mean(), raw=True)


def volatility_percentile(df: pd.DataFrame, window: int = 20, rank_window: int = 252) -> pd.Series:
    """Percentile rank of current realized volatility within the trailing rank window."""
    rv = realized_volatility(df, window)
    rw = _window(rank_window, "rank_window")
    return rv.rolling(rw, min_periods=rw).apply(lambda x: (x <= x[-1]).mean(), raw=True)


def distance_from_52w_high(df: pd.DataFrame, window: int = 252) -> pd.Series:
    """close / rolling_252d_high - 1 (<= 0)."""
    _require_columns(df, ("high",))
    w = _window(window)
    high = df["high"].astype(float).rolling(w, min_periods=w).max()
    return df["close"].astype(float) / high.replace(0.0, np.nan) - 1.0


# ---------------------------------------------------------------------------
# Registry

IndicatorFn = Callable[..., pd.Series]

# name -> (function, default params, lookback(default), source columns)
_REGISTRY: Dict[str, Tuple[IndicatorFn, Dict[str, Any], int, Tuple[str, ...]]] = {
    "sma": (sma, {"window": 20}, 20, ("close",)),
    "ema": (ema, {"window": 20}, 20, ("close",)),
    "rsi": (rsi, {"window": 14}, 15, ("close",)),
    "atr": (atr, {"window": 14}, 15, ("high", "low", "close")),
    "normalized_atr": (normalized_atr, {"window": 14}, 15, ("high", "low", "close")),
    "realized_volatility": (realized_volatility, {"window": 20}, 21, ("close",)),
    "return": (rolling_return, {"window": 20}, 21, ("close",)),
    "roc": (roc, {"window": 12}, 13, ("close",)),
    "rolling_high": (rolling_high, {"window": 20}, 20, ("high",)),
    "rolling_low": (rolling_low, {"window": 20}, 20, ("low",)),
    "donchian_upper": (donchian_upper, {"window": 20}, 21, ("high",)),
    "donchian_lower": (donchian_lower, {"window": 20}, 21, ("low",)),
    "volume_sma": (volume_sma, {"window": 20}, 20, ("volume",)),
    "relative_volume": (relative_volume, {"window": 20}, 21, ("volume",)),
    "volume_zscore": (volume_zscore, {"window": 60}, 61, ("volume",)),
    "distance_from_ma": (distance_from_ma, {"window": 50}, 50, ("close",)),
    "rolling_drawdown": (rolling_drawdown, {"window": 252}, 1, ("close",)),
    "gap_pct": (gap_pct, {}, 2, ("open", "close")),
    "price_percentile": (price_percentile, {"window": 252}, 252, ("close",)),
    "volatility_percentile": (
        volatility_percentile,
        {"window": 20, "rank_window": 252},
        273,
        ("close",),
    ),
    "distance_from_52w_high": (distance_from_52w_high, {"window": 252}, 252, ("high",)),
}

# Features requiring an external benchmark series.
_BENCHMARK_FEATURES: Dict[str, Tuple[Any, Dict[str, Any], int, Tuple[str, ...]]] = {
    "relative_strength": (relative_strength, {"window": 63}, 64, ("close",)),
    "rolling_beta": (rolling_beta, {"window": 63}, 64, ("close",)),
    "rolling_correlation": (rolling_correlation, {"window": 63}, 64, ("close",)),
}


def available_features() -> Tuple[str, ...]:
    return tuple(sorted([*_REGISTRY, *_BENCHMARK_FEATURES]))


def make_spec(name: str, params: Mapping[str, Any] | None = None) -> FeatureSpec:
    """Build a validated FeatureSpec for a registered feature."""
    entry = _REGISTRY.get(name) or _BENCHMARK_FEATURES.get(name)
    if entry is None:
        raise FeatureError(f"unknown feature: {name!r}")
    _, defaults, lookback, columns = entry
    merged = {**defaults, **dict(params or {})}
    for k, v in merged.items():
        if "window" in k or k in ("rank_window",):
            _window(int(v), k)
    # Lookback grows with any window/rank_window override, plus one bar for
    # features that shift or diff.
    _SHIFTED = {
        "rsi",
        "atr",
        "normalized_atr",
        "relative_volume",
        "volume_zscore",
        "donchian_upper",
        "donchian_lower",
        "relative_strength",
        "rolling_beta",
        "rolling_correlation",
        "gap_pct",
        "return",
        "roc",
    }
    windows = [v for k, v in merged.items() if "window" in k and isinstance(v, int)]
    if windows:
        lookback = max(windows) + (1 if name in _SHIFTED else 0)
    return FeatureSpec(name=name, params=dict(merged), lookback=lookback, source_columns=columns)


@dataclass(frozen=True)
class FeatureFrame:
    """A computed feature matrix plus its provenance."""

    frame: pd.DataFrame
    specs: Tuple[FeatureSpec, ...]
    symbol: str
    extras: Mapping[str, Any] = field(default_factory=dict)

    def snapshot_hash(self, row_label: Any) -> str:
        """Hash of all feature values at ``row_label`` for signal provenance."""
        if row_label not in self.frame.index:
            raise FeatureError(f"row {row_label!r} not present in feature frame")
        row = self.frame.loc[row_label]
        payload = {
            "specs": [s.fingerprint() for s in self.specs],
            "row": {c: (None if pd.isna(row[c]) else float(row[c])) for c in self.frame.columns},
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def compute_features(
    df: pd.DataFrame,
    specs: Tuple[FeatureSpec, ...] | list[FeatureSpec],
    *,
    symbol: str = "",
    benchmark: pd.Series | None = None,
) -> FeatureFrame:
    """Compute all requested features as an aligned DataFrame.

    Columns are named ``"<name>_<param-suffix>"`` where the suffix encodes
    non-default parameters, keeping columns unique across parameterizations.
    """
    columns: Dict[str, pd.Series] = {}
    for spec in specs:
        key = spec.name
        defaults = _REGISTRY.get(key) or _BENCHMARK_FEATURES.get(key)
        if defaults is None:
            raise FeatureError(f"unknown feature: {key!r}")
        if dict(spec.params) == dict(defaults[1]):
            col = key
        else:
            suffix = "_".join(f"{k}{v}" for k, v in sorted(spec.params.items()))
            col = f"{key}_{suffix}"
        if col in columns:
            raise FeatureError(f"duplicate feature column: {col!r}")
        if key in _BENCHMARK_FEATURES:
            if benchmark is None:
                raise FeatureError(f"feature {key!r} requires a benchmark series")
            fn = _BENCHMARK_FEATURES[key][0]
            columns[col] = fn(df, benchmark, **spec.params)
        else:
            fn = _REGISTRY[key][0]
            columns[col] = fn(df, **spec.params)
    frame = pd.DataFrame(columns, index=df.index)
    return FeatureFrame(frame=frame, specs=tuple(specs), symbol=symbol)


def assert_point_in_time(df: pd.DataFrame, feature_fn: IndicatorFn, **params: Any) -> None:
    """Verify a feature function is point-in-time: prefix values must be stable.

    Computes the feature on the full frame and on every strict prefix at a
    sample of cut points; values at the cut must match exactly. Raises
    ``FeatureError`` on any divergence.
    """
    n = len(df)
    if n < 4:
        raise FeatureError("need at least 4 rows to assert point-in-time behavior")
    full = feature_fn(df, **params)
    cuts = sorted({n // 3, n // 2, (2 * n) // 3, n - 1})
    for cut in cuts:
        prefix = feature_fn(df.iloc[: cut + 1], **params)
        a = full.iloc[cut]
        b = prefix.iloc[-1]
        if pd.isna(a) and pd.isna(b):
            continue
        if not np.isclose(float(a), float(b), rtol=0, atol=1e-12, equal_nan=True):
            raise FeatureError(f"feature is not point-in-time at cut {cut}: {a} != {b}")
