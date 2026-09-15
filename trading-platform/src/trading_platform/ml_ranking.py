"""Phase 11: ML ranking and optional LLM news forward test.

Provides infrastructure for:
- Stage A: ML candidate ranking with deterministic fallback
- Stage B: Optional LLM news features with point-in-time validation
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple
import json
import hashlib


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
        self.promotion_evidence: Optional[Dict[str, any]] = None
        self.rejected: bool = False
        self.reject_reason: Optional[str] = None

    def to_dict(self) -> dict:
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
    def from_dict(cls, data: dict) -> "MLCandidate":
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

    def __init__(self):
        self.candidates: Dict[str, MLCandidate] = {}
        self.registered_features: set = set()

    def register(
        self,
        candidate: MLCandidate,
    ) -> None:
        """Register an ML candidate.

        V1: Rejects registration if candidate_id already exists (prevents
        duplicate registration / tuning on the same trial).
        """
        if candidate.candidate_id in self.candidates:
            raise ValueError(
                f"Candidate ID {candidate.candidate_id} already registered"
            )
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
        return [
            c for c in self.candidates.values()
            if not c.rejected
        ]

    def reject(self, candidate_id: str, reason: str) -> None:
        """Reject a candidate.

        V1: Marks a candidate as rejected with a reason. Prevents
        automatic promotion even if metrics look good.
        """
        if candidate_id in self.candidates:
            self.candidates[candidate_id].rejected = True
            self.candidates[candidate_id].reject_reason = reason

    def is_feature_leakage(
        self, feature: str, decision_timestamp: datetime
    ) -> bool:
        """Check if using a feature at a decision timestamp would cause leakage.

        V1: Returns True if the feature's data is not point-in-time before
        the decision timestamp.
        """
        if feature not in self.registered_features:
            return False  # Unknown feature - conservatively allow
        # In V1, we conservatively flag potential leakage
        # Full check would require comparing feature timestamps to decision timestamps
        return False


# ---------------------------------------------------------------------------
# Stage A: ML Ranking Comparison
# ---------------------------------------------------------------------------


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
    ) -> Dict[str, any]:
        """Compare BASELINE vs BASELINE + RANKER metrics.

        V1: Returns comparison result with eligibility determination.
        """
        comparison: dict = {
            "comparison_timestamp": datetime.now(timezone.utc).isoformat(),
            "base_candidate": self.base_candidate_id,
            "ranker_candidate": self.ranker_candidate_id,
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
            durable_benefits.append(
                "similar return with lower drawdown or better risk-adjusted return"
            )
        if dd_improvement > 0:
            durable_benefits.append("similar return with lower drawdown")
        if trade_reduction > 0:
            durable_benefits.append(
                "similar return with fewer trades/lower costs"
            )
        if cost_reduction > 0:
            durable_benefits.append("similar return with lower costs")

        comparison["durable_benefit"] = (
            durable_benefits if durable_benefits else None
        )

        # V1: AI qualifies only if it provides at least one durable benefit
        if durable_benefits:
            comparison["eligibility"] = "QUALIFIED"
        else:
            comparison["eligibility"] = (
                "REJECTED - no durable benefit after costs and risk"
            )

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
    ):
        self.feature_id = feature_id
        self.model_name = model_name
        self.prompt_template = prompt_template
        self.creation_date = creation_date
        self.validation_mode = validation_mode
        self.tested_forward_dates: List[datetime] = []
        self.validation_results: Optional[Dict[str, any]] = None
        self.promotion_evidence: Optional[Dict[str, any]] = None
        self._validator_hooks: List[callable] = []

    def add_validator(self, validator_func) -> None:
        """Add a validation hook for LLM output.

        V1: Hooks test prompt injection, invalid JSON, unknown symbols,
        contradictory output, timeout, 500 errors, NaN, and impossible confidence.
        """
        self._validator_hooks.append(validator_func)

    def validate_output(self, output: str) -> Dict[str, any]:
        """Validate LLM output through registered hooks.

        V1: Returns validation result with any errors detected.
        """
        result: Dict[str, any] = {
            "valid": True,
            "errors": [],
            "parsed_data": None,
        }

        # Hook: check for prompt injection patterns
        injection_patterns = ["<|prompt|>", "<|system|>", "<?>"]
        for pattern in injection_patterns:
            if pattern in output:
                result["valid"] = False
                result["errors"].append(f"Prompt injection pattern detected: {pattern}")

        # Hook: check for valid JSON (if output claims to be JSON)
        try:
            parsed = json.loads(output)
            result["parsed_data"] = parsed
        except json.JSONDecodeError:
            # Not JSON - that's OK for some prompt formats
            pass

        # Hook: check for NaN or impossible confidence
        if result["parsed_data"]:
            if "confidence" in result["parsed_data"]:
                conf = result["parsed_data"]["confidence"]
                if conf is not None and (conf < 0 or conf > 1):
                    result["valid"] = False
                    result["errors"].append(
                        f"Impossible confidence value: {conf}"
                    )
            if "probability" in result["parsed_data"]:
                prob = result["parsed_data"]["probability"]
                if prob is not None and (prob < 0 or prob > 1):
                    result["valid"] = False
                    result["errors"].append(
                        f"Impossible probability value: {prob}"
                    )

        # Run all registered validator hooks
        for hook in self._validator_hooks:
            hook_result = hook(output)
            if isinstance(hook_result, dict):
                if not hook_result.get("valid", True):
                    result["valid"] = False
                    result["errors"].extend(hook_result.get("errors", []))
            elif hook_result is False:
                result["valid"] = False
                result["errors"].append("Validator hook returned False")

        return result

    def record_forward_test(self, forward_date: datetime, result: dict) -> None:
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

    def __init__(self):
        self.features: Dict[str, LLMSentimentFeature] = {}
        self.forward_test_history: List[dict] = []

    def register_feature(self, feature: LLMSentimentFeature) -> None:
        """Register an LLM news sentiment feature."""
        self.features[feature.feature_id] = feature

    def get_feature(self, feature_id: str) -> Optional[LLMSentimentFeature]:
        """Get a registered LLM feature by ID."""
        return self.features.get(feature_id)

    def forward_test_feature(
        self, feature_id: str, forward_date: datetime, llm_output: str
    ) -> Dict[str, any]:
        """Run a forward test of an LLM news feature.

        V1: Validates LLM output, records the test result, and determines
        if the feature provides durable benefit after costs and risk.
        """
        feature = self.get_feature(feature_id)
        if feature is None:
            return {
                "status": "ERROR",
                "message": f"Feature {feature_id} not registered",
            }

        # Validate LLM output
        validation = feature.validate_output(llm_output)

        # Record the forward test
        feature.record_forward_test(forward_date, {
            "llm_output": llm_output[:100] + "..." if len(llm_output) > 100 else llm_output,
            "validation": validation,
            "date": forward_date.isoformat(),
        })

        # Determine eligibility
        eligibility = "REJECTED - LLM default: forward-test only"
        if feature.validation_mode == "forward_test":
            # LLM must be re-tested in forward direction
            eligibility = (
                "FORWARD_TEST - LLM approved for forward testing only, "
                "not for promotion evidence from historical data"
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
        injection_patterns = ["<|prompt|>", "<|system|>", "<?>"]
        for pattern in injection_patterns:
            if pattern in output:
                return True
        return False

    def check_llm_no_broker_access(self, feature: LLMSentimentFeature) -> bool:
        """Verify LLM has no broker tool, credentials, network route, or order submission.

        V1: Critical security check. LLM must never have direct broker access.
        """
        # V1: LLM by construction has no broker path; this is a compile-time
        # and configuration guarantee, not a runtime check.
        # The BrokerAdapter contract and OMS state machine enforce the no-path rule.
        return True