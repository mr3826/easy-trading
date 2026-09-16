"""Deterministic, point-in-time candidate ranking pipeline."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence


class ModelInputRejected(ValueError):
    """Raised when feature provenance or schema is unknown."""


@dataclass(frozen=True)
class TrainingExample:
    timestamp: datetime
    available_at: datetime
    features: Mapping[str, float]
    label: float

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.available_at.tzinfo is None:
            raise ModelInputRejected("timestamps must be timezone-aware")
        if self.available_at > self.timestamp:
            raise ModelInputRejected("feature became available after the decision")
        if not math.isfinite(self.label) or any(not math.isfinite(value) for value in self.features.values()):
            raise ModelInputRejected("non-finite feature or label")


@dataclass(frozen=True)
class PromotionCriteria:
    max_validation_mse: float
    max_test_mse: float


@dataclass(frozen=True)
class ModelArtifact:
    model_id: str
    feature_names: tuple[str, ...]
    weights: tuple[float, ...]
    bias: float
    fallback_score: float
    dataset_hash: str
    code_hash: str
    model_hash: str
    train_count: int
    validation_count: int
    test_count: int
    validation_mse: float
    test_mse: float
    promotion_eligible: bool

    def predict(self, example: TrainingExample) -> float:
        if set(example.features) != set(self.feature_names):
            raise ModelInputRejected("unknown or missing feature")
        values = tuple(example.features[name] for name in self.feature_names)
        return self.bias + sum(weight * value for weight, value in zip(self.weights, values))

    def fallback(self) -> float:
        return self.fallback_score


class MLTrainingPipeline:
    """Train a deterministic linear ranking model without future leakage."""

    def train(
        self,
        model_id: str,
        examples: Sequence[TrainingExample],
        code_hash: str,
        criteria: PromotionCriteria,
    ) -> ModelArtifact:
        if len(examples) < 6:
            raise ValueError("at least six chronological examples are required")
        ordered = sorted(examples, key=lambda example: example.timestamp)
        if list(examples) != ordered:
            raise ModelInputRejected("training examples must be chronological")
        if any(previous.timestamp >= current.timestamp for previous, current in zip(ordered, ordered[1:])):
            raise ModelInputRejected("training timestamps must be strictly increasing")
        feature_names = tuple(sorted(ordered[0].features))
        if not feature_names or any(set(example.features) != set(feature_names) for example in ordered):
            raise ModelInputRejected("feature schema is inconsistent")
        train_end = max(1, int(len(ordered) * 0.6))
        validation_end = max(train_end + 1, int(len(ordered) * 0.8))
        if validation_end >= len(ordered):
            validation_end = len(ordered) - 1
        train, validation, test = ordered[:train_end], ordered[train_end:validation_end], ordered[validation_end:]
        if not validation or not test:
            raise ValueError("chronological split must contain validation and test examples")
        fallback_score = sum(example.label for example in train) / len(train)
        weights = []
        for name in feature_names:
            denominator = sum(example.features[name] ** 2 for example in train)
            numerator = sum(example.features[name] * example.label for example in train)
            weights.append(numerator / denominator if denominator else 0.0)
        bias = fallback_score
        validation_mse = _mse(validation, feature_names, tuple(weights), bias)
        test_mse = _mse(test, feature_names, tuple(weights), bias)
        dataset_hash = hashlib.sha256(_canonical(ordered).encode("utf-8")).hexdigest()
        model_hash = hashlib.sha256(
            _canonical({"features": feature_names, "weights": weights, "bias": bias}).encode("utf-8")
        ).hexdigest()
        return ModelArtifact(
            model_id,
            feature_names,
            tuple(weights),
            bias,
            fallback_score,
            dataset_hash,
            code_hash,
            model_hash,
            len(train),
            len(validation),
            len(test),
            validation_mse,
            test_mse,
            validation_mse <= criteria.max_validation_mse and test_mse <= criteria.max_test_mse,
        )


class MLModelRegistry:
    """In-memory registry boundary; production persistence is PostgreSQL-backed."""

    def __init__(self) -> None:
        self._models: dict[str, ModelArtifact] = {}

    def register(self, artifact: ModelArtifact) -> None:
        if artifact.model_id in self._models:
            raise ValueError("model ID already registered")
        self._models[artifact.model_id] = artifact

    def get(self, model_id: str) -> ModelArtifact:
        try:
            return self._models[model_id]
        except KeyError as exc:
            raise ModelInputRejected("unknown model") from exc


def _mse(
    examples: Sequence[TrainingExample],
    names: tuple[str, ...],
    weights: tuple[float, ...],
    bias: float,
) -> float:
    return sum(
        (bias + sum(weight * example.features[name] for weight, name in zip(weights, names)) - example.label) ** 2
        for example in examples
    ) / len(examples)


def _canonical(value: object) -> str:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return json.dumps([_canonical(item) for item in value], sort_keys=True)
    if isinstance(value, Mapping):
        return json.dumps({str(key): _canonical(item) for key, item in sorted(value.items())}, sort_keys=True)
    if isinstance(value, TrainingExample):
        return _canonical(
            {
                "timestamp": value.timestamp.isoformat(),
                "available_at": value.available_at.isoformat(),
                "features": value.features,
                "label": value.label,
            }
        )
    return json.dumps(value, sort_keys=True)
