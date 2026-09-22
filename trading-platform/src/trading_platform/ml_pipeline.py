"""Deterministic, point-in-time candidate ranking pipeline."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from dataclasses import field as dataclasses_field
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
    feature_available_at: Mapping[str, datetime] = dataclasses_field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.available_at.tzinfo is None:
            raise ModelInputRejected("timestamps must be timezone-aware")
        if self.available_at > self.timestamp:
            raise ModelInputRejected("feature became available after the decision")
        if set(self.feature_available_at) != set(self.features):
            raise ModelInputRejected("feature provenance incomplete: every feature needs an availability timestamp")
        for available_at in self.feature_available_at.values():
            if available_at.tzinfo is None:
                raise ModelInputRejected("feature availability timestamps must be timezone-aware")
            if available_at > self.timestamp:
                raise ModelInputRejected("feature available after the decision")
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
    feature_schema_hash: str
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


def _prepare_splits(
    examples: Sequence[TrainingExample],
) -> tuple[list[TrainingExample], list[TrainingExample], list[TrainingExample], tuple[str, ...]]:
    """Validate chronological isolation and split into train/validation/test."""
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
    return train, validation, test, feature_names


class MLTrainingPipeline:
    """Train a deterministic linear ranking model without future leakage."""

    def train(
        self,
        model_id: str,
        examples: Sequence[TrainingExample],
        code_hash: str,
        criteria: PromotionCriteria,
    ) -> ModelArtifact:
        train, validation, test, feature_names = _prepare_splits(examples)
        fallback_score = sum(example.label for example in train) / len(train)
        weights = []
        for name in feature_names:
            denominator = sum(example.features[name] ** 2 for example in train)
            numerator = sum(example.features[name] * example.label for example in train)
            weights.append(numerator / denominator if denominator else 0.0)
        bias = fallback_score
        validation_mse = _mse(validation, feature_names, tuple(weights), bias)
        test_mse = _mse(test, feature_names, tuple(weights), bias)
        dataset_hash = hashlib.sha256(_canonical(ordered_examples(examples)).encode("utf-8")).hexdigest()
        model_hash = hashlib.sha256(
            _canonical({"features": feature_names, "weights": weights, "bias": bias}).encode("utf-8")
        ).hexdigest()
        feature_schema_hash = _feature_schema_hash(feature_names)
        return ModelArtifact(
            model_id=model_id,
            feature_names=feature_names,
            weights=tuple(weights),
            bias=bias,
            fallback_score=fallback_score,
            dataset_hash=dataset_hash,
            code_hash=code_hash,
            model_hash=model_hash,
            feature_schema_hash=feature_schema_hash,
            train_count=len(train),
            validation_count=len(validation),
            test_count=len(test),
            validation_mse=validation_mse,
            test_mse=test_mse,
            promotion_eligible=validation_mse <= criteria.max_validation_mse and test_mse <= criteria.max_test_mse,
        )


class TrainableRanker:
    """Trainable ranking model: deterministic gradient descent on train examples.

    Zero-initialized weights with a fixed number of fixed-rate steps and no
    randomness; identical inputs produce an identical model hash. Training
    uses only the chronological train split; validation and test examples are
    never fitted on. The deterministic baseline (fallback score) always runs
    first; this model ranks only when its promotion criteria are met.
    """

    def __init__(self, steps: int = 200, learning_rate: float = 0.05) -> None:
        self.steps = steps
        self.learning_rate = learning_rate

    def train(
        self,
        model_id: str,
        examples: Sequence[TrainingExample],
        code_hash: str,
        criteria: PromotionCriteria,
    ) -> ModelArtifact:
        train, validation, test, feature_names = _prepare_splits(examples)
        weights = [0.0] * len(feature_names)
        bias = 0.0
        n = len(train)
        for _ in range(self.steps):
            grad_w = [0.0] * len(feature_names)
            grad_b = 0.0
            for example in train:
                prediction = bias + sum(w * example.features[name] for w, name in zip(weights, feature_names))
                error = prediction - example.label
                for idx, name in enumerate(feature_names):
                    grad_w[idx] += error * example.features[name]
                grad_b += error
            weights = [w - self.learning_rate * (2.0 * g / n) for w, g in zip(weights, grad_w)]
            bias = bias - self.learning_rate * (2.0 * grad_b / n)
        fallback_score = sum(example.label for example in train) / n
        validation_mse = _mse(validation, feature_names, tuple(weights), bias)
        test_mse = _mse(test, feature_names, tuple(weights), bias)
        dataset_hash = hashlib.sha256(_canonical(ordered_examples(examples)).encode("utf-8")).hexdigest()
        model_hash = hashlib.sha256(
            _canonical(
                {
                    "features": feature_names,
                    "weights": weights,
                    "bias": bias,
                    "steps": self.steps,
                    "learning_rate": self.learning_rate,
                }
            ).encode("utf-8")
        ).hexdigest()
        feature_schema_hash = _feature_schema_hash(feature_names)
        return ModelArtifact(
            model_id=model_id,
            feature_names=feature_names,
            weights=tuple(weights),
            bias=bias,
            fallback_score=fallback_score,
            dataset_hash=dataset_hash,
            code_hash=code_hash,
            model_hash=model_hash,
            feature_schema_hash=feature_schema_hash,
            train_count=len(train),
            validation_count=len(validation),
            test_count=len(test),
            validation_mse=validation_mse,
            test_mse=test_mse,
            promotion_eligible=validation_mse <= criteria.max_validation_mse and test_mse <= criteria.max_test_mse,
        )


def ordered_examples(examples: Sequence[TrainingExample]) -> list[TrainingExample]:
    """Chronologically ordered copy of the examples."""
    return sorted(examples, key=lambda example: example.timestamp)


def _feature_schema_hash(feature_names: tuple[str, ...]) -> str:
    """SHA-256 of the canonical feature schema definition (provenance required)."""
    canonical = _canonical({"features": list(feature_names), "provenance_required": True})
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
                "feature_available_at": {
                    name: available_at.isoformat() for name, available_at in sorted(value.feature_available_at.items())
                },
                "label": value.label,
            }
        )
    return json.dumps(value, sort_keys=True)
