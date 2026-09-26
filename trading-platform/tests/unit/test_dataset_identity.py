"""Dataset identity/fingerprint invariants (docs/MVP1.md §Reproducibility)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
from trading_platform.research.data_quality import (
    MEMBERSHIP_SCHEMA_VERSION,
    build_membership_manifest_from_rows,
    load_membership_manifest,
)
from trading_platform.research.dataset import build_dataset_manifest, load_dataset_manifest, sha256_file


def _bars(tmp: Path, close0: float = 50.0) -> dict:
    idx = pd.date_range("2022-01-03", periods=10, freq="B", tz="UTC")
    frames = {}
    for symbol in ("AAA", "BBB"):
        frames[symbol] = pd.DataFrame(
            {
                "open": [close0] * 10,
                "high": [close0 * 1.01] * 10,
                "low": [close0 * 0.99] * 10,
                "close": [close0] * 10,
                "volume": [1000.0] * 10,
            },
            index=idx,
        )
        frames[symbol].to_parquet(tmp / f"{symbol}.parquet")
    frames["SPY"] = frames["AAA"].copy()
    frames["SPY"].to_parquet(tmp / "SPY.parquet")
    return frames


def _membership(tmp: Path) -> dict:
    manifest = build_membership_manifest_from_rows(
        [
            {"symbol": "AAA", "start": "2022-01-03", "end": "2022-01-07"},
            {"symbol": "BBB", "start": "2022-01-03", "end": None},
        ],
        source="unit",
    )
    path = tmp / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return {"path": path, "map": load_membership_manifest(path)}


def _report(verdict: str = "PASS") -> dict:
    return {"status": verdict, "version": "1.1.0", "warnings": []}


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    path = tmp_path / "x.bin"
    path.write_bytes(b"easy-trading")
    assert sha256_file(path) == hashlib.sha256(b"easy-trading").hexdigest()


def test_fingerprint_is_deterministic_across_calls(tmp_path: Path) -> None:
    bars = _bars(tmp_path)
    mem = _membership(tmp_path)
    kwargs = dict(
        data_dir=tmp_path,
        benchmark="SPY",
        membership_path=mem["path"],
        bars={k: v for k, v in bars.items() if k != "SPY"},
        benchmark_frame=bars["SPY"],
        membership=mem["map"],
        preflight_report=_report(),
    )
    first = build_dataset_manifest(**kwargs)
    second = build_dataset_manifest(**kwargs)
    assert first["dataset_fingerprint"] == second["dataset_fingerprint"]
    assert first["created_at"] and second["created_at"]  # timestamp recorded, excluded from identity


def test_any_byte_change_changes_identity(tmp_path: Path) -> None:
    bars = _bars(tmp_path)
    mem = _membership(tmp_path)
    base = build_dataset_manifest(
        data_dir=tmp_path,
        benchmark="SPY",
        membership_path=mem["path"],
        bars={k: v for k, v in bars.items() if k != "SPY"},
        benchmark_frame=bars["SPY"],
        membership=mem["map"],
        preflight_report=_report(),
    )
    # changed bar bytes
    _bars(tmp_path, close0=51.0)
    changed = build_dataset_manifest(
        data_dir=tmp_path,
        benchmark="SPY",
        membership_path=mem["path"],
        bars={k: v for k, v in bars.items() if k != "SPY"},
        benchmark_frame=bars["SPY"],
        membership=mem["map"],
        preflight_report=_report(),
    )
    assert base["dataset_fingerprint"] != changed["dataset_fingerprint"]


def test_verdict_and_warnings_participate_in_identity(tmp_path: Path) -> None:
    bars = _bars(tmp_path)
    mem = _membership(tmp_path)
    common = dict(
        data_dir=tmp_path,
        benchmark="SPY",
        membership_path=mem["path"],
        bars={k: v for k, v in bars.items() if k != "SPY"},
        benchmark_frame=bars["SPY"],
        membership=mem["map"],
    )
    clean = build_dataset_manifest(preflight_report=_report("PASS"), **common)
    warned = build_dataset_manifest(
        preflight_report={"status": "PASS_WITH_WARNINGS", "version": "1.1.0", "warnings": [{"name": "w"}]},
        accepted_warnings=[{"name": "w", "detail": "d"}],
        warnings_acknowledged=True,
        **common,
    )
    assert clean["dataset_fingerprint"] != warned["dataset_fingerprint"]
    assert warned["warnings_acknowledgement"]["required"] is True
    assert warned["accepted_warnings"] == [{"name": "w", "detail": "d"}]
    not_ack = build_dataset_manifest(
        preflight_report={"status": "PASS_WITH_WARNINGS", "version": "1.1.0", "warnings": [{"name": "w"}]},
        **common,
    )
    assert not_ack["warnings_acknowledgement"]["acknowledged"] is False


def test_manifest_covers_required_identity_components(tmp_path: Path) -> None:
    bars = _bars(tmp_path)
    mem = _membership(tmp_path)
    manifest = build_dataset_manifest(
        data_dir=tmp_path,
        benchmark="SPY",
        membership_path=mem["path"],
        bars={k: v for k, v in bars.items() if k != "SPY"},
        benchmark_frame=bars["SPY"],
        membership=mem["map"],
        preflight_report=_report(),
    )
    assert set(manifest["symbols"]) == {"AAA", "BBB"}
    assert manifest["membership"]["source"] == "unit"
    assert manifest["membership"]["n_exits"] == 1
    assert manifest["date_range"]["n_trading_days"] == 10
    assert manifest["corporate_actions"]["used"] is False
    assert manifest["data_quality"]["version"]
    assert manifest["version"]


def test_load_dataset_manifest_detects_tampering(tmp_path: Path) -> None:
    bars = _bars(tmp_path)
    mem = _membership(tmp_path)
    manifest = build_dataset_manifest(
        data_dir=tmp_path,
        benchmark="SPY",
        membership_path=mem["path"],
        bars={k: v for k, v in bars.items() if k != "SPY"},
        benchmark_frame=bars["SPY"],
        membership=mem["map"],
        preflight_report=_report(),
    )
    path = tmp_path / "dataset_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert load_dataset_manifest(path)["dataset_fingerprint"] == manifest["dataset_fingerprint"]
    bad = dict(manifest)
    bad["symbols"] = ["AAA"]
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        load_dataset_manifest(bad_path)
    not_a_manifest = tmp_path / "other.json"
    not_a_manifest.write_text('{"x": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="not a dataset manifest"):
        load_dataset_manifest(not_a_manifest)


def test_schema_version_constant_stable(tmp_path: Path) -> None:
    mem = _membership(tmp_path)
    payload = json.loads(mem["path"].read_text(encoding="utf-8"))
    assert payload["schema_version"] == MEMBERSHIP_SCHEMA_VERSION


def test_bare_list_manifest_form_supported(tmp_path: Path) -> None:
    """load_membership_manifest accepts a legacy bare-list manifest; the
    fingerprint builder must handle the same accepted form without crashing."""
    bars = _bars(tmp_path)
    path = tmp_path / "m.json"
    path.write_text(
        json.dumps(
            [
                {"symbol": "AAA", "start": "2022-01-03", "end": "2022-01-07"},
                {"symbol": "BBB", "start": "2022-01-03", "end": None},
            ]
        ),
        encoding="utf-8",
    )
    from trading_platform.research.data_quality import load_membership_manifest

    membership = load_membership_manifest(path)
    manifest = build_dataset_manifest(
        data_dir=tmp_path,
        benchmark="SPY",
        membership_path=path,
        bars={k: v for k, v in bars.items() if k != "SPY"},
        benchmark_frame=bars["SPY"],
        membership=membership,
        preflight_report=_report(),
    )
    assert manifest["membership"]["n_entries"] == 2
    assert manifest["membership"]["n_exits"] == 1
    assert len(manifest["dataset_fingerprint"]) == 64
