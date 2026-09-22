"""File-based shadow archive: raw inputs, decisions, problems, discrepancies.

Records are appended as JSONL with a per-record SHA-256 checksum so any
tampering is detectable and replay stays deterministic. The archive itself
satisfies the ``ShadowDecisionSink`` protocol (contract C3) and can forward
decisions to a downstream database sink; when no database sink is configured
the file-based archive remains fully functional.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from typing import Any, Awaitable, Mapping, Protocol

DECISIONS_FILE = "decisions.jsonl"
INPUTS_FILE = "inputs.jsonl"
PROBLEMS_FILE = "problems.jsonl"
DISCREPANCIES_FILE = "discrepancies.jsonl"

_FILES = (DECISIONS_FILE, INPUTS_FILE, PROBLEMS_FILE, DISCREPANCIES_FILE)


class ShadowDecisionSink(Protocol):
    """Durable decision-sink contract (C3): C provides the DB writer."""

    def record_shadow_decision(self, decision: Mapping[str, Any]) -> None | Awaitable[None]: ...


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """Deterministic canonical JSON encoding used for checksums.

    NaN and infinity are encoded as their default JSON literals so raw
    received inputs (including invalid bars) archive deterministically.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=True).encode("utf-8")


def payload_checksum(payload: Mapping[str, Any]) -> str:
    """Return the SHA-256 digest of a record payload."""
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _line_checksum(line: Mapping[str, Any]) -> str:
    record = line.get("record")
    if not isinstance(record, dict):
        return ""
    return payload_checksum(record)


class ShadowArchive:
    """Checksummed JSONL archive that also acts as a ShadowDecisionSink."""

    def __init__(self, directory: Path, db_sink: ShadowDecisionSink | None = None) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db_sink = db_sink
        self._sequences: dict[str, int] = {name: self._existing_count(name) for name in _FILES}

    def _existing_count(self, filename: str) -> int:
        path = self.directory / filename
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())

    def _append(self, filename: str, record: Mapping[str, Any]) -> None:
        seq = self._sequences[filename]
        self._sequences[filename] = seq + 1
        line = {"seq": seq, "checksum": payload_checksum(record), "record": dict(record)}
        path = self.directory / filename
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, sort_keys=True, separators=(",", ":"), allow_nan=True))
            handle.write("\n")

    def record_input(self, record: Mapping[str, Any]) -> None:
        """Archive a raw received input (bar record with checksum)."""
        self._append(INPUTS_FILE, record)

    def record_shadow_decision(self, decision: Mapping[str, Any]) -> None:
        """Persist a decision to the file archive and forward to the DB sink."""
        self._append(DECISIONS_FILE, decision)
        if self.db_sink is not None:
            result = self.db_sink.record_shadow_decision(decision)
            if inspect.isawaitable(result):
                result.close() if hasattr(result, "close") else None
                raise RuntimeError("async shadow sink requires ShadowArchive.record_shadow_decision_async")

    async def record_shadow_decision_async(self, decision: Mapping[str, Any]) -> None:
        """Persist and await an asynchronous database decision sink."""
        self._append(DECISIONS_FILE, decision)
        if self.db_sink is not None:
            result = self.db_sink.record_shadow_decision(decision)
            if inspect.isawaitable(result):
                await result

    def record_problem(self, record: Mapping[str, Any]) -> None:
        """Persist a data-quality problem (stale, missing, revised, duplicate)."""
        self._append(PROBLEMS_FILE, record)

    def record_discrepancy(self, record: Mapping[str, Any]) -> None:
        """Persist a replay discrepancy for escalation and audit."""
        self._append(DISCREPANCIES_FILE, record)

    def _read(self, filename: str) -> list[dict[str, Any]]:
        path = self.directory / filename
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                parsed = json.loads(line)
                records.append(parsed["record"])
        return records

    def read_inputs(self) -> list[dict[str, Any]]:
        """Return archived raw inputs in sequence order."""
        return self._read(INPUTS_FILE)

    def read_decisions(self) -> list[dict[str, Any]]:
        """Return archived decisions in sequence order."""
        return self._read(DECISIONS_FILE)

    def read_problems(self) -> list[dict[str, Any]]:
        """Return archived data-quality problems in sequence order."""
        return self._read(PROBLEMS_FILE)

    def read_discrepancies(self) -> list[dict[str, Any]]:
        """Return archived replay discrepancies in sequence order."""
        return self._read(DISCREPANCIES_FILE)

    def verify(self) -> bool:
        """Verify every archived line's checksum; false on any mismatch."""
        for filename in _FILES:
            path = self.directory / filename
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        return False
                    if parsed.get("checksum") != _line_checksum(parsed):
                        return False
        return True
