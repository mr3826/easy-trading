"""MVP-1 canonical research workflow: data gate -> fingerprint -> run-all -> verdict.

This module is the *application service* behind ``trading-platform research
run-all`` and the MVP E2E acceptance path. It orchestrates the existing
engines (``research.data_quality``, ``research.dataset``, ``research.runner``,
``promotion``) and adds no parallel implementations.

Hard rules enforced here:

- a preflight FAIL stops research (nothing runs);
- PASS_WITH_WARNINGS only proceeds with an explicit human acknowledgement,
  which is persisted into the dataset manifest, every family report's
  metadata and known-biases, and the final summary;
- the run is pinned to a dataset fingerprint + git commit + policy hash;
- "no strategy promoted" is a *successful* outcome, not an error.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from trading_platform.config import load_config
from trading_platform.promotion import PromotionPolicy
from trading_platform.research.data_quality import (
    STATUS_FAIL,
    STATUS_PASS_WARNINGS,
    Membership,
    membership_on,
    run_data_preflight,
)
from trading_platform.research.dataset import build_dataset_manifest
from trading_platform.research.runner import run_family_research
from trading_platform.strategies.candidates import STRATEGY_FAMILIES
from trading_platform.validation import BootstrapConfig

MVP_RESEARCH_VERSION = "1.0.0"

SUMMARY_JSON = "MVP_RESEARCH_SUMMARY.json"
SUMMARY_MD = "MVP_RESEARCH_SUMMARY.md"

VERDICT_APPROVED = "APPROVED"
VERDICT_REJECTED = "REJECTED"
VERDICT_RESEARCH_ONLY = "RESEARCH_ONLY"
VERDICT_ERROR = "ERROR"

# Exit codes for ``trading-platform research run-all`` (documented in docs/MVP1.md).
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_DATA_FAILED = 2
EXIT_EXTERNAL = 3
EXIT_WARNINGS_UNACKNOWLEDGED = 4


class MvpState(str, Enum):
    """Deterministic high-level product state (docs/MVP1.md)."""

    NOT_CONFIGURED = "NOT_CONFIGURED"
    REQUIRES_EXTERNAL_DATA = "REQUIRES_EXTERNAL_DATA"
    DATA_FAILED = "DATA_FAILED"
    DATA_READY = "DATA_READY"
    RESEARCH_RUNNING = "RESEARCH_RUNNING"
    NO_STRATEGY_PROMOTED = "NO_STRATEGY_PROMOTED"
    STRATEGY_RESEARCH_ONLY = "STRATEGY_RESEARCH_ONLY"
    STRATEGY_APPROVED_FOR_SHADOW = "STRATEGY_APPROVED_FOR_SHADOW"
    SHADOW_REQUIRES_FORWARD_EVIDENCE = "SHADOW_REQUIRES_FORWARD_EVIDENCE"
    PAPER_REQUIRES_EXTERNAL_SETUP = "PAPER_REQUIRES_EXTERNAL_SETUP"
    NOT_AUTHORIZED_LIVE = "NOT_AUTHORIZED_LIVE"


# Single source of truth for the research parameter grids (previously only
# inside scripts/run_strategy_research.py). Any change re-versions research.
PARAM_GRIDS: Dict[str, List[Dict[str, Any]]] = {
    "trend_relative_strength": [
        {"momentum_window": 63, "rs_window": 63},
        {"momentum_window": 126, "rs_window": 126},
        {"momentum_window": 63, "rs_window": 126},
        {"momentum_window": 126, "rs_window": 63},
    ],
    "breakout_volume": [
        {"breakout_window": 20, "min_relative_volume": 1.5},
        {"breakout_window": 55, "min_relative_volume": 1.5},
        {"breakout_window": 20, "min_relative_volume": 2.0},
        {"breakout_window": 55, "min_relative_volume": 2.0},
    ],
    "trend_pullback": [
        {"rsi_window": 3, "rsi_oversold": 20.0},
        {"rsi_window": 3, "rsi_oversold": 30.0},
        {"rsi_window": 5, "rsi_oversold": 20.0},
        {"rsi_window": 5, "rsi_oversold": 30.0},
    ],
}


def researchable_families() -> List[str]:
    """Families registered in ``STRATEGY_FAMILIES`` that can actually run.

    ``ma_cross_baseline`` is a control (no signal function) and is excluded;
    it stays available through the Phase-4 experiment harness.
    """
    return sorted(name for name, (_, signal) in STRATEGY_FAMILIES.items() if signal is not None)


def membership_calendar_map(membership: Membership, calendar: pd.DatetimeIndex) -> Dict[Any, List[str]]:
    """Expand range membership into decision-day -> member symbols (point-in-time)."""
    out: Dict[Any, List[str]] = {}
    for stamp in calendar:
        day = pd.Timestamp(stamp).date()
        out[stamp] = sorted(membership_on(membership, day))
    return out


class MvpRunError(RuntimeError):
    """Operationally actionable failure with an exit code and operator message."""

    def __init__(self, message: str, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def load_bar_frames(data_dir: Path, symbols: Sequence[str]) -> Dict[str, pd.DataFrame]:
    """Load the bar frames that actually exist in ``data_dir`` for ``symbols``."""
    frames: Dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        path = data_dir / f"{symbol}.parquet"
        if path.exists():
            frames[symbol] = pd.read_parquet(path)
    return frames


def resolve_mvp_state(output_root: Path, data_dir: Optional[Path], membership: Optional[Path]) -> MvpState:
    """Derive the product state from configuration + the latest run evidence."""
    latest = latest_summary(output_root)
    if latest is not None:
        summary = latest
        if summary.get("data_quality", {}).get("verdict") == STATUS_FAIL:
            return MvpState.DATA_FAILED
        promoted = [f for f in summary.get("families", []) if f.get("verdict") == VERDICT_APPROVED]
        if promoted:
            return MvpState.STRATEGY_APPROVED_FOR_SHADOW
        if any(f.get("verdict") == VERDICT_RESEARCH_ONLY for f in summary.get("families", [])):
            return MvpState.STRATEGY_RESEARCH_ONLY
        return MvpState.NO_STRATEGY_PROMOTED
    if data_dir is None or membership is None:
        return MvpState.NOT_CONFIGURED
    if not data_dir.exists() or not membership.exists():
        return MvpState.REQUIRES_EXTERNAL_DATA
    return MvpState.DATA_READY


def output_root_default() -> Path:
    return Path("artifacts") / "research"


def latest_summary(output_root: Path) -> Optional[Dict[str, Any]]:
    """Newest persisted MVP summary under the output root, if any."""
    if not output_root.exists():
        return None
    candidates = sorted(output_root.glob(f"*/{SUMMARY_JSON}")) + sorted(output_root.glob(f"*/*/{SUMMARY_JSON}"))
    if not candidates:
        direct = output_root / SUMMARY_JSON
        return json.loads(direct.read_text(encoding="utf-8")) if direct.exists() else None
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return json.loads(newest.read_text(encoding="utf-8"))


def run_mvp_research(
    *,
    data_dir: Path,
    benchmark: str,
    membership_path: Path,
    output_root: Path,
    accept_data_warnings: bool = False,
    n_folds: int = 4,
    min_train: int = 252,
    embargo: int = 30,
    bootstrap: BootstrapConfig = BootstrapConfig(n_resamples=1000, block_length=10, seed=42),
    code_commit: Optional[str] = None,
    families: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Execute the canonical MVP research workflow end to end.

    Returns the MVP summary dict (also persisted under
    ``<output_root>/<run-id>/``). Raises :class:`MvpRunError` (with exit code)
    when the data gate refuses or warnings are not acknowledged.

    ``families`` restricts which researchable families run (same gates, same
    fingerprinting); default is every registered researchable family.
    """
    from trading_platform.research.data_quality import MembershipManifestError, load_membership_manifest

    all_researchable = researchable_families()
    selected = list(all_researchable if families is None else families)
    unknown = sorted(set(selected) - set(all_researchable))
    if unknown:
        raise MvpRunError(
            f"unknown or non-researchable families: {unknown}; researchable: {all_researchable}", EXIT_ERROR
        )

    started_at = datetime.now(timezone.utc)
    if not membership_path.exists():
        raise MvpRunError(
            f"membership manifest not found: {membership_path} — obtain historical constituent "
            "membership (with exits) from your vendor and build it first: "
            "`trading-platform data build-membership --csv <vendor.csv> --output <manifest.json>`",
            EXIT_EXTERNAL,
        )
    try:
        membership = load_membership_manifest(membership_path)
    except (MembershipManifestError, ValueError, json.JSONDecodeError) as exc:
        raise MvpRunError(f"invalid membership manifest {membership_path}: {exc}", EXIT_DATA_FAILED) from exc

    symbols = sorted({*membership, benchmark})
    bars = load_bar_frames(data_dir, symbols)
    benchmark_frame = bars.pop(benchmark, None)
    if benchmark_frame is None:
        raise MvpRunError(
            f"benchmark {benchmark} not found at {data_dir / (benchmark + '.parquet')} — supply benchmark "
            "daily bars on the same trading calendar (REQUIRES_EXTERNAL_SETUP)",
            EXIT_EXTERNAL,
        )
    if not bars:
        raise MvpRunError(
            f"no universe bar data in {data_dir} for any of {symbols} — supply daily bars (REQUIRES_EXTERNAL_SETUP)",
            EXIT_EXTERNAL,
        )

    preflight = run_data_preflight(bars, benchmark_frame, membership)
    if preflight["status"] == STATUS_FAIL:
        verdict = "DATA_QUALITY: FAIL — research refused"
        raise MvpRunError(
            "data preflight FAILED: "
            + "; ".join(f"{c['name']}: {c['detail']}" for c in preflight["criticals"])
            + f" — fix the dataset; do not run strategy research on it ({verdict})",
            EXIT_DATA_FAILED,
        )
    if preflight["status"] == STATUS_PASS_WARNINGS and not accept_data_warnings:
        raise MvpRunError(
            "data preflight returned PASS_WITH_WARNINGS — every warning names a data defect and its "
            "research consequence. Review them, then re-run with --accept-data-warnings to record "
            "the acknowledgement. Warnings:\n"
            + "\n".join(f"  - {w['name']}: {w['detail']}" for w in preflight["warnings"]),
            EXIT_WARNINGS_UNACKNOWLEDGED,
        )

    accepted = list(preflight["warnings"]) if preflight["status"] == STATUS_PASS_WARNINGS else []
    manifest = build_dataset_manifest(
        data_dir=data_dir,
        benchmark=benchmark,
        membership_path=membership_path,
        bars=bars,
        benchmark_frame=benchmark_frame,
        membership=membership,
        preflight_report=preflight,
        accepted_warnings=accepted,
        warnings_acknowledged=bool(accepted) and accept_data_warnings,
    )
    fingerprint = str(manifest["dataset_fingerprint"])
    run_id = f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-{fingerprint[:8]}"
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "data_preflight.json").write_text(json.dumps(preflight, indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    if code_commit is None:
        try:
            from trading_platform.persistence.experiment import ExperimentRecord

            code_commit = ExperimentRecord.compute_code_hash()
        except RuntimeError:
            code_commit = "unknown"

    policy = PromotionPolicy()
    day_map = membership_calendar_map(membership, benchmark_frame.index)
    bias_lines = [f"data warning accepted by operator: {w['name']} — {w['detail']}" for w in accepted]
    run_metadata = {
        "run_id": run_id,
        "dataset_fingerprint": fingerprint,
        "dataset_manifest_version": manifest["version"],
        "data_preflight_verdict": preflight["status"],
        "accepted_warnings": accepted,
        "promotion_policy_hash": policy.policy_hash(),
        "mvp_research_version": MVP_RESEARCH_VERSION,
        "source_commit": code_commit,
    }

    family_results: List[Dict[str, Any]] = []
    experiments: List[Dict[str, Any]] = []
    for family in selected:
        grid = PARAM_GRIDS.get(family)
        if not grid:
            family_results.append({"family": family, "verdict": VERDICT_ERROR, "reason": "no pinned parameter grid"})
            continue
        try:
            report = run_family_research(
                family,
                bars,
                benchmark_frame,
                param_grid=grid,
                n_folds=n_folds,
                min_train=min_train,
                embargo=embargo,
                bootstrap=bootstrap,
                output_dir=run_dir,
                promotions_root=run_dir / "promotion",
                universe_membership=day_map,
                run_metadata=run_metadata,
                extra_known_biases=bias_lines,
            )
        except Exception as exc:  # noqa: BLE001 - one family failing must not erase the others
            family_results.append(
                {"family": family, "verdict": VERDICT_ERROR, "reason": f"{type(exc).__name__}: {exc}"}
            )
            continue
        verdict = family_verdict(report)
        best = _best_config(report)
        # Turnover is not instrumented by the vector backtester; report an
        # explicit, clearly-labelled trade-activity proxy (trades per OOS year)
        # rather than a fabricated turnover figure.
        oos_days = sum(int(f.get("test_end", 0)) - int(f.get("test_start", 0)) for f in report.get("folds", []))
        best_metrics = (best or {}).get("metrics", {}) if best else {}
        trades_per_year = float(best_metrics.get("trade_count", 0)) / (oos_days / 252.0) if oos_days > 0 else None
        family_results.append(
            {
                "family": family,
                "verdict": verdict,
                "trial_count": report["trial_count"],
                "best_config_id": best.get("config_id") if best else None,
                "metrics": best.get("metrics", {}) if best else {},
                "trade_activity_proxy_trades_per_year": trades_per_year,
                "significance": best.get("significance", {}) if best else {},
                "pbo": best.get("pbo", {}) if best else {},
                "walk_forward": best.get("walk_forward_consistency", {}) if best else {},
                "cost_stress": best.get("cost_stress", {}) if best else {},
                "benchmark": best.get("benchmark", {}) if best else {},
                "concentration": best.get("concentration", {}) if best else {},
                "regime_decomposition": best.get("regime_decomposition", {}) if best else {},
                "parameter_stability": report.get("family_diagnostics", {}).get("parameter_stability_sharpe", {}),
                "white_reality_check": report.get("family_diagnostics", {}).get("white_reality_check", {}),
                "rejection_reasons": _reasons(report),
                "evidence_ceiling": report["evidence_ceiling"],
                "known_biases": report["known_biases"],
                "family_diagnostics": report.get("family_diagnostics", {}),
            }
        )
        for cfg in report.get("config_reports", []):
            experiments.append(
                {
                    "family": family,
                    "config_id": cfg.get("config_id"),
                    "params": cfg.get("params", {}),
                    "status": cfg.get("status"),
                    "promotion": cfg.get("promotion", {}),
                    "metrics": cfg.get("metrics", {}),
                    "dataset_fingerprint": fingerprint,
                    "source_commit": code_commit,
                    "policy_hash": policy.policy_hash(),
                }
            )

    approved = [f for f in family_results if f.get("verdict") == VERDICT_APPROVED]
    summary: Dict[str, Any] = {
        "mvp_research_version": MVP_RESEARCH_VERSION,
        "run_id": run_id,
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_commit": code_commit,
        "dataset_fingerprint": fingerprint,
        "membership": {
            "file": membership_path.name,
            "sha256": manifest["membership"]["sha256"],
            "source": manifest["membership"]["source"],
        },
        "benchmark": benchmark,
        "date_range": manifest["date_range"],
        "symbols": manifest["symbols"],
        "n_warnings": len(accepted),
        "warnings": accepted,
        "warnings_acknowledged": bool(accepted) and accept_data_warnings,
        "data_quality": {
            "verdict": preflight["status"],
            "version": preflight["version"],
        },
        "promotion_policy": {"version": policy.version, "hash": policy.policy_hash()},
        "backtest_config": _backtest_config_pins(),
        "bootstrap": {
            "n_resamples": bootstrap.n_resamples,
            "block_length": bootstrap.block_length,
            "seed": bootstrap.seed,
        },
        "walk_forward": {"n_folds": n_folds, "min_train": min_train, "embargo": embargo, "anchored": True},
        "families_requested": list(selected),
        "metric_notes": {
            "turnover": "not instrumented by the vector backtester; per-family "
            "trade_activity_proxy_trades_per_year is a trade-activity proxy, NOT dollar turnover"
        },
        "families": family_results,
        "promoted": [f["family"] for f in approved],
        "final_verdict": (
            "STRATEGY_APPROVED_FOR_SHADOW"
            if approved
            else (
                "RESEARCH_ONLY_CANDIDATES"
                if any(f.get("verdict") == VERDICT_RESEARCH_ONLY for f in family_results)
                else "NO_STRATEGY_PROMOTED"
            )
        ),
        "next_action": _next_action(family_results, bool(accepted)),
        "live_status": load_config().live_status,
        "run_dir": str(run_dir),
    }
    (run_dir / "experiments.json").write_text(json.dumps(experiments, indent=2, sort_keys=True), encoding="utf-8")
    (run_dir / SUMMARY_JSON).write_text(json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8")
    (run_dir / SUMMARY_MD).write_text(render_mvp_summary(summary), encoding="utf-8")
    return summary


def run_single_family_research(
    *,
    family: str,
    data_dir: Path,
    benchmark: str,
    symbols: Sequence[str],
    output_dir: Path,
    n_folds: int = 4,
    min_train: int = 252,
    embargo: int = 30,
    bootstrap: BootstrapConfig = BootstrapConfig(n_resamples=1000, block_length=10, seed=42),
) -> Dict[str, Any]:
    """EXPLORATORY non-PIT single-family run (explicit operator opt-out).

    The canonical gated path is :func:`run_mvp_research` with a membership
    manifest; this form keeps the legacy script workflow available, but the
    survivorship evidence ceiling is always present and the run is labelled.
    """
    from trading_platform.research.runner import ExternalSetupRequired as _ESR

    if family not in PARAM_GRIDS:
        raise MvpRunError(f"family {family!r} is not researchable; researchable: {researchable_families()}", EXIT_ERROR)
    frames = load_bar_frames(data_dir, [*symbols, benchmark])
    if benchmark not in frames:
        raise _ESR(f"benchmark data not found for {benchmark} in {data_dir} (REQUIRES_EXTERNAL_SETUP)")
    universe = {s: f for s, f in frames.items() if s != benchmark}
    if not universe:
        raise _ESR(f"no universe bar data found in {data_dir} (REQUIRES_EXTERNAL_SETUP)")
    report = run_family_research(
        family,
        universe,
        frames[benchmark],
        param_grid=PARAM_GRIDS[family],
        n_folds=n_folds,
        min_train=min_train,
        embargo=embargo,
        bootstrap=bootstrap,
        output_dir=output_dir,
        promotions_root=Path(output_dir) / "promotions",
        run_metadata={"run_mode": "EXPLORATORY_NON_PIT"},
    )
    return report


def family_verdict(report: Mapping[str, Any]) -> str:
    """APPROVED / RESEARCH_ONLY / REJECTED for one family run.

    REJECTED means evidence exists and policy refused it; RESEARCH_ONLY means
    the evidence was insufficient to judge at all (never an approval).
    """
    promo = report.get("promotion_summary", {})
    if promo.get("approved"):
        return VERDICT_APPROVED
    configs = report.get("config_reports", [])
    if configs and all(c.get("status") != "VALIDATED" for c in configs):
        return VERDICT_RESEARCH_ONLY
    return VERDICT_REJECTED


def _best_config(report: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    best_id = report.get("best_config_id")
    for cfg in report.get("config_reports", []):
        if cfg.get("config_id") == best_id:
            return cfg
    configs = report.get("config_reports", [])
    return configs[0] if configs else None


def _reasons(report: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    best = _best_config(report)
    if best:
        out.extend(str(r) for r in best.get("promotion", {}).get("reasons", []))
    return out


def _backtest_config_pins() -> Dict[str, Any]:
    from trading_platform.research.backtest import BacktestConfig

    cfg = BacktestConfig()
    return {
        "version": cfg.version,
        "initial_cash": cfg.initial_cash,
        "max_positions": cfg.max_positions,
        "risk_fraction": cfg.risk_fraction,
        "max_holding_days": cfg.max_holding_days,
        "atr_stop_multiple": cfg.atr_stop_multiple,
        "atr_trailing_multiple": cfg.atr_trailing_multiple,
        "cost": {
            "commission_per_order": cfg.cost.commission_per_order,
            "slippage_pct": cfg.cost.slippage_pct,
            "stress_multipliers": [1.0, 1.5, 2.0],
        },
    }


def _next_action(families: Sequence[Mapping[str, Any]], warnings_accepted: bool) -> str:
    if any(f.get("verdict") == VERDICT_APPROVED for f in families):
        return (
            "Pinned strategy has APPROVED promotion artifacts: proceed to SHADOW operation "
            "(records WOULD_SUBMIT only); shadow still requires its own forward evidence; live remains NOT_AUTHORIZED."
        )
    if any(f.get("verdict") == VERDICT_ERROR for f in families):
        return "Investigate family ERROR verdicts in the per-family reports before trusting the run."
    if any(f.get("verdict") == VERDICT_RESEARCH_ONLY for f in families):
        return (
            "Evidence was insufficient to judge some families: extend the PIT history / fix data defects, "
            "then re-run. Do NOT proceed to shadow."
        )
    extra = " Note: warnings were accepted — treat conclusions as bounded by them." if warnings_accepted else ""
    return (
        "NO STRATEGY PROMOTED. Either source a longer/cleaner point-in-time dataset or revisit the "
        "hypothesis set; do NOT proceed to shadow/paper with any of these configurations." + extra
    )


def _f(value: Any, digits: int = 3) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def render_mvp_summary(summary: Mapping[str, Any]) -> str:
    """Human-readable MVP research summary (mirrors the JSON artifact)."""
    lines: List[str] = [
        f"# MVP Research Summary — run `{summary['run_id']}`",
        "",
        f"- source_commit: `{summary['source_commit']}`",
        f"- dataset_fingerprint: `{summary['dataset_fingerprint']}`",
        f"- membership: `{summary['membership']['file']}` (source: {summary['membership']['source'] or 'unspecified'})",
        f"- benchmark: {summary['benchmark']} | range: {summary['date_range']['start']} →"
        f" {summary['date_range']['end']} ({summary['date_range']['n_trading_days']} trading days)"
        f" | symbols: {len(summary['symbols'])}",
        f"- data_quality: **{summary['data_quality']['verdict']}** (v{summary['data_quality']['version']},"
        f" warnings={summary['n_warnings']}, acknowledged={summary['warnings_acknowledged']})",
        f"- promotion_policy: v{summary['promotion_policy']['version']}"
        f" hash `{summary['promotion_policy']['hash'][:12]}`",
        f"- generated: {summary['finished_at']}",
        "",
        "## Verdict",
        "",
        f"**{summary['final_verdict']}**",
        "",
        "| family | verdict | trials | trades | trades/yr† | expectancy | sharpe | sortino | maxDD | PF | PSR"
        " | DSR | PBO | wf+ folds | 2x cost | bench excess | top1 sym |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for fam in summary["families"]:
        m = fam.get("metrics", {}) or {}
        sig = fam.get("significance", {}) or {}
        pbo = fam.get("pbo", {}) or {}
        wf = fam.get("walk_forward", {}) or {}
        cs = fam.get("cost_stress", {}) or {}
        bench = fam.get("benchmark", {}) or {}
        conc = (fam.get("concentration", {}) or {}).get("symbols") or {}
        tpy = fam.get("trade_activity_proxy_trades_per_year")
        lines.append(
            "| {fam} | {verdict} | {trials} | {trades} | {tpy} | {exp} | {sharpe} | {sortino} | {mdd} | {pf}"
            " | {psr} | {dsr} | {pbo} | {wff} | {cs} | {be} | {top1} |".format(
                fam=fam["family"],
                verdict=fam.get("verdict", "?"),
                trials=fam.get("trial_count", "?"),
                trades=int(m.get("trade_count", 0)),
                tpy=_f(tpy, 1) if tpy is not None else "n/a",
                exp=_f(m.get("expectancy", 0.0), 2),
                sharpe=_f(m.get("sharpe", 0.0)),
                sortino=_f(m.get("sortino", 0.0)),
                mdd=_f(m.get("max_drawdown", 0.0)),
                pf=_f(m.get("profit_factor", 0.0)),
                psr=_f(sig.get("psr", {}).get("psr", 0.0)),
                dsr=_f(sig.get("dsr", {}).get("dsr", 0.0)),
                pbo=_f(pbo.get("pbo", 1.0)),
                wff=_f(wf.get("positive_fold_fraction", 0.0), 2),
                cs=str(cs.get("survives_2x", "?")),
                be=_f(bench.get("excess_return", 0.0)),
                top1=_f(conc.get("top1_share", 0.0), 2),
            )
        )
    lines.append("")
    lines.append("† trades/yr is a trade-activity proxy; the vector backtester does not measure dollar turnover.")
    lines.append("")
    lines.append("## Parameter stability / white reality check")
    lines.append("")
    for fam in summary["families"]:
        stab = fam.get("parameter_stability") or {}
        wrc = fam.get("white_reality_check") or {}
        bits = []
        if stab:
            bits.append(f"stability={'stable' if stab.get('stable') else 'unstable'} ({stab.get('reason', '')})")
        if wrc:
            bits.append(f"WRC p={_f(wrc.get('p_value'))}")
        if bits:
            lines.append(f"- **{fam['family']}**: " + "; ".join(bits))
    lines.append("")
    if summary["warnings"]:
        lines.append("## Accepted data warnings")
        lines.extend(f"- {w['name']}: {w['detail']}" for w in summary["warnings"])
        lines.append("")
    lines.append("## Rejection reasons (best configuration per family)")
    lines.append("")
    for fam in summary["families"]:
        reasons = fam.get("rejection_reasons") or []
        if fam.get("verdict") == VERDICT_APPROVED:
            lines.append(f"- **{fam['family']}**: APPROVED")
        elif reasons:
            lines.append(f"- **{fam['family']}** ({fam.get('verdict')}):")
            lines.extend(f"  - {r}" for r in reasons)
        else:
            lines.append(f"- **{fam['family']}**: {fam.get('verdict')} — {fam.get('reason', 'see family report')}")
    lines.append("")
    known: List[Tuple[str, str]] = []
    for fam in summary["families"]:
        for bias in fam.get("known_biases", []) or []:
            known.append((fam["family"], str(bias)))
    if known:
        lines.append("## Known biases")
        seen: set[str] = set()
        for fam_name, bias in known:
            key = f"{fam_name}|{bias}"
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- [{fam_name}] {bias}")
        lines.append("")
    lines.append("## Evidence ceiling")
    for fam in summary["families"]:
        lines.append(f"- {fam['family']}: {fam.get('evidence_ceiling', 'n/a')}")
    lines.append("")
    lines.append("## Exact next action")
    lines.append("")
    lines.append(f"{summary['next_action']}")
    lines.append("")
    lines.append(
        f"LIVE_STATUS={summary['live_status']} (permanent repository policy; this document does not change it)."
    )
    return "\n".join(lines)
