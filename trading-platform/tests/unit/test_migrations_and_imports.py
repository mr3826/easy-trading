from pathlib import Path

from trading_platform.backup import checksum, verify_checksum
from trading_platform.observability import check_dependency


def test_core_migration_has_append_only_and_idempotency_constraints() -> None:
    sql = Path("trading-platform/migrations/0001_core.sql").read_text()
    assert "UNIQUE" in sql
    assert "prevent_journal_update" in sql
    assert "TIMESTAMPTZ" in sql


def test_backup_checksum_detects_tampering(tmp_path: Path) -> None:
    backup = tmp_path / "backup.bin"
    backup.write_bytes(b"journal")
    digest = checksum(backup)
    assert verify_checksum(backup, digest)
    backup.write_bytes(b"tampered")
    assert not verify_checksum(backup, digest)


def test_dependency_probe_fails_closed_on_exception() -> None:
    health = check_dependency("database", lambda: 1 / 0)
    assert not health.healthy
