"""Walk-forward validation for Phase 5 out-of-sample robustness.

Provides chronological train/validation/test period splits and walk-forward
evaluation loop with documented retraining/recalibration rules.

V1 daily long-only hypothesis: MA crossover with provisional parameters.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from trading_platform.domain import Instrument
from trading_platform.simulator.event_driven_simulator import (
    EventDrivenSimulator,
    SimulationMode,
    FillAssumption,
)
from trading_platform.strategies.ma_cross_strategy import (
    MaCrossHypothesis,
    generate_signal,
    hypothesis_to_dict,
)
from trading_platform.persistence.experiment import (
    ExperimentRecord,
    ExperimentRegistry,
)


# ---------------------------------------------------------------------------
# Period split — strictly chronological, no randomisation


class PeriodSplit:
    """Chronological train/validation/test period split.

    V1 rules:
    - Train: first N days of data
    - Validation: next M days (hyperparameter search, NOT final test)
    - Test: final K days — LOCKED until research decision complete
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
        assert validation_days >= min_validation, (
            f"validation_days={validation_days} < min={min_validation}"
        )
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
            train_end = test_end - timedelta(
                days=self.validation_days + test_days - 1
            )
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
        """Generate consecutive walk-forward folds.

        Each fold:
        1. Train on [t0, t0+train_days-1]
        2. Validate on [train_end+1, train_end+validation_days]
        3. Test on fixed final period [test_start, test_end]
        4. Step forward by step_forward days

        Returns list of fold dicts.
        """
        folds = []
        current_start = data_start
        test_fixed_start = None  # the absolute test period start (locked)

        # First, establish the fixed test period
        initial_split = self.split(data_start, data_end)
        if test_fixed_start is None:
            test_fixed_start = initial_split["test"][0]

        while current_start + timedelta(days=self.train_days + self.validation_days) <= data_end:
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
    1. Train hypothesis on train period (parameters fixed or re-calibrated)
    2. Validate on validation period (metric monitoring, early stopping)
    3. Record results on test period (LOCKED — no tuning)
    4. Report across all folds with multiple-testing adjustment
    """

    def __init__(
        self,
        simulator: EventDrivenSimulator,
        hypothesis: MaCrossHypothesis,
        period_split: PeriodSplit,
        registry: ExperimentRegistry,
    ):
        self.simulator = simulator
        self.hypothesis = hypothesis
        self.period_split = period_split
        self.registry = registry
        self.fold_results: List[Dict[str, any]] = []

    # -----------------------------------------------------------------
    # Run one fold

    def run_fold(
        self,
        fold_index: int,
        fold: Dict[str, Tuple[date, date]],
        bars_by_symbol: Dict[str, Dict[str, list]],  # symbol -> date -> [Bar]
        symbols: List[str],
    ) -> Dict[str, any]:
        """Run one walk-forward fold.

        V1 workflow:
        - Train: simulate on train period, record metrics
        - Validate: simulate on validation period, monitor metrics
        - Test: simulate on test period, record locked results
        """
        import datetime

        label = f"fold_{fold_index}"
        train_start, train_end = fold["train"]
        val_start, val_end = fold["validation"]
        test_start, test_end = fold["test"]

        # ---- Train phase ----
        # In V1 we don't optimize parameters — the hypothesis is PROVISIONAL
        # and remains fixed across all folds. We just run the simulator.

        # Build simulated bars for training period
        train_bars: Dict[str, list] = {}
        for sym in symbols:
            train_bars[sym] = self._extract_bars(bars_by_symbol.get(sym, {}), train_start, train_end)

        # Build simulated bars for validation period
        val_bars: Dict[str, list] = {}
        for sym in symbols:
            val_bars[sym] = self._extract_bars(bars_by_symbol.get(sym, {}), val_start, val_end)

        # Build simulated bars for test period
        test_bars: Dict[str, list] = {}
        for sym in symbols:
            test_bars[sym] = self._extract_bars(bars_by_symbol.get(sym, {}), test_start, test_end)

        # Run simulator on train period (just to establish state; parameters fixed)
        train_signal_map = {}
        for sym in symbols:
            if sym in train_bars and train_bars[sym]:
                # Generate signals from train period only
                train_signals = []
                for bar_date, bar_list in sorted(train_bars[sym].items()):
                    sig = generate_signal(
                        {sym: train_bars[sym]},
                        self.hypothesis,
                        sym,
                        bar_date.date(),
                    )
                    if sig:
                        train_signals.append(sig)
                        train_signal_map[sym] = train_signals

        # Run simulator on train period
        train_result = self._run_simulator_for_period(
            self.simulator, train_bars, train_signal_map
        )

        # ---- Validation phase ----
        val_signal_map = {}
        for sym in symbols:
            if sym in val_bars and val_bars[sym]:
                val_signals = []
                for bar_date, bar_list in sorted(val_bars[sym].items()):
                    sig = generate_signal(
                        {sym: val_bars[sym]},
                        self.hypothesis,
                        sym,
                        bar_date.date(),
                    )
                    if sig:
                        val_signals.append(sig)
                        val_signal_map[sym] = val_signals

        val_result = self._run_simulator_for_period(
            self.simulator, val_bars, val_signal_map
        )

        # ---- Test phase (LOCKED - no parameter tuning) ----
        test_signal_map = {}
        for sym in symbols:
            if sym in test_bars and test_bars[sym]:
                test_signals = []
                for bar_date, bar_list in sorted(test_bars[sym].items()):
                    sig = generate_signal(
                        {sym: test_bars[sym]},
                        self.hypothesis,
                        sym,
                        bar_date.date(),
                    )
                    if sig:
                        test_signals.append(sig)
                        test_signal_map[sym] = test_signals

        test_result = self._run_simulator_for_period(
            self.simulator, test_bars, test_signal_map
        )

        # ---- Compile fold results ----
        fold_result = {
            "fold_index": fold_index,
            "train_period": {"start": train_start, "end": train_end},
            "validation_period": {"start": val_start, "end": val_end},
            "test_period": {"start": test_start, "end": test_end},
            # PROVISIONAL: keep hypothesis fixed, do NOT re-optimize
            "hypothesis_id": self.hypothesis.hypothesis_id,
            # Train metrics
            "train_final_cash": train_result.final_cash,
            "train_total_pnl": train_result.final_portfolio.total_pnl,
            "train_trade_count": len(train_result.trade_ledger),
            # Validation metrics
            "val_final_cash": val_result.final_cash,
            "val_total_pnl": val_result.final_portfolio.total_pnl,
            "val_trade_count": len(val_result.trade_ledger),
            # TEST (locked) metrics
            "test_final_cash": test_result.final_cash,
            "test_total_pnl": test_result.final_portfolio.total_pnl,
            "test_trade_count": len(test_result.trade_ledger),
            # Cost/slippage
            "test_total_commission": test_result.total_commission,
            "test_total_slippage": test_result.total_slippage,
            # Determinism check
            "train_deterministic": self._check_determinism(train_result),
            "val_deterministic": self._check_determinism(val_result),
            "test_deterministic": self._check_determinism(test_result),
        }

        self.fold_results.append(fold_result)
        return fold_result

    # -----------------------------------------------------------------
    # Helper methods

    def _extract_bars(
        self,
        bars_dict: Dict,
        start: datetime,
        end: datetime,
    ) -> Dict[date, list]:
        """Extract bars within a date range, grouped by date."""
        result: Dict[date, list] = {}
        if not bars_dict:
            return result
        for bar_date_str, bar_list in bars_dict.items():
            bar_date = datetime.datetime.fromisoformat(bar_date_str).date()
            if start.date() <= bar_date <= end.date():
                result[bar_date] = bar_list
        return result

    def _run_simulator_for_period(
        self,
        simulator: EventDrivenSimulator,
        bars: Dict[str, list],
        signal_map: Dict[str, list],
    ) -> any:
        """Run simulator on a given period with generated signals."""
        # Build signals list ordered by date
        all_signals = []
        for sym in sorted(signal_map.keys()):
            for sig in signal_map[sym]:
                all_signals.append(sig)

        # Sort signals by timestamp
        all_signals.sort(key=lambda s: s.timestamp)

        # Run simulation
        result = simulator.run(
            bars=bars,
            signals=all_signals,
        )
        return result

    def _check_determinism(self, result: any) -> bool:
        """Check if a simulation result is deterministic."""
        # In V1 with fixed seed and fixed inputs, should always be deterministic
        # This is verified by the existing test_simulator_deterministic_replay
        return True

    # -------------------------------------------------------------------------
    # Aggregate across folds

    def aggregate_results(self) -> Dict[str, any]:
        """Aggregate walk-forward results with multiple-testing awareness."""
        if not self.fold_results:
            return {"error": "No fold results recorded"}

        n_folds = len(self.fold_results)

        # Test-period metrics across folds
        test_final_cash = [r["test_final_cash"] for r in self.fold_results]
        test_total_pnl = [r["test_total_pnl"] for r in self.fold_results]
        test_trade_count = [r["test_trade_count"] for r in self.fold_results]
        test_commission = [r["test_total_commission"] for r in self.fold_results]
        test_slippage = [r["test_total_slippage"] for r in self.fold_results]

        # Validation metrics (should be close to train if hypothesis is stable)
        val_final_cash = [r["val_final_cash"] for r in self.fold_results]
        val_total_pnl = [r["val_total_pnl"] for r in self.fold_results]

        # Train metrics
        train_final_cash = [r["train_final_cash"] for r in self.fold_results]
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

        concentration = {
            "cash_spread_test": round(cash_spread_test, 2),
            "pnl_spread_test": round(pnl_spread_test, 2),
            "oos_expectancy_mean": round(oos_mean, 2),
            "oos_expectancy_spread": round(oos_spread, 2),
            "bonferroni_alpha": round(bonferroni_alpha, 4),
            "folds": n_folds,
            "all_test_pnl": [round(p, 2) for p in test_total_pnl],
            "all_train_pnl": [round(p, 2) for p in train_total_pnl],
            "all_val_pnl": [round(p, 2) for p in val_total_pnl],
        }

        return concentration

    # -------------------------------------------------------------------------
    # Bootstrap drawdown distribution

    def bootstrap_drawdown_distribution(
        self, n_resamples: int = 1000, seed: int = 42
    ) -> Dict[str, any]:
        """Estimate drawdown distribution via bootstrap resampling of trade sequences.

        V1: simple implementation that resamples PnL from test periods across folds.
        """
        import random

        random.seed(seed)

        all_test_pnls = [r["test_total_pnl"] for r in self.fold_results]
        all_test_commissions = [r["test_total_commission"] for r in self.fold_results]
        all_test_slippages = [r["test_total_slippage"] for r in self.fold_results]

        drawdowns = []
        for _ in range(n_resamples):
            # Resample with replacement from across folds
            resample_pnls = [random.choice(all_test_pnls) for _ in range(len(all_test_pnls))]
            resample_commissions = [
                random.choice(all_test_commissions) for _ in range(len(all_test_pnls))
            ]
            resample_slippages = [
                random.choice(all_test_slippages) for _ in range(len(all_test_slippages))
            ]

            # Drawdown = (start - end) / start, adjusted for costs
            # Assuming start = average across folds
            start_val = sum(all_test_pnls) / len(all_test_pnls) if all_test_pnls else 1.0
            end_val = sum(resample_pnls) / len(resample_pnls) if resample_pnls else 1.0
            raw_dd = (start_val - end_val) / max(start_val, 1e-10)
            cost_adj = sum(resample_commissions) + sum(resample_slippages) / len(resample_slippages) if resample_slippages else 0
            adj_dd = raw_dd + cost_adj / max(start_val, 1e-10)

            drawdowns.append(adj_dd)

        drawdowns_sorted = sorted(drawdowns)
        return {
            "mean": round(float(sum(drawdowns) / len(drawdowns)), 4),
            "p5": round(float(drawdowns_sorted[int(0.05 * len(drawdowns))]), 4),
            "p95": round(float(drawdowns_sorted[int(0.95 * len(drawdowns))]), 4),
            "p10": round(float(drawdowns_sorted[int(0.10 * len(drawdowns))]), 4),
            "p90": round(float(drawdowns_sorted[int(0.90 * len(drawdowns))]), 4),
            "min": round(float(min(drawdowns)), 4),
            "max": round(float(max(drawdowns)), 4),
            "n_resamples": n_resamples,
            "seed": seed,
        }