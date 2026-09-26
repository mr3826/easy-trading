"""Strategy performance-drift monitoring.

Approved strategies must not be trusted forever. Forward (shadow/paper)
observations are compared against the research distributions captured at
promotion time; degradation transitions the strategy through

    HEALTHY -> WATCH -> DEGRADED -> DISABLED

A DISABLED strategy blocks NEW positions (exits of existing positions are
never blocked). Drift monitoring never retunes parameters: a retuned
strategy is a NEW strategy version requiring full validation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np

DRIFT_MONITOR_VERSION = "1.0.0"

STATE_HEALTHY = "HEALTHY"
STATE_WATCH = "WATCH"
STATE_DEGRADED = "DEGRADED"
STATE_DISABLED = "DISABLED"


@dataclass(frozen=True)
class DriftExpectations:
    """Research-time reference distribution (captured at promotion)."""

    expectancy: float
    win_rate: float
    sharpe: float
    profit_factor: float
    max_drawdown: float  # negative
    avg_trades_per_month: float
    version: str = DRIFT_MONITOR_VERSION


@dataclass(frozen=True)
class DriftPolicy:
    """Drift tolerances. POLICY CHOICES."""

    min_forward_trades: int = 10  # don't judge before this sample
    watch_expectancy_ratio: float = 0.5  # forward/research below this -> WATCH
    degraded_expectancy_ratio: float = 0.0  # <= 0 -> DEGRADED
    watch_win_rate_drop: float = 0.10  # absolute drop -> WATCH
    degraded_win_rate_drop: float = 0.20  # absolute drop -> DEGRADED
    degraded_drawdown_multiple: float = 1.5  # forward DD > 1.5x research -> DEGRADED
    watch_sharpe_floor: float = 0.0
    degraded_consecutive_losses: int = 8
    version: str = DRIFT_MONITOR_VERSION


@dataclass(frozen=True)
class DriftStatus:
    strategy_id: str
    state: str
    reasons: Tuple[str, ...]
    forward: Mapping[str, float]
    expectations_met: bool
    version: str = DRIFT_MONITOR_VERSION

    def allows_new_positions(self) -> bool:
        return self.state in (STATE_HEALTHY, STATE_WATCH)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "state": self.state,
            "reasons": list(self.reasons),
            "forward": dict(self.forward),
            "expectations_met": self.expectations_met,
            "version": self.version,
        }


def _forward_metrics(trade_pnls: List[float]) -> Dict[str, float]:
    arr = np.asarray(trade_pnls, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {
            "trade_count": 0.0,
            "expectancy": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_consecutive_losses": 0.0,
        }
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    streak = longest = 0
    for v in arr:
        streak = streak + 1 if v < 0 else 0
        longest = max(longest, streak)
    return {
        "trade_count": float(arr.size),
        "expectancy": float(arr.mean()),
        "win_rate": float(wins.size / arr.size),
        "profit_factor": float(wins.sum() / abs(losses.sum())) if losses.size and abs(losses.sum()) > 0 else 0.0,
        "max_consecutive_losses": float(longest),
    }


class DriftMonitor:
    """Tracks forward performance per strategy and classifies drift state."""

    def __init__(self, policy: Optional[DriftPolicy] = None) -> None:
        self.policy = policy or DriftPolicy()
        self._expectations: Dict[str, DriftExpectations] = {}
        self._forward_trades: Dict[str, List[float]] = {}
        self._manual_disable: Dict[str, str] = {}

    def register(self, strategy_id: str, expectations: DriftExpectations) -> None:
        self._expectations[strategy_id] = expectations
        self._forward_trades.setdefault(strategy_id, [])

    def record_trade(self, strategy_id: str, net_pnl: float) -> None:
        if not math.isfinite(net_pnl):
            raise ValueError("net_pnl must be finite")
        if strategy_id not in self._expectations:
            raise KeyError(f"strategy {strategy_id!r} has no registered expectations")
        self._forward_trades[strategy_id].append(float(net_pnl))

    def disable(self, strategy_id: str, reason: str) -> None:
        self._manual_disable[strategy_id] = reason

    def status(self, strategy_id: str) -> DriftStatus:
        exp = self._expectations.get(strategy_id)
        if exp is None:
            return DriftStatus(strategy_id, STATE_DISABLED, ("no registered expectations",), {}, False)
        forward = _forward_metrics(self._forward_trades.get(strategy_id, []))
        reasons: List[str] = []
        state = STATE_HEALTHY

        if strategy_id in self._manual_disable:
            return DriftStatus(strategy_id, STATE_DISABLED, (self._manual_disable[strategy_id],), forward, False)

        if forward["trade_count"] < self.policy.min_forward_trades:
            reasons.append("insufficient forward sample; treating as early")
            # Early life: only severe signals can escalate.
            if forward["max_consecutive_losses"] >= self.policy.degraded_consecutive_losses:
                state = STATE_DEGRADED
                reasons.append("severe consecutive losses in early forward sample")
            return DriftStatus(strategy_id, state, tuple(reasons), forward, True)

        # Expectancy ratio vs research.
        if exp.expectancy > 0:
            ratio = forward["expectancy"] / exp.expectancy
            if ratio <= self.policy.degraded_expectancy_ratio:
                state = STATE_DEGRADED
                reasons.append(f"forward expectancy collapsed (ratio {ratio:.2f})")
            elif ratio < self.policy.watch_expectancy_ratio:
                state = max_state(state, STATE_WATCH)
                reasons.append(f"forward expectancy at {ratio:.0%} of research")
        # Win-rate drop.
        drop = exp.win_rate - forward["win_rate"]
        if drop >= self.policy.degraded_win_rate_drop:
            state = STATE_DEGRADED
            reasons.append(f"win rate dropped {drop:.0%} vs research")
        elif drop >= self.policy.watch_win_rate_drop:
            state = max_state(state, STATE_WATCH)
            reasons.append(f"win rate dropped {drop:.0%}")
        # Loss streak.
        if forward["max_consecutive_losses"] >= self.policy.degraded_consecutive_losses:
            state = max_state(state, STATE_DEGRADED)
            reasons.append("consecutive-loss threshold breached")

        met = state in (STATE_HEALTHY,)
        return DriftStatus(strategy_id, state, tuple(reasons), forward, met)


_ORDER = {STATE_HEALTHY: 0, STATE_WATCH: 1, STATE_DEGRADED: 2, STATE_DISABLED: 3}


def max_state(a: str, b: str) -> str:
    return a if _ORDER[a] >= _ORDER[b] else b
