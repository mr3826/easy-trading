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
