"""Risk limits for Phase 4 research harness.

V1 limits (PROVISIONAL, per ADRs):
- Max 3 positions total (per ADR V1: max 3 positions)
- Max sector exposure: concentration check
- Gross exposure limit (long + short)
- Settled-cash check: no leverage, cash account
- Provisional portfolio risk limits (drawdown, turnover, etc.)

All values are PROVISIONAL and subject to adjustment after
experiment G4/S1 candidate validation.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from trading_platform.domain import Instrument, Position

# ---------------------------------------------------------------------------
# Position limit — V1: max 3 positions total


MAX_POSITIONS_V1 = 3


def check_position_limit(
    new_qty: int,
    current_positions: Dict[str, Position],
    instrument: Instrument,
    max_positions: int = MAX_POSITIONS_V1,
) -> Tuple[bool, str]:
    """Check if a new position would exceed the V1 max-3-positions limit.

    Returns (approved, reason).
    """
    # Count current long positions (quantity > 0)
    current_count = sum(1 for pos in current_positions.values() if pos.quantity > 0)

    # If we're adding a new long position (not increasing existing)
    # and would exceed the limit
    if new_qty > 0 and instrument.symbol not in current_positions:
        if current_count >= max_positions:
            return False, f"Max positions ({max_positions}) reached"

    # If reducing/closing a position, always allowed
    if new_qty <= 0:
        return True, "Position reduction/closure allowed"

    # Increasing existing position — always allowed (may still hit other limits)
    if instrument.symbol in current_positions:
        return True, "Increasing existing position"

    # New position within limit
    return True, "Within position limit"


# ---------------------------------------------------------------------------
# Sector concentration — V1 simplified check


def check_sector_concentration(
    instrument: Instrument,
    current_positions: Dict[str, Position],
    sector_map: Optional[Dict[str, str]] = None,
) -> Tuple[bool, str]:
    """Check sector concentration limit (V1 simplified).

    V1: no explicit sector cap, but concentration is implicitly limited
    by the max-3-positions rule. This function provides an early-warning
    check if a sector map is available.

    Returns (approved, reason).
    """
    if sector_map is None or instrument.symbol not in sector_map:
        return True, "No sector map available (implicitly limited by max positions)"

    instrument_sector = sector_map[instrument.symbol]
    # Count positions per sector
    sector_counts: Dict[str, int] = {}
    for sym, pos in current_positions.items():
        sec = sector_map.get(sym, "unknown")
        sector_counts[sec] = sector_counts.get(sec, 0) + (1 if pos.quantity > 0 else 0)

    # If adding this instrument would create too much concentration
    if instrument_sector in sector_counts:
        new_count = sector_counts[instrument_sector] + 1
    else:
        new_count = 1

    # V1: no hard sector cap, but warn if > 2 positions in same sector
    if new_count > 2:
        return (
            False,
            f"Sector concentration: {new_count} positions in {instrument_sector}",
        )
    return (
        True,
        f"Sector concentration OK: {new_count} position(s) in {instrument_sector}",
    )


# ---------------------------------------------------------------------------
# Gross exposure limit


def check_gross_exposure(
    current_positions: Dict[str, Position],
    gross_limit: float = 1_000_000.0,  # V1 PROVISIONAL: $1M notional gross
) -> Tuple[bool, str]:
    """Check gross exposure limit.

    Gross exposure = sum of |market_value| across all positions.

    Returns (approved, reason).
    """
    total_gross = sum(abs(pos.market_value) for pos in current_positions.values())

    if total_gross > gross_limit:
        return (
            False,
            f"Gross exposure ${total_gross:,.0f} exceeds limit ${gross_limit:,.0f}",
        )
    return True, f"Gross exposure ${total_gross:,.0f} within limit ${gross_limit:,.0f}"


# ---------------------------------------------------------------------------
# Settled-cash / buying-power check (cash account, no leverage)


def check_buying_power(
    order_qty: int,
    order_price: float,
    current_cash: float,
    current_positions: Dict[str, Position],
    commission: float,
) -> Tuple[bool, str]:
    """Check if a buy order is affordable in a cash account.

    V1: no leverage, cash = settled cash only.
    Estimated cost = abs(qty) * price + commission; commission must be
    supplied from policy/config so no fee assumption is hard-coded here.

    Returns (approved, reason).
    """
    estimated_cost = abs(order_qty) * order_price + commission

    if estimated_cost > current_cash:
        affordable_qty = int((current_cash - commission) / order_price) if order_price > 0 else 0
        return (
            False,
            f"Insufficient cash: need ${estimated_cost:.2f}, have ${current_cash:.2f} "
            f"(affordable ~{affordable_qty} shares)",
        )
    return True, f"Buying power OK: ${estimated_cost:.2f} <= ${current_cash:.2f}"


# ---------------------------------------------------------------------------
# Provisional portfolio risk limits (drawdown, turnover, exposure)


class PortfolioRiskLimits:
    """V1 provisional portfolio risk limits.

    These are SOFT limits for research purposes — hard risk engine
    (Phase 5) will enforce versioned policies persisted to PostgreSQL.
    """

    def __init__(
        self,
        max_drawdown_pct: float = 10.0,  # V1 PROVISIONAL: 10% max drawdown
        max_turnover_pct: float = 20.0,  # V1 PROVISIONAL: 20% max turnover
        max_gross_exposure_pct: float = 50.0,  # V1 PROVISIONAL: 50% of equity
        min_cash_reserve_pct: float = 5.0,  # V1 PROVISIONAL: 5% cash reserve
    ):
        self.max_drawdown_pct = max_drawdown_pct
        self.max_turnover_pct = max_turnover_pct
        self.max_gross_exposure_pct = max_gross_exposure_pct
        self.min_cash_reserve_pct = min_cash_reserve_pct

    def check_drawdown(self, total_pnl: float, starting_cash: float, current_cash: float) -> Tuple[bool, str]:
        """Check max drawdown limit.

        Returns (approved, reason).
        """
        if starting_cash == 0:
            return True, "No starting capital"
        drawdown = (starting_cash - current_cash) / starting_cash
        if drawdown > self.max_drawdown_pct / 100:
            return False, (f"Max drawdown exceeded: {drawdown * 100:.1f}% > {self.max_drawdown_pct:.1f}%")
        return (
            True,
            f"Drawdown OK: {drawdown * 100:.1f}% <= {self.max_drawdown_pct:.1f}%",
        )

    def check_turnover(self, total_buy_qty: int, total_sell_qty: int, starting_shares: int = 0) -> Tuple[bool, str]:
        """Check turnover limit (cumulative)."""
        # Simplified: ratio of shares traded vs starting position
        if starting_shares == 0:
            return True, "No prior position for turnover calc"
        total_turnover = (total_buy_qty + total_sell_qty) / max(starting_shares, 1)
        if total_turnover > self.max_turnover_pct / 100:
            return False, (f"Max turnover exceeded: {total_turnover * 100:.1f}% > {self.max_turnover_pct:.1f}%")
        return (
            True,
            f"Turnover OK: {total_turnover * 100:.1f}% <= {self.max_turnover_pct:.1f}%",
        )

    def check_gross_exposure_pct(self, gross_exposure: float, equity: float) -> Tuple[bool, str]:
        """Check gross exposure as percentage of equity."""
        if equity == 0:
            return True, "No equity base"
        pct = (gross_exposure / equity) * 100
        if pct > self.max_gross_exposure_pct:
            return False, (f"Gross exposure {pct:.1f}% > max {self.max_gross_exposure_pct:.1f}%")
        return True, f"Gross exposure {pct:.1f}% <= {self.max_gross_exposure_pct:.1f}%"

    def check_cash_reserve(self, cash: float, equity: float) -> Tuple[bool, str]:
        """Check minimum cash reserve percentage."""
        if equity == 0:
            return True, "No equity base"
        reserve_pct = (cash / equity) * 100
        if reserve_pct < self.min_cash_reserve_pct:
            return False, (f"Cash reserve {reserve_pct:.1f}% < min {self.min_cash_reserve_pct:.1f}%")
        return (
            True,
            f"Cash reserve {reserve_pct:.1f}% >= {self.min_cash_reserve_pct:.1f}%",
        )
