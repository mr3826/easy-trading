"""``trading-platform research`` — list/run/run-all with the data gate enforced.

All commands reuse the application services in ``research.mvp``; nothing here
computes evidence itself. ``research run-all`` is the canonical MVP workflow.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from trading_platform.cli._common import (
    EXIT_DATA_FAILED,
    EXIT_ERROR,
    EXIT_EXTERNAL,
    EXIT_OK,
    research_output_root,
)
from trading_platform.research.mvp import (
    MVP_RESEARCH_VERSION,
    PARAM_GRIDS,
    SUMMARY_JSON,
    MvpRunError,
    latest_summary,
    run_mvp_research,
    run_single_family_research,
)
from trading_platform.strategies.candidates import CANDIDATE_STRATEGY_VERSION, STRATEGY_FAMILIES
from trading_platform.validation import BootstrapConfig


def _common_research_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--n-folds", type=int, default=4)
    p.add_argument("--min-train", type=int, default=252)
    p.add_argument("--embargo", type=int, default=30)
    p.add_argument(
        "--bootstrap-resamples",
        type=int,
        default=1000,
        help="bootstrap resamples (pinned into every report; lower only for smoke tests)",
    )


def cmd_list_strategies(args: argparse.Namespace) -> int:
    print(f"candidate_strategies_version={CANDIDATE_STRATEGY_VERSION} mvp_research_version={MVP_RESEARCH_VERSION}")
    for name in sorted(STRATEGY_FAMILIES):
        _, signal = STRATEGY_FAMILIES[name]
        kind = "researchable" if signal is not None else "control (not family-runnable)"
        grid = len(PARAM_GRIDS.get(name, []))
        print(f"{name:26s} {kind} trials_per_run={grid}")
    return EXIT_OK


def _run_gated(args: argparse.Namespace, families: Optional[List[str]]) -> int:
    try:
        summary = run_mvp_research(
            data_dir=args.data_dir,
            benchmark=args.benchmark,
            membership_path=args.membership,
            output_root=args.output_dir,
            accept_data_warnings=args.accept_data_warnings,
            n_folds=args.n_folds,
            min_train=args.min_train,
            embargo=args.embargo,
            bootstrap=BootstrapConfig(n_resamples=args.bootstrap_resamples, block_length=10, seed=42),
            families=families,
        )
    except MvpRunError as exc:
        print(f"ERROR: {exc}")
        return exc.exit_code
    print(f"DATA QUALITY: {summary['data_quality']['verdict']}")
    for fam in summary["families"]:
        print(f"{fam['family']:26s} {fam['verdict']}")
    print(f"FINAL_VERDICT: {summary['final_verdict']}")
    print(f"run_id={summary['run_id']} dataset_fingerprint={summary['dataset_fingerprint'][:12]}")
    print(f"summary={summary['run_dir']}/{SUMMARY_JSON}")
    print(f"next: {summary['next_action']}")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    if args.membership is not None:
        return _run_gated(args, families=[args.family])
    if not args.allow_non_pit:
        print(
            "ERROR: research requires a point-in-time membership manifest (data-quality Gate Zero).\n"
            "       pass --membership <manifest.json> (recommended: `trading-platform research run-all`),\n"
            "       or pass --allow-non-pit explicitly for an exploratory run whose conclusions are\n"
            "       capped by survivorship bias and never promotion-eligible."
        )
        return EXIT_DATA_FAILED
    from trading_platform.research.runner import ExternalSetupRequired

    if not args.symbols:
        print("ERROR: --symbols required with --allow-non-pit")
        return EXIT_ERROR
    try:
        report = run_single_family_research(
            family=args.family,
            data_dir=args.data_dir,
            benchmark=args.benchmark,
            symbols=args.symbols,
            output_dir=args.output_dir,
            n_folds=args.n_folds,
            min_train=args.min_train,
            embargo=args.embargo,
            bootstrap=BootstrapConfig(n_resamples=args.bootstrap_resamples, block_length=10, seed=42),
        )
    except MvpRunError as exc:
        print(f"ERROR: {exc}")
        return exc.exit_code
    except ExternalSetupRequired as exc:
        print(f"ERROR: {exc}")
        print("status=REQUIRES_EXTERNAL_SETUP")
        return EXIT_EXTERNAL
    approved = report["promotion_summary"]["approved"]
    print(f"mode=EXPLORATORY_NON_PIT (evidence_ceiling={report['evidence_ceiling']})")
    print(f"family={report['family_name']} trials={report['trial_count']}")
    print(f"approved={approved or 'NONE'}")
    return EXIT_OK


def cmd_run_all(args: argparse.Namespace) -> int:
    return _run_gated(args, families=None)


def _load_run_summaries(output_dir: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not output_dir.exists():
        return out
    for path in sorted(output_dir.glob(f"*/{SUMMARY_JSON}")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def cmd_research_status(args: argparse.Namespace) -> int:
    summaries = _load_run_summaries(args.output_dir)
    if not summaries:
        print("runs=NONE (canonical workflow: `trading-platform research run-all ...`)")
        return EXIT_OK
    print(f"runs={len(summaries)} output_dir={args.output_dir}")
    for s in summaries:
        verdicts = ",".join(f"{f['family']}={f['verdict']}" for f in s.get("families", []))
        print(
            f"{s['run_id']} data={s.get('data_quality', {}).get('verdict')} "
            f"verdict={s.get('final_verdict')} data_fp={str(s.get('dataset_fingerprint'))[:12]} [{verdicts}]"
        )
    return EXIT_OK


def cmd_research_latest(args: argparse.Namespace) -> int:
    summary = latest_summary(args.output_dir)
    if summary is None:
        print("latest run=NONE")
        return EXIT_EXTERNAL
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return EXIT_OK


def cmd_report_show(args: argparse.Namespace) -> int:
    output_dir: Path = args.output_dir
    run_id = args.run
    if run_id is None:
        summary = latest_summary(output_dir)
        if summary is None:
            print("latest run=NONE")
            return EXIT_EXTERNAL
        run_id = str(summary["run_id"])
    run_dir = output_dir / run_id
    if not run_dir.exists():
        print(f"ERROR: run not found: {run_dir}")
        return EXIT_ERROR
    if args.family:
        candidates = sorted(run_dir.glob(f"{args.family}--*.md"))
        if not candidates:
            print(f"ERROR: no {args.family} report in {run_dir}")
            return EXIT_ERROR
        print(candidates[-1].read_text(encoding="utf-8"))
        return EXIT_OK
    md = run_dir / "MVP_RESEARCH_SUMMARY.md"
    js = run_dir / SUMMARY_JSON
    if args.format == "json":
        if not js.exists():
            print(f"ERROR: {js} not found")
            return EXIT_ERROR
        print(js.read_text(encoding="utf-8"))
        return EXIT_OK
    if not md.exists():
        print(f"ERROR: {md} not found")
        return EXIT_ERROR
    print(md.read_text(encoding="utf-8"))
    return EXIT_OK


def add_subparser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    research = sub.add_parser("research", help="strategy-family research through the data gate")
    rsub = research.add_subparsers(dest="research_command", required=True)

    lst = rsub.add_parser("list-strategies", help="registered families + pinned parameter grids")
    lst.set_defaults(func=cmd_list_strategies)

    run = rsub.add_parser(
        "run",
        help="one family; gated by --membership, or explicitly exploratory with --allow-non-pit",
    )
    run.add_argument("--family", required=True)
    run.add_argument("--data-dir", required=True, type=Path)
    run.add_argument("--benchmark", default="SPY")
    run.add_argument("--membership", default=None, type=Path)
    run.add_argument("--symbols", nargs="*", default=[])
    run.add_argument("--allow-non-pit", action="store_true")
    run.add_argument(
        "--accept-data-warnings",
        action="store_true",
        help="explicitly acknowledge PASS_WITH_WARNINGS from the data gate (gated mode)",
    )
    run.add_argument("--output-dir", default=research_output_root(), type=Path)
    _common_research_args(run)
    run.set_defaults(func=cmd_run)

    runall = rsub.add_parser("run-all", help="canonical MVP workflow across all researchable families")
    runall.add_argument("--data-dir", required=True, type=Path)
    runall.add_argument("--benchmark", default="SPY")
    runall.add_argument("--membership", required=True, type=Path)
    runall.add_argument("--output-dir", default=research_output_root(), type=Path)
    runall.add_argument(
        "--accept-data-warnings",
        action="store_true",
        help="explicitly acknowledge PASS_WITH_WARNINGS; persisted into the dataset manifest, "
        "family reports and MVP summary",
    )
    _common_research_args(runall)
    runall.set_defaults(func=cmd_run_all)

    st = rsub.add_parser("status", help="list persisted MVP runs and their verdicts")
    st.add_argument("--output-dir", default=research_output_root(), type=Path)
    st.set_defaults(func=cmd_research_status)

    latest = rsub.add_parser("latest", help="print the newest run summary as JSON")
    latest.add_argument("--output-dir", default=research_output_root(), type=Path)
    latest.set_defaults(func=cmd_research_latest)

    rep = sub.add_parser("report", help="human-readable run reports")
    psub = rep.add_subparsers(dest="report_command", required=True)
    show = psub.add_parser("show", help="print MVP_RESEARCH_SUMMARY (md or json)")
    show.add_argument("--run", default=None, help="run id (default: latest)")
    show.add_argument("--family", default=None, help="print one family's full report instead")
    show.add_argument("--format", choices=("md", "json"), default="md")
    show.add_argument("--output-dir", default=research_output_root(), type=Path)
    show.set_defaults(func=cmd_report_show)
