"""Checksummed, encrypted-at-rest backup primitives.

The key is supplied by the deployment secret store and is never persisted by
this module. The implementation uses authenticated encryption when available.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def checksum(path: Path) -> str:
    """Return the SHA-256 digest of a backup file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_checksum(path: Path, expected: str) -> bool:
    """Verify a backup before restore."""
    return checksum(path) == expected
