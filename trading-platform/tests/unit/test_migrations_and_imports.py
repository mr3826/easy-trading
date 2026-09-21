from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag
from trading_platform.backup import checksum, decrypt_backup, encrypt_backup, verify_checksum
from trading_platform.observability import check_dependency


def test_core_migration_has_append_only_and_idempotency_constraints() -> None:
    migration_path = Path(__file__).resolve().parents[2] / "migrations" / "0001_core.sql"
    sql = migration_path.read_text()
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


def test_backup_encryption_authenticates_restore(tmp_path: Path) -> None:
    plain = tmp_path / "journal.json"
    encrypted = tmp_path / "journal.bin"
    restored = tmp_path / "restored.json"
    plain.write_bytes(b"journal payload")
    key = b"0123456789abcdef0123456789abcdef"
    encrypted_digest = encrypt_backup(plain, encrypted, key)
    assert verify_checksum(encrypted, encrypted_digest)
    decrypt_backup(encrypted, restored, key)
    assert restored.read_bytes() == plain.read_bytes()
    encrypted.write_bytes(encrypted.read_bytes()[:-1] + b"x")
    with pytest.raises(InvalidTag):
        decrypt_backup(encrypted, restored, key)


def test_dependency_probe_fails_closed_on_exception() -> None:
    health = check_dependency("database", lambda: 1 / 0)
    assert not health.healthy
