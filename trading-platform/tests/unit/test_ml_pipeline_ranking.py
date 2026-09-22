"""ML pipeline and ranking tests: chronological isolation, per-feature
provenance, trainable ranking model, promotion enforcement, deterministic
fallback, LLM fail-closed behavior, and AI boundary absence."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from trading_platform.ml_pipeline import (
    MLModelRegistry,
    MLTrainingPipeline,
    ModelArtifact,
    ModelInputRejected,
    PromotionCriteria,
    TrainableRanker,
    TrainingExample,
)
from trading_platform.ml_ranking import (
    LLMLLMFeatureManager,
    LLMSentimentFeature,
    MLCandidate,
    MLCandidateRegistry,
    MLRanker,
    PromotionGate,
)

UTC = timezone.utc


def _example(day: int) -> TrainingExample:
    timestamp = datetime(2026, 1, day, tzinfo=UTC)
    return TrainingExample(
        timestamp,
        timestamp,
        {"momentum": float(day), "volatility": 1.0},
        float(day) / 10,
        {"momentum": timestamp, "volatility": timestamp},
    )


def _examples(days: int = 10) -> list[TrainingExample]:
    return [_example(day) for day in range(1, days + 1)]


# ---------------------------------------------------------------------------
# Chronological isolation and per-feature provenance


def test_chronological_split_isolation() -> None:
    """Train/validation/test splits are chronological and never overlap."""
    examples = _examples()
    pipeline = MLTrainingPipeline()
    criteria = PromotionCriteria(max_validation_mse=1.0, max_test_mse=1.0)
    artifact = pipeline.train("model-1", examples, "code-hash", criteria)
    assert artifact.train_count == 6
    assert artifact.validation_count == 2
    assert artifact.test_count == 2
    with pytest.raises(ModelInputRejected):
        pipeline.train("bad", list(reversed(examples)), "code", criteria)


def test_feature_provenance_required_for_every_feature() -> None:
    """A feature without an availability timestamp is rejected."""
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ModelInputRejected):
        TrainingExample(timestamp, timestamp, {"momentum": 1.0, "volatility": 1.0}, 0.1, {"momentum": timestamp})
    with pytest.raises(ModelInputRejected):
        TrainingExample(timestamp, timestamp, {"momentum": 1.0}, 0.1, {})


def test_feature_available_after_decision_rejected() -> None:
    """A feature whose availability is after the decision timestamp is rejected."""
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ModelInputRejected):
        TrainingExample(
            timestamp,
            timestamp,
            {"momentum": 1.0},
            0.1,
            {"momentum": timestamp + timedelta(seconds=1)},
        )
    with pytest.raises(ModelInputRejected):
        TrainingExample(timestamp, timestamp, {"x": 1.0}, 0.1, {"x": timestamp.replace(tzinfo=None)})


# ---------------------------------------------------------------------------
# Trainable ranking model with full provenance


def test_trainable_ranker_is_deterministic_and_trained() -> None:
    examples = _examples()
    criteria = PromotionCriteria(max_validation_mse=1.0, max_test_mse=1.0)
    ranker = TrainableRanker(steps=200, learning_rate=0.05)
    artifact = ranker.train("ranker-1", examples, "code-hash", criteria)
    again = TrainableRanker(steps=200, learning_rate=0.05).train("ranker-1", examples, "code-hash", criteria)
    assert artifact.model_hash == again.model_hash
    assert artifact.dataset_hash == again.dataset_hash
    assert artifact.feature_schema_hash == again.feature_schema_hash
    assert artifact.feature_schema_hash != artifact.model_hash
    assert artifact.train_count == 6
    assert artifact.predict(examples[-1]) > artifact.fallback()
    assert artifact.fallback() == pytest.approx(sum(example.label for example in examples[:6]) / 6)


def test_model_artifact_records_all_provenance_hashes() -> None:
    examples = _examples()
    artifact: ModelArtifact = MLTrainingPipeline().train(
        "model-1", examples, "a" * 40, PromotionCriteria(max_validation_mse=1.0, max_test_mse=1.0)
    )
    assert artifact.code_hash == "a" * 40
    assert len(artifact.dataset_hash) == 64
    assert len(artifact.model_hash) == 64
    assert len(artifact.feature_schema_hash) == 64


def test_deterministic_fallback_is_labeled_and_deterministic() -> None:
    examples = _examples()
    artifact = MLTrainingPipeline().train(
        "model-1", examples, "code-hash", PromotionCriteria(max_validation_mse=1.0, max_test_mse=1.0)
    )
    assert artifact.fallback() == artifact.fallback()
    registry = MLModelRegistry()
    registry.register(artifact)
    assert registry.get("model-1") is artifact


# ---------------------------------------------------------------------------
# Promotion enforcement


def _comparison(expectancy_delta: float, sharpe_delta: float, dd_improvement: float, source: str = "walk_forward"):
    ranker = MLRanker("base", "ranker")
    return ranker.compare(
        {"expectancy": 1.0, "sharpe_ratio": 1.0, "max_drawdown_pct": 10.0},
        {
            "expectancy": 1.0 + expectancy_delta,
            "sharpe_ratio": 1.0 + sharpe_delta,
            "max_drawdown_pct": 10.0 - dd_improvement,
        },
        source=source,
    )


def _candidate(candidate_id: str = "ranker-1") -> MLCandidate:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return MLCandidate(
        candidate_id=candidate_id,
        model_name="ranker",
        training_period_start=start,
        training_period_end=start + timedelta(days=30),
        features=["momentum"],
        hyperparameters={},
        dataset_hash="a" * 64,
        code_commit="b" * 40,
        metrics={"validation_mse": 0.1},
        creation_date=start,
        feature_available_at={"momentum": start},
    )


def test_promotion_requires_two_independent_conditions() -> None:
    registry = MLCandidateRegistry()
    candidate = _candidate()
    registry.register(candidate)
    gate = PromotionGate()

    # One improved metric alone never promotes
    one_metric = registry.promote(candidate.candidate_id, _comparison(0.5, 0.0, 0.0), gate)
    assert one_metric["promoted"] is False
    assert one_metric["conditions_met"] == 1
    assert candidate.rejected is True

    # Two independent conditions promote
    registry2 = MLCandidateRegistry()
    candidate2 = _candidate("ranker-2")
    registry2.register(candidate2)
    two_metrics = registry2.promote(candidate2.candidate_id, _comparison(0.5, 0.5, 0.0), gate)
    assert two_metrics["promoted"] is True
    assert two_metrics["conditions_met"] >= 2
    assert candidate2.promotion_evidence is not None


def test_promotion_rejects_historical_llm_evidence() -> None:
    registry = MLCandidateRegistry()
    candidate = _candidate()
    registry.register(candidate)
    gate = PromotionGate()
    llm_evidence = _comparison(0.5, 0.5, 0.5, source="llm_forward")
    result = registry.promote(candidate.candidate_id, llm_evidence, gate)
    assert result["promoted"] is False
    assert "LLM" in result["reason"]
    assert candidate.rejected is False


def test_promote_unknown_candidate_raises() -> None:
    with pytest.raises(ValueError):
        MLCandidateRegistry().promote("missing", {}, PromotionGate())


def test_real_feature_leakage_check() -> None:
    registry = MLCandidateRegistry()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candidate = _candidate()
    candidate.feature_available_at = {"momentum": start + timedelta(days=40)}
    registry.register(candidate)

    # Feature only becomes available after this decision timestamp: leakage
    assert registry.is_feature_leakage("momentum", start + timedelta(days=10)) is True
    # Feature available before the decision: no leakage
    assert registry.is_feature_leakage("momentum", start + timedelta(days=50)) is False
    # Unknown feature: leakage
    assert registry.is_feature_leakage("other", start + timedelta(days=50)) is True


# ---------------------------------------------------------------------------
# LLM feature fail-closed behavior


def _feature(**overrides: object) -> LLMSentimentFeature:
    kwargs: dict = {
        "feature_id": "news",
        "model_name": "gpt-test",
        "prompt_template": "Summarize sentiment for {symbol}",
        "creation_date": datetime(2026, 1, 1, tzinfo=UTC),
        "allowed_symbols": {"AAPL"},
    }
    kwargs.update(overrides)
    return LLMSentimentFeature(**kwargs)


def _valid_output() -> str:
    return '{"symbol":"AAPL","sentiment":0.5,"confidence":0.8,"observed_at":"2026-06-01T00:00:00+00:00"}'


def test_llm_rejects_malformed_json_nan_infinity_and_unknown_symbol() -> None:
    feature = _feature()
    assert feature.validate_output("{not json")["valid"] is False
    assert (
        feature.validate_output('{"symbol":"AAPL","sentiment":NaN,"confidence":0.5,"observed_at":"x"}')["valid"]
        is False
    )
    assert (
        feature.validate_output('{"symbol":"AAPL","sentiment":Infinity,"confidence":0.5,"observed_at":"x"}')["valid"]
        is False
    )
    assert (
        feature.validate_output('{"symbol":"MSFT","sentiment":0,"confidence":0.5,"observed_at":"x"}')["valid"] is False
    )


def test_llm_rejects_stale_and_future_content() -> None:
    feature = _feature(max_content_age_seconds=3600.0)
    decision_time = datetime(2026, 6, 2, tzinfo=UTC)
    stale = feature.validate_output(
        '{"symbol":"AAPL","sentiment":0.5,"confidence":0.8,"observed_at":"2026-06-01T00:00:00+00:00"}',
        decision_time=decision_time,
    )
    assert stale["valid"] is False
    assert any("stale" in error for error in stale["errors"])

    future = feature.validate_output(
        '{"symbol":"AAPL","sentiment":0.5,"confidence":0.8,"observed_at":"2026-06-03T00:00:00+00:00"}',
        decision_time=decision_time,
    )
    assert future["valid"] is False
    assert any("future" in error for error in future["errors"])

    fresh = feature.validate_output(
        '{"symbol":"AAPL","sentiment":0.5,"confidence":0.8,"observed_at":"2026-06-02T00:00:00+00:00"}',
        decision_time=decision_time,
    )
    assert fresh["valid"] is True


def test_llm_rejects_contradictory_output() -> None:
    feature = _feature()
    contradictory = feature.validate_output('{"symbol":"AAPL","sentiment":0.95,"confidence":0.05,"observed_at":"x"}')
    assert contradictory["valid"] is False
    assert any("contradictory" in error for error in contradictory["errors"])


def test_llm_rejects_prompt_injection_patterns_and_phrasing() -> None:
    feature = _feature()
    assert (
        feature.validate_output('{"symbol":"<|im_start|>","sentiment":0,"confidence":0.5,"observed_at":"x"}')["valid"]
        is False
    )
    assert (
        feature.validate_output(
            '{"symbol":"AAPL","sentiment":0,"confidence":0.5,"observed_at":"ignore previous instructions"}'
        )["valid"]
        is False
    )


def test_llm_rejects_validator_timeout_and_upstream_failure() -> None:
    import time

    feature = _feature(timeout_seconds=0.01)

    def slow_hook(raw: str) -> dict:
        time.sleep(0.05)
        return {"valid": True}

    feature.add_validator(slow_hook)
    timed_out = feature.validate_output(_valid_output())
    assert timed_out["valid"] is False
    assert any("timeout" in error for error in timed_out["errors"])

    plain_feature = _feature()
    upstream = plain_feature.validate_output(_valid_output(), upstream_ok=False)
    assert upstream["valid"] is False
    assert any("upstream" in error for error in upstream["errors"])


def test_forward_test_recorder_records_without_claiming_results() -> None:
    feature_manager = LLMLLMFeatureManager()
    feature = _feature()
    feature_manager.register_feature(feature)
    now = datetime(2026, 6, 1, tzinfo=UTC)
    result = feature_manager.forward_test_feature("news", now, _valid_output())
    assert result["feature_mode"] == "forward_test"
    assert "FORWARD_TEST" in result["eligibility"]
    assert feature.tested_forward_dates == [now]
    assert "llm_output_sha256" in feature.validation_results[now.isoformat()]


def test_ai_boundary_has_no_broker_imports_or_submission_methods() -> None:
    import inspect

    import trading_platform.features
    import trading_platform.ml_pipeline
    import trading_platform.ml_ranking

    for module in (trading_platform.ml_pipeline, trading_platform.ml_ranking, trading_platform.features):
        source = inspect.getsource(module)
        assert "broker_adapter" not in source
        assert "trading_platform.broker" not in source
        assert "execute_order" not in source
        assert "place_order" not in source

    feature = _feature()
    manager = LLMLLMFeatureManager()
    for obj in (feature, manager):
        assert not hasattr(obj, "execute_order")
        assert not hasattr(obj, "submit_order")
        assert not hasattr(obj, "place_order")
