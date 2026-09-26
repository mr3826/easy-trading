"""MVP application-service invariants: registry, state model, verdicts."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from trading_platform.research.data_quality import Membership
from trading_platform.research.mvp import (
    PARAM_GRIDS,
    SUMMARY_JSON,
    MvpRunError,
    MvpState,
    family_verdict,
    membership_calendar_map,
    render_mvp_summary,
    researchable_families,
    resolve_mvp_state,
    run_mvp_research,
)


def test_researchable_families_excludes_control() -> None:
    fams = researchable_families()
    assert fams == ["breakout_volume", "trend_pullback", "trend_relative_strength"]
    assert "ma_cross_baseline" not in fams  # registered control, not family-runnable


def test_param_grids_cover_every_researchable_family() -> None:
    assert set(PARAM_GRIDS) == set(researchable_families())
    for grid in PARAM_GRIDS.values():
        assert len(grid) >= 2  # promotion policy requires parameter-stability evidence


def test_membership_calendar_map_is_point_in_time() -> None:
    membership: Membership = {
        "AAA": [(date(2022, 1, 3), date(2022, 6, 30))],  # ends
        "BBB": [(date(2022, 4, 1), None)],  # joins later, open-ended
    }
    idx = pd.DatetimeIndex(["2022-01-04", "2022-04-01", "2022-06-30", "2022-07-01"], tz="UTC")
    day_map = membership_calendar_map(membership, idx)
    assert day_map[idx[0]] == ["AAA"]
    assert day_map[idx[1]] == ["AAA", "BBB"]  # start inclusive
    assert day_map[idx[2]] == ["AAA", "BBB"]  # end inclusive
    assert day_map[idx[3]] == ["BBB"]  # exit applied next day


def test_family_verdict_mapping() -> None:
    approved = {"promotion_summary": {"approved": ["c1"]}, "config_reports": [{"status": "VALIDATED"}]}
    rejected = {"promotion_summary": {"approved": []}, "config_reports": [{"status": "VALIDATED"}]}
    insufficient = {"promotion_summary": {"approved": []}, "config_reports": [{"status": "INSUFFICIENT_DATA"}]}
    assert family_verdict(approved) == "APPROVED"
    assert family_verdict(rejected) == "REJECTED"
    assert family_verdict(insufficient) == "RESEARCH_ONLY"  # unjudgeable != disproven


def _summary(final: str, verdicts: dict[str, str], data_verdict: str = "PASS") -> dict:
    return {
        "run_id": "r1",
        "final_verdict": final,
        "promoted": [f for f, v in verdicts.items() if v == "APPROVED"],
        "data_quality": {"verdict": data_verdict, "version": "1.1.0"},
        "dataset_fingerprint": "f" * 64,
        "families": [{"family": f, "verdict": v} for f, v in verdicts.items()],
        "source_commit": "c",
        "finished_at": "t",
        "started_at": "t",
        "benchmark": "SPY",
        "symbols": [],
        "date_range": {"start": "2022-01-03", "end": "2022-06-30", "n_trading_days": 120},
        "membership": {"file": "m.json", "sha256": "s", "source": "u"},
        "n_warnings": 0,
        "warnings": [],
        "warnings_acknowledged": False,
        "promotion_policy": {"version": "1.0.0", "hash": "h"},
        "next_action": "x",
        "live_status": "NOT_AUTHORIZED",
    }


def _persist(tmp: Path, summary: dict) -> None:
    run = tmp / str(summary["run_id"])
    run.mkdir(parents=True, exist_ok=True)
    (run / SUMMARY_JSON).write_text(json.dumps(summary), encoding="utf-8")


def test_resolve_state_without_configuration(tmp_path: Path) -> None:
    assert resolve_mvp_state(tmp_path, None, None) is MvpState.NOT_CONFIGURED
    missing = tmp_path / "nope"
    assert resolve_mvp_state(tmp_path, missing, missing) is MvpState.REQUIRES_EXTERNAL_DATA
    data = tmp_path / "bars"
    data.mkdir()
    memb = tmp_path / "m.json"
    memb.write_text("{}", encoding="utf-8")
    assert resolve_mvp_state(tmp_path, data, memb) is MvpState.DATA_READY


def test_resolve_state_from_latest_run(tmp_path: Path) -> None:
    _persist(tmp_path, _summary("NO_STRATEGY_PROMOTED", {"a": "REJECTED", "b": "REJECTED"}))
    assert resolve_mvp_state(tmp_path, None, None) is MvpState.NO_STRATEGY_PROMOTED
    _persist(tmp_path, _summary("RESEARCH_ONLY_CANDIDATES", {"a": "RESEARCH_ONLY"}))
    assert resolve_mvp_state(tmp_path, None, None) is MvpState.STRATEGY_RESEARCH_ONLY
    _persist(tmp_path, _summary("STRATEGY_APPROVED_FOR_SHADOW", {"a": "APPROVED"}))
    assert resolve_mvp_state(tmp_path, None, None) is MvpState.STRATEGY_APPROVED_FOR_SHADOW


def test_state_enum_is_closed_and_serializable() -> None:
    values = {s.value for s in MvpState}
    assert "NOT_AUTHORIZED_LIVE" in values and "REQUIRES_EXTERNAL_DATA" in values
    assert MvpState.NO_STRATEGY_PROMOTED.value == "NO_STRATEGY_PROMOTED"


def test_run_rejects_unknown_family_selection(tmp_path: Path) -> None:
    with pytest.raises(MvpRunError) as excinfo:
        run_mvp_research(
            data_dir=tmp_path,
            benchmark="SPY",
            membership_path=tmp_path / "m.json",
            output_root=tmp_path,
            families=["nonexistent_family"],
        )
    assert excinfo.value.exit_code == 1


def test_render_summary_never_omits_rejection_reasons() -> None:
    summary = _summary(
        "NO_STRATEGY_PROMOTED",
        {"a": "REJECTED", "b": "RESEARCH_ONLY"},
    )
    summary["families"][0]["rejection_reasons"] = ["trade_count 4 < 30"]
    summary["families"][0]["trial_count"] = 4
    summary["families"][1]["reason"] = "insufficient OOS evidence"
    md = render_mvp_summary(summary)
    assert "NO_STRATEGY_PROMOTED" in md
    assert "trade_count 4 < 30" in md
    assert "insufficient OOS evidence" in md
    assert "Exact next action" in md
    assert "LIVE_STATUS=NOT_AUTHORIZED" in md
