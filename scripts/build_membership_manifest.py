"""Build a schema-valid PIT membership manifest from a vendor CSV.

Input CSV header: symbol,start[,end]  (blank/NA end = open-ended membership).
The output JSON is immediately validated through the same loader the preflight
uses, so malformed ranges, overlaps, and missing symbols fail HERE — not
silently later during research.

Usage:
    uv run python scripts/build_membership_manifest.py \
        --csv vendor_membership_history.csv \
        --output universe_membership.json \
        --source "vendor X index membership export 2026-09"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", default="", help="provenance note stored in the manifest")
    args = parser.parse_args()

    from trading_platform.research.data_quality import (
        MembershipManifestError,
        build_membership_manifest_from_csv,
        load_membership_manifest,
    )

    try:
        manifest = build_membership_manifest_from_csv(args.csv, source=args.source)
    except (MembershipManifestError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Round-trip: what we wrote must load through the exact preflight loader.
    membership = load_membership_manifest(args.output)
    n_entries = len(manifest["entries"])
    exits = sum(1 for ranges in membership.values() for _, end in ranges if end is not None)
    print(f"OK symbols={len(membership)} entries={n_entries} exits={exits} -> {args.output}")
    if exits == 0:
        print(
            "WARNING: manifest has zero membership exits — the preflight will fail it as "
            "survivor-only data. This usually means the vendor export only contains "
            "current constituents; obtain full historical membership including removals."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
