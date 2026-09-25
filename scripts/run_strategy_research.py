"""Run strategy-family reverification research and write versioned reports.

Usage:
    uv run python scripts/run_strategy_research.py \
        --family trend_relative_strength \
        --data-dir /path/to/daily-bars \
        --benchmark SPY \
        --symbols AAPL MSFT ... \
        --output-dir artifacts/research

Exits non-zero with REQUIRES_EXTERNAL_SETUP when data is absent. Never
touches broker or live-trading paths: this is a read-only research tool.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--family", required=True, choices=["trend_relative_strength", "breakout_volume", "trend_pullback"]
    )
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--output-dir", default=Path("artifacts/research"), type=Path)
    parser.add_argument("--n-folds", type=int, default=4)
    parser.add_argument("--min-train", type=int, default=252)
    parser.add_argument("--embargo", type=int, default=30)
    args = parser.parse_args()

    from trading_platform.research.runner import ExternalSetupRequired, load_parquet_universe, run_family_research
    from trading_platform.validation import BootstrapConfig

    try:
        frames = load_parquet_universe(args.data_dir, [*args.symbols, args.benchmark])
    except ExternalSetupRequired as exc:
        print(f"ERROR: {exc}")
        print("status=REQUIRES_EXTERNAL_SETUP")
        return 2
    benchmark = frames.pop(args.benchmark)

    grids = {
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
    report = run_family_research(
        args.family,
        frames,
        benchmark,
        param_grid=grids[args.family],
        n_folds=args.n_folds,
        min_train=args.min_train,
        embargo=args.embargo,
        bootstrap=BootstrapConfig(n_resamples=1000, block_length=10, seed=42),
        output_dir=args.output_dir,
        promotions_root=args.output_dir / "promotions",
    )
    approved = report["promotion_summary"]["approved"]
    print(f"family={report['family_name']} trials={report['trial_count']}")
    print(f"approved={approved or 'NONE'}")
    print(f"evidence_ceiling={report['evidence_ceiling']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
