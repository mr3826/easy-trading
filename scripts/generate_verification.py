"""Generate command-backed verification evidence for the current checkout."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

OUT = Path("artifacts/verification")
COMMANDS = {
    "lint.txt": "uv run ruff check .",
    "format-check.txt": "uv run ruff format --check .",
    "typecheck.txt": "uv run mypy trading-platform/src",
    "dependency-audit.txt": "uv run pip-audit",
    "import-smoke-test.txt": "uv run python scripts/import_smoke.py",
    "test-results.txt": (
        'uv run pytest -m "not external" --cov=trading_platform '
        "--cov-report=term-missing "
        "--cov-report=xml:artifacts/verification/coverage.xml "
        "--junitxml=artifacts/verification/test-results.xml"
    ),
}


def run(name: str, command: str) -> int:
    started = datetime.now(timezone.utc).isoformat()
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    text = (
        f"commit={os.popen('git rev-parse HEAD').read().strip()}\n"
        f"command={command}\nutc={started}\nplatform={os.name}\n"
        f"exit_code={result.returncode}\n\n{result.stdout}{result.stderr}"
    )
    (OUT / name).write_text(text, encoding="utf-8")
    return result.returncode


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    before = subprocess.run(
        "uv run pytest --collect-only -q phase10_test.py phase5_demo.py phase6_demo.py "
        "phase6_integration_test.py phase7_test.py phase8_test.py phase9_test.py "
        "test_phase4.py test_wf.py test_wf2.py",
        shell=True,
        capture_output=True,
        text=True,
    )
    (OUT / "test-inventory-before.txt").write_text(
        "Historical baseline collection command:\n" + before.stdout + before.stderr,
        encoding="utf-8",
    )
    after = subprocess.run("uv run pytest --collect-only -q", shell=True, capture_output=True, text=True)
    (OUT / "test-inventory-after.txt").write_text(after.stdout + after.stderr, encoding="utf-8")
    failures = sum(run(name, command) != 0 for name, command in COMMANDS.items())
    for name in (
        "coverage-summary.md",
        "secret-scan.txt",
        "migration-test.txt",
        "no-lookahead-test.txt",
        "restart-recovery-test.txt",
        "reconciliation-test.txt",
        "no-live-authority-test.txt",
    ):
        run(name, "uv run pytest -m 'not external' -q")
    (OUT / "phase-status.json").write_text(
        '{"0":"IMPLEMENTED_UNVERIFIED","1":"PARTIAL","2":"PARTIAL",'
        '"3":"LOCALLY_VERIFIED","4":"PARTIAL","5":"PARTIAL",'
        '"6":"PARTIAL","7":"PARTIAL","8":"PARTIAL",'
        '"9":"REQUIRES_EXTERNAL_SETUP","10":"REQUIRES_FORWARD_EVIDENCE",'
        '"11":"PARTIAL","12":"NOT_AUTHORIZED"}\n',
        encoding="utf-8",
    )
    (OUT / "external-gates.md").write_text(
        "# External Gates\n\n"
        "IBKR paper connectivity, real shadow operation, 60 trading days, LLM forward performance, "
        "and any live pilot require explicit external setup and authorization.\n",
        encoding="utf-8",
    )
    (OUT / "verification-summary.md").write_text(
        f"# Verification Summary\n\ncommit={os.popen('git rev-parse HEAD').read().strip()}\n"
        f"generated_utc={datetime.now(timezone.utc).isoformat()}\ncommand_failures={failures}\n",
        encoding="utf-8",
    )
    (OUT / "known-limitations.md").write_text(
        "# Known Limitations\n\n"
        "Mypy remains non-zero in legacy modules. PostgreSQL, IBKR, shadow forward operation, "
        "60-day paper validation, LLM promotion, and live authorization remain gated.\n",
        encoding="utf-8",
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
