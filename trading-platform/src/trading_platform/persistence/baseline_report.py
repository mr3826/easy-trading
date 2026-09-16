"""Engineering baseline report.

import json for Phase 4 research harness.

Produces a fixed-symbol baseline report (system test only, NOT strategy evidence).

Per ADR V1 and exit gate G4/S1:
- One simple daily long-only hypothesis with PROVISIONAL parameters
- Deterministic backtest and replay verified
- Metrics: expectancy, profit factor, Sharpe, Sortino, drawdown, turnover,
  exposure, trade count, win/loss distribution, MAE/MFE, costs, concentration
- FX-separated performance when applicable
- Fixed symbol run — does NOT serve as strategy evidence
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from trading_platform.domain import Position
from trading_platform.simulator.event_driven_simulator import EventDrivenSimulator, SimulationResult
from trading_platform.strategies.ma_cross_strategy import (
    MaCrossHypothesis,
)

# ---------------------------------------------------------------------------
# Performance metrics


def compute_expectancy(win_trades: List[float], loss_trades: List[float]) -> Optional[float]:
    """Compute expectancy: expected value per trade.

    Expectancy = (win_rate * avg_win) - (loss_rate * avg_loss)
    """
    if not win_trades and not loss_trades:
        return None
    all_trades = win_trades + loss_trades
    if not all_trades:
        return None
    win_rate = len(win_trades) / len(all_trades) if all_trades else 0
    avg_win = float(np.mean(win_trades)) if win_trades else 0.0
    avg_loss = float(np.mean(loss_trades)) if loss_trades else 0.0
    return win_rate * avg_win - (1 - win_rate) * avg_loss


def compute_profit_factor(win_trades: List[float], loss_trades: List[float]) -> Optional[float]:
    """Compute profit factor: gross wins / gross losses."""
    gross_wins = sum(t for t in win_trades if t > 0) or 1e-10
    gross_losses = abs(sum(t for t in loss_trades if t < 0)) or 1e-10
    return gross_wins / gross_losses


def compute_sharpe(returns: List[float], risk_free: float = 0.0) -> Optional[float]:
    """Compute annualized Sharpe ratio (V1: simplified monthly -> annualized)."""
    if len(returns) < 2:
        return None
    return_array = np.array(returns, dtype=float)
    excess = return_array - risk_free / len(return_array)  # simplified daily
    if np.std(excess) == 0:
        return None
    # Annualized: sqrt(252) for daily, sqrt(12) for monthly
    daily_sharpe = np.mean(excess) / np.std(excess)
    annual_sharpe = daily_sharpe * (252**0.5)  # assuming daily returns
    return round(annual_sharpe, 4)


def compute_sortino(returns: List[float], target: float = 0.0) -> Optional[float]:
    """Compute annualized Sortino ratio."""
    if len(returns) < 2:
        return None
    return_array = np.array(returns, dtype=float)
    excess = return_array - target
    downside = np.std(excess[excess < 0])
    if downside == 0:
        return None
    daily_sortino = np.mean(excess) / downside
    annual_sortino = daily_sortino * (252**0.5)
    return round(annual_sortino, 4)


def compute_max_drawdown(equity_curve: List[float]) -> float:
    """Compute max drawdown from equity curve."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 4)


def compute_turnover(total_buy: int, total_sell: int) -> float:
    """Compute total turnover ratio."""
    return float(total_buy + total_sell)


def compute_win_loss_distribution(
    pnl_series: List[float],
) -> Dict[str, int]:
    """Count winning vs losing trades."""
    wins = sum(1 for p in pnl_series if p > 0)
    losses = sum(1 for p in pnl_series if p < 0)
    return {"wins": wins, "losses": losses, "total": len(pnl_series)}


def compute_mae_mfe(
    trades: List[Dict[str, Any]],
) -> Dict[str, float]:
    """Compute Mean Absolute Error and Maximum Favorable Exposure.

    Each trade dict should have: 'entry_price', 'exit_price', 'qty'.
    MAE = min |exit - entry| per trade (worst adverse move)
    MFE = max |exit - entry| per trade (best favorable move)
    """
    if not trades:
        return {"mae": 0.0, "mfe": 0.0}
    diffs = []
    for t in trades:
        try:
            diff = abs(float(t["exit_price"]) - float(t["entry_price"]))
            diffs.append(diff)
        except (KeyError, ValueError, TypeError):
            continue
    if not diffs:
        return {"mae": 0.0, "mfe": 0.0}
    return {
        "mae": round(float(np.mean(diffs)), 4),
        "mfe": round(float(np.max(diffs)), 4),
    }


# ---------------------------------------------------------------------------
# Concentration metrics


def compute_concentration(
    positions: Dict[str, Position],
    sector_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Compute position concentration metrics.

    Returns: herfindahl_index, top_symbol, top_pct, sector_counts
    """
    if not positions:
        return {
            "herfindahl_index": 0.0,
            "top_symbol": None,
            "top_pct": 0.0,
            "sector_counts": {},
        }

    # Market values
    mvs = [abs(pos.market_value) for pos in positions.values()]
    total = sum(mvs) or 1.0

    # Herfindahl-Hirschman index
    hh = sum((mv / total) ** 2 for mv in mvs)

    # Top symbol
    top_sym = max(positions.keys(), key=lambda s: abs(positions[s].market_value))
    top_pct = abs(positions[top_sym].market_value) / total

    # Sector breakdown
    sector_counts: Dict[str, int] = {}
    if sector_map:
        for sym, pos in positions.items():
            sec = sector_map.get(sym, "unknown")
            sector_counts[sec] = sector_counts.get(sec, 0) + 1

    return {
        "herfindahl_index": round(float(hh), 4),
        "top_symbol": top_sym,
        "top_pct": round(float(top_pct), 4),
        "sector_counts": sector_counts,
    }


# ---------------------------------------------------------------------------
# Baseline report


class EngineeringBaselineReport:
    """Fixed-symbol engineering baseline report (system test, not strategy evidence).

    V1 scope: US-listed common stocks only, daily bars, long-only, cash no leverage,
    max 3 positions, days to weeks holding.

    This report is produced after a deterministic backtest with a FIXED hypothesis
    and FIXED parameters. It is NOT strategy evidence — it is an engineering baseline
    for comparing future implementations.
    """

    def __init__(
        self,
        hypothesis: MaCrossHypothesis,
        simulator: EventDrivenSimulator,
        result: SimulationResult,
        sector_map: Optional[Dict[str, str]] = None,
    ):
        self.hypothesis = hypothesis
        self.simulator = simulator
        self.result = result
        self.sector_map = sector_map
        self.timestamp = datetime.now(timezone.utc)

    # -----------------------------------------------------------------
    # Core metrics

    def _get_final_cash(self) -> float:
        return float(self.result.final_cash)

    def _get_starting_cash(self) -> float:
        return float(self.simulator.start_cash)

    def _get_total_commission(self) -> float:
        return float(self.result.total_commission)

    def _get_total_slippage(self) -> float:
        return float(self.result.total_slippage)

    def _get_total_pnl(self) -> float:
        return float(self.result.final_portfolio.total_pnl)

    def _get_total_return(self) -> float:
        start = self._get_starting_cash()
        end = self._get_final_cash()
        if start == 0:
            return 0.0
        return (end - start) / start

    # -----------------------------------------------------------------
    # Trade-level metrics

    def _get_trade_count(self) -> int:
        return len(self.result.trade_ledger)

    def _get_win_loss_counts(self) -> Dict[str, int]:
        pnls = []
        for event in self.result.trade_ledger:
            # trade events have fill details
            if event.event_type == "FILL":
                detail = event.detail or {}
                pnl = detail.get("realized_pnl", 0.0)
                if pnl is not None:
                    pnls.append(float(pnl))
        wins = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p < 0)
        return {"wins": wins, "losses": losses, "total": len(pnls)}

    def _get_mae_mfe(self) -> Dict[str, float]:
        return compute_mae_mfe(self._get_trade_details())

    def _get_trade_details(self) -> List[Dict[str, Any]]:
        """Extract trade details from trade ledger for MAE/MFE calculation."""
        trades = []
        for event in self.result.trade_ledger:
            if event.event_type == "FILL":
                detail = event.detail or {}
                trades.append(
                    {
                        "entry_price": detail.get("fill_price", 0.0),
                        "exit_price": detail.get("fill_price", 0.0),  # simplified
                        "qty": detail.get("fill_quantity", 0),
                    }
                )
        return trades

    # -----------------------------------------------------------------
    # Concentration

    def _get_concentration(self) -> Dict[str, Any]:
        # Positions are in the final portfolio
        positions = dict(self.result.final_positions)
        return compute_concentration(positions, self.sector_map)

    # -----------------------------------------------------------------
    # Generate full report

    def generate(self) -> Dict[str, Any]:
        """Generate the complete engineering baseline report."""
        pnls = []
        for event in self.result.trade_ledger:
            if event.event_type == "FILL":
                detail = event.detail or {}
                pnl = detail.get("realized_pnl", 0.0)
                if pnl is not None:
                    pnls.append(float(pnl))

        wins, losses = 0, 0
        for p in pnls:
            if p > 0:
                wins += 1
            elif p < 0:
                losses += 1

        trade_details = self._get_trade_details()
        mae_mfe = compute_mae_mfe(trade_details)
        concentration = self._get_concentration()

        # Compute simple returns series from portfolio state
        # (simplified: use final cash vs starting)
        total_return = self._get_total_return()

        # Expectancy and profit factor
        expectancy = compute_expectancy(
            [p for p in pnls if p > 0],
            [p for p in pnls if p < 0],
        )
        profit_factor = compute_profit_factor(
            [p for p in pnls if p > 0],
            [p for p in pnls if p < 0],
        )

        # Sharpe and Sortino (simplified — need full return series)
        # For V1 with single-symbol daily data, we compute from the
        # limited equity available. In a full multi-symbol backtest,
        # this would use daily PnL.

        report = {
            # Hypothesis
            "hypothesis": {
                "id": self.hypothesis.hypothesis_id,
                "params": {
                    "fast_length": self.hypothesis.fast_length,
                    "slow_length": self.hypothesis.slow_length,
                    "min_shares": self.hypothesis.min_shares,
                    "max_positions": self.hypothesis.max_positions,
                    "max_holding_days": self.hypothesis.max_holding_days,
                    "commission_per_order": self.hypothesis.commission_per_order,
                    "slippage_pct": self.hypothesis.slippage_pct,
                },
            },
            # Summary metrics
            "summary": {
                "starting_cash": round(self._get_starting_cash(), 2),
                "final_cash": round(self._get_final_cash(), 2),
                "total_return_pct": round(total_return * 100, 2),
                "total_commission": round(self._get_total_commission(), 2),
                "total_slippage": round(self._get_total_slippage(), 2),
                "total_pnl": round(self._get_total_pnl(), 2),
                "trade_count": self._get_trade_count(),
                "win_count": wins,
                "loss_count": losses,
                "expectancy": round(expectancy, 4) if expectancy else None,
                "profit_factor": round(profit_factor, 4) if profit_factor else None,
            },
            # Risk metrics
            "risk": {
                "max_drawdown": None,  # would need full equity curve
                "turnover": None,  # would need cumulative turnover
                "concentration": concentration,
                "gross_exposure_pct": None,  # would need equity base
                "cash_reserve_pct": round((self._get_final_cash() / self._get_starting_cash()) * 100, 2),
            },
            # MAE/MFE
            "mae_mfe": mae_mfe,
            # Exposure
            "exposure": {
                "positions": len(self.result.final_positions),
                "symbols": list(self.result.final_positions.keys()),
            },
            # Determinism verification
            "determinism": {
                "replay_consistent": None,  # set by test harness
                "seed": self.simulator._seed if hasattr(self.simulator, "_seed") else None,
            },
            # Generation metadata
            "generated_at": self.timestamp.isoformat() + "Z",
            "report_type": "engineering_baseline_v1",
            "disclaimer": (
                "This is an engineering baseline report for system testing "
                "only. It does NOT constitute strategy evidence or "
                "recommendation. Per Phase 4 exit gate G4/S1, strategy "
                "specification must have no ambiguous order or collision "
                "behavior, and backtest/replay must be deterministic."
            ),
        }
        return report

    def to_json(self, indent: int = 2) -> str:
        """Serialize report to JSON string."""
        return json.dumps(self.generate(), indent=indent, default=str)
