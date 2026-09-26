"""Operator CLI surface behavior (unit level, no full research runs)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from trading_platform.cli import main
from trading_platform.cli._common import try_writable
from trading_platform.cli.status_cmd import build_status_payload
from trading_platform.research.data_quality import build_membership_manifest_from_rows

START = "2021-01-04"


def _bars(data: Path, n_days: int = 240, available_at: bool = True) -> None:
    data.mkdir(parents=True, exist_ok=True)
    idx = pd.date_range(START, periods=n_days, freq="B", tz="UTC")
    rng = np.random.default_rng(5)
    for i, symbol in enumerate(["AAA", "BBB", "CCC"]):
        close = 50 * np.cumprod(1 + rng.normal(0.0003, 0.012, n_days))
        open_ = close * 1.001
        df = pd.DataFrame(
            {
                "open": open_,
                "high": np.maximum(open_, close) * 1.005,
                "low": np.minimum(open_, close) * 0.995,
                "close": close,
                "volume": np.full(n_days, 2.5e6),
            },
            index=idx,
        )
        if available_at:
            df["available_at"] = idx
        df.to_parquet(data / f"{symbol}.parquet")
    close = 50 * np.cumprod(1 + rng.normal(0.0001, 0.01, n_days))
    open_ = close * 1.001
    bench = pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * 1.005,
            "low": np.minimum(open_, close) * 0.995,
            "close": close,
            "volume": np.full(n_days, 2.5e6),
        },
        index=idx,
    )
    if available_at:
        bench["available_at"] = idx
    bench.to_parquet(data / "SPY.parquet")


def _manifest(tmp: Path) -> Path:
    idx = pd.date_range(START, periods=240, freq="B", tz="UTC")
    rows = [
        {"symbol": "AAA", "start": idx[0].date().isoformat(), "end": idx[100].date().isoformat()},
        {"symbol": "BBB", "start": idx[0].date().isoformat(), "end": None},
        {"symbol": "CCC", "start": idx[60].date().isoformat(), "end": None},
    ]
    manifest = build_membership_manifest_from_rows(rows, source="unit")
    path = tmp / "m.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_build_membership_missing_csv_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["data", "build-membership", "--csv", str(tmp_path / "x.csv"), "--output", str(tmp_path / "o.json")])
    assert code == 2
    assert "ERROR" in capsys.readouterr().out


def test_preflight_writes_report_and_exits_pass(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "bars"
    _bars(data)
    manifest = _manifest(tmp_path)
    out = tmp_path / "pf.json"
    code = main(
        [
            "data",
            "preflight",
            "--data-dir",
            str(data),
            "--benchmark",
            "SPY",
            "--membership",
            str(manifest),
            "--output",
            str(out),
        ]
    )
    assert code == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert "report=" in capsys.readouterr().out


def test_preflight_benchmark_missing_is_external_setup(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "bars"
    data.mkdir()
    manifest = _manifest(tmp_path)
    code = main(["data", "preflight", "--data-dir", str(data), "--benchmark", "SPY", "--membership", str(manifest)])
    assert code == 3
    assert "REQUIRES_EXTERNAL_SETUP" in capsys.readouterr().out


def test_preflight_invalid_manifest_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "bars"
    _bars(data)
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    code = main(["data", "preflight", "--data-dir", str(data), "--benchmark", "SPY", "--membership", str(bad)])
    assert code == 2
    assert "invalid membership manifest" in capsys.readouterr().out


def test_research_run_gated_by_membership(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "bars"
    _bars(data)
    manifest = _manifest(tmp_path)
    code = main(
        [
            "research",
            "run",
            "--family",
            "trend_relative_strength",
            "--data-dir",
            str(data),
            "--benchmark",
            "SPY",
            "--membership",
            str(manifest),
            "--output-dir",
            str(tmp_path / "research"),
            "--bootstrap-resamples",
            "20",
            "--n-folds",
            "2",
            "--min-train",
            "100",
            "--embargo",
            "10",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "DATA QUALITY:" in out
    assert "trend_relative_strength" in out


def test_report_show_missing_run_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["report", "show", "--output-dir", str(tmp_path), "--run", "nope"]) == 1
    assert "not found" in capsys.readouterr().out
    assert main(["report", "show", "--output-dir", str(tmp_path)]) == 3  # no latest run
    capsys.readouterr()


def test_research_status_and_latest_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["research", "status", "--output-dir", str(tmp_path)]) == 0
    assert "NONE" in capsys.readouterr().out
    assert main(["research", "latest", "--output-dir", str(tmp_path)]) == 3
    capsys.readouterr()


def test_status_payload_shape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TRADING_DATA_DIR", raising=False)
    monkeypatch.delenv("TRADING_MEMBERSHIP", raising=False)
    payload = build_status_payload(tmp_path)
    assert payload["live_trading_enabled"] is False
    assert payload["live_status"] == "NOT_AUTHORIZED"
    assert payload["mvp_state"] in {"NOT_CONFIGURED", "REQUIRES_EXTERNAL_DATA"}
    assert json.dumps(payload)  # serializable


def test_main_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_try_writable_roundtrip(tmp_path: Path) -> None:
    ok, detail = try_writable(tmp_path / "nested" / "dir")
    assert ok is True
    assert "nested" in detail
