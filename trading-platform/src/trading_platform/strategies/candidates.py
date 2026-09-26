"""Independently testable strategy hypotheses (Candidates A/B/C).

Each family is a deterministic rule set over the point-in-time feature
engine and regime engine. Families are intentionally separate so each can be
individually validated, rejected, or promoted. The existing MA crossover in
``ma_cross_strategy`` remains the untouched control/baseline (Candidate D).

All candidates are LONG-ONLY and emit :class:`SignalEvidence`. Entry prices
are planned at the NEXT bar's open; stops are ATR-based; the strategy never
sizes positions (that is ``trade_planning``'s job) and never overrides risk.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd
from trading_platform.features.indicators import FeatureSpec, make_spec
from trading_platform.regimes import RegimeClassification
from trading_platform.trade_planning import SignalEvidence

CANDIDATE_STRATEGY_VERSION = "1.0.0"


@dataclass(frozen=True)
class CandidateParams:
    """Base parameters shared by candidate families (POLICY CHOICES)."""

    atr_window: int = 14
    atr_stop_multiple: float = 2.5
    min_median_dollar_volume: float = 1_000_000.0
    max_normalized_atr: float = 0.08  # volatility filter
    max_holding_days: int = 30
    volume_window: int = 20


def _finite(value: Any) -> bool:
    return value is not None and isinstance(value, (int, float, np.floating)) and math.isfinite(float(value))


def _hash_row(row: pd.Series) -> str:
    import hashlib
    import json

    payload = {c: (None if pd.isna(row[c]) else float(row[c])) for c in row.index}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _dollar_volume_ok(med_dv: float, floor: float) -> bool:
    return _finite(med_dv) and float(med_dv) >= floor


# ---------------------------------------------------------------------------
# Candidate A — Trend + Relative Strength


@dataclass(frozen=True)
class TrendRelativeStrengthParams(CandidateParams):
    momentum_window: int = 63
    rs_window: int = 63
    min_relative_strength: float = 0.0
    trend_ma_window: int = 100
    allowed_trends: Tuple[str, ...] = ("bullish",)
    allowed_volatility: Tuple[str, ...] = ("low", "normal", "high")

    @property
    def strategy_id(self) -> str:
        return f"trend_rs_m{self.momentum_window}_rs{self.rs_window}_atr{self.atr_stop_multiple}"

    def feature_specs(self) -> List[FeatureSpec]:
        return [
            make_spec("sma", {"window": self.trend_ma_window}),
            make_spec("return", {"window": self.momentum_window}),
            make_spec("relative_strength", {"window": self.rs_window}),
            make_spec("atr", {"window": self.atr_window}),
            make_spec("normalized_atr", {"window": self.atr_window}),
            make_spec("volume_sma", {"window": self.volume_window}),
        ]


def trend_relative_strength_signal(
    symbol: str,
    features: pd.DataFrame,
    regime: Optional[RegimeClassification],
    params: TrendRelativeStrengthParams,
    decision_timestamp: str,
) -> Optional[SignalEvidence]:
    """BUY when market trend regime allows, symbol momentum and relative
    strength are positive, volatility and liquidity filters pass."""
    p = params
    row = features.iloc[-1]
    rejections: List[str] = []

    regime_map: Mapping[str, Any] = regime.to_dict() if regime else {}

    if regime is None:
        rejections.append("regime unavailable")
    else:
        if regime.trend.value not in p.allowed_trends:
            rejections.append(f"trend regime {regime.trend.value} not allowed")
        if regime.volatility.value not in p.allowed_volatility:
            rejections.append(f"volatility regime {regime.volatility.value} not allowed")
        if regime.liquidity.value != "acceptable":
            rejections.append("liquidity regime unacceptable")

    sma_col = f"sma_window{p.trend_ma_window}"
    ret_col = f"return_window{p.momentum_window}"
    rs_col = f"relative_strength_window{p.rs_window}"
    atr_col = f"atr_window{p.atr_window}"
    natr_col = f"normalized_atr_window{p.atr_window}"

    close: Optional[float] = None
    if "close" in row and _finite(row["close"]):
        close = float(row["close"])
    if close is None:
        rejections.append("close unavailable")
    trend_ma = row.get(sma_col)
    momentum = row.get(ret_col)
    rs = row.get(rs_col)
    atr_v = row.get(atr_col)
    natr = row.get(natr_col)

    trend_ok = _finite(trend_ma) and close is not None and close > float(trend_ma)
    if not trend_ok:
        rejections.append("price below trend filter")
    if not (_finite(momentum) and float(momentum) > 0):
        rejections.append("momentum not positive")
    if not (_finite(rs) and float(rs) > p.min_relative_strength):
        rejections.append("relative strength insufficient")
    if not _finite(atr_v):
        rejections.append("atr unavailable")
    if _finite(natr) and float(natr) > p.max_normalized_atr:
        rejections.append("volatility filter breached")

    if close is None or not _finite(atr_v):
        base = SignalEvidence(
            symbol=symbol,
            decision_timestamp=decision_timestamp,
            strategy_id=p.strategy_id,
            strategy_version=CANDIDATE_STRATEGY_VERSION,
            market_regime=regime_map,
            raw_signal="HOLD",
            trend_confirmation=bool(trend_ok),
            relative_strength=float(rs) if _finite(rs) else float("nan"),
            volatility_state=regime.volatility.value if regime else "unknown",
            liquidity_state=regime.liquidity.value if regime else "unknown",
            volume_confirmation=False,
            feature_snapshot_hash=_hash_row(row),
            expected_entry=close or 0.0,
            initial_stop=close or 0.0,
            planned_exit="atr_trailing",
            estimated_transaction_cost=0.0,
            signal_score=0.0,
            rejection_reasons=tuple(rejections or ["no signal"]),
        )
        return base

    stop = float(close) - p.atr_stop_multiple * float(atr_v)
    score = float(rs) if _finite(rs) else 0.0
    return SignalEvidence(
        symbol=symbol,
        decision_timestamp=decision_timestamp,
        strategy_id=p.strategy_id,
        strategy_version=CANDIDATE_STRATEGY_VERSION,
        market_regime=regime_map,
        raw_signal="BUY" if not rejections else "HOLD",
        trend_confirmation=bool(trend_ok),
        relative_strength=float(rs) if _finite(rs) else float("nan"),
        volatility_state=regime.volatility.value if regime else "unknown",
        liquidity_state=regime.liquidity.value if regime else "unknown",
        volume_confirmation=False,
        feature_snapshot_hash=_hash_row(row),
        expected_entry=float(close),
        initial_stop=max(stop, 0.01),
        planned_exit="atr_trailing",
        estimated_transaction_cost=0.0,
        signal_score=score,
        rejection_reasons=tuple(rejections),
    )


# ---------------------------------------------------------------------------
# Candidate B — Breakout + Volume Confirmation


@dataclass(frozen=True)
class BreakoutVolumeParams(CandidateParams):
    breakout_window: int = 55
    min_relative_volume: float = 1.5
    trend_ma_window: int = 50
    allowed_trends: Tuple[str, ...] = ("bullish", "neutral")
    allowed_volatility: Tuple[str, ...] = ("low", "normal", "high")

    @property
    def strategy_id(self) -> str:
        return f"breakout_volume_b{self.breakout_window}_rv{self.min_relative_volume}"

    def feature_specs(self) -> List[FeatureSpec]:
        return [
            make_spec("donchian_upper", {"window": self.breakout_window}),
            make_spec("relative_volume", {"window": self.volume_window}),
            make_spec("sma", {"window": self.trend_ma_window}),
            make_spec("atr", {"window": self.atr_window}),
            make_spec("normalized_atr", {"window": self.atr_window}),
        ]


def breakout_volume_signal(
    symbol: str,
    features: pd.DataFrame,
    regime: Optional[RegimeClassification],
    params: BreakoutVolumeParams,
    decision_timestamp: str,
) -> SignalEvidence:
    """BUY when close breaks the prior N-bar Donchian high with volume
    confirmation, inside an allowed trend/volatility/liquidity regime."""
    p = params
    row = features.iloc[-1]
    rejections: List[str] = []
    regime_map: Mapping[str, Any] = regime.to_dict() if regime else {}

    if regime is None:
        rejections.append("regime unavailable")
    else:
        if regime.trend.value not in p.allowed_trends:
            rejections.append(f"trend regime {regime.trend.value} not allowed")
        if regime.volatility.value not in p.allowed_volatility:
            rejections.append(f"volatility regime {regime.volatility.value} not allowed")
        if regime.liquidity.value != "acceptable":
            rejections.append("liquidity regime unacceptable")

    don_col = f"donchian_upper_window{p.breakout_window}"
    rvol_col = f"relative_volume_window{p.volume_window}"
    atr_col = f"atr_window{p.atr_window}"
    natr_col = f"normalized_atr_window{p.atr_window}"

    close = float(row["close"]) if "close" in row and _finite(row["close"]) else None
    channel = row.get(don_col)
    rvol = row.get(rvol_col)
    atr_v = row.get(atr_col)
    natr = row.get(natr_col)

    breakout = _finite(channel) and close is not None and close > float(channel)
    if not breakout:
        rejections.append("no channel breakout")
    volume_ok = _finite(rvol) and float(rvol) >= p.min_relative_volume
    if not volume_ok:
        rejections.append("volume confirmation missing")
    if not _finite(atr_v):
        rejections.append("atr unavailable")
    if close is None:
        rejections.append("close unavailable")
    if _finite(natr) and float(natr) > p.max_normalized_atr:
        rejections.append("volatility filter breached")

    entry = float(close) if close is not None else 0.0
    stop = entry - p.atr_stop_multiple * float(atr_v) if (_finite(atr_v) and close is not None) else entry
    score = float(rvol) if _finite(rvol) else 0.0
    return SignalEvidence(
        symbol=symbol,
        decision_timestamp=decision_timestamp,
        strategy_id=p.strategy_id,
        strategy_version=CANDIDATE_STRATEGY_VERSION,
        market_regime=regime_map,
        raw_signal="BUY" if not rejections else "HOLD",
        trend_confirmation=bool(breakout),
        relative_strength=float("nan"),
        volatility_state=regime.volatility.value if regime else "unknown",
        liquidity_state=regime.liquidity.value if regime else "unknown",
        volume_confirmation=bool(volume_ok),
        feature_snapshot_hash=_hash_row(row),
        expected_entry=entry,
        initial_stop=max(stop, 0.01) if close is not None else 0.0,
        planned_exit="atr_trailing",
        estimated_transaction_cost=0.0,
        signal_score=score,
        rejection_reasons=tuple(rejections),
    )


# ---------------------------------------------------------------------------
# Candidate C — Trend Pullback


@dataclass(frozen=True)
class TrendPullbackParams(CandidateParams):
    long_ma_window: int = 200
    medium_ma_window: int = 50
    rsi_window: int = 3
    rsi_oversold: float = 20.0
    recovery_rsi: float = 30.0  # stabilization trigger: RSI crosses back up
    allowed_trends: Tuple[str, ...] = ("bullish",)
    allowed_volatility: Tuple[str, ...] = ("low", "normal")

    @property
    def strategy_id(self) -> str:
        return f"trend_pullback_ma{self.long_ma_window}_rsi{self.rsi_window}_{int(self.rsi_oversold)}"

    def feature_specs(self) -> List[FeatureSpec]:
        return [
            make_spec("sma", {"window": self.long_ma_window}),
            make_spec("sma", {"window": self.medium_ma_window}),
            make_spec("rsi", {"window": self.rsi_window}),
            make_spec("atr", {"window": self.atr_window}),
            make_spec("normalized_atr", {"window": self.atr_window}),
            make_spec("volume_sma", {"window": self.volume_window}),
        ]


def trend_pullback_signal(
    symbol: str,
    features: pd.DataFrame,
    regime: Optional[RegimeClassification],
    params: TrendPullbackParams,
    decision_timestamp: str,
) -> SignalEvidence:
    """BUY a stabilizing pullback inside a long-term uptrend.

    Requires: close > long MA, medium MA > long MA (trend structure), the
    PRIOR bar's RSI below the oversold threshold, and the current RSI back
    above the recovery threshold (stabilization/recovery trigger).
    """
    p = params
    if len(features) == 0:
        raise ValueError("pullback signal requires at least one bar")
    row = features.iloc[-1]
    # The first decision day may legitimately have a single bar: the prior-bar
    # RSI comparison is impossible then, so the signal FAILS its conditions
    # (rejected HOLD below) instead of crashing the caller's backtest.
    prev = features.iloc[-2] if len(features) >= 2 else row
    rejections: List[str] = []
    regime_map: Mapping[str, Any] = regime.to_dict() if regime else {}

    if regime is None:
        rejections.append("regime unavailable")
    else:
        if regime.trend.value not in p.allowed_trends:
            rejections.append(f"trend regime {regime.trend.value} not allowed")
        if regime.volatility.value not in p.allowed_volatility:
            rejections.append(f"volatility regime {regime.volatility.value} not allowed")
        if regime.liquidity.value != "acceptable":
            rejections.append("liquidity regime unacceptable")

    long_col = f"sma_window{p.long_ma_window}"
    med_col = f"sma_window{p.medium_ma_window}"
    rsi_col = f"rsi_window{p.rsi_window}"
    atr_col = f"atr_window{p.atr_window}"
    natr_col = f"normalized_atr_window{p.atr_window}"

    close = float(row["close"]) if "close" in row and _finite(row["close"]) else None
    long_ma = row.get(long_col)
    med_ma = row.get(med_col)
    rsi_now = row.get(rsi_col)
    rsi_prev = prev.get(rsi_col)
    atr_v = row.get(atr_col)
    natr = row.get(natr_col)

    long_ok = _finite(long_ma) and close is not None and close > float(long_ma)
    med_ok = _finite(med_ma) and _finite(long_ma) and float(med_ma) > float(long_ma)
    pullback = _finite(rsi_prev) and float(rsi_prev) < p.rsi_oversold
    recovery = _finite(rsi_now) and float(rsi_now) >= p.recovery_rsi

    if not long_ok:
        rejections.append("long-term trend not positive")
    if not med_ok:
        rejections.append("medium-term trend structure not positive")
    if not pullback:
        rejections.append("no pullback condition")
    if not recovery:
        rejections.append("no stabilization trigger")
    if not _finite(atr_v):
        rejections.append("atr unavailable")
    if close is None:
        rejections.append("close unavailable")
    if _finite(natr) and float(natr) > p.max_normalized_atr:
        rejections.append("volatility filter breached")

    entry = float(close) if close is not None else 0.0
    stop = entry - p.atr_stop_multiple * float(atr_v) if (_finite(atr_v) and close is not None) else entry
    # Score by depth of pullback: deeper oversold + recovery ranks higher.
    score = max(0.0, p.recovery_rsi - (float(rsi_prev) if _finite(rsi_prev) else p.recovery_rsi))
    return SignalEvidence(
        symbol=symbol,
        decision_timestamp=decision_timestamp,
        strategy_id=p.strategy_id,
        strategy_version=CANDIDATE_STRATEGY_VERSION,
        market_regime=regime_map,
        raw_signal="BUY" if not rejections else "HOLD",
        trend_confirmation=bool(long_ok and med_ok),
        relative_strength=float("nan"),
        volatility_state=regime.volatility.value if regime else "unknown",
        liquidity_state=regime.liquidity.value if regime else "unknown",
        volume_confirmation=False,
        feature_snapshot_hash=_hash_row(row),
        expected_entry=entry,
        initial_stop=max(stop, 0.01) if close is not None else 0.0,
        planned_exit="atr_trailing",
        estimated_transaction_cost=0.0,
        signal_score=score,
        rejection_reasons=tuple(rejections),
    )


# ---------------------------------------------------------------------------
# Family registry


STRATEGY_FAMILIES: Dict[str, Any] = {
    "trend_relative_strength": (TrendRelativeStrengthParams, trend_relative_strength_signal),
    "breakout_volume": (BreakoutVolumeParams, breakout_volume_signal),
    "trend_pullback": (TrendPullbackParams, trend_pullback_signal),
    "ma_cross_baseline": ("MaCrossHypothesis", None),  # control; lives in ma_cross_strategy
}


def registered_strategy_ids() -> Tuple[str, ...]:
    return tuple(sorted(STRATEGY_FAMILIES))
