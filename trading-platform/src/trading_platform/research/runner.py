"""Research runner: executes full hypothesis reverification for a family.

Pipeline per family:

    features -> regimes -> parameter grid
    -> purged walk-forward OOS backtests (per config)
    -> cost stress (1x / 1.5x / 2x)
    -> metrics, PSR/DSR, bootstrap, PBO, concentration, regime decomposition
    -> family-level parameter stability + White reality check
    -> promotion gate decision
    -> persisted JSON + Markdown report (versioned artifact directory)

Every tested configuration is recorded — failures are data. When the
universe data is not the point-in-time constituent set, the report carries
an explicit survivorship-bias evidence ceiling.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

from trading_platform.features.indicators import compute_features
from trading_platform.promotion import (
    PromotionPolicy,
    evaluate_promotion,
    persist_decision,
)
from trading_platform.regimes import RegimeEngine
from trading_platform.research.backtest import (
    BacktestConfig,
    CostModel,
    run_backtest,
)
from trading_platform.research.report import render_markdown_report
from trading_platform.strategies.candidates import STRATEGY_FAMILIES
from trading_platform.validation import (
    COST_STRESS_MULTIPLIERS,
    BootstrapConfig,
    purged_walk_forward_splits,
    validate_configuration,
)
from trading_platform.validation.engine import (
    ConfigResult,
    FamilyEvidence,
    family_level_diagnostics,
)

RESEARCH_RUNNER_VERSION = "1.0.0"


class ExternalSetupRequired(RuntimeError):
    """Raised when required external inputs (market data) are absent."""


def _git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:  # noqa: BLE001 - provenance best-effort
        return "unknown"


def _dataset_hash(frames: Mapping[str, pd.DataFrame]) -> str:
    h = hashlib.sha256()
    for symbol in sorted(frames):
        df = frames[symbol]
        h.update(symbol.encode())
        h.update(pd.util.hash_pandas_object(df.round(8)).to_numpy().tobytes())
    return h.hexdigest()


def build_feature_frames(
    ohlc_by_symbol: Mapping[str, pd.DataFrame],
    benchmark_close: pd.Series,
    param_variants: Sequence[Mapping[str, Any]],
) -> Dict[str, pd.DataFrame]:
    """Compute the union of feature specs across the grid per symbol.

    Frames include raw OHLC columns so strategies and the backtester can use
    price/volume directly.
    """
    spec_keys: Dict[str, Any] = {}
    for params in param_variants:
        for spec in params["_specs"]:
            spec_keys[spec.fingerprint()] = spec
    specs = sorted(spec_keys.values(), key=lambda s: s.fingerprint())
    out: Dict[str, pd.DataFrame] = {}
    for symbol, ohlc in ohlc_by_symbol.items():
        ff = compute_features(ohlc, specs, symbol=symbol, benchmark=benchmark_close)
        frame = ohlc.join(ff.frame)
        out[symbol] = frame
    return out


def run_family_research(
    family_name: str,
    ohlc_by_symbol: Mapping[str, pd.DataFrame],
    benchmark: pd.DataFrame,
    *,
    param_grid: Sequence[Mapping[str, Any]],
    n_folds: int = 4,
    min_train: int = 252,
    embargo: int = 30,
    anchored: bool = True,
    backtest_config: Optional[BacktestConfig] = None,
    promotion_policy: Optional[PromotionPolicy] = None,
    bootstrap: Optional[BootstrapConfig] = BootstrapConfig(n_resamples=500, block_length=10, seed=42),
    output_dir: Optional[Path] = None,
    promotions_root: Optional[Path] = None,
    universe_is_point_in_time: bool = False,
    universe_membership: Optional[Mapping[Any, Sequence[str]]] = None,
) -> Dict[str, Any]:
    """Full reverification for one strategy family. Returns the report dict."""
    if family_name not in STRATEGY_FAMILIES or STRATEGY_FAMILIES[family_name][1] is None:
        raise ValueError(f"unknown or non-candidate family: {family_name!r}")
    if not ohlc_by_symbol:
        raise ExternalSetupRequired("no universe data supplied (REQUIRES_EXTERNAL_SETUP)")
    params_cls, signal_fn = STRATEGY_FAMILIES[family_name]
    cfg = backtest_config or BacktestConfig()
    engine = RegimeEngine()

    benchmark_close = benchmark["close"].astype(float)
    regime_frame = engine.classify_series(benchmark)
    regimes_by_date = {d: engine.classify_at(benchmark, d) for d in regime_frame.index}

    # Instantiate parameter objects and their feature specs.
    param_objects: List[Tuple[str, Any]] = []
    for raw in param_grid:
        cleaned = {k: v for k, v in raw.items() if not k.startswith("_")}
        params = params_cls(**cleaned)
        param_objects.append((params.strategy_id, params))

    frames = build_feature_frames(
        ohlc_by_symbol,
        benchmark_close,
        [{**{}, "_specs": p.feature_specs()} for _, p in param_objects],
    )
    scale = frames[next(iter(frames))]
    n_obs = len(scale)

    # Universal trading calendar = benchmark dates (point-in-time).
    cal = list(benchmark_close.index)
    folds = purged_walk_forward_splits(
        len(cal), n_folds=n_folds, min_train=min_train, embargo=embargo, anchored=anchored
    )
    family_id = f"{family_name}@{_dataset_hash(frames)[:12]}"
    family_ev = FamilyEvidence(family_id, trial_count=len(param_objects), config_ids=[c for c, _ in param_objects])

    # Optional point-in-time universe membership: decision day -> member set.
    member_fn = None
    if universe_membership:
        import bisect

        member_dates = sorted(universe_membership)
        member_sets = {d: set(symbols) for d, symbols in universe_membership.items()}

        def member_fn(decision_day: Any) -> Set[str]:
            idx = bisect.bisect_right(member_dates, decision_day) - 1
            return member_sets[member_dates[idx]] if idx >= 0 else set()

    config_results: List[ConfigResult] = []
    config_full_returns: Dict[str, np.ndarray] = {}
    per_config_reports: List[Dict[str, Any]] = []

    for config_id, params in param_objects:
        oos_returns: List[float] = []
        oos_index: List[Any] = []
        oos_trades: List[float] = []
        symbol_pnl: Dict[str, float] = {}
        fold_sharpes: List[float] = []
        for fold in folds:
            test_days = cal[fold.test_start : fold.test_end]
            sub_frames = {s: df.loc[: test_days[-1]] for s, df in frames.items()}
            res = run_backtest(sub_frames, signal_fn, params, regimes_by_date, cfg, member_fn)
            dr = res.daily_returns
            in_test = dr.loc[[d for d in dr.index if d in set(test_days)]]
            oos_returns.extend(float(v) for v in in_test.to_numpy())
            oos_index.extend(list(in_test.index))
            for t in res.trades:
                if t.exit_date in set(test_days):
                    oos_trades.append(t.net_pnl)
                    symbol_pnl[t.symbol] = symbol_pnl.get(t.symbol, 0.0) + t.net_pnl
            if in_test.size >= 2 and float(in_test.std(ddof=1)) > 0:
                fold_sharpes.append(float(in_test.mean() / in_test.std(ddof=1) * np.sqrt(252)))
            else:
                fold_sharpes.append(0.0)

        # Cost stress on the full concatenated OOS path.
        cost_stress: Dict[float, Mapping[str, float]] = {}
        for mult in COST_STRESS_MULTIPLIERS:
            stress_cfg = BacktestConfig(
                initial_cash=cfg.initial_cash,
                max_positions=cfg.max_positions,
                risk_fraction=cfg.risk_fraction,
                max_holding_days=cfg.max_holding_days,
                atr_stop_multiple=cfg.atr_stop_multiple,
                atr_trailing_multiple=cfg.atr_trailing_multiple,
                cost=CostModel(cfg.cost.commission_per_order, cfg.cost.slippage_pct, mult),
            )
            oos_r: List[float] = []
            oos_t: List[float] = []
            for fold in folds:
                test_days = cal[fold.test_start : fold.test_end]
                sub_frames = {s: df.loc[: test_days[-1]] for s, df in frames.items()}
                res = run_backtest(sub_frames, signal_fn, params, regimes_by_date, stress_cfg, member_fn)
                dr = res.daily_returns
                in_test = dr.loc[[d for d in dr.index if d in set(test_days)]]
                oos_r.extend(float(v) for v in in_test.to_numpy())
                oos_t.extend(t.net_pnl for t in res.trades if t.exit_date in set(test_days))
            arr = np.asarray(oos_r)
            pnls = np.asarray(oos_t)
            cost_stress[mult] = {
                "total_return": float(np.prod(1.0 + arr) - 1.0) if arr.size else 0.0,
                "expectancy": float(pnls.mean()) if pnls.size else 0.0,
                "sharpe": float(arr.mean() / arr.std(ddof=1) * np.sqrt(252))
                if arr.size >= 2 and arr.std(ddof=1) > 0
                else 0.0,
            }

        config_full_returns[config_id] = np.asarray(oos_returns)
        bench_series = benchmark_close.pct_change().reindex(pd.Index(oos_index)).fillna(0.0).to_numpy()
        regime_labels = regime_frame["trend"].reindex(pd.Index(oos_index))
        config_results.append(
            ConfigResult(
                config_id=config_id,
                params={k: v for k, v in vars(params).items()},
                daily_returns=oos_returns,
                trade_pnls=oos_trades,
                symbol_pnls=symbol_pnl,
                benchmark_returns=bench_series,
                regime_labels=regime_labels,
                cost_stress=cost_stress,
                fold_sharpes=fold_sharpes,
            )
        )

    # PBO across configurations on the aligned OOS return matrix.
    min_len = min(len(v) for v in config_full_returns.values()) if config_full_returns else 0
    pbo_matrix = (
        np.column_stack([v[:min_len] for v in config_full_returns.values()])
        if min_len >= 32 and len(config_full_returns) >= 2
        else None
    )
    family_diag = family_level_diagnostics(
        config_results,
        family_ev,
        benchmark_returns=(benchmark_close.pct_change().dropna().to_numpy() if len(benchmark_close) > 1 else None),
        bootstrap=bootstrap,
    )

    for i, result in enumerate(config_results):
        report = validate_configuration(
            result,
            family_ev,
            bootstrap=bootstrap,
            pbo_returns_matrix=pbo_matrix,
        )
        policy = promotion_policy or PromotionPolicy()
        if len(param_objects) < 2:
            policy = PromotionPolicy(**{**vars(policy), "require_parameter_stability": False})
        decision = evaluate_promotion(family_name, report, policy, family_diagnostics=family_diag)
        decision_dict = decision.to_dict()
        report["promotion"] = decision_dict
        per_config_reports.append(report)
        persist_decision(decision, root=promotions_root)

    best = max(
        per_config_reports,
        key=lambda r: r.get("metrics", {}).get("sharpe", float("-inf")),
        default=None,
    )
    pit_universe = universe_is_point_in_time or universe_membership is not None
    summary = {
        "family_id": family_id,
        "family_name": family_name,
        "source_commit": _git_commit(),
        "dataset_hash": _dataset_hash(frames) if frames else "",
        "universe": sorted(frames.keys()),
        "n_observations": n_obs,
        "folds": [f.to_dict() for f in folds],
        "embargo": embargo,
        "anchored": anchored,
        "trial_count": len(param_objects),
        "config_reports": per_config_reports,
        "family_diagnostics": family_diag,
        "best_config_id": best["config_id"] if best else None,
        "promotion_summary": {
            "approved": [
                r["config_id"] for r in per_config_reports if r.get("promotion", {}).get("status") == "APPROVED"
            ],
            "rejected": [
                r["config_id"] for r in per_config_reports if r.get("promotion", {}).get("status") == "REJECTED"
            ],
        },
        "known_biases": []
        if pit_universe
        else [
            "survivorship/universe bias: universe is not point-in-time constituent membership",
            "daily bars only: intraday fills modeled, not observed",
            "costs modeled (fixed commission + slippage); spreads approximated, not observed",
        ],
        "evidence_ceiling": ("FULL_OOS_CAPABLE" if pit_universe else "CAPPED: universe bias present"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": RESEARCH_RUNNER_VERSION,
    }
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = output_dir / f"{family_name}--{stamp}"
        base.with_suffix(".json").write_text(
            json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        base.with_suffix(".md").write_text(render_markdown_report(summary), encoding="utf-8")
    return summary


def load_parquet_universe(data_dir: Path, symbols: Sequence[str]) -> Dict[str, pd.DataFrame]:
    """Load a universe of daily OHLCV frames from a parquet data directory."""
    if not data_dir.exists():
        raise ExternalSetupRequired(f"data directory {data_dir} not found (REQUIRES_EXTERNAL_SETUP)")
    frames: Dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        path = data_dir / f"{symbol}.parquet"
        if not path.exists():
            raise ExternalSetupRequired(f"data for {symbol} not found at {path} (REQUIRES_EXTERNAL_SETUP)")
        df = pd.read_parquet(path)
        frames[symbol] = df
    return frames
