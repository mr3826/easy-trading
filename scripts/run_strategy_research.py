"""Run one strategy family's reverification research (exploratory, non-PIT).

Backward-compatible wrapper: parameter grids and the family runner now live in
``trading_platform.research.mvp`` (shared with ``trading-platform research``).
For the canonical gated workflow with a point-in-time membership manifest use:

    trading-platform research run-all --data-dir ... --benchmark SPY \
        --membership universe_membership.json --output-dir artifacts/research

Exits non-zero with REQUIRES_EXTERNAL_SETUP when data is absent. Never
touches broker or live-trading paths: this is a read-only research tool.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

FAMILIES = ["trend_relative_strength", "breakout_volume", "trend_pullback"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", required=True, choices=FAMILIES)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--output-dir", default=Path("artifacts/research"), type=Path)
    parser.add_argument("--n-folds", type=int, default=4)
    parser.add_argument("--min-train", type=int, default=252)
    parser.add_argument("--embargo", type=int, default=30)
    args = parser.parse_args()

    from trading_platform.research.mvp import run_single_family_research
    from trading_platform.research.runner import ExternalSetupRequired
    from trading_platform.validation import BootstrapConfig

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
            bootstrap=BootstrapConfig(n_resamples=1000, block_length=10, seed=42),
        )
    except ExternalSetupRequired as exc:
        print(f"ERROR: {exc}")
        print("status=REQUIRES_EXTERNAL_SETUP")
        return 2
    approved = report["promotion_summary"]["approved"]
    print(f"family={report['family_name']} trials={report['trial_count']}")
    print(f"approved={approved or 'NONE'}")
    print(f"evidence_ceiling={report['evidence_ceiling']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
