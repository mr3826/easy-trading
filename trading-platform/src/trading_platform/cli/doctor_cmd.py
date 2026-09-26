"""``trading-platform doctor`` — one-shot MVP operability diagnostics.

Safe by construction: connects nothing that can trade, probes the database
read-only, never prints secret values, and fails closed. Exit code is
non-zero whenever MVP operation is not currently possible.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from trading_platform.cli._common import EXIT_ERROR, EXIT_EXTERNAL, EXIT_OK, env_path, fmt_level, try_writable
from trading_platform.config import load_config
from trading_platform.promotion import load_approvals
from trading_platform.research.data_quality import MembershipManifestError, load_membership_manifest

MIN_PYTHON = (3, 12)


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    level: str  # PASS | FAIL | BLOCKED | WARN | INFO
    detail: str = ""


@dataclass
class DoctorReport:
    checks: List[DoctorCheck] = field(default_factory=list)
    mvp_status: str = "UNKNOWN"

    @property
    def exit_code(self) -> int:
        levels = {c.level for c in self.checks}
        if "FAIL" in levels:
            return EXIT_ERROR
        if "BLOCKED" in levels:
            return EXIT_EXTERNAL
        return EXIT_OK

    def render(self) -> str:
        return "\n".join(fmt_level(c.level, c.name, c.detail) for c in self.checks) + f"\nMVP_STATUS={self.mvp_status}"


def _migrations_dir() -> Optional[Path]:
    candidate = Path(__file__).resolve().parents[3] / "migrations"
    return candidate if candidate.exists() else None


def _probe_database(dsn: str) -> Tuple[bool, Optional[List[str]], str]:
    """Read-only connectivity + migration-state probe; never writes.

    Returns ``(connected, missing_migrations_or_None, detail)``.
    """

    async def scenario() -> List[str]:
        import asyncpg

        conn = await asyncpg.connect(dsn, timeout=5)
        try:
            exists = await conn.fetchval("SELECT to_regclass('public.schema_migrations')")
            if not exists:
                return []
            rows = await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
            return [str(r["version"]) for r in rows]
        finally:
            await conn.close()

    try:
        applied = asyncio.run(asyncio.wait_for(scenario(), timeout=15))
    except Exception as exc:  # noqa: BLE001 - diagnostics must never raise
        return False, None, f"connection failed: {type(exc).__name__}"
    migrations = _migrations_dir()
    if migrations is None:
        return True, None, "connected; migrations directory not found in this installation"
    expected = sorted(p.stem for p in migrations.glob("*.sql"))
    missing = [e for e in expected if e not in applied]
    detail = f"connected; {len(applied)}/{len(expected)} migrations applied"
    return True, missing, detail


def run_doctor(
    *,
    data_dir: Optional[Path] = None,
    membership: Optional[Path] = None,
    output_root: Optional[Path] = None,
) -> DoctorReport:
    """Execute every MVP diagnostic check and derive the product status."""
    from trading_platform.cli._common import research_output_root
    from trading_platform.research.mvp import resolve_mvp_state

    checks: List[DoctorCheck] = []
    env = os.environ

    # 1. Python version.
    ok = sys.version_info >= MIN_PYTHON
    checks.append(
        DoctorCheck(
            "python",
            "PASS" if ok else "FAIL",
            f"{sys.version.split()[0]} (requires >= {'.'.join(map(str, MIN_PYTHON))})",
        )
    )

    # 2. Installed package + git revision.
    try:
        pkg_version = importlib.metadata.version("trading-platform")
        pkg_detail = f"trading-platform {pkg_version}"
    except importlib.metadata.PackageNotFoundError:
        pkg_detail = "metadata unavailable (running from source tree)"
    commit: str
    try:
        from trading_platform.persistence.experiment import ExperimentRecord

        commit = ExperimentRecord.compute_code_hash()
    except RuntimeError:
        commit = "unknown"
    checks.append(DoctorCheck("package", "PASS", f"{pkg_detail}; revision {commit[:12]}"))

    # 3. Configuration / safety (fail-closed load, never echoes secrets).
    cfg = load_config()
    safety_note = ""
    if env.get("LIVE_TRADING_ENABLED", "").strip().lower() == "true" or env.get("LIVE_STATUS", "").strip() not in (
        "",
        "NOT_AUTHORIZED",
    ):
        safety_note = " (unsafe env values were force-rejected by policy)"
    checks.append(
        DoctorCheck(
            "configuration",
            "PASS",
            f"TRADING_ENV={cfg.environment}, live disabled by policy{safety_note}",
        )
    )

    # 4. Database (optional for MVP research; enforced when configured).
    dsn = env.get("DATABASE_URL", "").strip()
    if not dsn:
        checks.append(DoctorCheck("database", "INFO", "DATABASE_URL not configured (optional for research)"))
        checks.append(DoctorCheck("migrations", "INFO", "skipped (no DATABASE_URL)"))
    else:
        connected, missing, db_detail = _probe_database(dsn)
        checks.append(DoctorCheck("database", "PASS" if connected else "FAIL", db_detail))
        if not connected:
            checks.append(DoctorCheck("migrations", "FAIL", "database unreachable"))
        elif missing is None:
            checks.append(DoctorCheck("migrations", "PASS", db_detail))
        elif missing:
            checks.append(DoctorCheck("migrations", "FAIL", f"not applied: {missing}"))
        else:
            checks.append(DoctorCheck("migrations", "PASS", db_detail))

    # 5. Writable artifact paths.
    from trading_platform.persistence.experiment import experiments_root

    output = output_root or research_output_root()
    for name, path in (
        ("artifact directory", output),
        ("promotions directory", env_path("TRADING_PROMOTIONS_ROOT") or Path("artifacts") / "promotions"),
        ("experiments directory", experiments_root()),
        ("journal path", env_path("TRADING_JOURNAL_PATH") or Path("artifacts") / "journal"),
    ):
        writable, detail = try_writable(Path(path))
        checks.append(DoctorCheck(name, "PASS" if writable else "FAIL", detail))

    # 6. Research data + membership manifest.
    data = data_dir or env_path("TRADING_DATA_DIR")
    manifest = membership or env_path("TRADING_MEMBERSHIP")
    if data is None or manifest is None:
        checks.append(
            DoctorCheck(
                "research data",
                "BLOCKED",
                "TRADING_DATA_DIR / TRADING_MEMBERSHIP not configured "
                "(or pass --data-dir/--membership; see docs/DATA_SOURCING.md)",
            )
        )
    elif not data.exists() or not manifest.exists():
        missing = [str(p) for p in (data, manifest) if not Path(p).exists()]
        checks.append(DoctorCheck("research data", "BLOCKED", f"not found: {', '.join(missing)}"))
    else:
        try:
            members = load_membership_manifest(manifest)
            checks.append(DoctorCheck("research data", "PASS", f"{data}; {len(members)} membership symbols"))
        except (MembershipManifestError, ValueError) as exc:
            checks.append(
                DoctorCheck(
                    "research data",
                    "FAIL",
                    f"invalid membership manifest {manifest}: {exc} — rebuild via "
                    "`trading-platform data build-membership`",
                )
            )

    # 7. Live boundary (permanent).
    checks.append(
        DoctorCheck(
            "live trading",
            "PASS" if (not cfg.live_trading_enabled and cfg.live_status == "NOT_AUTHORIZED") else "FAIL",
            "disabled; LIVE_STATUS=NOT_AUTHORIZED (permanent policy)",
        )
    )

    # 8. IBKR paper configuration (never connects, never submits).
    paper_host = env.get("IBKR_PAPER_HOST", "").strip()
    if paper_host:
        checks.append(
            DoctorCheck(
                "ibkr paper",
                "INFO",
                f"host/port/client-id env present ({paper_host}); not probed by doctor",
            )
        )
    else:
        checks.append(DoctorCheck("ibkr paper", "INFO", "not configured"))

    # 9. Promotion artifact state.
    approvals = load_approvals()
    checks.append(
        DoctorCheck(
            "promotion artifacts",
            "INFO",
            f"current-policy approvals: {sorted(approvals) or 'NONE (no strategy promoted)'}",
        )
    )

    # 10. Clock sanity.
    now = datetime.now(timezone.utc)
    checks.append(
        DoctorCheck(
            "clock",
            "PASS" if 2020 <= now.year <= 2100 else "WARN",
            now.isoformat(timespec="seconds"),
        )
    )

    report = DoctorReport(checks=checks)
    report.mvp_status = resolve_mvp_state(output, data, manifest).value
    return report


def cmd_doctor(args: object) -> int:
    """argparse entry for ``trading-platform doctor``."""
    report = run_doctor()
    print(report.render())
    return report.exit_code
