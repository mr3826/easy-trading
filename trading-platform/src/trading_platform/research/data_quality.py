"""Dataset point-in-time preflight validation.

A data-QUALITY gate: it answers "can this universe be trusted for research?"
before anyone looks at a backtest. It never tunes or promotes anything; it
either passes, passes with recorded warnings, or fails closed with explicit
reasons.

CRITICAL (fail):
- bars: required OHLCV columns, unique sorted UTC DatetimeIndex, OHLC sanity,
  positive prices, non-negative volume, no NaN, every bar date present on the
  benchmark trading calendar, ``available_at`` >= bar timestamp when present
- membership manifest (required): valid ranges, no overlaps, bar symbols all
  covered by membership, membership starts within data, at least one member
  exit before the final data day, membership sets actually change over time
  (a static manifest is indistinguishable from using today's survivors)
- benchmark: close column present, no NaN

WARNING (pass, recorded):
- missing ``available_at`` metadata (availability assumed at bar close)
- member symbols with no bar data (delisted names unavailable -> exits are
  untradeable in research)
- low per-symbol bar coverage during membership; sampled adds-only growth
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np
import pandas as pd

DATA_QUALITY_VERSION = "1.1.0"

STATUS_PASS = "PASS"
STATUS_PASS_WARNINGS = "PASS_WITH_WARNINGS"
STATUS_FAIL = "FAIL"

CRITICAL = "CRITICAL"
WARNING = "WARNING"

MEMBERSHIP_SCHEMA_VERSION = "1.0"
REQUIRED_BAR_COLUMNS = ("open", "high", "low", "close", "volume")

# symbol -> sorted (start, end-or-None) inclusive membership ranges.
Membership = Dict[str, List[Tuple[date, Optional[date]]]]


class MembershipManifestError(ValueError):
    """Malformed membership manifest."""


# Vendor-supplied symbol strings are interpolated into filesystem paths by the
# research loaders; without this shape check a manifest symbol like
# "../../elsewhere/target" would read/hashes files outside --data-dir.
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")


def validate_symbol(symbol: str) -> str:
    """Canonicalize a symbol or raise; guards manifest/CLI strings before path use."""
    cleaned = str(symbol).strip().upper()
    if not TICKER_RE.fullmatch(cleaned):
        raise MembershipManifestError(
            f"invalid symbol {symbol!r}: expected a US-equity ticker (letters, digits, dot, dash; 1-12 chars)"
        )
    return cleaned


def load_bar_frames(data_dir: Path, symbols: Sequence[str]) -> Dict[str, pd.DataFrame]:
    """Load the bar frames that actually exist in ``data_dir`` for ``symbols``.

    Single implementation shared by preflight, run-all, and dataset identity.
    Symbols are validated to ticker shape first (path-traversal guard).
    """
    frames: Dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        clean = validate_symbol(symbol)
        path = data_dir / f"{clean}.parquet"
        if path.exists():
            frames[clean] = pd.read_parquet(path)
    return frames


@dataclass(frozen=True)
class CheckResult:
    name: str
    severity: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "severity": self.severity, "ok": self.ok, "detail": self.detail}


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(str(value)).date()


def _validate_entries(entries: List[Any]) -> Membership:
    out: Membership = {}
    for i, item in enumerate(entries):
        if not isinstance(item, dict):
            raise MembershipManifestError(f"entry {i} must be an object")
        symbol = item.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            raise MembershipManifestError(f"entry {i}: invalid symbol")
        symbol = validate_symbol(symbol)
        try:
            start = _to_date(item["start"])
        except (KeyError, ValueError) as exc:
            raise MembershipManifestError(f"entry {i}: invalid start: {exc}") from exc
        end_raw = item.get("end")
        end: Optional[date] = None if end_raw is None else _to_date(end_raw)
        if end is not None and end < start:
            raise MembershipManifestError(f"entry {i}: end before start")
        out.setdefault(symbol, []).append((start, end))
    for symbol, intervals in out.items():
        ordered = sorted(intervals)
        for (s1, e1), (s2, _) in zip(ordered, ordered[1:]):
            if e1 is not None and s2 <= e1:
                raise MembershipManifestError(f"overlapping membership ranges for {symbol}")
        out[symbol] = ordered
    return out


def load_membership_manifest(path: Path) -> Membership:
    """Load and structurally validate a PIT constituent-membership manifest.

    Schema (JSON)::

        {"schema_version": "1.0",
         "entries": [{"symbol": "AAPL", "start": "2020-01-02",
                       "end": "2023-05-01" | null}, ...]}

    ``end`` null means the membership range is open (current constituent).
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        if str(raw.get("schema_version")) != MEMBERSHIP_SCHEMA_VERSION:
            raise MembershipManifestError(
                f"schema_version must be {MEMBERSHIP_SCHEMA_VERSION!r}, got {raw.get('schema_version')!r}"
            )
        entries = raw.get("entries")
        if not isinstance(entries, list):
            raise MembershipManifestError("'entries' must be a list")
    elif isinstance(raw, list):
        entries = raw
    else:
        raise MembershipManifestError("manifest must be a list or {'entries': [...]} object")
    return _validate_entries(entries)


def build_membership_manifest_from_rows(rows: Sequence[Mapping[str, Any]], *, source: str = "") -> Dict[str, Any]:
    """Convert vendor rows (symbol/start[/end]) into a schema-valid manifest dict.

    Blank/``NA``/``null`` ends become open-ended membership. Symbols are
    upper-cased; malformed dates raise. The result loads through
    :func:`load_membership_manifest`, which re-validates overlaps and ranges.
    """
    entries: List[Dict[str, Any]] = []
    for i, row in enumerate(rows):
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            raise MembershipManifestError(f"row {i}: missing symbol")
        try:
            start = _to_date(row["start"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MembershipManifestError(f"row {i}: invalid start: {exc}") from exc
        end_raw = row.get("end")
        end: Optional[date] = None
        if end_raw is not None and str(end_raw).strip() not in ("", "null", "None", "NA", "N/A", "-"):
            try:
                end = _to_date(end_raw)
            except (TypeError, ValueError) as exc:
                raise MembershipManifestError(f"row {i}: invalid end: {exc}") from exc
        entries.append({"symbol": symbol, "start": start.isoformat(), "end": end.isoformat() if end else None})
    _validate_entries(entries)  # fail at build time, not at preflight time
    manifest: Dict[str, Any] = {"schema_version": MEMBERSHIP_SCHEMA_VERSION, "entries": entries}
    if source:
        manifest["source"] = source
    return manifest


def build_membership_manifest_from_csv(csv_path: Path, *, source: str = "") -> Dict[str, Any]:
    """Read a vendor CSV (header: symbol,start[,end]) into a manifest dict."""
    import csv

    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or "symbol" not in [c.strip().lower() for c in reader.fieldnames]:
            raise MembershipManifestError("CSV must have a 'symbol' header column")
        normalized = ({str(k).strip().lower(): v for k, v in row.items() if k is not None} for row in reader)
        return build_membership_manifest_from_rows(list(normalized), source=source or csv_path.name)


def write_membership_manifest_file(csv_path: Path, output_path: Path, *, source: str = "") -> Dict[str, int]:
    """Build a manifest from a vendor CSV, persist it, and round-trip validate.

    Returns summary stats; ``exits == 0`` means the vendor export is
    current-constituents-only and the preflight will fail it.
    """
    manifest = build_membership_manifest_from_csv(csv_path, source=source)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Round-trip: what we wrote must load through the exact preflight loader.
    membership = load_membership_manifest(output_path)
    return {
        "symbols": len(membership),
        "entries": len(manifest["entries"]),
        "exits": sum(1 for ranges in membership.values() for _, end in ranges if end is not None),
    }


def format_membership_build_result(stats: Mapping[str, int], output_path: Path) -> List[str]:
    """Shared operator-facing report lines for a membership build (CLI + scripts)."""
    lines = [f"OK symbols={stats['symbols']} entries={stats['entries']} exits={stats['exits']} -> {output_path}"]
    if stats["exits"] == 0:
        lines.append(
            "WARNING: manifest has zero membership exits — the preflight will fail it as "
            "survivor-only data. This usually means the vendor export only contains "
            "current constituents; obtain full historical membership including removals."
        )
    return lines


def membership_on(membership: Membership, day: date) -> Set[str]:
    """Symbols whose membership ranges include ``day`` (inclusive)."""
    return {
        s
        for s, ranges in membership.items()
        if any(start <= day and (end is None or day <= end) for start, end in ranges)
    }


def _utc_offset_zero(index: pd.DatetimeIndex) -> bool:
    if index.tz is None or len(index) == 0:
        return False
    try:
        offsets = {index.tz.utcoffset(t.to_pydatetime()) for t in index[: min(len(index), 400)]}
    except Exception:
        return False
    return bool(offsets) and all(off == timedelta(0) for off in offsets)


def _bar_checks(name: str, df: pd.DataFrame, calendar_dates: Set[date]) -> List[CheckResult]:
    checks: List[CheckResult] = []
    missing = [c for c in REQUIRED_BAR_COLUMNS if c not in df.columns]
    checks.append(CheckResult(f"columns:{name}", CRITICAL, not missing, f"missing {missing}" if missing else ""))
    if missing or len(df) == 0:
        if len(df) == 0:
            checks.append(CheckResult(f"empty:{name}", CRITICAL, False, "no bars"))
        return checks
    idx = df.index
    ok_index = isinstance(idx, pd.DatetimeIndex) and idx.is_monotonic_increasing and idx.is_unique
    checks.append(CheckResult(f"index:{name}", CRITICAL, bool(ok_index), "index must be unique sorted DatetimeIndex"))
    if not isinstance(idx, pd.DatetimeIndex):
        return checks
    checks.append(
        CheckResult(f"utc:{name}", CRITICAL, _utc_offset_zero(idx), "bar timestamps must be UTC (zero offset)")
    )
    bar_days = {t.date() for t in idx}
    off_calendar = sorted(bar_days - calendar_dates)
    checks.append(
        CheckResult(
            f"calendar:{name}",
            CRITICAL,
            not off_calendar,
            f"{len(off_calendar)} bar dates absent from benchmark trading calendar" if off_calendar else "",
        )
    )
    o = df["open"].astype(float)
    h = df["high"].astype(float)
    lo = df["low"].astype(float)
    c = df["close"].astype(float)
    v = df["volume"].astype(float)
    nan_bad = bool(df[list(REQUIRED_BAR_COLUMNS)].isna().any().any())
    finite_ok = bool(
        np.isfinite(np.concatenate([o.to_numpy(), h.to_numpy(), lo.to_numpy(), c.to_numpy()])).all()
        and np.isfinite(v.to_numpy()).all()
    )
    positive = bool((c > 0).all() and (o > 0).all() and (h > 0).all() and (lo > 0).all() and (v >= 0).all())
    sanity = bool((h >= lo).all() and (h >= o).all() and (h >= c).all() and (lo <= o).all() and (lo <= c).all())
    checks.append(CheckResult(f"nan:{name}", CRITICAL, (not nan_bad) and finite_ok, "NaN or non-finite OHLCV"))
    checks.append(CheckResult(f"positive:{name}", CRITICAL, positive, "non-positive prices or volume"))
    checks.append(CheckResult(f"ohlc-sanity:{name}", CRITICAL, sanity, "high/low inconsistent with open/close"))
    if "available_at" in df.columns:
        av = pd.DatetimeIndex(pd.to_datetime(df["available_at"]))
        bad = bool((av.tz_localize(None) < idx.tz_localize(None)).any())
        checks.append(CheckResult(f"availability:{name}", CRITICAL, not bad, "available_at precedes bar timestamp"))
    else:
        checks.append(
            CheckResult(
                f"availability-metadata:{name}",
                WARNING,
                True,
                "no available_at column; assuming end-of-day availability (unverified)",
            )
        )
    return checks


def run_data_preflight(
    bars: Mapping[str, pd.DataFrame],
    benchmark: pd.DataFrame,
    membership: Optional[Membership] = None,
) -> Dict[str, Any]:
    """Deterministic PIT data-quality verdict for a proposed research dataset."""
    checks: List[CheckResult] = []

    if not bars:
        checks.append(CheckResult("universe", CRITICAL, False, "no bar data"))
        return _finalize(checks, bars, benchmark, membership)

    if "close" not in benchmark.columns or len(benchmark) < 2:
        checks.append(CheckResult("benchmark", CRITICAL, False, "benchmark missing close or too short"))
        return _finalize(checks, bars, benchmark, membership)
    bench_close = benchmark["close"].astype(float)
    checks.append(
        CheckResult("benchmark-continuity", CRITICAL, bool(bench_close.notna().all()), "NaN benchmark closes")
    )
    calendar_dates = {t.date() for t in benchmark.index}
    all_days = sorted(calendar_dates)
    last_day = all_days[-1]

    for symbol in sorted(bars):
        checks.extend(_bar_checks(symbol, bars[symbol], calendar_dates))

    if membership is None:
        checks.append(
            CheckResult(
                "membership-manifest",
                CRITICAL,
                False,
                "no point-in-time constituent membership supplied; research would use "
                "today's survivors (survivorship bias)",
            )
        )
        return _finalize(checks, bars, benchmark, membership)

    for symbol, ranges in sorted(membership.items()):
        if start_gt_end_after_data := [s for s, _ in ranges if s > last_day]:
            checks.append(
                CheckResult(
                    f"membership-future:{symbol}",
                    CRITICAL,
                    False,
                    f"membership starts after data ends: {start_gt_end_after_data}",
                )
            )
        if symbol not in bars:
            checks.append(
                CheckResult(
                    f"member-no-bars:{symbol}",
                    WARNING,
                    True,
                    "member symbol has no bar data (delisted name unavailable; exits are untradeable in research)",
                )
            )
    uncovered = sorted(set(bars) - set(membership))
    checks.append(
        CheckResult(
            "bars-without-membership",
            CRITICAL,
            not uncovered,
            f"symbols have bar data but no membership: {uncovered}" if uncovered else "",
        )
    )

    any_exit = any(end is not None and end < last_day for ranges in membership.values() for _, end in ranges)
    checks.append(
        CheckResult(
            "membership-exits",
            CRITICAL,
            any_exit,
            "manifest contains no membership exits before the final data day — indistinguishable "
            "from using today's surviving constituents",
        )
    )

    sampled = all_days[:: max(1, len(all_days) // 120)]
    sets: List[FrozenSet[str]] = [frozenset(membership_on(membership, d)) for d in sampled]
    static = len(sets) > 1 and all(s == sets[0] for s in sets)
    checks.append(
        CheckResult(
            "membership-dynamics",
            CRITICAL,
            not static,
            "membership never changes across the whole period (static == today's survivors)",
        )
    )
    adds_only = bool(sets) and all(a <= b for a, b in zip(sets, sets[1:])) and (len(sets[0]) < len(sets[-1]))
    if adds_only:
        checks.append(
            CheckResult(
                "membership-adds-only",
                WARNING,
                True,
                "sampled membership only ever grows — verify delistings/removals are present",
            )
        )

    for symbol, ranges in sorted(membership.items()):
        if symbol not in bars:
            continue
        df = bars[symbol]
        idx = df.index
        tz = idx.tz if isinstance(idx, pd.DatetimeIndex) else None
        if not isinstance(idx, pd.DatetimeIndex):
            continue
        for start, end in ranges:
            stop = end or last_day
            days = idx.normalize()
            mask = (days >= pd.Timestamp(start, tz=tz)) & (days <= pd.Timestamp(stop, tz=tz))
            span = len([d for d in all_days if start <= d <= stop])
            have = int(mask.sum())
            if span and have / span < 0.80:
                checks.append(
                    CheckResult(
                        f"coverage:{symbol}:{start.isoformat()}",
                        WARNING,
                        True,
                        f"only {have}/{span} bars during membership {start}..{stop}",
                    )
                )

    return _finalize(checks, bars, benchmark, membership)


def run_preflight_for_paths(
    data_dir: Path, benchmark: str, membership_path: Path
) -> Tuple[Dict[str, Any], Dict[str, pd.DataFrame], pd.DataFrame, Membership]:
    """File-level preflight: load manifest + bars from disk and run the gate.

    Single implementation shared by ``trading-platform data preflight``, the
    canonical MVP workflow, and legacy scripts; returns
    ``(report, bars, benchmark_frame, membership)`` so the caller can reuse
    exactly what the gate judged (dataset identity, research inputs). Raises
    :class:`MembershipManifestError` for an invalid manifest and
    :class:`FileNotFoundError` with an actionable message when required inputs
    are missing (the caller maps these to the external-setup exit code).
    """
    if not membership_path.exists():
        raise FileNotFoundError(f"membership manifest not found: {membership_path}")
    membership = load_membership_manifest(membership_path)
    bench = validate_symbol(benchmark)
    symbols = sorted({*membership, bench})
    bars = load_bar_frames(data_dir, symbols)
    benchmark_frame = bars.pop(bench, None)
    if benchmark_frame is None:
        raise FileNotFoundError(f"benchmark data not found: {data_dir / (bench + '.parquet')}")
    if not bars:
        raise FileNotFoundError(f"no universe bar data found in {data_dir}")
    return run_data_preflight(bars, benchmark_frame, membership), bars, benchmark_frame, membership


def _finalize(
    checks: List[CheckResult],
    bars: Mapping[str, pd.DataFrame],
    benchmark: pd.DataFrame,
    membership: Optional[Membership],
) -> Dict[str, Any]:
    criticals = [c for c in checks if c.severity == CRITICAL and not c.ok]
    warnings = [c for c in checks if c.severity == WARNING and c.detail]
    status = STATUS_FAIL if criticals else (STATUS_PASS_WARNINGS if warnings else STATUS_PASS)
    return {
        "status": status,
        "n_symbols": len(bars),
        "n_calendar_days": len({t.date() for t in benchmark.index}),
        "n_membership_symbols": len(membership or {}),
        "checks": [c.to_dict() for c in sorted(checks, key=lambda c: (c.severity, c.name))],
        "criticals": [c.to_dict() for c in criticals],
        "warnings": [c.to_dict() for c in warnings],
        "version": DATA_QUALITY_VERSION,
    }
