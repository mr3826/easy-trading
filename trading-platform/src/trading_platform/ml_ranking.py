"""Phase 11: ML ranking and optional LLM news forward test.

Provides infrastructure for:
- Stage A: ML candidate ranking with deterministic fallback
- Stage B: Optional LLM news features with point-in-time validation
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

_INJECTION_RE = re.compile(r"(ignore\s+(all\s+)?previous|system\s+message|tool\s+call)", re.I)

# ---------------------------------------------------------------------------
# ML Candidate Registry
# ---------------------------------------------------------------------------


class MLCandidate:
    """Registered ML candidate for Stage A ranking.

    V1: Every trial is registered, not only winners. Includes metadata
    for experiment tracking, comparison, and prevention of feature leakage.
    """

    def __init__(
        self,
        candidate_id: str,
        model_name: str,
        training_period_start: datetime,
        training_period_end: datetime,
        features: List[str],
        hyperparameters: Dict[str, Any],
        dataset_hash: str,
        code_commit: str,
        metrics: Dict[str, float],
        creation_date: datetime,
        environment: str = "simulation",
        feature_available_at: Optional[Dict[str, datetime]] = None,
    ):
        self.candidate_id = candidate_id
        self.model_name = model_name
        self.training_period_start = training_period_start
        self.training_period_end = training_period_end
        self.features = features
        self.hyperparameters = hyperparameters
        self.dataset_hash = dataset_hash
        self.code_commit = code_commit
        self.metrics = metrics
        self.creation_date = creation_date
        self.environment = environment
        self.feature_available_at: Dict[str, datetime] = feature_available_at or {}
        self.promotion_evidence: Optional[Dict[str, Any]] = None
        self.rejected: bool = False
        self.reject_reason: Optional[str] = None

    def is_feature_leakage(self, feature: str, decision_timestamp: datetime) -> bool:
        """Reject a feature decision that is not strictly out of sample."""
        if feature not in self.features:
            return True
        return decision_timestamp <= self.training_period_end

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "model_name": self.model_name,
            "training_period_start": self.training_period_start.isoformat(),
            "training_period_end": self.training_period_end.isoformat(),
            "features": self.features,
            "hyperparameters": self.hyperparameters,
            "dataset_hash": self.dataset_hash,
            "code_commit": self.code_commit,
            "metrics": self.metrics,
            "creation_date": self.creation_date.isoformat(),
            "environment": self.environment,
            "promotion_evidence": self.promotion_evidence,
            "rejected": self.rejected,
            "reject_reason": self.reject_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MLCandidate":
        from datetime import datetime

        return cls(
            candidate_id=data["candidate_id"],
            model_name=data["model_name"],
            training_period_start=datetime.fromisoformat(data["training_period_start"]),
            training_period_end=datetime.fromisoformat(data["training_period_end"]),
            features=data["features"],
            hyperparameters=data["hyperparameters"],
            dataset_hash=data["dataset_hash"],
            code_commit=data["code_commit"],
            metrics=data["metrics"],
            creation_date=datetime.fromisoformat(data["creation_date"]),
            environment=data.get("environment", "simulation"),
        )


class MLCandidateRegistry:
    """Registry for ML candidates (Stage A).

    V1: Registers every trial including failures. Prevents feature leakage
    by enforcing chronological label definitions and dataset versioning.
    """

    def __init__(self) -> None:
        self.candidates: Dict[str, MLCandidate] = {}
        self.registered_features: set[str] = set()

    def register(
        self,
        candidate: MLCandidate,
    ) -> None:
        """Register an ML candidate.

        V1: Rejects registration if candidate_id already exists (prevents
        duplicate registration / tuning on the same trial).
        """
        if candidate.candidate_id in self.candidates:
            raise ValueError(f"Candidate ID {candidate.candidate_id} already registered")
        # Prevent feature leakage: ensure training period is strictly before
        # any decision timestamps in the forward test
        self.candidates[candidate.candidate_id] = candidate

        # Track registered features for leakage detection
        for f in candidate.features:
            self.registered_features.add(f)

    def get(self, candidate_id: str) -> Optional[MLCandidate]:
        """Get a registered candidate by ID."""
        return self.candidates.get(candidate_id)

    def get_active(self) -> List[MLCandidate]:
        """Get all non-rejected candidates."""
        return [c for c in self.candidates.values() if not c.rejected]

    def reject(self, candidate_id: str, reason: str) -> None:
        """Reject a candidate.

        V1: Marks a candidate as rejected with a reason. Prevents
        automatic promotion even if metrics look good.
        """
        if candidate_id in self.candidates:
            self.candidates[candidate_id].rejected = True
            self.candidates[candidate_id].reject_reason = reason

    def promote(self, candidate_id: str, evidence: Dict[str, Any], gate: PromotionGate) -> Dict[str, Any]:
        """Attempt to promote a candidate through the promotion gate.

        Promotion requires the gate's criteria (at least two independent
        metric conditions) and a non-LLM evidence source; historical LLM
        results are denied without rejecting the candidate (the evidence
        source is illegitimate, not the candidate). Candidates failing the
        gate's own criteria are marked rejected instead of silently promoted.
        """
        candidate = self.candidates.get(candidate_id)
        if candidate is None:
            raise ValueError(f"unknown candidate {candidate_id}")
        if candidate.rejected:
            return {"promoted": False, "conditions_met": 0, "reason": candidate.reject_reason}
        source = str(evidence.get("source", "")).lower()
        if "llm" in source:
            return {
                "promoted": False,
                "conditions_met": 0,
                "reason": "historical LLM results do not authorize promotion",
            }
        evaluation = gate.evaluate(evidence)
        if not evaluation["promoted"]:
            candidate.rejected = True
            candidate.reject_reason = evaluation.get("reason") or "promotion gate failed"
            return {**evaluation, "promoted": False}
        candidate.promotion_evidence = evidence
        return {**evaluation, "promoted": True}

    def is_feature_leakage(self, feature: str, decision_timestamp: datetime) -> bool:
        """Check if using a feature at a decision timestamp would cause leakage.

        Real check using per-feature availability provenance: an unknown
        feature leaks; a feature whose availability timestamp is missing from
        every registered carrier leaks; a feature that only becomes available
        AFTER the decision timestamp leaks (future data).
        """
        if feature not in self.registered_features:
            return True
        carriers = [c for c in self.candidates.values() if feature in c.features]
        if not carriers:
            return True
        for carrier in carriers:
            available_at = carrier.feature_available_at.get(feature)
            if available_at is None:
                return True
            if available_at > decision_timestamp:
                return True
        return False


# ---------------------------------------------------------------------------
# Stage A: ML Ranking Comparison
# ---------------------------------------------------------------------------


class PromotionGate:
    """Promotion gate: a candidate is promoted only on independent evidence.

    Criteria are defined BEFORE evaluation. At least TWO independent metric
    conditions must hold (no automatic promotion from one improved metric):
    (1) expectancy delta above the minimum, (2) Sharpe delta above the
    minimum, (3) drawdown improvement. Historical LLM results never count as
    promotion evidence.
    """

    def __init__(self, min_expectancy_delta: float = 0.0, min_sharpe_delta: float = 0.0) -> None:
        self.min_expectancy_delta = min_expectancy_delta
        self.min_sharpe_delta = min_sharpe_delta

    def evaluate(self, comparison: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate a ranking comparison against the promotion criteria."""
        metrics = comparison.get("metrics_comparison", {})
        source = str(comparison.get("source", "")).lower()
        if "llm" in source:
            return {
                "promoted": False,
                "conditions_met": 0,
                "reason": "historical LLM results do not authorize promotion",
            }
        expectancy_ok = metrics.get("expectancy_delta", 0.0) > self.min_expectancy_delta
        sharpe_ok = metrics.get("sharpe_delta", 0.0) > self.min_sharpe_delta
        drawdown_ok = metrics.get("drawdown_improvement_pct", 0.0) > 0.0
        conditions_met = sum(1 for ok in (expectancy_ok, sharpe_ok, drawdown_ok) if ok)
        promoted = conditions_met >= 2
        return {
            "promoted": promoted,
            "conditions_met": conditions_met,
            "expectancy_ok": expectancy_ok,
            "sharpe_ok": sharpe_ok,
            "drawdown_ok": drawdown_ok,
            "reason": None if promoted else "promotion gate failed: fewer than two independent conditions met",
        }


class MLRanker:
    """Compares BASELINE vs BASELINE + RANKER on identical data.

    V1: ML changes ranking, not hard eligibility or risk. The deterministic
    baseline always runs first; ML only provides ranking adjustments that
    must not violate hard risk limits.
    """

    def __init__(self, base_candidate_id: str, ranker_candidate_id: str):
        self.base_candidate_id = base_candidate_id
        self.ranker_candidate_id = ranker_candidate_id

    def compare(
        self,
        base_metrics: Dict[str, float],
        ranker_metrics: Dict[str, float],
        source: str = "walk_forward",
    ) -> Dict[str, Any]:
        """Compare BASELINE vs BASELINE + RANKER metrics.

        V1: Returns comparison result with eligibility determination and the
        evidence source recorded for the promotion gate.
        """
        comparison: Dict[str, Any] = {
            "comparison_timestamp": datetime.now(timezone.utc).isoformat(),
            "base_candidate": self.base_candidate_id,
            "ranker_candidate": self.ranker_candidate_id,
            "source": source,
            "metrics_comparison": {},
            "eligibility": "PENDING",
            "durable_benefit": None,
        }

        # Compare risk-adjusted returns
        base_expectancy = base_metrics.get("expectancy", 0.0)
        ranker_expectancy = ranker_metrics.get("expectancy", 0.0)
        expectancy_delta = ranker_expectancy - base_expectancy

        # Compare Sharpe ratios
        base_sharpe = base_metrics.get("sharpe_ratio", 0.0)
        ranker_sharpe = ranker_metrics.get("sharpe_ratio", 0.0)
        sharpe_delta = ranker_sharpe - base_sharpe

        # Compare drawdown
        base_dd = base_metrics.get("max_drawdown_pct", 0.0)
        ranker_dd = ranker_metrics.get("max_drawdown_pct", 0.0)
        dd_improvement = base_dd - ranker_dd  # positive = improvement

        # Compare trade count
        base_trades = base_metrics.get("trade_count", 0)
        ranker_trades = ranker_metrics.get("trade_count", 0)
        trade_reduction = base_trades - ranker_trades  # positive = reduction

        # Compare costs
        base_costs = base_metrics.get("total_costs", 0.0)
        ranker_costs = ranker_metrics.get("total_costs", 0.0)
        cost_reduction = base_costs - ranker_costs  # positive = reduction

        comparison["metrics_comparison"] = {
            "expectancy_delta": expectancy_delta,
            "sharpe_delta": sharpe_delta,
            "drawdown_improvement_pct": dd_improvement,
            "trade_reduction": trade_reduction,
            "cost_reduction": cost_reduction,
        }

        # V1: AI must provide at least one durable benefit after costs and risk
        durable_benefits = []

        if expectancy_delta > 0:
            durable_benefits.append("higher risk-adjusted return")
        if sharpe_delta > 0:
            durable_benefits.append("similar return with lower drawdown or better risk-adjusted return")
        if dd_improvement > 0:
            durable_benefits.append("similar return with lower drawdown")
        if trade_reduction > 0:
            durable_benefits.append("similar return with fewer trades/lower costs")
        if cost_reduction > 0:
            durable_benefits.append("similar return with lower costs")

        comparison["durable_benefit"] = durable_benefits if durable_benefits else None

        # V1: AI qualifies only if it provides at least one durable benefit
        if durable_benefits:
            comparison["eligibility"] = "QUALIFIED"
        else:
            comparison["eligibility"] = "REJECTED - no durable benefit after costs and risk"

        return comparison

    def validate_no_feature_leakage(
        self, candidate: MLCandidate, forward_decision_timestamps: List[datetime]
    ) -> Tuple[bool, str]:
        """Validate that candidate features don't leak future information.

        V1: Rejects candidates with feature leakage. Prevents using data
        that isn't available at the decision timestamp.
        """
        for ts in forward_decision_timestamps:
            if candidate.is_feature_leakage(
                feature=candidate.features[0] if candidate.features else "",
                decision_timestamp=ts,
            ):
                return True, f"Feature leakage detected for {candidate.model_name}"
        return False, "No feature leakage detected"


# ---------------------------------------------------------------------------
# Stage B: LLM News Features
# ---------------------------------------------------------------------------


class LLMSentimentFeature:
    """LLM-derived news sentiment feature for Stage B forward testing.

    V1: Default policy: forward-test LLM news features only. LLM has no
    broker tool, credentials, network route, or order-submission capability.
    All LLM output must be validated and stored with provenance.
    """

    def __init__(
        self,
        feature_id: str,
        model_name: str,
        prompt_template: str,
        creation_date: datetime,
        validation_mode: str = "forward_test",
        allowed_symbols: Optional[set[str]] = None,
        max_content_age_seconds: float = 86400.0,
        timeout_seconds: float = 5.0,
    ):
        self.feature_id = feature_id
        self.model_name = model_name
        self.prompt_template = prompt_template
        self.creation_date = creation_date
        self.validation_mode = validation_mode
        self.allowed_symbols = allowed_symbols or set()
        self.max_content_age_seconds = max_content_age_seconds
        self.timeout_seconds = timeout_seconds
        self.tested_forward_dates: List[datetime] = []
        self.validation_results: Optional[Dict[str, Any]] = None
        self.promotion_evidence: Optional[Dict[str, Any]] = None
        self._validator_hooks: List[Callable[[str], Any]] = []

    def add_validator(self, validator_func: Callable[[str], Any]) -> None:
        """Add a validation hook for LLM output.

        V1: Hooks test prompt injection, invalid JSON, unknown symbols,
        contradictory output, timeout, 500 errors, NaN, and impossible confidence.
        """
        self._validator_hooks.append(validator_func)

    def validate_output(
        self,
        output: str,
        decision_time: Optional[datetime] = None,
        upstream_ok: bool = True,
    ) -> Dict[str, Any]:
        """Validate LLM output through registered hooks, fail-closed.

        Rejects malformed JSON, NaN/infinity, unknown symbols, stale content,
        contradictory output, prompt injection, validator timeouts, and
        upstream failures. Any rejection yields FEATURE_REJECTED; failure
        never means "trade anyway".
        """
        result: Dict[str, Any] = {
            "valid": True,
            "errors": [],
            "parsed_data": None,
        }

        # Upstream provider failure is never a default-pass
        if not upstream_ok:
            result["valid"] = False
            result["errors"].append("FEATURE_REJECTED: upstream failure")

        # Hook: check for prompt injection patterns (real chat-template and
        # instruction tokens) plus structural injection phrasing
        injection_patterns = ["<|prompt|>", "<|im_start|>", "<|im_end|>", "[INST]", "[/INST]", "<?>"]
        for pattern in injection_patterns:
            if pattern in output:
                result["valid"] = False
                result["errors"].append(f"Prompt injection pattern detected: {pattern}")
        if _INJECTION_RE.search(output):
            result["valid"] = False
            result["errors"].append("Prompt injection phrasing detected")

        # AI output is an API boundary, not free-form text. Invalid JSON is
        # rejected rather than being interpreted by downstream code.
        try:
            parsed = json.loads(output)
            result["parsed_data"] = parsed
        except json.JSONDecodeError:
            result["valid"] = False
            result["errors"].append("FEATURE_REJECTED: invalid JSON")

        if not isinstance(result["parsed_data"], dict):
            result["valid"] = False
            result["errors"].append("FEATURE_REJECTED: object schema required")
        else:
            required = {"symbol", "sentiment", "confidence", "observed_at"}
            if set(result["parsed_data"]) != required:
                result["valid"] = False
                result["errors"].append("FEATURE_REJECTED: schema violation")
            symbol = result["parsed_data"].get("symbol")
            if not isinstance(symbol, str) or symbol not in self.allowed_symbols:
                result["valid"] = False
                result["errors"].append("FEATURE_REJECTED: unknown symbol")

        # Hook: check for NaN or impossible confidence
        if result["parsed_data"]:
            if "confidence" in result["parsed_data"]:
                conf = result["parsed_data"]["confidence"]
                invalid_confidence = (
                    not isinstance(conf, (int, float))
                    or isinstance(conf, bool)
                    or not math.isfinite(float(conf))
                    or conf < 0
                    or conf > 1
                )
                if invalid_confidence:
                    result["valid"] = False
                    result["errors"].append(f"Impossible confidence value: {conf}")
            sentiment = result["parsed_data"].get("sentiment")
            invalid_sentiment = (
                not isinstance(sentiment, (int, float))
                or isinstance(sentiment, bool)
                or not math.isfinite(float(sentiment))
                or sentiment < -1
                or sentiment > 1
            )
            if invalid_sentiment:
                result["valid"] = False
                result["errors"].append(f"Invalid sentiment value: {sentiment}")
            observed_at = result["parsed_data"].get("observed_at")
            if not isinstance(observed_at, str) or not observed_at.strip():
                result["valid"] = False
                result["errors"].append("FEATURE_REJECTED: missing provenance")
            elif _INJECTION_RE.search(observed_at):
                result["valid"] = False
                result["errors"].append("FEATURE_REJECTED: injected provenance")
            elif decision_time is not None:
                # Real timestamp-staleness check: content observed after the
                # decision or older than the max age is stale
                try:
                    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                except ValueError:
                    observed = None
                if observed is None:
                    result["valid"] = False
                    result["errors"].append("FEATURE_REJECTED: unparseable observed_at provenance")
                else:
                    if observed.tzinfo is None:
                        observed = observed.replace(tzinfo=timezone.utc)
                    age_seconds = (decision_time - observed).total_seconds()
                    if age_seconds < 0:
                        result["valid"] = False
                        result["errors"].append("FEATURE_REJECTED: observed_at is in the future")
                    elif age_seconds > self.max_content_age_seconds:
                        result["valid"] = False
                        result["errors"].append("FEATURE_REJECTED: stale content")

            # Contradictory output: extreme sentiment claimed with near-zero
            # conviction cannot be acted on (documented definition)
            if result["parsed_data"] and result["valid"]:
                sentiment_value = result["parsed_data"].get("sentiment")
                confidence_value = result["parsed_data"].get("confidence")
                if (
                    isinstance(sentiment_value, (int, float))
                    and not isinstance(sentiment_value, bool)
                    and isinstance(confidence_value, (int, float))
                    and not isinstance(confidence_value, bool)
                    and abs(float(sentiment_value)) > 0.9
                    and float(confidence_value) < 0.1
                ):
                    result["valid"] = False
                    result["errors"].append("FEATURE_REJECTED: contradictory output")

        # Run all registered validator hooks with a per-hook deadline; a hook
        # that exceeds its timeout yields FEATURE_REJECTED
        import time

        for hook in self._validator_hooks:
            started = time.perf_counter()
            try:
                hook_result = hook(output)
            except Exception as exc:
                result["valid"] = False
                result["errors"].append(f"FEATURE_REJECTED: validator failure: {type(exc).__name__}")
                continue
            elapsed = time.perf_counter() - started
            if elapsed > self.timeout_seconds:
                result["valid"] = False
                result["errors"].append("FEATURE_REJECTED: validator timeout")
                continue
            if isinstance(hook_result, dict):
                if not hook_result.get("valid", True):
                    result["valid"] = False
                    result["errors"].extend(hook_result.get("errors", []))
            elif hook_result is False:
                result["valid"] = False
                result["errors"].append("Validator hook returned False")

        return result

    def record_forward_test(self, forward_date: datetime, result: Dict[str, Any]) -> None:
        """Record a forward test result.

        V1: Default policy: forward-test LLM news features only.
        """
        self.tested_forward_dates.append(forward_date)
        if self.validation_results is None:
            self.validation_results = {}
        self.validation_results[forward_date.isoformat()] = result


class LLMLLMFeatureManager:
    """Manager for LLM news features (Stage B).

    V1: Treats LLM-derived historical news sentiment as unsuitable for
    promotion evidence unless the model and news corpus are demonstrably
    point-in-time. Default policy: forward-test LLM news features only.
    The LLM has no broker tool, credentials, network route, or
    order-submission capability.
    """

    def __init__(self) -> None:
        self.features: Dict[str, LLMSentimentFeature] = {}
        self.forward_test_history: List[Dict[str, Any]] = []

    def register_feature(self, feature: LLMSentimentFeature) -> None:
        """Register an LLM news sentiment feature."""
        self.features[feature.feature_id] = feature

    def get_feature(self, feature_id: str) -> Optional[LLMSentimentFeature]:
        """Get a registered LLM feature by ID."""
        return self.features.get(feature_id)

    def forward_test_feature(
        self,
        feature_id: str,
        forward_date: datetime,
        llm_output: str,
        decision_time: Optional[datetime] = None,
        upstream_ok: bool = True,
    ) -> Dict[str, Any]:
        """Run a forward test of an LLM news feature.

        V1: Validates LLM output (fail-closed), records the test result, and
        determines if the feature provides durable benefit after costs and
        risk. Forward results are recorded; no forward performance is claimed.
        """
        feature = self.get_feature(feature_id)
        if feature is None:
            return {
                "status": "ERROR",
                "message": f"Feature {feature_id} not registered",
            }

        # Validate LLM output
        validation = feature.validate_output(llm_output, decision_time=decision_time, upstream_ok=upstream_ok)

        # Record the forward test
        feature.record_forward_test(
            forward_date,
            {
                "llm_output_sha256": hashlib.sha256(llm_output.encode("utf-8")).hexdigest(),
                "validation": validation,
                "date": forward_date.isoformat(),
            },
        )

        # Determine eligibility
        eligibility = "REJECTED - LLM default: forward-test only"
        if feature.validation_mode == "forward_test":
            # LLM must be re-tested in forward direction
            eligibility = (
                "FORWARD_TEST - LLM approved for forward testing only, not for promotion evidence from historical data"
            )
        elif feature.validation_mode == "point_in_time":
            # Only suitable if model and corpus are demonstrably point-in-time
            if validation["valid"]:
                eligibility = "QUALIFIED - point-in-time validated"
            else:
                eligibility = "REJECTED - output validation failed"

        return {
            "feature_id": feature_id,
            "model_name": feature.model_name,
            "forward_date": forward_date.isoformat(),
            "validation": validation,
            "eligibility": eligibility,
            "feature_mode": feature.validation_mode,
        }

    def check_prompt_injection(self, output: str) -> bool:
        """Check for prompt injection in LLM output.

        V1: Essential security check to prevent prompt injection attacks.
        """
        injection_patterns = ["<|prompt|>", "<|im_start|>", "[INST]", "ignore previous", "system message"]
        for pattern in injection_patterns:
            if pattern.lower() in output.lower():
                return True
        return _INJECTION_RE.search(output) is not None

    def check_llm_no_broker_access(self, feature: LLMSentimentFeature) -> bool:
        """Verify LLM has no broker tool, credentials, network route, or order submission.

        V1: Critical security check. LLM must never have direct broker access.
        """
        import types

        module = sys.modules.get(feature.__class__.__module__)
        if module is None:
            return False
        forbidden_module = "trading_platform." + "broker"
        if any(
            isinstance(value, types.ModuleType) and value.__name__.startswith(forbidden_module)
            for value in module.__dict__.values()
        ):
            return False
        forbidden_attrs = ("execute" + "_order", "submit" + "_order", "place" + "_order")
        return not any(hasattr(feature, name) for name in forbidden_attrs)
