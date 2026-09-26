"""Dataset point-in-time preflight: is this universe trustworthy for research?

Run BEFORE any strategy research. Exit codes: 0=PASS, 1=PASS_WITH_WARNINGS,
2=FAIL (data-quality gate failed; do not proceed to strategy conclusions),
3=REQUIRES_EXTERNAL_SETUP (data/manifest files missing).

Usage:
    uv run python scripts/run_data_preflight.py \
        --data-dir /path/to/daily-bars \
        --benchmark SPY \
        --membership /path/to/universe_membership.json \
        --output artifacts/research/data_preflight.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXIT_PASS = 0
EXIT_WARNINGS = 1
EXIT_FAIL = 2
EXIT_EXTERNAL = 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--membership", required=True, type=Path, help="PIT constituent manifest JSON")
    parser.add_argument("--output", default=None, type=Path)
    args = parser.parse_args()

    import pandas as pd
    from trading_platform.research.data_quality import (
        STATUS_FAIL,
        STATUS_PASS,
        load_membership_manifest,
        run_data_preflight,
    )

    if not args.membership.exists():
        print(f"ERROR: membership manifest not found: {args.membership}")
        print("status=REQUIRES_EXTERNAL_SETUP")
        return EXIT_EXTERNAL
    try:
        membership = load_membership_manifest(args.membership)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: invalid membership manifest: {exc}")
        return EXIT_FAIL

    symbols = sorted({*membership, args.benchmark})
    bars: dict = {}
    for symbol in symbols:
        path = args.data_dir / f"{symbol}.parquet"
        if path.exists():
            bars[symbol] = pd.read_parquet(path)
    benchmark = bars.pop(args.benchmark, None)
    if benchmark is None:
        print(f"ERROR: benchmark data not found at {args.data_dir / (args.benchmark + '.parquet')}")
        print("status=REQUIRES_EXTERNAL_SETUP")
        return EXIT_EXTERNAL
    if not bars:
        print("ERROR: no universe bar data found in --data-dir")
        print("status=REQUIRES_EXTERNAL_SETUP")
        return EXIT_EXTERNAL

    report = run_data_preflight(bars, benchmark, membership)
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
        return EXIT_FAIL
    return EXIT_PASS if report["status"] == STATUS_PASS else EXIT_WARNINGS


if __name__ == "__main__":
    sys.exit(main())
