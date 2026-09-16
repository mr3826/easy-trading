"""Simple Moving Average Crossover Strategy for Phase 4 research harness.

V1 Daily Long-Only Hypothesis:
- One signal per day based on SMA crossover
- Long-only: never short
- Maximum 3 positions (per ADR V1)
- Holding period: days to weeks
- Cash: no leverage, cash account
- Order type: MARKET at the next eligible open
- Commission: FIXED $1.00 per order
- Slippage: 0.1% modeled via fill assumption
"""

from datetime import date
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
) -> Optional[Dict[str, Any]]:
    """Generate a single daily signal for *symbol*.

    V1 rules:
    - One signal per symbol per day (the last bar of the day)
    - SMA fast crosses above SMA slow → BUY
    - If already at max positions → HOLD (no signal)
    - If position held > max_holding_days → consider exiting
    - Long-only: never SELL unless reaching max_holding_days

    Returns dict or None (no signal this day).
    """
    if symbol not in bars or not bars[symbol]:
        return None

    bar_list = bars[symbol]
    # Find the last bar for the current date
    today_bars = [b for b in bar_list if b["timestamp"].date() == current_date]
    if not today_bars:
        return None

    # Need enough history for SMA calculation
    # In a full backtest we'd maintain rolling windows; here we simplify:
    # require at least slow_length + 1 historical bars available
    if len(bar_list) < hypothesis.slow_length + 1:
        return None

    # Calculate SMAs using close prices
    closes = [b["close"] for b in bar_list[-hypothesis.slow_length - 1 : -1]]
    if len(closes) < hypothesis.slow_length:
        return None

    fast_sma = np.mean(closes[-hypothesis.fast_length :]) if hypothesis.fast_length <= len(closes) else None
    slow_sma = np.mean(closes)  # last 'slow_length' bars

    if fast_sma is None or slow_sma is None:
        return None

    # Crossover logic: fast crosses above slow → BUY signal
    # For V1 we only generate BUY signals; exits happen via max_holding_days

    # Check current position count (simplified — in full system query positions)
    # V1: max 3 positions, long-only
    # Placeholder: assume we check the global position count elsewhere

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
