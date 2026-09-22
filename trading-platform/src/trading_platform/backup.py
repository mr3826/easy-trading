"""Checksummed, encrypted-at-rest backup primitives.

The key is supplied by the deployment secret store and is never persisted by
this module. The implementation uses authenticated encryption when available.
Archive-level backups encrypt a whole directory as a deterministic tar with
per-file SHA-256 checksums in an embedded manifest; restore verifies every
checksum before any file is written.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MANIFEST_NAME = "_manifest.json"
MANIFEST_VERSION = 1


def checksum(path: Path) -> str:
    """Return the SHA-256 digest of a backup file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_checksum(path: Path, expected: str) -> bool:
    """Verify a backup before restore."""
    return checksum(path) == expected


def _encrypt_payload(payload: bytes, key: bytes) -> bytes:
    if len(key) not in {16, 24, 32}:
        raise ValueError("AES-GCM key must be 128, 192, or 256 bits")
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, payload, None)


def encrypt_backup(source: Path, destination: Path, key: bytes) -> str:
    """Encrypt a backup using AES-GCM and return its ciphertext checksum.

    The caller owns key retrieval and rotation. A fresh nonce is generated for
    every file and prepended to the ciphertext; the key is never written.
    """
    destination.write_bytes(_encrypt_payload(source.read_bytes(), key))
    return checksum(destination)


def decrypt_backup(source: Path, destination: Path, key: bytes) -> None:
    """Authenticate and decrypt a backup; invalid ciphertext raises."""
    encrypted = source.read_bytes()
    if len(encrypted) < 12:
        raise ValueError("encrypted backup is truncated")
    nonce, ciphertext = encrypted[:12], encrypted[12:]
    destination.write_bytes(AESGCM(key).decrypt(nonce, ciphertext, None))


@dataclass(frozen=True)
class ArchiveFileEntry:
    name: str
    size: int
    sha256: str


def _tar_add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mtime = 0
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))


def _directory_payload(source_dir: Path) -> bytes:
    """Deterministic tar of a directory's files with an embedded manifest.

    Files are added in sorted name order with fixed metadata, so the payload
    bytes are deterministic for fixed file contents.
    """
    files = sorted(path for path in source_dir.iterdir() if path.is_file())
    entries: list[dict[str, Any]] = []
    blobs: list[tuple[str, bytes]] = []
    for path in files:
        data = path.read_bytes()
        entries.append({"name": path.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        blobs.append((path.name, data))
    manifest_bytes = json.dumps(
        {"version": MANIFEST_VERSION, "files": entries}, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in [(MANIFEST_NAME, manifest_bytes), *blobs]:
            _tar_add(tar, name, data)
    return buffer.getvalue()


def encrypt_directory(source_dir: Path, destination: Path, key: bytes) -> str:
    """Encrypt a directory of files and return the ciphertext checksum.

    The manifest with per-file SHA-256 checksums travels inside the encrypted
    payload, so restore can verify every rebuilt file without a separate
    manifest artifact.
    """
    if len(key) not in {16, 24, 32}:
        raise ValueError("AES-GCM key must be 128, 192, or 256 bits")
    destination.write_bytes(_encrypt_payload(_directory_payload(source_dir), key))
    return checksum(destination)


def _read_tar_contents(payload: bytes) -> dict[str, bytes]:
    contents: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            handle = tar.extractfile(member)
            if handle is None:
                raise ValueError(f"archive member {member.name} is unreadable")
            contents[member.name] = handle.read()
    return contents


def restore_backup(
    archive: Path,
    dest: Path,
    key: bytes,
    *,
    expected_checksum: str | None = None,
) -> list[Path]:
    """Clean-room restore: verify, decrypt, rebuild files, verify checksums.

    Any archive checksum mismatch (when ``expected_checksum`` is supplied),
    invalid ciphertext, missing manifest, unsafe member name, or per-file
    checksum failure raises before a file is written.
    """
    if len(key) not in {16, 24, 32}:
        raise ValueError("AES-GCM key must be 128, 192, or 256 bits")
    if expected_checksum is not None and not verify_checksum(archive, expected_checksum):
        raise ValueError("archive checksum mismatch: refusing to restore")
    encrypted = archive.read_bytes()
    if len(encrypted) < 12:
        raise ValueError("encrypted archive is truncated")
    nonce, ciphertext = encrypted[:12], encrypted[12:]
    payload = AESGCM(key).decrypt(nonce, ciphertext, None)
    contents = _read_tar_contents(payload)
    if MANIFEST_NAME not in contents:
        raise ValueError("archive manifest is missing")
    manifest = json.loads(contents.pop(MANIFEST_NAME).decode("utf-8"))
    if manifest.get("version") != MANIFEST_VERSION:
        raise ValueError("unsupported archive manifest version")
    expected_files = {str(entry["name"]): entry for entry in manifest["files"]}
    for name in expected_files:
        if name in {".", ".."} or name != Path(name).name:
            raise ValueError(f"unsafe archive member name: {name}")
    if set(expected_files) != set(contents):
        raise ValueError("archive contents do not match manifest")
    if dest.exists():
        if dest.is_symlink() or getattr(dest.stat(), "st_reparse_tag", False):
            raise ValueError("restore destination is a symlink or reparse point")
        if not dest.is_dir() or any(dest.iterdir()):
            raise ValueError("restore destination must be a new or empty directory")
    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{dest.name}.restore-", dir=parent))
    try:
        rebuilt: list[Path] = []
        for name, entry in sorted(expected_files.items()):
            data = contents[name]
            if len(data) != int(str(entry["size"])) or hashlib.sha256(data).hexdigest() != str(entry["sha256"]):
                raise ValueError(f"archive member {name} failed checksum verification")
            target = staging / name
            if target.exists() or target.is_symlink() or getattr(target.parent.stat(), "st_reparse_tag", False):
                raise ValueError(f"restore target is unsafe: {name}")
            target.write_bytes(data)
            rebuilt.append(dest / name)
        if dest.exists():
            dest.rmdir()
        os.replace(staging, dest)
        return rebuilt
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
