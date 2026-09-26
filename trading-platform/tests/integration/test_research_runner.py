"""End-to-end synthetic research-runner test: family validation + reporting."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from trading_platform.research.runner import (
    ExternalSetupRequired,
    load_parquet_universe,
    run_family_research,
)
from trading_platform.validation import BootstrapConfig


def _universe() -> tuple[dict, pd.DataFrame]:
    rng = np.random.default_rng(42)
    n = 700
    idx = pd.date_range("2021-01-04", periods=n, freq="B")
    market_ret = rng.normal(0.0004, 0.012, n)

    def make(beta: float, alpha: float, seed: int) -> pd.DataFrame:
        r = np.random.default_rng(seed)
        rets = alpha + beta * market_ret + r.normal(0, 0.015, n)
        close = 50 * np.cumprod(1 + rets)
        return pd.DataFrame(
            {
                "open": close * (1 + r.normal(0, 0.002, n)),
                "high": close * 1.008,
                "low": close * 0.992,
                "close": close,
                "volume": r.integers(2_000_000, 6_000_000, n).astype(float),
            },
            index=idx,
        )

    universe = {f"SYM{i}": make(1.0, 0.0002, 100 + i) for i in range(6)}
    benchmark = pd.DataFrame(
        {
            "close": 100 * np.cumprod(1 + market_ret),
            "volume": np.full(n, 1e8),
        },
        index=idx,
    )
    return universe, benchmark


@pytest.mark.integration
def test_research_runner_records_all_trials(tmp_path: Path) -> None:
    universe, benchmark = _universe()
    report = run_family_research(
        "trend_relative_strength",
        universe,
        benchmark,
        param_grid=[
            {"momentum_window": 63, "rs_window": 63},
            {"momentum_window": 126, "rs_window": 126},
        ],
        n_folds=3,
        min_train=252,
        embargo=30,
        bootstrap=BootstrapConfig(n_resamples=50, block_length=10, seed=7),
        output_dir=tmp_path / "research",
        promotions_root=tmp_path / "promotions",
    )
    # Every tried configuration is recorded -- failures are data.
    assert report["trial_count"] == 2
    assert len(report["config_reports"]) == 2
    for cfg in report["config_reports"]:
        assert cfg["status"] in ("VALIDATED", "INSUFFICIENT_DATA")
        assert "promotion" in cfg
        assert cfg["promotion"]["status"] in ("APPROVED", "REJECTED", "RESEARCH_ONLY")
        assert "cost_stress" in cfg
        assert "concentration" in cfg
        assert "significance" in cfg
        assert "benchmark" in cfg
    # Universe is synthetic & non-PIT: evidence ceiling must say so.
    assert report["evidence_ceiling"].startswith("CAPPED")
    assert any("survivorship" in b for b in report["known_biases"])
    artifacts = list((tmp_path / "research").glob("trend_relative_strength--*.json"))
    assert artifacts, "report JSON must be persisted"
    payload = json.loads(artifacts[0].read_text())
    assert payload["family_name"] == "trend_relative_strength"
    assert list((tmp_path / "research").glob("trend_relative_strength--*.md"))
    # All trial promotion decisions persisted too.
    assert list((tmp_path / "promotions").glob("**/*.json"))


@pytest.mark.integration
def test_membership_removes_survivorship_ceiling(tmp_path: Path) -> None:
    universe, benchmark = _universe()
    symbols = sorted(universe)
    membership = {d: set(symbols[:3]) for d in benchmark.index}
    report = run_family_research(
        "trend_relative_strength",
        universe,
        benchmark,
        param_grid=[{"momentum_window": 63, "rs_window": 63}],
        n_folds=3,
        min_train=252,
        embargo=30,
        bootstrap=BootstrapConfig(n_resamples=50, block_length=10, seed=7),
        output_dir=tmp_path,
        promotions_root=tmp_path / "promotions",
        universe_membership=membership,
    )
    assert report["evidence_ceiling"] == "FULL_OOS_CAPABLE"
    assert not any("survivorship" in b for b in report["known_biases"])


@pytest.mark.integration
def test_load_universe_requires_setup(tmp_path: Path) -> None:
    with pytest.raises(ExternalSetupRequired):
        load_parquet_universe(tmp_path / "missing", ["SPY"])
    (tmp_path / "data").mkdir()
    with pytest.raises(ExternalSetupRequired):
        load_parquet_universe(tmp_path / "data", ["SPY"])


@pytest.mark.integration
def test_non_pit_evidence_cannot_mint_paper_eligibility(tmp_path: Path, monkeypatch) -> None:
    """Security boundary (regression): an APPROVED verdict computed on a
    survivorship-biased (non-PIT) universe is downgraded to RESEARCH_ONLY and
    can never persist the artifact kind the paper orchestrator trusts."""
    import trading_platform.research.runner as runner
    from trading_platform.promotion import PromotionDecision

    def fake_approved(*args, **kwargs):  # simulate every promotion class passing
        return PromotionDecision(
            status="APPROVED",
            strategy_id="family",
            config_id="cfg",
            reasons=(),
            policy_hash="p",
            report_hash="r",
            decided_at="2026-01-01T00:00:00+00:00",
        )

    monkeypatch.setattr(runner, "evaluate_promotion", fake_approved)
    universe, benchmark = _universe()
    grid = [{"momentum_window": 63, "rs_window": 63}, {"momentum_window": 126, "rs_window": 126}]
    common = dict(
        param_grid=grid,
        n_folds=2,
        min_train=500,
        embargo=10,
        bootstrap=BootstrapConfig(n_resamples=30, block_length=10, seed=7),
        promotions_root=tmp_path / "promotions",
    )
    report = runner.run_family_research("trend_relative_strength", universe, benchmark, **common)
    assert report["promotion_summary"]["approved"] == []
    for cfg in report["config_reports"]:
        assert cfg["promotion"]["status"] == "RESEARCH_ONLY"
        assert "survivorship" in " ".join(cfg["promotion"]["reasons"])
    artifacts = list((tmp_path / "promotions").glob("**/*.json"))
    assert artifacts
    for path in artifacts:
        assert json.loads(path.read_text())["status"] != "APPROVED"

    # PIT membership lifts the ceiling: the same passing evidence persists as
    # APPROVED (real gates still run in production; this isolates the wiring).
    membership = {d: sorted(universe) for d in benchmark.index}
    report2 = runner.run_family_research(
        "trend_relative_strength", universe, benchmark, universe_membership=membership, **common
    )
    assert report2["promotion_summary"]["approved"]
    assert any(json.loads(p.read_text())["status"] == "APPROVED" for p in (tmp_path / "promotions").glob("**/*.json"))
