"""``trading-platform data`` — vendor membership manifest + PIT dataset preflight.

Thin, safe wrappers over ``research.data_quality`` services (the same ones the
legacy scripts use). No network, no broker, read-only on provided paths.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from trading_platform.cli._common import (
    EXIT_DATA_FAILED,
    EXIT_EXTERNAL,
    EXIT_OK,
    EXIT_PASS_WARNINGS,
)
from trading_platform.research.data_quality import (
    STATUS_FAIL,
    STATUS_PASS,
    MembershipManifestError,
    run_preflight_for_paths,
    write_membership_manifest_file,
)


def cmd_build_membership(args: argparse.Namespace) -> int:
    try:
        stats = write_membership_manifest_file(args.csv, args.output, source=args.source)
    except (MembershipManifestError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return EXIT_DATA_FAILED
    print(f"OK symbols={stats['symbols']} entries={stats['entries']} exits={stats['exits']} -> {args.output}")
    if stats["exits"] == 0:
        print(
            "WARNING: manifest has zero membership exits — the preflight will fail it as "
            "survivor-only data. This usually means the vendor export only contains "
            "current constituents; obtain full historical membership including removals."
        )
    return EXIT_OK


def cmd_preflight(args: argparse.Namespace) -> int:
    try:
        report = run_preflight_for_paths(args.data_dir, args.benchmark, args.membership)
    except MembershipManifestError as exc:
        print(f"ERROR: invalid membership manifest: {exc}")
        return EXIT_DATA_FAILED
    except (FileNotFoundError, OSError) as exc:
        print(f"ERROR: {exc}")
        print("status=REQUIRES_EXTERNAL_SETUP")
        return EXIT_EXTERNAL
    print(f"status={report['status']} symbols={report['n_symbols']} days={report['n_calendar_days']}")
    for critical in report["criticals"]:
        print(f"CRITICAL {critical['name']}: {critical['detail']}")
    for warning in report["warnings"]:
        print(f"WARNING  {warning['name']}: {warning['detail']}")
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"report={args.output}")
    if report["status"] == STATUS_FAIL:
        return EXIT_DATA_FAILED
    return EXIT_OK if report["status"] == STATUS_PASS else EXIT_PASS_WARNINGS


def add_subparser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    data = sub.add_parser("data", help="dataset ingestion + point-in-time quality gate")
    dsub = data.add_subparsers(dest="data_command", required=True)

    build = dsub.add_parser("build-membership", help="vendor membership CSV -> validated PIT manifest")
    build.add_argument("--csv", required=True, type=Path)
    build.add_argument("--output", required=True, type=Path)
    build.add_argument("--source", default="", help="provenance note stored in the manifest")
    build.set_defaults(func=cmd_build_membership)

    pre = dsub.add_parser(
        "preflight",
        help="fail-closed PIT data-quality gate (exit 0 PASS, 4 warnings, 2 FAIL, 3 external setup)",
    )
    pre.add_argument("--data-dir", required=True, type=Path)
    pre.add_argument("--benchmark", required=True)
    pre.add_argument("--membership", required=True, type=Path, help="PIT constituent manifest JSON")
    pre.add_argument("--output", default=None, type=Path)
    pre.set_defaults(func=cmd_preflight)
