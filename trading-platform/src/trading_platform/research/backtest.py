"""Daily-bar research backtester for strategy candidates.

A deliberately simple, deterministic vector research backtester used for
hypothesis screening and reverification. The event-driven simulator remains
the reference execution model; this engine exists so parameter surfaces,
walk-forward folds, cost stresses and bootstrap analyses can be computed
cheaply and reproducibly.

Execution model (long-only, daily bars):
- Entries fill at the NEXT bar's open plus modeled slippage.
- ATR initial stop and ATR trailing stop, evaluated on bar LOW.
- STOP FILLS ARE NOT GUARANTEED AT THE STOP PRICE. If a bar OPENS below
  the stop (overnight gap), the fill is the open price minus slippage —
  the strategy realizes the gap loss.
- Time stop after ``max_holding_days``.
- Optional opposite-signal exit.
- Costs: fixed commission per side + slippage fraction, scalable by a
  cost multiplier for stress testing.
- Portfolio: max N positions, equal risk-based sizing using the sizing
  policy, cash-only, no leverage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

BACKTESTER_VERSION = "1.0.0"

SignalFn = Callable[[str, pd.DataFrame, Any, Any, str], Any]


@dataclass(frozen=True)
class CostModel:
    commission_per_order: float = 1.0
    slippage_pct: float = 0.001
    multiplier: float = 1.0

    def order_cost(self) -> float:
        return self.commission_per_order * self.multiplier

    def slip(self, price: float) -> float:
        return price * self.slippage_pct * self.multiplier


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 100_000.0
    max_positions: int = 3
    risk_fraction: float = 0.01
    max_holding_days: int = 30
    atr_stop_multiple: float = 2.5
    atr_trailing_multiple: float = 3.0
    max_position_notional_pct: float = 0.20
    cost: CostModel = CostModel()
    version: str = BACKTESTER_VERSION


@dataclass(frozen=True)
class Trade:
    symbol: str
    entry_date: Any
    exit_date: Any
    entry_price: float
    exit_price: float
    quantity: int
    net_pnl: float
    exit_reason: str
    gap_through_stop: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "entry_date": str(self.entry_date),
            "exit_date": str(self.exit_date),
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "net_pnl": self.net_pnl,
            "exit_reason": self.exit_reason,
            "gap_through_stop": self.gap_through_stop,
        }


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: pd.Series  # daily portfolio returns index-aligned
    daily_returns: pd.Series
    trades: Tuple[Trade, ...]
    final_equity: float
    config: BacktestConfig

    def trade_pnls(self) -> List[float]:
        return [t.net_pnl for t in self.trades]

    def symbol_pnls(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for t in self.trades:
            out[t.symbol] = out.get(t.symbol, 0.0) + t.net_pnl
        return out


def _size(
    equity: float,
    entry: float,
    stop: float,
    risk_fraction: float,
    cash: float,
    order_cost: float,
    notional_cap: float,
) -> int:
    """Risk-based sizing at the ACTUAL fill price, clamped by cash (incl.
    commission), position notional cap, and equity."""
    risk_per_share = entry - stop
    if risk_per_share <= 0 or entry <= 0:
        return 0
    qty = math.floor(equity * risk_fraction / risk_per_share)
    qty_afford = math.floor(max(cash - order_cost, 0.0) / entry)
    qty_notional = math.floor(notional_cap / entry)
    return int(max(0, min(qty, qty_afford, qty_notional)))


def run_backtest(
    features_by_symbol: Mapping[str, pd.DataFrame],
    signal_fn: SignalFn,
    params: Any,
    regimes_by_date: Optional[Mapping[Any, Any]],
    config: Optional[BacktestConfig] = None,
    member_fn: Optional[Any] = None,
) -> BacktestResult:
    """Run a long-only multi-symbol backtest over per-symbol feature frames.

    ``member_fn(decision_day) -> set[str]`` optionally restricts entry
    candidates to point-in-time universe membership at the decision day;
    when provided, symbols outside membership are never even evaluated
    (removes survivorship bias only if the membership data itself is PIT).

    Each feature frame is indexed by date and must include OHLC columns plus
    the strategy's feature columns. ``signal_fn(symbol,
    features_up_to_t, params, regime_at_t, timestamp_str)`` returns
    SignalEvidence or None; only ELIGIBLE BUY evidence produces entries.
    Regimes are looked up by date; missing regime -> evidence rejection
    handled inside the strategy function.
    """
    cfg = config or BacktestConfig()
    # Union of all dates in order.
    all_dates = sorted({d for df in features_by_symbol.values() for d in df.index})
    if not all_dates:
        raise ValueError("no dates in universe")

    cash = cfg.initial_cash
    equity = cfg.initial_cash
    positions: Dict[str, Dict[str, Any]] = {}
    trades: List[Trade] = []
    daily_returns: List[float] = []
    curve_index: List[Any] = []
    prev_equity = equity

    for i, day in enumerate(all_dates):
        # -- mark-to-market & exits on today's bar ------------------------
        # The stop enforced against today's bar must be fully knowable from
        # data up to the previous bar: exits first, trailing update after.
        for symbol in list(positions):
            df = features_by_symbol[symbol]
            if day not in df.index:
                continue
            bar = df.loc[day]
            pos = positions[symbol]
            exit_price: Optional[float] = None
            exit_reason = ""
            gap = False

            stop = pos["stop"]
            if math.isfinite(float(bar["open"])) and float(bar["open"]) < stop:
                # GAP THROUGH STOP: filled at the open, not the stop.
                exit_price = float(bar["open"]) - cfg.cost.slip(float(bar["open"]))
                exit_reason = "gap_through_stop"
                gap = True
            elif math.isfinite(float(bar["low"])) and float(bar["low"]) <= stop:
                exit_price = stop - cfg.cost.slip(stop)
                exit_reason = "atr_stop"
            if exit_price is None and i - pos["entry_idx"] >= cfg.max_holding_days:
                exit_price = float(bar["close"]) - cfg.cost.slip(float(bar["close"]))
                exit_reason = "time_stop"

            if exit_price is not None:
                qty = pos["quantity"]
                gross = (exit_price - pos["entry_price"]) * qty
                net = gross - cfg.cost.order_cost()
                cash += exit_price * qty - cfg.cost.order_cost()
                trades.append(
                    Trade(
                        symbol=symbol,
                        entry_date=pos["entry_date"],
                        exit_date=day,
                        entry_price=pos["entry_price"],
                        exit_price=exit_price,
                        quantity=qty,
                        net_pnl=float(net),
                        exit_reason=exit_reason,
                        gap_through_stop=gap,
                    )
                )
                del positions[symbol]
                continue

            # Still holding: ratchet the trailing stop with TODAY's data so
            # the new stop only ever governs the NEXT bar (no same-bar
            # lookahead on close/ATR).
            atr_v = bar.get(pos["atr_col"])
            pos["highest_close"] = max(pos["highest_close"], float(bar["close"]))
            if atr_v is not None and math.isfinite(float(atr_v)):
                trail = pos["highest_close"] - cfg.atr_trailing_multiple * float(atr_v)
                pos["stop"] = max(pos["stop"], trail)

        equity = cash + sum(
            float(features_by_symbol[s].loc[day, "close"]) * p["quantity"]
            for s, p in positions.items()
            if day in features_by_symbol[s].index
        )
        daily_returns.append((equity - prev_equity) / prev_equity if prev_equity > 0 else 0.0)
        curve_index.append(day)
        prev_equity = equity

        # -- entries: signals from YESTERDAY's close fill at TODAY's open -
        if i == 0:
            continue
        if len(positions) >= cfg.max_positions:
            continue
        prev_day = all_dates[i - 1]
        regime = regimes_by_date.get(prev_day) if regimes_by_date else None
        candidates: List[Tuple[float, str, Any, pd.DataFrame, Any]] = []
        for symbol, df in features_by_symbol.items():
            if symbol in positions or prev_day not in df.index or day not in df.index:
                continue
            if member_fn is not None and symbol not in member_fn(prev_day):
                continue
            hist = df.loc[:prev_day]
            evidence = signal_fn(symbol, hist, regime, params, str(prev_day))
            if evidence is not None and getattr(evidence, "eligible", False) and evidence.raw_signal == "BUY":
                candidates.append((evidence.signal_score, symbol, evidence, df, df.loc[day]))
        candidates.sort(key=lambda c: (-c[0], c[1]))
        for score, symbol, evidence, df, today_bar in candidates:
            if len(positions) >= cfg.max_positions:
                break
            open_price = today_bar.get("open")
            if open_price is None or not math.isfinite(float(open_price)) or float(open_price) <= 0:
                continue
            entry_price = float(open_price) + cfg.cost.slip(float(open_price))
            stop = float(evidence.initial_stop)
            qty = _size(
                equity,
                entry_price,
                stop,
                cfg.risk_fraction,
                cash,
                cfg.cost.order_cost(),
                equity * cfg.max_position_notional_pct,
            )
            if qty < 1:
                continue
            atr_value = evidence_atr(evidence, df, prev_day, cfg)
            cash -= entry_price * qty + cfg.cost.order_cost()
            positions[symbol] = {
                "quantity": qty,
                "entry_price": entry_price,
                "entry_date": day,
                "entry_idx": i,
                "stop": stop,
                "highest_close": float(today_bar.get("close", open_price)),
                "atr_col": atr_value[0],
            }

    equity_series = pd.Series(np.cumprod(1.0 + np.asarray(daily_returns)), index=pd.Index(curve_index))
    return BacktestResult(
        equity_curve=equity_series,
        daily_returns=pd.Series(daily_returns, index=pd.Index(curve_index)),
        trades=tuple(trades),
        final_equity=float(equity_series.iloc[-1]) * cfg.initial_cash if len(equity_series) else cfg.initial_cash,
        config=cfg,
    )


def bar_placeholder(evidence: Any, df: pd.DataFrame, day: Any) -> Any:
    """Deprecated shim retained for interface stability; returns today's bar."""
    return df.loc[day]


def evidence_atr(evidence: Any, df: pd.DataFrame, day: Any, cfg: BacktestConfig) -> Tuple[str, float]:
    """Find the ATR column used for the trailing stop."""
    for col in df.columns:
        if col.startswith("atr"):
            return col, float(df.loc[day, col]) if math.isfinite(float(df.loc[day, col])) else float("nan")
    return "atr", float("nan")
