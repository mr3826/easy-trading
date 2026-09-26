"""Canonical dataset identity: pin the exact bytes a research run consumed.

The dataset manifest is the immutable fingerprint of an MVP research run:
which files, which checksums, which calendar, which membership provenance,
which data-quality verdict, and which warnings a human explicitly accepted.
Every research artifact must be traceable back to a ``dataset_fingerprint``.

This module composes the existing preflight gate (``research.data_quality``)
and the runner's frame-level hash; it does NOT replace either.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import pandas as pd

from trading_platform.research.data_quality import (
    DATA_QUALITY_VERSION,
    MEMBERSHIP_SCHEMA_VERSION,
    Membership,
)

DATASET_MANIFEST_VERSION = "1.0.0"

# Research consumes daily bars only; corporate actions are applied by the
# vendor upstream (adjusted series). Recorded so a consumer can see the gap.
CORPORATE_ACTIONS_POLICY = {
    "used": False,
    "note": "bars are assumed vendor-adjusted; corporate-action events are not "
    "independently applied by MVP research — see docs/DATA_SOURCING.md",
}


def sha256_file(path: Path) -> str:
    """Streamed SHA-256 of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frame_span(frame: pd.DataFrame) -> Dict[str, Any]:
    idx = frame.index
    start = pd.Timestamp(idx.min()).tz_localize(None) if len(idx) else None
    end = pd.Timestamp(idx.max()).tz_localize(None) if len(idx) else None
    return {
        "n_bars": int(len(idx)),
        "start": start.date().isoformat() if start is not None else None,
        "end": end.date().isoformat() if end is not None else None,
    }


def build_dataset_manifest(
    *,
    data_dir: Path,
    benchmark: str,
    membership_path: Path,
    bars: Mapping[str, pd.DataFrame],
    benchmark_frame: pd.DataFrame,
    membership: Membership,
    preflight_report: Mapping[str, Any],
    accepted_warnings: Sequence[Mapping[str, Any]] = (),
    warnings_acknowledged: bool = False,
) -> Dict[str, Any]:
    """Build the canonical dataset manifest for a research run.

    ``bars``/``benchmark_frame``/``membership`` are the same objects already
    loaded by the preflight so identity is computed over exactly what the gate
    judged. Bar membership in the manifest reflects the files actually found in
    ``data_dir`` — the same rule the preflight used.
    """
    bar_entries: Dict[str, Any] = {}
    for symbol in sorted(bars):
        path = data_dir / f"{symbol}.parquet"
        bar_entries[symbol] = {
            "file": path.name,
            "sha256": sha256_file(path),
            **_frame_span(bars[symbol]),
        }
    bench_path = data_dir / f"{benchmark}.parquet"
    manifest_path = membership_path
    manifest_text = json.loads(manifest_path.read_text(encoding="utf-8"))
    n_entries = len(manifest_text.get("entries", manifest_text if isinstance(manifest_text, list) else []))
    components: Dict[str, Any] = {
        "version": DATASET_MANIFEST_VERSION,
        "data_dir": data_dir.name,
        "benchmark": {
            "symbol": benchmark,
            "file": bench_path.name,
            "sha256": sha256_file(bench_path),
            **_frame_span(benchmark_frame),
        },
        "bars": bar_entries,
        "symbols": sorted(bars),
        "date_range": {
            "start": _frame_span(benchmark_frame)["start"],
            "end": _frame_span(benchmark_frame)["end"],
            "n_trading_days": len({t.date() for t in benchmark_frame.index}),
        },
        "membership": {
            "file": manifest_path.name,
            "sha256": sha256_file(manifest_path),
            "schema_version": MEMBERSHIP_SCHEMA_VERSION,
            "source": str(manifest_text.get("source", "")) if isinstance(manifest_text, dict) else "",
            "n_symbols": len(membership),
            "n_entries": n_entries,
            "n_exits": sum(1 for ranges in membership.values() for _, end in ranges if end is not None),
        },
        "corporate_actions": CORPORATE_ACTIONS_POLICY,
        "data_quality": {
            "version": str(preflight_report.get("version", DATA_QUALITY_VERSION)),
            "verdict": str(preflight_report.get("status", "UNKNOWN")),
            "n_warnings": len(preflight_report.get("warnings", [])),
        },
        "accepted_warnings": [
            {"name": str(w.get("name", "")), "detail": str(w.get("detail", ""))} for w in accepted_warnings
        ],
        "warnings_acknowledgement": {
            "required": str(preflight_report.get("status")) == "PASS_WITH_WARNINGS",
            "mechanism": "--accept-data-warnings",
            "acknowledged": bool(warnings_acknowledged),
        },
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    identity = {k: v for k, v in components.items() if k != "created_at"}
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    components["dataset_fingerprint"] = fingerprint
    return components


def load_dataset_manifest(path: Path) -> Dict[str, Any]:
    """Load a persisted dataset manifest and validate its fingerprint."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "dataset_fingerprint" not in payload:
        raise ValueError(f"{path} is not a dataset manifest")
    expected = payload["dataset_fingerprint"]
    identity = {k: v for k, v in payload.items() if k not in ("dataset_fingerprint", "created_at")}
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
    recomputed = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if recomputed != expected:
        raise ValueError(f"dataset manifest fingerprint mismatch: {path}")
    return payload


def short_fingerprint(manifest: Mapping[str, Any]) -> str:
    """First 12 hex chars of the dataset fingerprint, for run ids and tables."""
    return str(manifest.get("dataset_fingerprint", "0" * 12))[:12]


__all__ = [
    "DATASET_MANIFEST_VERSION",
    "build_dataset_manifest",
    "load_dataset_manifest",
    "sha256_file",
    "short_fingerprint",
]
