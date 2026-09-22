"""Experiment persistence for Phase 4 research harness.

V1 persists every experiment including:
- hypothesis and parameters
- dataset hash and universe version
- code commit, dependency lock hash and policy hash
- cost/slippage assumptions
- seed and result metrics

All experiments are stored as JSON files in a per-experiment directory
under the experiments root. This enables deterministic replay and
comparison across runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from trading_platform.strategies.ma_cross_strategy import (
    MaCrossHypothesis,
    dict_to_hypothesis,
    hypothesis_to_dict,
)

# ---------------------------------------------------------------------------
# Paths

EXPERIMENTS_ROOT = Path(
    os.environ.get("TRADING_EXPERIMENTS_ROOT", str(Path(tempfile.gettempdir()) / "trading_experiments"))
)


def experiments_root() -> Path:
    """Return the current experiments root, honoring the env override."""
    return Path(os.environ.get("TRADING_EXPERIMENTS_ROOT", str(Path(tempfile.gettempdir()) / "trading_experiments")))


def _ensure_root(root: Path) -> None:
    """Ensure the experiments root directory exists."""
    root.mkdir(parents=True, exist_ok=True)


class MalformedExperimentRecord(ValueError):
    """Raised when an experiment record cannot be parsed or is invalid."""


# ---------------------------------------------------------------------------
# Provenance hash validation


def validate_provenance_hash(value: str, name: str) -> str:
    """Validate a provenance hash string.

    A provenance value must be non-empty. Pure-hex values must be 40
    (SHA-1, full git SHA) or 64 (SHA-256) characters; shorter hex strings
    are rejected as truncated hashes. Placeholder sentinels are rejected.
    """
    if not isinstance(value, str):
        raise MalformedExperimentRecord(f"{name} must be a string, got {type(value).__name__}")
    cleaned = value.strip()
    if not cleaned:
        raise MalformedExperimentRecord(f"{name} must be a non-empty provenance string")
    lowered = cleaned.lower()
    if lowered == "placeholder_code_commit_hash" or "placeholder" in lowered:
        raise MalformedExperimentRecord(f"{name} contains a placeholder sentinel, not a real hash")
    if all(c in "0123456789abcdef" for c in lowered) and len(lowered) not in (40, 64):
        raise MalformedExperimentRecord(
            f"{name} looks like a truncated hash (length {len(lowered)}); expected 40 (SHA-1) or 64 (SHA-256)"
        )
    return cleaned


# ---------------------------------------------------------------------------
# Experiment record


class ExperimentRecord:
    """Experiment record persisted to disk.

    Every experiment gets a unique ID and its full state is serialized
    to JSON for deterministic replay and comparison.
    """

    def __init__(
        self,
        experiment_id: str,
        hypothesis: MaCrossHypothesis,
        dataset_hash: str,
        universe_version: str,
        code_commit: str,
        dependency_lock: str,
        cost_slippage_assumptions: Dict[str, Any],
        seed: int,
        policy_hash: str = "",
        result_metrics: Optional[Dict[str, float]] = None,
        notes: str = "",
    ):
        self.experiment_id = validate_provenance_hash(experiment_id, "experiment_id") or experiment_id
        self.hypothesis = hypothesis
        self.dataset_hash = validate_provenance_hash(dataset_hash, "dataset_hash")
        self.universe_version = universe_version
        self.code_commit = validate_provenance_hash(code_commit, "code_commit")
        self.dependency_lock = validate_provenance_hash(dependency_lock, "dependency_lock")
        self.policy_hash = validate_provenance_hash(policy_hash, "policy_hash") if policy_hash else ""
        self.cost_slippage_assumptions = cost_slippage_assumptions
        self.seed = seed
        self.result_metrics = result_metrics or {}
        self.notes = notes
        self.created_at = datetime.now(timezone.utc)
        self._path: Optional[Path] = None

    # -----------------------------------------------------------------
    # Persistence

    def save(self, root: Optional[Path] = None) -> Path:
        """Write the experiment record to disk.

        Returns the Path where it was saved.
        """
        root = root or experiments_root()
        _ensure_root(root)
        exp_dir = root / self.experiment_id
        if exp_dir.exists():
            # Avoid overwriting — append version
            version = 1
            while (root / f"{self.experiment_id}_v{version}").exists():
                version += 1
            exp_dir = root / f"{self.experiment_id}_v{version}"

        exp_dir.mkdir(parents=True, exist_ok=True)
        record_path = exp_dir / "experiment.json"
        data = self._to_dict()
        with open(record_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        self._path = record_path
        return record_path

    # -----------------------------------------------------------------
    # Deserialization

    @staticmethod
    def load(experiment_id: str, root: Optional[Path] = None) -> Optional["ExperimentRecord"]:
        """Load an experiment record by ID.

        Returns None if not found.
        """
        root = root or experiments_root()
        _ensure_root(root)
        # Try exact match first
        record_path = root / experiment_id / "experiment.json"
        if record_path.exists():
            return ExperimentRecord._from_dict(json.loads(record_path.read_text()))
        # Try versioned match
        if "_" in experiment_id:
            base_id = experiment_id.rsplit("_v", 1)[0]
            for entry in root.iterdir():
                if entry.is_dir() and entry.name.startswith(base_id):
                    p = entry / "experiment.json"
                    if p.exists():
                        return ExperimentRecord._from_dict(json.loads(p.read_text()))
        return None

    # -----------------------------------------------------------------
    # Serialization helpers

    def _to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "hypothesis": hypothesis_to_dict(self.hypothesis),
            "dataset_hash": self.dataset_hash,
            "universe_version": self.universe_version,
            "code_commit": self.code_commit,
            "dependency_lock": self.dependency_lock,
            "policy_hash": self.policy_hash,
            "cost_slippage_assumptions": self.cost_slippage_assumptions,
            "seed": self.seed,
            "result_metrics": self.result_metrics,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() + "Z",
        }

    @staticmethod
    def _from_dict(data: Dict[str, Any]) -> "ExperimentRecord":
        if not isinstance(data, dict):
            raise MalformedExperimentRecord("experiment record must be a JSON object")
        required = (
            "experiment_id",
            "hypothesis",
            "dataset_hash",
            "universe_version",
            "code_commit",
            "dependency_lock",
            "cost_slippage_assumptions",
            "seed",
        )
        missing = [key for key in required if key not in data]
        if missing:
            raise MalformedExperimentRecord(f"experiment record missing required fields: {missing}")
        if not isinstance(data["seed"], int) or isinstance(data["seed"], bool):
            raise MalformedExperimentRecord("experiment record seed must be an integer")
        er = ExperimentRecord(
            experiment_id=data["experiment_id"],
            hypothesis=dict_to_hypothesis(data["hypothesis"]),
            dataset_hash=data["dataset_hash"],
            universe_version=data["universe_version"],
            code_commit=data["code_commit"],
            dependency_lock=data["dependency_lock"],
            cost_slippage_assumptions=data["cost_slippage_assumptions"],
            seed=data["seed"],
            policy_hash=data.get("policy_hash", ""),
            result_metrics=data.get("result_metrics"),
            notes=data.get("notes", ""),
        )
        er._path = None
        return er

    # -------------------------------------------------------------------------
    # Experiment ID generation

    @staticmethod
    def new_experiment_id() -> str:
        """Generate a new unique experiment ID."""
        return str(uuid.uuid4())

    # -------------------------------------------------------------------------
    # Hashing helpers

    @staticmethod
    def compute_dataset_hash(bars_data: str) -> str:
        """Compute SHA-256 hash of dataset content for reproducibility."""
        return hashlib.sha256(bars_data.encode()).hexdigest()

    @staticmethod
    def compute_code_hash() -> str:
        """Compute the code provenance: the full git HEAD SHA.

        Prefers ``git rev-parse HEAD`` in the current working tree. When git
        is unavailable, falls back to the ``TRADING_PLATFORM_CODE_SHA`` env
        var (validated non-empty). Fails closed with RuntimeError when no
        provenance can be established; placeholders are never returned.
        """
        env_sha = os.environ.get("TRADING_PLATFORM_CODE_SHA", "").strip()
        if env_sha:
            return validate_provenance_hash(env_sha, "TRADING_PLATFORM_CODE_SHA")
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                "code provenance unavailable: git rev-parse failed and TRADING_PLATFORM_CODE_SHA is not set"
            ) from exc
        return validate_provenance_hash(completed.stdout.strip(), "git HEAD")

    @staticmethod
    def compute_dependency_lock_hash(lock_path: Path) -> str:
        """Compute SHA-256 of the dependency lock file bytes."""
        digest = hashlib.sha256()
        with open(lock_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def compute_policy_hash(policy_mapping: Mapping[str, Any]) -> str:
        """Compute SHA-256 of a canonical JSON serialization of the policy."""
        canonical = json.dumps(policy_mapping, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Experiment registry (simple in-memory + disk)


class ExperimentRegistry:
    """Registry for tracking experiments.

    Lightweight JSON-based registry. Existing records are loaded from disk;
    malformed records are REJECTED with MalformedExperimentRecord instead of
    silently skipped. Use a fresh root (e.g. tmp_path in tests) to avoid
    failing on stale malformed files in a shared directory.
    """

    def __init__(self, root: Optional[Path] = None):
        self.root = root or experiments_root()
        _ensure_root(self.root)
        self._records: Dict[str, ExperimentRecord] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        """Load existing experiment records from disk; malformed records fail loudly."""
        _ensure_root(self.root)
        if not self.root.exists():
            return
        malformed: list[str] = []
        for entry in self.root.iterdir():
            if entry.is_dir():
                json_path = entry / "experiment.json"
                if json_path.exists():
                    try:
                        record = ExperimentRecord._from_dict(json.loads(json_path.read_text()))
                        self._records[record.experiment_id] = record
                    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                        malformed.append(f"{json_path}: {exc}")
        if malformed:
            raise MalformedExperimentRecord("malformed experiment records found: " + "; ".join(malformed))

    def register(self, record: ExperimentRecord) -> None:
        """Register a new experiment record."""
        self._records[record.experiment_id] = record
        record.save(self.root)

    def get(self, experiment_id: str) -> Optional[ExperimentRecord]:
        """Get an experiment record by ID."""
        return self._records.get(experiment_id)

    def list_all(self) -> list[ExperimentRecord]:
        """List all registered experiments."""
        return list(self._records.values())

    def list_ids(self) -> list[str]:
        """List all experiment IDs."""
        return list(self._records.keys())

    def delete(self, experiment_id: str) -> bool:
        """Delete an experiment record."""
        if experiment_id in self._records:
            del self._records[experiment_id]
            # Also delete from disk
            _ensure_root(self.root)
            exp_dir = self.root / experiment_id
            if exp_dir.exists():
                import shutil

                shutil.rmtree(exp_dir)
            return True
        return False

    def count(self) -> int:
        """Return number of registered experiments."""
        return len(self._records)
