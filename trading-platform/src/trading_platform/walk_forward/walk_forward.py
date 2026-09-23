"""Walk-forward validation for Phase 5 out-of-sample robustness.

Provides chronological train/validation/test period splits and a walk-forward
evaluation loop with documented retraining/recalibration rules.

V1 daily long-only hypothesis: MA crossover with provisional parameters.
Signals are generated point-in-time (only bars at or before each decision
bar may influence the decision) and applied per symbol, so no future bar
and no other symbol's price can leak into a decision.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from trading_platform.domain import Bar, Instrument, OrderSide, OrderType, PortfolioSnapshot, Signal, TimeInForce
from trading_platform.persistence.baseline_report import compute_max_drawdown, compute_turnover
from trading_platform.persistence.experiment import (
    ExperimentRegistry,
)
from trading_platform.simulator.event_driven_simulator import (
    EventDrivenSimulator,
    SimulationResult,
)
from trading_platform.strategies.ma_cross_strategy import (
    MaCrossHypothesis,
    generate_signal,
)

# ---------------------------------------------------------------------------
# Period split — strictly chronological, no randomisation


class PeriodSplit:
    """Chronological train/validation/test period split.

    V1 rules:
    - Train: first N days of the requested range
    - Validation: next M days (metric monitoring, NOT final test)
    - Test: the K days immediately following validation; when the requested
      range is too short to fit all three windows, the test window is pinned
      to the end of the range and the earlier windows shrink (min sizes are
      enforced)
    - No overlap, no shuffling, strictly forward-moving
    """

    def __init__(
        self,
        train_days: int,
        validation_days: int,
        test_days: int,
        min_train: int = 30,
        min_validation: int = 10,
        min_test: int = 15,
    ):
        # Validation of sizes
        assert train_days >= min_train, f"train_days={train_days} < min={min_train}"
        assert validation_days >= min_validation, f"validation_days={validation_days} < min={min_validation}"
        assert test_days >= min_test, f"test_days={test_days} < min={min_test}"

        self.train_days = train_days
        self.validation_days = validation_days
        self.test_days = test_days
        self.min_test = min_test
        self.min_train = min_train
        self.min_validation = min_validation

    # -----------------------------------------------------------------
    # Split a date range

    def split(self, start: date, end: date) -> Dict[str, Tuple[date, date]]:
        """Split the [start, end] range into (train, validation, test).

        Returns dict with keys: "train", "validation", "test",
        each value is (fold_start, fold_end) inclusive.
        """
        total_days = (end - start).days + 1  # inclusive
        train_end = start + timedelta(days=self.train_days - 1)
        val_end = train_end + timedelta(days=self.validation_days - 1)
        test_end = val_end + timedelta(days=self.test_days - 1)

        # Validate we don't exceed the end date
        if (test_end - start).days >= total_days:
            # Shrink test to fit
            available_test = (end - val_end).days + 1
            test_days = max(available_test, self.min_test)
            val_end = end - timedelta(days=test_days - 1)
            test_end = end
            # Recalculate validation
            train_end = test_end - timedelta(days=self.validation_days + test_days - 1)
            if (train_end - start).days + 1 < self.min_train:
                raise ValueError("Data too short for requested split sizes")

        return {
            "train": (start, train_end),
            "validation": (train_end + timedelta(days=1), val_end),
            "test": (val_end + timedelta(days=1), test_end),
        }

    # -------------------------------------------------------------------------
    # Walk-forward iterator

    def walk_forward(
        self,
        data_start: date,
        data_end: date,
        step_forward: int = 1,
    ) -> List[Dict[str, Tuple[date, date]]]:
        """Generate consecutive walk-forward folds with a rolling window.

        Each fold:
        1. Train on [t0, t0+train_days-1]
        2. Validate on [train_end+1, train_end+validation_days]
        3. Test on [val_end+1, val_end+test_days]
        4. Step forward by step_forward days

        The window rolls forward: with step_forward=1 many folds re-evaluate
        heavily overlapping test windows, which inflates the multiple-testing
        problem without adding new out-of-sample data. aggregate_results()
        applies a Bonferroni adjustment and the research methodology
        documents this caveat explicitly. A fold is only generated while a
        complete train+validation+test window fits in the data range.
        """
        folds: List[Dict[str, Tuple[date, date]]] = []
        current_start = data_start

        full_fold_days = self.train_days + self.validation_days + self.test_days - 1
        while current_start + timedelta(days=full_fold_days) <= data_end:
            fold_split = self.split(current_start, data_end)
            folds.append(fold_split)

            # Step forward
            next_start = current_start + timedelta(days=step_forward)
            if next_start >= data_end:
                break
            current_start = next_start

        return folds

    # -------------------------------------------------------------------------
    # Fixed final test accessor

    def get_test_period(self, all_dates: List[date]) -> Optional[Tuple[date, date]]:
        """Get the fixed test period across all walk-forward folds.

        The test period is established on the first split and remains
        locked throughout the walk-forward process.
        """
        if not hasattr(self, "_test_period"):
            # Establish from first available data
            if all_dates:
                start = all_dates[0]
                end = all_dates[-1]
                self._test_period = self.split(start, end)["test"]
        return getattr(self, "_test_period", None)


# ---------------------------------------------------------------------------
# Walk-forward evaluator


class WalkForwardEvaluator:
    """Run walk-forward simulation across multiple folds.

    V1 workflow per fold:
    1. Train: simulate on train period, record metrics (parameters fixed)
    2. Validate: simulate on validation period, monitor metrics
    3. Test: simulate on test period, record locked results
    4. Report across all folds with multiple-testing adjustment

    Signals are generated point-in-time with an as-of bound (no future bar
    may influence a decision) and applied per symbol through chained
    per-symbol simulations; the portfolio carries across symbols in
    chronological symbol order. This is a documented V1 simplification:
    position limits bind across symbols via the simulator's internal risk
    engine, while signal generation tracks positions per symbol.
    """

    def __init__(
        self,
        simulator: EventDrivenSimulator,
        hypothesis: MaCrossHypothesis,
        period_split: PeriodSplit,
        registry: ExperimentRegistry,
        verify_determinism: bool = True,
    ):
        self.simulator = simulator
        self.hypothesis = hypothesis
        self.simulator.max_positions = hypothesis.max_positions
        self.period_split = period_split
        self.registry = registry
        self.verify_determinism = verify_determinism
        self.fold_results: List[Dict[str, Any]] = []

    # -----------------------------------------------------------------
    # Run one fold

    def run_fold(
        self,
        fold_index: int,
        fold: Dict[str, Tuple[date, date]],
        bars_by_symbol: Dict[str, Dict[str, List[Any]]],  # symbol -> date-str -> [Bar or mapping]
        symbols: List[str],
    ) -> Dict[str, Any]:
        """Run one walk-forward fold.

        V1 workflow:
        - Train: point-in-time signals + chained simulation, record metrics
        - Validate: same, monitor metrics
        - Test: same, record locked results
        """
        train_start, train_end = fold["train"]
        val_start, val_end = fold["validation"]
        test_start, test_end = fold["test"]

        train_outcome = self._run_phase(bars_by_symbol, symbols, train_start, train_end)
        val_outcome = self._run_phase(bars_by_symbol, symbols, val_start, val_end)
        test_outcome = self._run_phase(bars_by_symbol, symbols, test_start, test_end)

        train_metrics = self._aggregate_phase(train_outcome)
        val_metrics = self._aggregate_phase(val_outcome)
        test_metrics = self._aggregate_phase(test_outcome)

        fold_result = {
            "fold_index": fold_index,
            "train_period": {"start": train_start, "end": train_end},
            "validation_period": {"start": val_start, "end": val_end},
            "test_period": {"start": test_start, "end": test_end},
            # PROVISIONAL: keep hypothesis fixed, do NOT re-optimize
            "hypothesis_id": self.hypothesis.hypothesis_id,
            # Train metrics
            "train_final_cash": train_metrics["final_cash"],
            "train_total_pnl": train_metrics["total_pnl"],
            "train_trade_count": train_metrics["trade_count"],
            # Validation metrics
            "val_final_cash": val_metrics["final_cash"],
            "val_total_pnl": val_metrics["total_pnl"],
            "val_trade_count": val_metrics["trade_count"],
            # TEST (locked) metrics
            "test_final_cash": test_metrics["final_cash"],
            "test_total_pnl": test_metrics["total_pnl"],
            "test_trade_count": test_metrics["trade_count"],
            "test_trade_pnls": test_metrics["trade_pnls"],
            "test_start_equity": self.simulator.start_cash,
            # Cost/slippage
            "test_total_commission": test_metrics["commission"],
            "test_total_slippage": test_metrics["slippage"],
            "test_estimated_slippage": test_metrics["estimated_slippage"],
            # Robustness analysis
            "test_turnover": test_metrics["turnover"],
            "test_max_drawdown": test_metrics["max_drawdown"],
            "test_gross_exposure_pct": test_metrics["gross_exposure_pct"],
            "long_only_preserved": train_metrics["long_only"]
            and val_metrics["long_only"]
            and test_metrics["long_only"],
            # Determinism check
            "train_deterministic": self._verify_phase_determinism(train_outcome),
            "val_deterministic": self._verify_phase_determinism(val_outcome),
            "test_deterministic": self._verify_phase_determinism(test_outcome),
        }

        self.fold_results.append(fold_result)
        return fold_result

    # -----------------------------------------------------------------
    # Phase execution

    def _run_phase(
        self,
        bars_by_symbol: Dict[str, Dict[str, List[Any]]],
        symbols: List[str],
        phase_start: date,
        phase_end: date,
    ) -> Dict[str, Any]:
        """Generate point-in-time signals and run chained per-symbol simulations.

        For every decision date the symbol's full history is filtered at the
        decision bar (as-of), so no future bar can influence the decision and
        earlier-period bars legitimately provide SMA warm-up (isolation still
        holds: train decisions never see validation/test bars).

        A lightweight per-phase position state machine is used only for
        signal generation (long-only exits); the simulator state is
        authoritative. A BUY that expires unfilled creates a phantom-long in
        the signal-generation state (documented V1 simplification).
        """
        signal_lists: Dict[str, List[Optional[Signal]]] = {}
        phase_bars_by_symbol: Dict[str, List[Bar]] = {}

        for sym in sorted(symbols):
            raw_entries = bars_by_symbol.get(sym, {})
            full_history: List[Bar] = []
            for bar_date_str in sorted(raw_entries.keys(), key=lambda d: datetime.fromisoformat(d)):
                for raw_bar in raw_entries[bar_date_str]:
                    full_history.append(self._normalize_bar(raw_bar, Instrument(sym)))

            phase_bars = [b for b in full_history if phase_start <= b.timestamp.date() <= phase_end]
            phase_bars_by_symbol[sym] = phase_bars

            history_dicts: List[Dict[str, Any]] = [
                {
                    "timestamp": b.timestamp,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "available_at": b.available_at,
                }
                for b in full_history
            ]

            aligned: List[Optional[Signal]] = []
            position: Optional[Dict[str, Any]] = None
            for bar in phase_bars:
                bar_date = bar.timestamp.date()
                raw_signal = generate_signal(
                    {sym: history_dicts},
                    self.hypothesis,
                    sym,
                    bar_date,
                    as_of=bar.timestamp,
                    position_held=position,
                )
                sig: Optional[Signal] = None
                if raw_signal is not None:
                    sig = Signal(
                        instrument=Instrument(sym),
                        side=OrderSide[raw_signal["side"]],
                        quantity=int(raw_signal["quantity"]),
                        price=raw_signal.get("price"),
                        order_type=OrderType[raw_signal["order_type"]],
                        time_in_force=TimeInForce[raw_signal["time_in_force"]],
                    )
                aligned.append(sig)

                if sig is not None and sig.side == OrderSide.BUY:
                    position = {"quantity": sig.quantity, "days_held": 0}
                elif sig is not None and sig.side == OrderSide.SELL:
                    position = None
                elif position is not None:
                    position = {"quantity": position["quantity"], "days_held": position["days_held"] + 1}

            signal_lists[sym] = aligned

        # Sequential per-symbol chained simulations (portfolio carries over in
        # chronological symbol order)
        portfolio: Optional[PortfolioSnapshot] = None
        results: List[SimulationResult] = []
        for sym in sorted(symbols):
            result = self.simulator.run(
                bars=phase_bars_by_symbol[sym],
                signals=signal_lists[sym],
                initial_portfolio=portfolio,
            )
            results.append(result)
            portfolio = result.final_portfolio

        return {"results": results, "signal_lists": signal_lists, "phase_bars_by_symbol": phase_bars_by_symbol}

    def _normalize_bar(self, raw_bar: Any, instrument: Instrument) -> Bar:
        """Normalize a Bar-or-mapping value into a validated Bar."""
        if isinstance(raw_bar, Bar):
            return raw_bar
        if not isinstance(raw_bar, Mapping):
            raise TypeError("walk-forward bars must be Bar or mapping values")
        timestamp_value = raw_bar["timestamp"]
        timestamp = datetime.fromisoformat(timestamp_value) if isinstance(timestamp_value, str) else timestamp_value
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        available_at_value = raw_bar.get("available_at")
        available_at = (
            datetime.fromisoformat(available_at_value) if isinstance(available_at_value, str) else available_at_value
        )
        if available_at is not None and available_at.tzinfo is None:
            available_at = available_at.replace(tzinfo=timezone.utc)
        return Bar(
            instrument=instrument,
            timestamp=timestamp,
            open=float(raw_bar["open"]),
            high=float(raw_bar["high"]),
            low=float(raw_bar["low"]),
            close=float(raw_bar["close"]),
            volume=int(raw_bar["volume"]),
            available_at=available_at,
        )

    def _aggregate_phase(self, outcome: Dict[str, Any]) -> Dict[str, Any]:
        """Aggregate chained per-symbol simulation results for one phase."""
        results: List[SimulationResult] = outcome["results"]

        trade_pnls: List[float] = []
        total_buy = 0
        total_sell = 0
        for result in results:
            for event in result.trade_ledger:
                if event.event_type == "FILL":
                    detail = event.detail or {}
                    trade_pnls.append(float(detail.get("realized_pnl", 0.0)))
                    fill_qty = int(detail.get("fill_quantity", 0))
                    if fill_qty > 0:
                        total_buy += fill_qty
                    elif fill_qty < 0:
                        total_sell += abs(fill_qty)

        equity_curve: List[float] = []
        for result in results:
            for snapshot in result.portfolio_series:
                market_value = sum(pos.market_value for pos in snapshot.positions.values())
                equity_curve.append(snapshot.cash + market_value)

        long_only = all(pos.quantity >= 0 for result in results for pos in result.final_positions.values())

        return {
            "final_cash": sum(result.final_cash for result in results),
            "total_pnl": sum(result.final_portfolio.total_pnl for result in results),
            "trade_count": sum(len(result.trade_ledger) for result in results),
            "commission": sum(result.total_commission for result in results),
            "slippage": sum(result.total_slippage for result in results),
            "estimated_slippage": sum(
                abs(int((event.detail or {}).get("fill_quantity", 0)))
                * float((event.detail or {}).get("fill_price", 0.0))
                * self.hypothesis.slippage_pct
                for result in results
                for event in result.trade_ledger
                if event.event_type == "FILL"
            ),
            "trade_pnls": trade_pnls,
            "turnover": compute_turnover(total_buy, total_sell, equity=self.simulator.start_cash),
            "max_drawdown": compute_max_drawdown(equity_curve),
            "gross_exposure_pct": None if not results else self._final_gross_exposure_pct(results),
            "long_only": long_only,
        }

    def _final_gross_exposure_pct(self, results: List[SimulationResult]) -> Optional[float]:
        """Gross exposure of the chained final portfolio as % of equity."""
        final = results[-1].final_portfolio
        gross = float(final.gross_exposure)
        cash = float(results[-1].final_cash)
        market_value = sum(pos.market_value for pos in final.positions.values())
        equity = cash + market_value
        if equity <= 0:
            return None
        return round(gross / equity * 100, 2)

    def _verify_phase_determinism(self, outcome: Dict[str, Any]) -> bool:
        """Verify determinism by re-running the phase and comparing ledgers.

        The phase is re-run from its recorded bars and signal lists with the
        same simulator; identical inputs must produce identical final cash
        and trade ledgers.
        """
        if not self.verify_determinism:
            return True
        results: List[SimulationResult] = outcome["results"]
        if not results:
            return True
        phase_bars_by_symbol: Dict[str, List[Bar]] = outcome["phase_bars_by_symbol"]
        signal_lists: Dict[str, List[Optional[Signal]]] = outcome["signal_lists"]

        portfolio: Optional[PortfolioSnapshot] = None
        rerun_cash = 0.0
        rerun_ledger: List[Tuple[str, str, Tuple[Tuple[str, Any], ...]]] = []
        for sym in sorted(phase_bars_by_symbol.keys()):
            rerun = self.simulator.run(
                bars=phase_bars_by_symbol[sym],
                signals=signal_lists[sym],
                initial_portfolio=portfolio,
            )
            rerun_cash += rerun.final_cash
            for event in rerun.trade_ledger:
                detail = tuple(sorted((event.detail or {}).items(), key=lambda kv: str(kv[0])))
                rerun_ledger.append((event.event_type, event.order_id, detail))
            portfolio = rerun.final_portfolio

        original_cash = sum(result.final_cash for result in results)
        original_ledger: List[Tuple[str, str, Tuple[Tuple[str, Any], ...]]] = []
        for result in results:
            for event in result.trade_ledger:
                detail = tuple(sorted((event.detail or {}).items(), key=lambda kv: str(kv[0])))
                original_ledger.append((event.event_type, event.order_id, detail))

        return rerun_cash == original_cash and rerun_ledger == original_ledger

    # -----------------------------------------------------------------
    # Parameter stability analysis

    def parameter_stability_analysis(
        self,
        bars_by_symbol: Dict[str, Dict[str, List[Any]]],
        symbols: List[str],
        folds: List[Dict[str, Tuple[date, date]]],
        perturbation: float = 0.2,
    ) -> Dict[str, Any]:
        """Run folds with perturbed parameters and report test-PnL dispersion.

        Fast/slow lengths are perturbed by ±perturbation (clamped to >= 2 and
        slow > fast). PROVISIONAL parameters are never tuned toward
        profitability; this analysis only measures how sensitive the test
        results are to parameter changes (parameter stability).
        """
        base_results = [r["test_total_pnl"] for r in self.fold_results]
        variants: List[Dict[str, Any]] = []
        all_variant_pnls: List[float] = []

        for direction in (-1, 1):
            fast = max(2, int(round(self.hypothesis.fast_length * (1 + direction * perturbation))))
            slow = max(fast + 1, int(round(self.hypothesis.slow_length * (1 + direction * perturbation))))
            variant_hypothesis = MaCrossHypothesis(
                fast_length=fast,
                slow_length=slow,
                min_shares=self.hypothesis.min_shares,
                max_positions=self.hypothesis.max_positions,
                max_holding_days=self.hypothesis.max_holding_days,
                commission_per_order=self.hypothesis.commission_per_order,
                slippage_pct=self.hypothesis.slippage_pct,
            )
            variant_evaluator = WalkForwardEvaluator(
                self.simulator, variant_hypothesis, self.period_split, self.registry, verify_determinism=False
            )
            for idx, fold in enumerate(folds):
                fold_result = variant_evaluator.run_fold(idx, fold, bars_by_symbol, symbols)
                all_variant_pnls.append(fold_result["test_total_pnl"])
            variants.append(
                {
                    "hypothesis_id": variant_hypothesis.hypothesis_id,
                    "test_pnls": [round(r["test_total_pnl"], 2) for r in variant_evaluator.fold_results],
                }
            )

        mean_pnl = sum(all_variant_pnls) / len(all_variant_pnls) if all_variant_pnls else 0.0
        spread = max(all_variant_pnls) - min(all_variant_pnls) if all_variant_pnls else 0.0
        return {
            "perturbation": perturbation,
            "variants": variants,
            "base_test_pnls": [round(p, 2) for p in base_results],
            "variant_mean_test_pnl": round(mean_pnl, 2),
            "variant_test_pnl_spread": round(spread, 2),
            "note": "Parameter stability only; PROVISIONAL parameters are never tuned toward profitability",
        }

    # -----------------------------------------------------------------
    # Cost and slippage sensitivity analysis

    def cost_sensitivity_analysis(
        self,
        bars_by_symbol: Dict[str, Dict[str, List[Any]]],
        symbols: List[str],
        folds: List[Dict[str, Tuple[date, date]]],
        commission_multipliers: Tuple[float, ...] = (0.0, 1.0, 3.0),
        slippage_multipliers: Tuple[float, ...] = (0.0, 1.0),
    ) -> Dict[str, Any]:
        """Re-run folds under cost variants and report metric deltas.

        Commission multipliers scale the per-order commission. Slippage
        multipliers are applied POST-HOC (fill notional x slippage_pct x
        multiplier) because MARKET fills with price=None record zero
        realized slippage in the simulator; this is a documented V1
        approximation.
        """
        base_pnl = sum(r["test_total_pnl"] for r in self.fold_results)
        scenarios: List[Dict[str, Any]] = []

        for cm in commission_multipliers:
            for sm in slippage_multipliers:
                variant_hypothesis = MaCrossHypothesis(
                    fast_length=self.hypothesis.fast_length,
                    slow_length=self.hypothesis.slow_length,
                    min_shares=self.hypothesis.min_shares,
                    max_positions=self.hypothesis.max_positions,
                    max_holding_days=self.hypothesis.max_holding_days,
                    commission_per_order=self.hypothesis.commission_per_order * cm,
                    slippage_pct=self.hypothesis.slippage_pct,
                )
                variant_simulator = EventDrivenSimulator(
                    mode=self.simulator.mode,
                    commission_model=self.simulator.commission_model,
                    commission_rate=self.simulator.commission_rate * cm,
                    start_cash=self.simulator.start_cash,
                    fill_assumption=self.simulator.fill_assumption,
                    seed=self.simulator.seed,
                    max_positions=self.simulator.max_positions,
                )
                variant_evaluator = WalkForwardEvaluator(
                    variant_simulator,
                    variant_hypothesis,
                    self.period_split,
                    self.registry,
                    verify_determinism=False,
                )
                for idx, fold in enumerate(folds):
                    variant_evaluator.run_fold(idx, fold, bars_by_symbol, symbols)

                raw_pnl = sum(r["test_total_pnl"] for r in variant_evaluator.fold_results)
                estimated_slippage = (
                    sum(r.get("test_estimated_slippage", 0.0) for r in variant_evaluator.fold_results) * sm
                )
                pnl = raw_pnl - estimated_slippage

                scenarios.append(
                    {
                        "commission_multiplier": cm,
                        "slippage_multiplier": sm,
                        "test_total_pnl": round(pnl, 2),
                        "delta_vs_base": round(pnl - base_pnl, 2),
                        "estimated_slippage": round(estimated_slippage, 2),
                    }
                )

        return {
            "base_test_pnl": round(base_pnl, 2),
            "scenarios": scenarios,
            "note": (
                "Cost and slippage sensitivity; slippage deltas are documented "
                "post-hoc approximations (MARKET fills record zero realized slippage)"
            ),
        }

    # -------------------------------------------------------------------------
    # Aggregate across folds

    def aggregate_results(self) -> Dict[str, Any]:
        """Aggregate walk-forward results with multiple-testing awareness."""
        if not self.fold_results:
            return {"error": "No fold results recorded"}

        n_folds = len(self.fold_results)

        # Test-period metrics across folds
        test_final_cash = [r["test_final_cash"] for r in self.fold_results]
        test_total_pnl = [r["test_total_pnl"] for r in self.fold_results]
        test_commission = [r["test_total_commission"] for r in self.fold_results]
        test_slippage = [r["test_total_slippage"] for r in self.fold_results]
        test_drawdowns = [r["test_max_drawdown"] for r in self.fold_results]
        test_turnovers = [r["test_turnover"] for r in self.fold_results]
        test_exposures = [
            r["test_gross_exposure_pct"] for r in self.fold_results if r["test_gross_exposure_pct"] is not None
        ]

        # Validation metrics (should be close to train if hypothesis is stable)
        val_total_pnl = [r["val_total_pnl"] for r in self.fold_results]

        # Train metrics
        train_total_pnl = [r["train_total_pnl"] for r in self.fold_results]

        # Consistency checks
        cash_spread_test = max(test_final_cash) - min(test_final_cash)
        pnl_spread_test = max(test_total_pnl) - min(test_total_pnl)

        # Multiple-testing: simple Bonferroni-adjusted significance threshold
        # (in production would use proper FDR procedure)
        alpha = 0.05
        bonferroni_alpha = alpha / n_folds if n_folds > 0 else alpha

        # Out-of-sample expectancy (test - train) after costs
        oos_expectancy = []
        for i in range(n_folds):
            oos = test_total_pnl[i] - train_total_pnl[i] - (test_commission[i] + test_slippage[i])
            oos_expectancy.append(oos)

        oos_mean = sum(oos_expectancy) / n_folds if n_folds > 0 else 0
        oos_spread = max(oos_expectancy) - min(oos_expectancy) if n_folds > 1 else 0

        return {
            "cash_spread_test": round(cash_spread_test, 2),
            "pnl_spread_test": round(pnl_spread_test, 2),
            "oos_expectancy_mean": round(oos_mean, 2),
            "oos_expectancy_spread": round(oos_spread, 2),
            "bonferroni_alpha": round(bonferroni_alpha, 4),
            "folds": n_folds,
            "all_test_pnl": [round(p, 2) for p in test_total_pnl],
            "all_train_pnl": [round(p, 2) for p in train_total_pnl],
            "all_val_pnl": [round(p, 2) for p in val_total_pnl],
            "max_drawdown_max": round(max(test_drawdowns), 4) if test_drawdowns else None,
            "turnover_max": round(max(test_turnovers), 4) if test_turnovers else None,
            "gross_exposure_pct_mean": round(sum(test_exposures) / len(test_exposures), 2) if test_exposures else None,
            "long_only_preserved_all_folds": all(r["long_only_preserved"] for r in self.fold_results),
        }

    # -------------------------------------------------------------------------
    # Bootstrap drawdown distribution

    def bootstrap_drawdown_distribution(self, n_resamples: int = 1000, seed: int = 42) -> Dict[str, Any]:
        """Estimate the drawdown distribution via bootstrap resampling of trade returns.

        Resamples individual trade PnLs from the recorded test periods across
        folds, builds an equity curve per resample starting from the
        simulator's starting cash, and computes the max drawdown on that
        equity curve. Deterministic given the seed.
        """
        import random

        if not self.fold_results:
            return {"error": "No fold results recorded"}

        random.seed(seed)

        all_trade_pnls: List[float] = []
        for r in self.fold_results:
            all_trade_pnls.extend(r.get("test_trade_pnls", []))

        if not all_trade_pnls:
            return {"error": "No recorded trade PnLs for bootstrap"}

        start_equity = float(self.simulator.start_cash)

        drawdowns: List[float] = []
        for _ in range(n_resamples):
            resample = [random.choice(all_trade_pnls) for _ in range(len(all_trade_pnls))]
            equity = start_equity
            peak = equity
            max_dd = 0.0
            for pnl in resample:
                equity += pnl
                if equity > peak:
                    peak = equity
                if peak > 0:
                    dd = (peak - equity) / peak
                    if dd > max_dd:
                        max_dd = dd
            drawdowns.append(max_dd)

        drawdowns_sorted = sorted(drawdowns)

        def _pct(p: float) -> float:
            idx = min(int(p * len(drawdowns_sorted)), len(drawdowns_sorted) - 1)
            return round(float(drawdowns_sorted[idx]), 4)

        return {
            "mean": round(float(sum(drawdowns) / len(drawdowns)), 4),
            "p5": _pct(0.05),
            "p10": _pct(0.10),
            "p90": _pct(0.90),
            "p95": _pct(0.95),
            "min": round(float(min(drawdowns)), 4),
            "max": round(float(max(drawdowns)), 4),
            "n_resamples": n_resamples,
            "seed": seed,
        }
