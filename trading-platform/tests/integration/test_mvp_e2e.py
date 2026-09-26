"""MVP-1 end-to-end acceptance: the operator workflow, not strategy alpha.

Deterministic synthetic fixtures drive the full product path through the
canonical CLI:

    vendor-like membership CSV -> builder -> manifest -> data preflight ->
    fingerprint -> run-all families -> reports -> promotion gate -> MVP summary

plus every fail-closed negative path. Passing this file proves the APP works;
it is NOT evidence of profitable strategy behavior (that requires licensed
real PIT data + elapsed forward evidence — see docs/MVP1.md).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from trading_platform.cli import main
from trading_platform.cli.doctor_cmd import run_doctor
from trading_platform.research.dataset import load_dataset_manifest
from trading_platform.research.mvp import SUMMARY_JSON, run_mvp_research
from trading_platform.validation import BootstrapConfig

pytestmark = pytest.mark.integration

START = "2021-01-04"
SYMBOLS = ["AAA", "BBB", "CCC", "DDD"]


def _calendar(n_days: int) -> pd.DatetimeIndex:
    return pd.date_range(START, periods=n_days, freq="B", tz="UTC")


def _frame(idx: pd.DatetimeIndex, seed: int, drift: float, *, flat: bool, available_at: bool) -> pd.DataFrame:
    n = len(idx)
    if flat:
        close = np.full(n, 50.0)
        open_ = close
    else:
        rng = np.random.default_rng(seed)
        close = 50.0 * np.cumprod(1.0 + drift + rng.normal(0.0, 0.013, n))
        open_ = close * (1.0 + rng.normal(0.0, 0.002, n))
    df = pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) * 1.005,
            "low": np.minimum(open_, close) * 0.995,
            "close": close,
            "volume": np.full(n, 2_500_000.0),
        },
        index=idx,
    )
    if available_at:
        df["available_at"] = idx
    return df


def _write_universe(
    data_dir: Path,
    n_days: int,
    *,
    drift: float = 0.0004,
    flat: bool = False,
    available_at: bool = True,
) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    idx = _calendar(n_days)
    for i, symbol in enumerate(SYMBOLS):
        _frame(idx, 7 + i, drift, flat=flat, available_at=available_at).to_parquet(data_dir / f"{symbol}.parquet")
    _frame(idx, 991, 0.0002, flat=flat, available_at=available_at).to_parquet(data_dir / "SPY.parquet")


def _vendor_csv(path: Path, n_days: int) -> None:
    """Membership with one exit and one delayed add (dynamic, exits present)."""
    idx = _calendar(n_days)
    rows = [
        f"AAA,{idx[0].date().isoformat()},{idx[n_days // 2].date().isoformat()}",
        f"BBB,{idx[0].date().isoformat()},",
        f"CCC,{idx[n_days // 3].date().isoformat()},",
        f"DDD,{idx[0].date().isoformat()},",
    ]
    path.write_text("symbol,start,end\n" + "\n".join(rows) + "\n", encoding="utf-8")


def _prepare(
    root: Path,
    n_days: int,
    *,
    flat: bool = False,
    available_at: bool = True,
) -> tuple[Path, Path]:
    """Write bars + vendor CSV, build manifest through the CLI. Returns (data, manifest)."""
    data = root / "bars"
    _write_universe(data, n_days, flat=flat, available_at=available_at)
    csv = root / "vendor.csv"
    _vendor_csv(csv, n_days)
    manifest = root / "universe_membership.json"
    assert main(["data", "build-membership", "--csv", str(csv), "--output", str(manifest), "--source", "e2e"]) == 0
    return data, manifest


class _Session:
    """One full run-all produced at module scope and asserted by many tests."""

    def __init__(self, root: Path, data: Path, manifest: Path, output: Path, run_dir: Path, console: str):
        self.root = root
        self.data = data
        self.manifest = manifest
        self.output = output
        self.run_dir = run_dir
        self.console = console
        self.summary = json.loads((run_dir / SUMMARY_JSON).read_text(encoding="utf-8"))
        self.dataset_manifest = json.loads((run_dir / "dataset_manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def clean_session(tmp_path_factory: pytest.TempPathFactory) -> _Session:
    import contextlib
    import io

    root = tmp_path_factory.mktemp("mvp_clean")
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        data, manifest = _prepare(root, 360)
        output = root / "research"
        code = main(
            [
                "research",
                "run-all",
                "--data-dir",
                str(data),
                "--benchmark",
                "SPY",
                "--membership",
                str(manifest),
                "--output-dir",
                str(output),
                "--bootstrap-resamples",
                "50",
                "--n-folds",
                "2",
                "--min-train",
                "120",
                "--embargo",
                "20",
            ]
        )
    console = buffer.getvalue()
    assert code == 0, console
    run_dir = next(p for p in output.iterdir() if p.is_dir())
    return _Session(root, data, manifest, output, run_dir, console)


class TestHappyPath:
    def test_gate_and_verdict_line(self, clean_session: _Session) -> None:
        assert "DATA QUALITY: PASS" in clean_session.console
        assert "FINAL_VERDICT:" in clean_session.console
        assert "run_id=" in clean_session.console

    def test_artifact_layout(self, clean_session: _Session) -> None:
        run = clean_session.run_dir
        for name in ("data_preflight.json", "dataset_manifest.json", "experiments.json", SUMMARY_JSON):
            assert (run / name).exists(), name
        assert (run / "MVP_RESEARCH_SUMMARY.md").exists()
        for family in ("trend_relative_strength", "breakout_volume", "trend_pullback"):
            assert list(run.glob(f"{family}--*.json")), family
            assert list(run.glob(f"{family}--*.md")), family
        assert list((run / "promotion").glob("**/*.json"))

    def test_dataset_identity_pins_exact_bytes(self, clean_session: _Session) -> None:
        ds = clean_session.dataset_manifest
        assert len(ds["dataset_fingerprint"]) == 64
        assert ds["data_quality"]["verdict"] == "PASS"
        assert ds["membership"]["source"] == "e2e"
        assert ds["membership"]["n_exits"] == 1
        assert set(ds["symbols"]) == set(SYMBOLS)
        assert ds["benchmark"]["symbol"] == "SPY"
        for entry in ds["bars"].values():
            assert len(entry["sha256"]) == 64
        loaded = load_dataset_manifest(clean_session.run_dir / "dataset_manifest.json")
        assert loaded["dataset_fingerprint"] == ds["dataset_fingerprint"]
        tampered = json.loads((clean_session.run_dir / "dataset_manifest.json").read_text(encoding="utf-8"))
        tampered["benchmark"]["sha256"] = "0" * 64
        bad_path = clean_session.root / "tampered_manifest.json"
        bad_path.write_text(json.dumps(tampered), encoding="utf-8")
        with pytest.raises(ValueError, match="fingerprint mismatch"):
            load_dataset_manifest(bad_path)

    def test_every_trial_retained_and_pinned(self, clean_session: _Session) -> None:
        experiments = json.loads((clean_session.run_dir / "experiments.json").read_text(encoding="utf-8"))
        assert len(experiments) == 12  # 3 families x 4 pinned configs; failures are data
        assert all(
            e["dataset_fingerprint"] == clean_session.dataset_manifest["dataset_fingerprint"] for e in experiments
        )
        assert all(e["source_commit"] == clean_session.summary["source_commit"] for e in experiments)

    def test_summary_traceability_and_promotion_consistency(self, clean_session: _Session) -> None:
        summary = clean_session.summary
        assert len(summary["source_commit"]) == 40  # pinned git commit
        assert summary["promotion_policy"]["version"] and len(summary["promotion_policy"]["hash"]) == 64
        assert summary["backtest_config"]["cost"]["commission_per_order"] == 1.0  # cost model pinned
        assert summary["data_quality"]["verdict"] == "PASS"
        assert summary["live_status"] == "NOT_AUTHORIZED"
        promoted = [f["family"] for f in summary["families"] if f["verdict"] == "APPROVED"]
        assert summary["promoted"] == promoted
        assert summary["final_verdict"] == (
            "STRATEGY_APPROVED_FOR_SHADOW"
            if promoted
            else (
                "RESEARCH_ONLY_CANDIDATES"
                if any(f["verdict"] == "RESEARCH_ONLY" for f in summary["families"])
                else "NO_STRATEGY_PROMOTED"
            )
        )
        for fam in summary["families"]:
            if fam["verdict"] == "REJECTED":
                assert fam["rejection_reasons"], "rejections must always name reasons"
            # family reports must be traceable to the run identity
            report = json.loads(list(clean_session.run_dir.glob(f"{fam['family']}--*.json"))[0].read_text("utf-8"))
            assert report["dataset_fingerprint"] == summary["dataset_fingerprint"]
            assert report["run_id"] == summary["run_id"]

    def test_reports_and_status_commands(self, clean_session: _Session, capsys, monkeypatch) -> None:
        summary = clean_session.summary
        assert main(["research", "status", "--output-dir", str(clean_session.output)]) == 0
        assert summary["run_id"] in capsys.readouterr().out
        assert main(["report", "show", "--output-dir", str(clean_session.output)]) == 0
        assert f"run `{summary['run_id']}`" in capsys.readouterr().out
        assert (
            main(
                [
                    "report",
                    "show",
                    "--output-dir",
                    str(clean_session.output),
                    "--run",
                    summary["run_id"],
                    "--family",
                    "breakout_volume",
                ]
            )
            == 0
        )
        assert "Strategy Research Report" in capsys.readouterr().out
        assert main(["report", "show", "--output-dir", str(clean_session.output), "--format", "json"]) == 0
        assert summary["run_id"] in capsys.readouterr().out
        monkeypatch.setenv("TRADING_RESEARCH_OUTPUT", str(clean_session.output))
        assert main(["status"]) == 0
        out = capsys.readouterr().out
        assert "MVP_STATE=" in out and "LIVE_STATUS=NOT_AUTHORIZED" in out
        assert summary["run_id"] in out

    def test_doctor_green_with_configured_data(self, clean_session: _Session, monkeypatch) -> None:
        monkeypatch.setenv("TRADING_DATA_DIR", str(clean_session.data))
        monkeypatch.setenv("TRADING_MEMBERSHIP", str(clean_session.manifest))
        monkeypatch.setenv("TRADING_RESEARCH_OUTPUT", str(clean_session.output))
        report = run_doctor()
        assert report.exit_code == 0, report.render()
        levels = {c.name: c.level for c in report.checks}
        assert levels["research data"] == "PASS"
        assert levels["live trading"] == "PASS"


@pytest.fixture(scope="module")
def warn_env(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    root = tmp_path_factory.mktemp("mvp_warn")
    # flat prices + no available_at -> PASS_WITH_WARNINGS, no promotable signal
    data, manifest = _prepare(root, 240, flat=True, available_at=False)
    return data, manifest, root / "research"


class TestWarningsAcknowledgement:
    """§5: PASS_WITH_WARNINGS blocks the canonical flow until explicitly accepted."""

    def test_preflight_exit1_and_run_blocks_without_ack(
        self, warn_env: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        data, manifest, output = warn_env
        assert (
            main(["data", "preflight", "--data-dir", str(data), "--benchmark", "SPY", "--membership", str(manifest)])
            == 1
        )
        capsys.readouterr()
        code = main(
            [
                "research",
                "run-all",
                "--data-dir",
                str(data),
                "--benchmark",
                "SPY",
                "--membership",
                str(manifest),
                "--output-dir",
                str(output),
            ]
        )
        assert code == 4
        out = capsys.readouterr().out
        assert "PASS_WITH_WARNINGS" in out and "--accept-data-warnings" in out
        assert not output.exists() or not list(output.glob(f"*/{SUMMARY_JSON}"))

    def test_accepted_warnings_persist_and_no_promotion_succeeds(
        self, warn_env: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
    ) -> None:
        data, manifest, output = warn_env
        code = main(
            [
                "research",
                "run-all",
                "--data-dir",
                str(data),
                "--benchmark",
                "SPY",
                "--membership",
                str(manifest),
                "--output-dir",
                str(output),
                "--accept-data-warnings",
                "--bootstrap-resamples",
                "40",
                "--n-folds",
                "2",
                "--min-train",
                "100",
                "--embargo",
                "10",
            ]
        )
        assert code == 0  # successful MVP execution even though nothing is promoted
        out = capsys.readouterr().out
        assert "DATA QUALITY: PASS_WITH_WARNINGS" in out
        run_dirs = [p for p in output.iterdir() if p.is_dir()]
        assert len(run_dirs) == 1
        run = run_dirs[0]
        ds = json.loads((run / "dataset_manifest.json").read_text(encoding="utf-8"))
        assert ds["warnings_acknowledgement"]["acknowledged"] is True
        assert ds["accepted_warnings"]
        summary = json.loads((run / SUMMARY_JSON).read_text(encoding="utf-8"))
        assert summary["warnings_acknowledged"] is True and summary["n_warnings"] >= 1
        assert summary["promoted"] == []
        md = (run / "MVP_RESEARCH_SUMMARY.md").read_text(encoding="utf-8")
        assert "Accepted data warnings" in md
        report = json.loads(list(run.glob("trend_relative_strength--*.json"))[0].read_text(encoding="utf-8"))
        assert any("data warning accepted" in b for b in report["known_biases"])
        assert report["data_preflight_verdict"] == "PASS_WITH_WARNINGS"


class TestFailClosed:
    def test_missing_membership_is_external_setup(self, tmp_path: Path, capsys) -> None:
        data = tmp_path / "bars"
        _write_universe(data, 240)
        _vendor_csv(tmp_path / "vendor.csv", 240)
        code = main(
            [
                "research",
                "run-all",
                "--data-dir",
                str(data),
                "--benchmark",
                "SPY",
                "--membership",
                str(tmp_path / "nope.json"),
                "--output-dir",
                str(tmp_path / "research"),
            ]
        )
        assert code == 3
        assert "build-membership" in capsys.readouterr().out

    def test_static_membership_without_exits_fails_gate(self, tmp_path: Path, capsys) -> None:
        data = tmp_path / "bars"
        _write_universe(data, 240)
        idx = _calendar(240)
        csv = tmp_path / "current_only.csv"
        csv.write_text(
            "symbol,start,end\n" + "\n".join(f"{s},{idx[0].date().isoformat()}," for s in SYMBOLS) + "\n",
            encoding="utf-8",
        )
        manifest = tmp_path / "m.json"
        assert main(["data", "build-membership", "--csv", str(csv), "--output", str(manifest)]) == 0
        assert "zero membership exits" in capsys.readouterr().out
        assert (
            main(["data", "preflight", "--data-dir", str(data), "--benchmark", "SPY", "--membership", str(manifest)])
            == 2
        )
        assert "membership-exits" in capsys.readouterr().out
        # research refuses to start even if the operator insists on run-all
        code = main(
            [
                "research",
                "run-all",
                "--data-dir",
                str(data),
                "--benchmark",
                "SPY",
                "--membership",
                str(manifest),
                "--output-dir",
                str(tmp_path / "research"),
                "--accept-data-warnings",
            ]
        )
        assert code == 2
        capsys.readouterr()

    def test_bad_ohlc_fails_gate(self, tmp_path: Path, capsys) -> None:
        data, manifest = _prepare(tmp_path, 240)
        path = data / "CCC.parquet"
        df = pd.read_parquet(path)
        df.iloc[100, df.columns.get_loc("high")] = df["low"].iloc[100] * 0.5
        df.to_parquet(path)
        capsys.readouterr()
        code = main(["data", "preflight", "--data-dir", str(data), "--benchmark", "SPY", "--membership", str(manifest)])
        assert code == 2
        assert "ohlc-sanity" in capsys.readouterr().out

    def test_research_run_refuses_non_pit_without_opt_in(self, clean_session: _Session, tmp_path: Path, capsys) -> None:
        code = main(
            [
                "research",
                "run",
                "--family",
                "trend_relative_strength",
                "--data-dir",
                str(clean_session.data),
                "--benchmark",
                "SPY",
                "--symbols",
                *SYMBOLS,
                "--output-dir",
                str(tmp_path / "research"),
            ]
        )
        assert code == 2  # Gate Zero: no manifest -> no research
        assert "point-in-time" in capsys.readouterr().out

    def test_explicit_non_pit_run_is_labelled_capped(self, clean_session: _Session, tmp_path: Path, capsys) -> None:
        code = main(
            [
                "research",
                "run",
                "--family",
                "trend_relative_strength",
                "--data-dir",
                str(clean_session.data),
                "--benchmark",
                "SPY",
                "--symbols",
                *SYMBOLS,
                "--allow-non-pit",
                "--output-dir",
                str(tmp_path / "research"),
                "--bootstrap-resamples",
                "40",
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
        assert "EXPLORATORY_NON_PIT" in out and "CAPPED" in out

    def test_insufficient_evidence_never_approves(self, tmp_path: Path, capsys) -> None:
        """Short history vs pinned grids: promotion must fail closed
        (REJECTED/RESEARCH_ONLY only, never APPROVED)."""
        data, manifest = _prepare(tmp_path, 300)
        capsys.readouterr()
        summary = run_mvp_research(
            data_dir=data,
            benchmark="SPY",
            membership_path=manifest,
            output_root=tmp_path / "research",
            n_folds=2,
            min_train=260,
            embargo=20,
            bootstrap=BootstrapConfig(n_resamples=30, block_length=10, seed=42),
            families=["trend_relative_strength"],
        )
        assert summary["promoted"] == []
        assert summary["final_verdict"] != "STRATEGY_APPROVED_FOR_SHADOW"
        for fam in summary["families"]:
            assert fam["verdict"] in ("REJECTED", "RESEARCH_ONLY", "ERROR")
            if fam["verdict"] in ("REJECTED", "RESEARCH_ONLY"):
                decisions = list((Path(summary["run_dir"]) / "promotion").glob("**/*.json"))
                assert all(json.loads(p.read_text(encoding="utf-8"))["status"] != "APPROVED" for p in decisions)
