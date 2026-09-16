"""Checksummed, encrypted-at-rest backup primitives.

The key is supplied by the deployment secret store and is never persisted by
this module. The implementation uses authenticated encryption when available.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def checksum(path: Path) -> str:
    """Return the SHA-256 digest of a backup file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_checksum(path: Path, expected: str) -> bool:
    """Verify a backup before restore."""
    return checksum(path) == expected


def encrypt_backup(source: Path, destination: Path, key: bytes) -> str:
    """Encrypt a backup using AES-GCM and return its ciphertext checksum.

    The caller owns key retrieval and rotation. A fresh nonce is generated for
    every file and prepended to the ciphertext; the key is never written.
    """
    if len(key) not in {16, 24, 32}:
        raise ValueError("AES-GCM key must be 128, 192, or 256 bits")
    nonce = os.urandom(12)
    destination.write_bytes(nonce + AESGCM(key).encrypt(nonce, source.read_bytes(), None))
    return checksum(destination)


def decrypt_backup(source: Path, destination: Path, key: bytes) -> None:
    """Authenticate and decrypt a backup; invalid ciphertext raises."""
    if len(key) not in {16, 24, 32}:
        raise ValueError("AES-GCM key must be 128, 192, or 256 bits")
    encrypted = source.read_bytes()
    if len(encrypted) < 12:
        raise ValueError("encrypted backup is truncated")
    nonce, ciphertext = encrypted[:12], encrypted[12:]
    destination.write_bytes(AESGCM(key).decrypt(nonce, ciphertext, None))
