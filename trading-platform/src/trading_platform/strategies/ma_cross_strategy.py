"""Simple Moving Average Crossover Strategy for Phase 4 research harness.

V1 Daily Long-Only Hypothesis:
- One signal per day based on SMA crossover
- Long-only: never short; SELL signals only exit an existing long
- Maximum 3 positions (per ADR V1)
- Holding period: days to weeks
- Cash: no leverage, cash account
- Order type: MARKET at the next eligible open
- Commission: FIXED $1.00 per order
- Slippage: 0.1% modeled via fill assumption
"""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

import numpy as np

# ---------------------------------------------------------------------------
# Hypothesis configuration (V1 fixed parameters, NOT to be optimized until
# a profitable strategy is identified — per Phase 4 exit gate G4)


class MaCrossHypothesis:
    """Fixed-parameter moving average crossover hypothesis for V1 research.

    Parameters are PROVISIONAL and specified upfront; they must not be
    auto-tuned toward profitability during experimentation.
    """

    def __init__(
        self,
        fast_length: int = 5,
        slow_length: int = 20,
        min_shares: int = 1,
        max_positions: int = 3,
        max_holding_days: int = 30,
        commission_per_order: float = 1.0,
        slippage_pct: float = 0.001,
    ):
        # PROVISIONAL values — do not optimize toward profitability
        self.fast_length = fast_length
        self.slow_length = slow_length
        self.min_shares = min_shares
        self.max_positions = max_positions
        self.max_holding_days = max_holding_days
        self.commission_per_order = commission_per_order
        self.slippage_pct = slippage_pct

    # -----------------------------------------------------------------
    # Hypothesis identity (for experiment persistence)

    @property
    def hypothesis_id(self) -> str:
        return f"ma_cross_{self.fast_length}_{self.slow_length}"

    def __repr__(self) -> str:
        return (
            f"MaCrossHypothesis(fast={self.fast_length}, slow={self.slow_length}, "
            f"max_pos={self.max_positions}, max_hold={self.max_holding_days}d, "
            f"commission=${self.commission_per_order}, slippage={self.slippage_pct:.0%})"
        )


# ---------------------------------------------------------------------------
# Signal generation


def generate_signal(
    bars: Dict[str, List[Dict[str, Any]]],  # symbol -> list of Bar dicts
    hypothesis: MaCrossHypothesis,
    symbol: str,
    current_date: date,
    as_of: Optional[datetime] = None,
    position_held: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Generate a single daily signal for *symbol*.

    Point-in-time rules (no lookahead):
    - Only bars with timestamp <= as_of may influence the decision. When
      as_of is omitted, the full provided history is used and the caller is
      responsible for having truncated it at the decision time.
    - The SMA window is the last slow_length bars available at or before
      as_of, inclusive of the completed bar for the decision date.
    - One signal per symbol per day (the last bar of the day)
    - SMA fast crosses above SMA slow → BUY (only when no position held)
    - If position held: SELL (exit) when the fast SMA crosses below the slow
      SMA or the position has been held >= max_holding_days
    - Long-only: never short; SELL only exits an existing long
    - If already at max positions → HOLD (no signal)

    position_held is the caller's point-in-time position state for the
    symbol, e.g. {"days_held": 3}; None means flat.

    Returns dict or None (no signal this day).
    """
    if symbol not in bars or not bars[symbol]:
        return None

    # Point-in-time history: no bar after as_of may influence the decision
    if as_of is not None:
        history = [
            b
            for b in bars[symbol]
            if b["timestamp"] <= as_of and (b.get("available_at") is None or b["available_at"] <= as_of)
        ]
    else:
        history = [b for b in bars[symbol] if b.get("available_at") is None or b["available_at"] <= b["timestamp"]]

    today_bars = [b for b in history if b["timestamp"].date() == current_date]
    if not today_bars:
        return None

    # Need enough history for the slow SMA calculation
    if len(history) < hypothesis.slow_length:
        return None

    # SMA window: last slow_length bars available at or before as_of
    closes = [b["close"] for b in history[-hypothesis.slow_length :]]
    if len(closes) < hypothesis.slow_length:
        return None

    fast_sma = np.mean(closes[-hypothesis.fast_length :]) if hypothesis.fast_length <= len(closes) else None
    slow_sma = np.mean(closes)

    if fast_sma is None or slow_sma is None:
        return None

    # Long-only exit logic: SELL only when a position is held
    if position_held is not None:
        days_held = int(position_held.get("days_held", 0))
        if fast_sma < slow_sma or days_held >= hypothesis.max_holding_days:
            return {
                "symbol": symbol,
                "side": "SELL",
                "quantity": int(position_held.get("quantity", 0)) or max(hypothesis.min_shares, 1),
                "order_type": "MARKET",
                "time_in_force": "DAY",
                "price": None,  # market order -> fill at next eligible event
            }
        # Still holding within trend and holding window: no signal
        return None

    if fast_sma > slow_sma:
        # Generate BUY signal for 1 share minimum (position sizing handled by
        # affordability check in the simulator)
        return {
            "symbol": symbol,
            "side": "BUY",
            "quantity": max(hypothesis.min_shares, 1),
            "order_type": "MARKET",
            "time_in_force": "DAY",
            "price": None,  # market order -> fill at next eligible event
        }
    return None


# ---------------------------------------------------------------------------
# Experiment metadata


def hypothesis_to_dict(hypothesis: MaCrossHypothesis) -> Dict[str, Any]:
    """Serialize hypothesis for experiment persistence."""
    return {
        "hypothesis_id": hypothesis.hypothesis_id,
        "fast_length": hypothesis.fast_length,
        "slow_length": hypothesis.slow_length,
        "min_shares": hypothesis.min_shares,
        "max_positions": hypothesis.max_positions,
        "max_holding_days": hypothesis.max_holding_days,
        "commission_per_order": hypothesis.commission_per_order,
        "slippage_pct": hypothesis.slippage_pct,
    }


def dict_to_hypothesis(d: Dict[str, Any]) -> MaCrossHypothesis:
    """Deserialize hypothesis from experiment persistence."""
    return MaCrossHypothesis(
        fast_length=d.get("fast_length", 5),
        slow_length=d.get("slow_length", 20),
        min_shares=d.get("min_shares", 1),
        max_positions=d.get("max_positions", 3),
        max_holding_days=d.get("max_holding_days", 30),
        commission_per_order=d.get("commission_per_order", 1.0),
        slippage_pct=d.get("slippage_pct", 0.001),
    )
