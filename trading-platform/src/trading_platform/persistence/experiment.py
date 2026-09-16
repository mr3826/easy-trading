"""Experiment persistence for Phase 4 research harness.

V1 persists every experiment including:
- hypothesis and parameters
- dataset hash and universe version
- code commit and dependency lock hash
- cost/slippage assumptions
- seed and result metrics

All experiments are stored as JSON files in a per-experiment directory
under the experiments root. This enables deterministic replay and
comparison across runs.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from trading_platform.strategies.ma_cross_strategy import (
    MaCrossHypothesis,
    dict_to_hypothesis,
    hypothesis_to_dict,
)

# ---------------------------------------------------------------------------
# Paths


EXPERIMENTS_ROOT = Path("/tmp/trading_experiments")  # configurable in production


def _ensure_root() -> None:
    """Ensure the experiments root directory exists."""
    EXPERIMENTS_ROOT.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Experiment record


class ExperimentRecord:
    """Immutable-ish experiment record persisted to disk.

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
        result_metrics: Optional[Dict[str, float]] = None,
        notes: str = "",
    ):
        self.experiment_id = experiment_id
        self.hypothesis = hypothesis
        self.dataset_hash = dataset_hash
        self.universe_version = universe_version
        self.code_commit = code_commit
        self.dependency_lock = dependency_lock
        self.cost_slippage_assumptions = cost_slippage_assumptions
        self.seed = seed
        self.result_metrics = result_metrics or {}
        self.notes = notes
        self.created_at = datetime.now(timezone.utc)
        self._path: Optional[Path] = None

    # -----------------------------------------------------------------
    # Persistence

    def save(self, root: Path = EXPERIMENTS_ROOT) -> Path:
        """Write the experiment record to disk.

        Returns the Path where it was saved.
        """
        _ensure_root()
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
    def load(experiment_id: str, root: Path = EXPERIMENTS_ROOT) -> Optional["ExperimentRecord"]:
        """Load an experiment record by ID.

        Returns None if not found.
        """
        _ensure_root()
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
        base = {
            "experiment_id": self.experiment_id,
            "hypothesis": hypothesis_to_dict(self.hypothesis),
            "dataset_hash": self.dataset_hash,
            "universe_version": self.universe_version,
            "code_commit": self.code_commit,
            "dependency_lock": self.dependency_lock,
            "cost_slippage_assumptions": self.cost_slippage_assumptions,
            "seed": self.seed,
            "result_metrics": self.result_metrics,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() + "Z",
        }
        return base

    @staticmethod
    def _from_dict(data: Dict[str, Any]) -> "ExperimentRecord":
        hyp = dict_to_hypothesis(data["hypothesis"])
        er = ExperimentRecord(
            experiment_id=data["experiment_id"],
            hypothesis=hyp,
            dataset_hash=data["dataset_hash"],
            universe_version=data["universe_version"],
            code_commit=data["code_commit"],
            dependency_lock=data["dependency_lock"],
            cost_slippage_assumptions=data["cost_slippage_assumptions"],
            seed=data["seed"],
            result_metrics=data.get("result_metrics"),
            notes=data.get("notes", ""),
        )
        er._path = None  # will be set on save
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
        """Compute SHA-256 hash of dataset for reproducibility."""
        return hashlib.sha256(bars_data.encode()).hexdigest()

    @staticmethod
    def compute_code_hash() -> str:
        """Compute hash of the current code state.

        In production this would read the actual source files or use
        `git rev-parse HEAD`. Here we use a placeholder.
        """
        # Placeholder — in production: hashlib.sha256(
        #    open("pyproject.toml").read().encode()
        # ).hexdigest()
        return "placeholder_code_commit_hash"


# ---------------------------------------------------------------------------
# Experiment registry (simple in-memory + disk)


class ExperimentRegistry:
    """Simple registry for tracking experiments.

    In V1 this is a lightweight JSON-based registry. Phase 5 will
    integrate with PostgreSQL + proper metadata tables.
    """

    def __init__(self, root: Path = EXPERIMENTS_ROOT):
        self.root = root
        _ensure_root()
        self._records: Dict[str, ExperimentRecord] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        """Load existing experiment records from disk."""
        _ensure_root()
        if not self.root.exists():
            return
        for entry in self.root.iterdir():
            if entry.is_dir():
                json_path = entry / "experiment.json"
                if json_path.exists():
                    try:
                        record = ExperimentRecord._from_dict(json.loads(json_path.read_text()))
                        self._records[record.experiment_id] = record
                    except Exception:
                        # Skip malformed records
                        pass

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
            _ensure_root()
            exp_dir = self.root / experiment_id
            if exp_dir.exists():
                import shutil

                shutil.rmtree(exp_dir)
            return True
        return False

    def count(self) -> int:
        """Return number of registered experiments."""
        return len(self._records)
