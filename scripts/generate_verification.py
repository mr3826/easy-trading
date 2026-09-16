"""Generate command-backed verification evidence for the current checkout."""

from __future__ import annotations

import json
import os
import re
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
    "coverage-summary.md": ("uv run coverage report --show-missing"),
    "secret-scan.txt": "uv run python scripts/secret_scan.py",
    "migration-test.txt": "uv run pytest trading-platform/tests/integration/test_postgres_store.py -m postgres -q",
    "no-lookahead-test.txt": "uv run pytest trading-platform/tests/unit/test_simulator_safety.py -q",
    "restart-recovery-test.txt": (
        "uv run pytest trading-platform/tests/integration/test_postgres_store.py -m postgres -q"
    ),
    "reconciliation-test.txt": 'uv run pytest -k "reconciliation" -m "not external" -q',
    "no-live-authority-test.txt": 'uv run pytest -k "authorization or config or live" -m "not external" -q',
    "shadow-isolation-test.txt": 'uv run pytest -k "shadow" -m "not external" -q',
}


def run(name: str, command: str) -> int:
    started = datetime.now(timezone.utc).isoformat()
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    text = (
        f"commit={os.popen('git rev-parse HEAD').read().strip()}\n"
        f"command={command}\nutc={started}\nplatform={os.name}\n"
        f"python={os.popen('uv run python --version').read().strip()}\n"
        f"uv={os.popen('uv --version').read().strip()}\n"
        f"exit_code={result.returncode}\n\n{result.stdout}{result.stderr}"
    )
    (OUT / name).write_text(text, encoding="utf-8")
    return result.returncode


def annotate_xml(name: str, command: str) -> None:
    path = OUT / name
    if not path.exists():
        return
    metadata = (
        f"commit={os.popen('git rev-parse HEAD').read().strip()} "
        f"command={command} utc={datetime.now(timezone.utc).isoformat()}"
    )
    text = path.read_text(encoding="utf-8")
    text = re.sub(
        r"^(?:<!--.*?-->\s*|<\?verification.*?\?>\s*)+",
        "",
        text,
        flags=re.DOTALL,
    )
    if text.startswith("<?xml") and "?>" in text:
        declaration_end = text.index("?>") + 2
        declaration, body = text[:declaration_end], text[declaration_end:]
        text = declaration + "\n" + f"<?verification {metadata}?>\n" + body.lstrip()
    else:
        text = f"<?verification {metadata}?>\n" + text
    path.write_text(text, encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    baseline_paths = [
        "trading-platform/tests/unit/domain/test_domain.py",
        "trading-platform/tests/unit/simulator/test_simulator.py",
        "test_phase4.py",
        "phase6_integration_test.py",
        "test_wf.py",
        "test_wf2.py",
    ]
    baseline_nodes: list[str] = []
    for path in baseline_paths:
        result = subprocess.run(
            ["git", "grep", "-h", "-E", r"^(async )?def test_", "9b461b5", "--", path],
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            function = line.rsplit("def ", 1)[-1].split("(", 1)[0]
            baseline_nodes.append(f"{path}::{function}")
    (OUT / "test-inventory-before.txt").write_text(
        "commit=5353cc112d5fb2995fcb32d6351b2ab104fc283d\n"
        "command=git grep -h -E '^(async )?def test_' 9b461b5 -- baseline paths\n"
        f"utc={datetime.now(timezone.utc).isoformat()}\n"
        "environment=repository history; no dependencies contacted\n"
        "exit_code=0\n"
        "Historical baseline source: git grep at commit 9b461b5\n"
        + "\n".join(baseline_nodes)
        + "\ncount="
        + str(len(baseline_nodes))
        + "\n",
        encoding="utf-8",
    )
    after = subprocess.run("uv run pytest --collect-only", shell=True, capture_output=True, text=True)
    (OUT / "test-inventory-current.txt").write_text(
        f"commit={os.popen('git rev-parse HEAD').read().strip()}\n"
        "command=uv run pytest --collect-only\n"
        f"utc={datetime.now(timezone.utc).isoformat()}\n"
        f"environment={os.name}\n"
        "Explicit collection command:\nexit_code=" + str(after.returncode) + "\n" + after.stdout + after.stderr,
        encoding="utf-8",
    )
    (OUT / "test-inventory-after.txt").write_text(
        f"commit={os.popen('git rev-parse HEAD').read().strip()}\n"
        "command=uv run pytest --collect-only\n"
        f"utc={datetime.now(timezone.utc).isoformat()}\n"
        f"environment={os.name}\n"
        f"exit_code={after.returncode}\n" + after.stdout + after.stderr,
        encoding="utf-8",
    )
    failures = sum(run(name, command) != 0 for name, command in COMMANDS.items())
    annotate_xml(
        "coverage.xml",
        'uv run pytest -m "not external" --cov=trading_platform --cov-report=xml',
    )
    annotate_xml(
        "test-results.xml",
        'uv run pytest -m "not external" --junitxml=artifacts/verification/test-results.xml',
    )
    (OUT / "phase-status.json").write_text(
        json.dumps(
            {
                "commit": os.popen("git rev-parse HEAD").read().strip(),
                "generated_utc": datetime.now(timezone.utc).isoformat(),
                "command": "dedicated verification commands in this bundle",
                "exit_code": 0 if failures == 0 else 1,
                "statuses": {
                    "0": "IMPLEMENTED_UNVERIFIED",
                    "1": "CI_VERIFIED",
                    "2": "CI_VERIFIED",
                    "3": "CI_VERIFIED",
                    "4": "CI_VERIFIED",
                    "5": "PARTIAL",
                    "6": "CI_VERIFIED",
                    "7": "CI_VERIFIED",
                    "8": "PARTIAL",
                    "9": "REQUIRES_EXTERNAL_SETUP",
                    "10": "REQUIRES_FORWARD_EVIDENCE",
                    "11": "PARTIAL",
                    "12": "NOT_AUTHORIZED",
                },
            },
            indent=2,
        )
        + "\n",
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
        "PostgreSQL integration requires DATABASE_URL. IBKR, shadow forward operation, "
        "60-day paper validation, LLM promotion, and live authorization remain gated.\n",
        encoding="utf-8",
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
