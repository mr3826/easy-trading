"""Build a schema-valid PIT membership manifest from a vendor CSV.

Thin backward-compatible wrapper: the real logic lives in
``trading_platform.research.data_quality`` and is shared with
``trading-platform data build-membership``. Do not fork behavior here.

Usage:
    uv run python scripts/build_membership_manifest.py \
        --csv vendor_membership_history.csv \
        --output universe_membership.json \
        --source "vendor X index membership export 2026-09"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", default="", help="provenance note stored in the manifest")
    args = parser.parse_args()

    from trading_platform.cli._common import EXIT_DATA_FAILED
    from trading_platform.research.data_quality import MembershipManifestError, write_membership_manifest_file

    try:
        stats = write_membership_manifest_file(args.csv, args.output, source=args.source)
    except (MembershipManifestError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return EXIT_DATA_FAILED
    print(f"OK symbols={stats['symbols']} entries={stats['entries']} exits={stats['exits']} -> {args.output}")
    if stats["exits"] == 0:
        print(
            "WARNING: manifest has zero membership exits — the preflight will fail it as "
            "survivor-only data. This usually means the vendor export only contains "
            "current constituents; obtain full historical membership including removals."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
