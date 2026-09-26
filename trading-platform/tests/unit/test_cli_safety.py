"""CLI safety + backward-compatibility contract (Security Reviewer discipline).

The productized CLI must: keep the legacy ``--status`` output, expose NO path
to enable live trading, never leak secrets through diagnostics, and fail closed
on unsafe/invalid configuration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from trading_platform.cli import main
from trading_platform.cli._common import EXIT_OK
from trading_platform.cli.doctor_cmd import run_doctor


def test_legacy_status_output_is_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--status"]) == 0
    out = capsys.readouterr().out
    assert out == "LIVE_TRADING_ENABLED=false\nLIVE_STATUS=NOT_AUTHORIZED\n"


def test_env_cannot_enable_live_via_cli(monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_STATUS", "AUTHORIZED")
    assert main(["status"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "LIVE_TRADING_ENABLED=false" in out
    assert "LIVE_STATUS=NOT_AUTHORIZED" in out


def test_no_flag_or_command_can_authorize_live(monkeypatch) -> None:
    """No subcommand even accepts a live flag, and env overrides are rejected."""
    from trading_platform.cli import build_parser
    from trading_platform.config import load_config

    parser = build_parser()
    for argv in (["live"], ["--enable-live"], ["run-live"], ["status", "--enable-live"]):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_STATUS", "AUTHORIZED")
    cfg = load_config()
    assert cfg.live_trading_enabled is False and cfg.live_status == "NOT_AUTHORIZED"


def test_doctor_output_never_leaks_secret_like_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TRADING_DATA_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("TRADING_MEMBERSHIP", str(tmp_path / "missing.json"))
    monkeypatch.setenv("DB_PASSWORD", "hunter2-super-secret")
    monkeypatch.setenv("IBKR_PAPER_PASSWORD", "do-not-print")
    report = run_doctor()
    rendered = report.render()
    assert "hunter2-super-secret" not in rendered
    assert "do-not-print" not in rendered
    assert "IBKR_PAPER" not in rendered or "not configured" in rendered


def test_doctor_reports_blocked_without_data_exit_nonzero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TRADING_DATA_DIR", raising=False)
    monkeypatch.delenv("TRADING_MEMBERSHIP", raising=False)
    monkeypatch.setenv("TRADING_RESEARCH_OUTPUT", str(tmp_path / "out"))
    report = run_doctor()
    assert report.exit_code == 3  # REQUIRES_EXTERNAL_DATA / NOT_CONFIGURED blocks operation
    levels = {c.name: c.level for c in report.checks}
    assert levels["live trading"] == "PASS"
    assert levels["research data"] == "BLOCKED"
    assert report.mvp_status in {"NOT_CONFIGURED", "REQUIRES_EXTERNAL_DATA"}


def test_doctor_fails_on_invalid_membership_manifest(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "bars"
    data.mkdir()
    bad = tmp_path / "m.json"
    bad.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("TRADING_DATA_DIR", str(data))
    monkeypatch.setenv("TRADING_MEMBERSHIP", str(bad))
    monkeypatch.setenv("TRADING_RESEARCH_OUTPUT", str(tmp_path / "out"))
    report = run_doctor()
    assert report.exit_code == 1  # hard FAIL, not merely blocked
    assert any(c.level == "FAIL" and c.name == "research data" for c in report.checks)


def test_cli_imports_no_execution_or_broker_modules() -> None:
    """Security boundary: importing the CLI must not drag in broker/OMS/risk.

    (The research path is read-only and cannot reach submission; this guards
    against productization quietly wiring execution into the operator CLI.)
    """
    import subprocess
    import sys

    probe = (
        "import json, sys;"
        "import trading_platform.cli;"
        "bad=[m for m in sys.modules if m.startswith('trading_platform') and"
        " any(k in m for k in ('broker','oms','risk','orchestration','shadow','simulator','execution'))];"
        "print(json.dumps(sorted(bad)))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    offenders = json.loads(out.stdout.strip())
    assert offenders == [], f"execution-capable modules imported by CLI: {offenders}"
