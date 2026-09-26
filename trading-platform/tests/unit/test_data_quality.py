"""Dataset PIT preflight tests: pass paths, survivorship red flags, schema."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from trading_platform.research.data_quality import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_PASS_WARNINGS,
    MembershipManifestError,
    build_membership_manifest_from_csv,
    build_membership_manifest_from_rows,
    load_membership_manifest,
    membership_on,
    run_data_preflight,
)


def _calendar(n: int = 300) -> pd.DatetimeIndex:
    return pd.date_range("2022-01-03", periods=n, freq="B", tz="UTC")


def _bars(idx: pd.DatetimeIndex, scale: float = 1.0) -> pd.DataFrame:
    n = len(idx)
    close = 50 * scale * np.cumprod(1 + np.full(n, 0.0002))
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.008,
            "low": close * 0.992,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
            "available_at": idx,
        },
        index=idx,
    )


def _good_setup() -> tuple[dict, pd.DataFrame, dict]:
    idx = _calendar()
    benchmark = pd.DataFrame(
        {"close": np.cumprod(1 + np.full(len(idx), 0.0003)) * 400, "volume": np.full(len(idx), 1e8)}, index=idx
    )
    bars = {"AAA": _bars(idx), "BBB": _bars(idx, 1.1), "CCC": _bars(idx[:180])}
    last = idx[-1].date()
    mid = idx[170].date()
    membership = {
        "AAA": [(idx[0].date(), None)],
        "BBB": [(idx[0].date(), None)],
        "CCC": [(idx[0].date(), mid)],  # real exit: delisted mid-sample
    }
    assert last > mid
    return bars, benchmark, membership


def test_good_pit_dataset_passes() -> None:
    bars, benchmark, membership = _good_setup()
    report = run_data_preflight(bars, benchmark, membership)
    assert report["status"] == STATUS_PASS, report["criticals"]
    assert report["n_membership_symbols"] == 3


def test_missing_membership_fails_closed() -> None:
    bars, benchmark, _ = _good_setup()
    report = run_data_preflight(bars, benchmark, None)
    assert report["status"] == STATUS_FAIL
    assert any(c["name"] == "membership-manifest" for c in report["criticals"])


def test_static_survivors_manifest_fails() -> None:
    """A manifest of today's survivors spanning the whole period is exactly the
    survivorship contamination this gate exists to refuse."""
    bars, benchmark, membership = _good_setup()
    idx0 = _calendar()[0].date()
    static = {sym: [(idx0, None)] for sym in ("AAA", "BBB", "CCC")}
    bars["CCC"] = _bars(_calendar())  # CCC has full history too now
    report = run_data_preflight(bars, benchmark, static)
    assert report["status"] == STATUS_FAIL
    names = {c["name"] for c in report["criticals"]}
    assert "membership-exits" in names
    assert "membership-dynamics" in names


def test_bars_without_membership_fail() -> None:
    bars, benchmark, membership = _good_setup()
    bars["ZZZ"] = _bars(_calendar())
    report = run_data_preflight(bars, benchmark, membership)
    assert report["status"] == STATUS_FAIL
    assert any(c["name"] == "bars-without-membership" for c in report["criticals"])


def test_member_without_bars_is_warning_not_critical() -> None:
    bars, benchmark, membership = _good_setup()
    del bars["CCC"]
    report = run_data_preflight(bars, benchmark, membership)
    assert report["status"] == STATUS_PASS_WARNINGS
    assert any(w["name"] == "member-no-bars:CCC" for w in report["warnings"])
    assert not report["criticals"]


def test_bad_ohlc_and_nan_detected() -> None:
    bars, benchmark, membership = _good_setup()
    broken = bars["AAA"].copy()
    broken.iloc[10, broken.columns.get_loc("close")] = np.nan
    report = run_data_preflight({"AAA": broken, **{k: v for k, v in bars.items() if k != "AAA"}}, benchmark, membership)
    assert report["status"] == STATUS_FAIL
    assert any(c["name"] == "nan:AAA" for c in report["criticals"])

    insane = bars["AAA"].copy()
    insane.iloc[5, insane.columns.get_loc("high")] = insane["close"].iloc[5] - 10.0
    report = run_data_preflight({"AAA": insane, **{k: v for k, v in bars.items() if k != "AAA"}}, benchmark, membership)
    assert any(c["name"] == "ohlc-sanity:AAA" for c in report["criticals"])


def test_off_calendar_detected() -> None:
    bars, benchmark, membership = _good_setup()
    saturday = pd.DatetimeIndex([pd.Timestamp("2022-01-08", tz="UTC")])  # a Saturday
    extra = pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0, "available_at": saturday},
        index=saturday,
    )
    off = pd.concat([bars["AAA"].iloc[:5], extra, bars["AAA"].iloc[5:]])
    bars2 = {**bars, "AAA": off}
    report = run_data_preflight(bars2, benchmark, membership)
    assert report["status"] == STATUS_FAIL
    assert any(c["name"] == "calendar:AAA" for c in report["criticals"])


def test_non_utc_detected() -> None:
    bars, benchmark, membership = _good_setup()
    ny = bars["AAA"].copy()
    ny.index = ny.index.tz_convert("America/New_York")
    bars2 = {**bars, "AAA": ny}
    report = run_data_preflight(bars2, benchmark, membership)
    assert report["status"] == STATUS_FAIL
    assert any(c["name"] == "utc:AAA" for c in report["criticals"])


def test_availability_metadata_checked() -> None:
    bars, benchmark, membership = _good_setup()
    av = bars["AAA"].copy()
    stamps = av.index
    av["available_at"] = stamps
    av.iloc[20, av.columns.get_loc("available_at")] = stamps[5]  # available before bar
    report = run_data_preflight({"AAA": av, **{k: v for k, v in bars.items() if k != "AAA"}}, benchmark, membership)
    assert any(c["name"] == "availability:AAA" and not c["ok"] for c in report["checks"])

    # missing availability column => warning, not failure
    plain = {k: v.drop(columns=["available_at"]) for k, v in bars.items()}
    report = run_data_preflight(plain, benchmark, membership)
    assert report["status"] == STATUS_PASS_WARNINGS
    assert any(w["name"].startswith("availability-metadata:") for w in report["warnings"])


def test_membership_helpers_and_manifest_loading(tmp_path: Path) -> None:
    d = date(2022, 6, 1)
    m = {"AAA": [(date(2022, 1, 1), d)]}
    assert membership_on(m, date(2022, 3, 1)) == {"AAA"}
    assert membership_on(m, date(2022, 7, 1)) == set()

    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"schema_version": "1.0", "entries": [{"symbol": "aaa", "start": "2022-01-01"}]}))
    loaded = load_membership_manifest(manifest)
    assert "AAA" in loaded and loaded["AAA"][0][1] is None

    manifest.write_text(json.dumps({"schema_version": "0.9", "entries": []}))
    with pytest.raises(MembershipManifestError):
        load_membership_manifest(manifest)

    bad_range = {"schema_version": "1.0", "entries": [{"symbol": "A", "start": "2022-05-05", "end": "2022-01-01"}]}
    manifest.write_text(json.dumps(bad_range))
    with pytest.raises(MembershipManifestError):
        load_membership_manifest(manifest)

    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "entries": [
                    {"symbol": "A", "start": "2022-01-01", "end": "2022-06-01"},
                    {"symbol": "A", "start": "2022-05-01"},
                ],
            }
        )
    )
    with pytest.raises(MembershipManifestError):
        load_membership_manifest(manifest)


def test_csv_builder_roundtrips_through_loader(tmp_path: Path) -> None:
    csv_path = tmp_path / "m.csv"
    csv_path.write_text(
        "symbol,start,end\naapl,2022-01-03,\nbbb,2022-01-03,2022-06-01\nCCC,2022-02-01,NA\n",
        encoding="utf-8",
    )
    manifest = build_membership_manifest_from_csv(csv_path, source="vendor export test")
    assert manifest["source"] == "vendor export test"
    out = tmp_path / "manifest.json"
    out.write_text(json.dumps(manifest))
    loaded = load_membership_manifest(out)
    assert loaded["AAPL"] == [(date(2022, 1, 3), None)]
    assert loaded["BBB"] == [(date(2022, 1, 3), date(2022, 6, 1))]
    assert loaded["CCC"] == [(date(2022, 2, 1), None)]


def test_csv_builder_rejects_bad_rows(tmp_path: Path) -> None:
    with pytest.raises(MembershipManifestError):
        build_membership_manifest_from_rows([{"symbol": "", "start": "2022-01-01"}])
    with pytest.raises(MembershipManifestError):
        build_membership_manifest_from_rows([{"symbol": "A", "start": "not-a-date"}])
    with pytest.raises(MembershipManifestError):
        build_membership_manifest_from_rows(
            [
                {"symbol": "A", "start": "2022-01-01", "end": "2022-06-01"},
                {"symbol": "A", "start": "2022-05-01"},
            ]
        )
    bad = tmp_path / "bad.csv"
    bad.write_text("ticker,start\nX,2022-01-01\n", encoding="utf-8")
    with pytest.raises(MembershipManifestError):
        build_membership_manifest_from_csv(bad)


def test_report_is_deterministic_and_json_safe() -> None:
    bars, benchmark, membership = _good_setup()
    a = run_data_preflight(bars, benchmark, membership)
    b = run_data_preflight(bars, benchmark, membership)
    text_a = json.dumps(a, sort_keys=True, default=str)
    text_b = json.dumps(b, sort_keys=True, default=str)
    assert text_a == text_b
    future = _bars(_calendar() + pd.Timedelta(days=1))  # bar beyond benchmark end
    bars2 = dict(bars)
    bars2["AAA"] = future
    rep2 = run_data_preflight(bars2, benchmark, membership)
    assert rep2["status"] == STATUS_FAIL  # calendar:AAA
