"""Shared CLI helpers and stable exit codes.

Exit codes (documented in docs/MVP1.md):

* ``0`` success (including a successful run whose verdict is NO STRATEGY PROMOTED)
* ``1`` unexpected/operational error
* ``2`` data-quality gate failed (FAIL) or invalid inputs
* ``3`` REQUIRES_EXTERNAL_SETUP / REQUIRES_EXTERNAL_DATA
* ``4`` PASS_WITH_WARNINGS without ``--accept-data-warnings``
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_DATA_FAILED = 2
EXIT_EXTERNAL = 3
EXIT_WARNINGS_UNACKNOWLEDGED = 4

# ``data preflight`` preserves the legacy script's numeric contract:
# 0=PASS, 1=PASS_WITH_WARNINGS, 2=FAIL, 3=REQUIRES_EXTERNAL_SETUP.
EXIT_PASS_WARNINGS = 1


def env_path(name: str) -> Optional[Path]:
    """Environment-configured path, or None when unset/blank."""
    raw = os.environ.get(name, "").strip()
    return Path(raw) if raw else None


def research_output_root() -> Path:
    return env_path("TRADING_RESEARCH_OUTPUT") or Path("artifacts") / "research"


def fmt_level(level: str, name: str, detail: str) -> str:
    return f"[{level}] {name}" + (f": {detail}" if detail else "")


def try_writable(path: Path) -> tuple[bool, str]:
    """Return (writable, detail). Creates parent directories only for the probe."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".trading-platform-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True, str(path)
    except OSError as exc:
        return False, f"{path}: {type(exc).__name__}: {exc}"
