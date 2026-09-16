"""Safe command-line entry points for the platform."""

from __future__ import annotations

import argparse


def main() -> int:
    """Run the non-trading platform CLI.

    The CLI intentionally exposes inspection only until an explicitly
    authorized execution application exists.
    """
    parser = argparse.ArgumentParser(prog="trading-platform")
    parser.add_argument("--status", action="store_true", help="show safe status")
    args = parser.parse_args()
    if args.status:
        print("LIVE_TRADING_ENABLED=false")
        print("LIVE_STATUS=NOT_AUTHORIZED")
    return 0
