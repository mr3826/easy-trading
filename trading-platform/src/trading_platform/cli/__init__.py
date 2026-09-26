"""The canonical operator CLI: ``trading-platform`` (inspect + research only).

The CLI exposes installation, diagnostics, dataset gating, strategy research
and reports — it intentionally has NO path that can authorize, configure or
submit live trading. Live remains disabled by permanent platform policy
(``trading_platform.config`` / ``trading_platform.authorization``).

Subcommands:

``status``            where the project stands (MVP state + live boundary)
``doctor``            one-shot MVP operability diagnostics
``data``              build-membership, preflight
``research``          list-strategies, run, run-all, status, latest
``report``            show
"""

from __future__ import annotations

import argparse
from typing import List, Optional, Sequence

import trading_platform.cli.data_cmd as data_cmd
import trading_platform.cli.doctor_cmd as doctor_cmd
import trading_platform.cli.research_cmd as research_cmd
from trading_platform.cli.status_cmd import cmd_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trading-platform",
        description="Safe operator CLI: research application (no live execution path exists).",
    )
    # Backward compatibility: `trading-platform --status` kept its original meaning.
    parser.add_argument("--status", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")

    status = sub.add_parser("status", help="show MVP state and the permanent live boundary")
    status.add_argument("--output-dir", default=None, help="research output root (default: artifacts/research)")
    status.add_argument("--json", action="store_true", help="machine-readable payload")
    status.set_defaults(func=cmd_status)

    doctor = sub.add_parser("doctor", help="diagnose MVP operability (non-zero exit when not operable)")
    doctor.set_defaults(func=doctor_cmd.cmd_doctor)

    data_cmd.add_subparser(sub)
    research_cmd.add_subparser(sub)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the platform CLI. Returns the process exit code."""
    import sys

    raw: List[str] = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if raw is None:
        import sys

        raw = sys.argv[1:]
    if raw == ["--status"]:
        # Legacy exact output preserved for existing scripts/operators.
        print("LIVE_TRADING_ENABLED=false")
        print("LIVE_STATUS=NOT_AUTHORIZED")
        return 0
    args = parser.parse_args(raw)
    if getattr(args, "command", None) is None:
        parser.print_help()
        return 0
    return int(args.func(args))
