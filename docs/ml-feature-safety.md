# ML Feature Safety

## Fail-closed behavior

- Invalid model or LLM output becomes `FEATURE_REJECTED`; AI failure never
  means "trade anyway".
- `TrainingExample` rejects non-chronological input, naive timestamps,
  non-finite values, and features without per-feature availability
  provenance; a feature whose availability is after the decision timestamp is
  rejected.
- LLM output validation rejects: malformed JSON, NaN/infinity, unknown
  symbols, stale content (`observed_at` parsed and checked against
  `max_content_age_seconds` and the decision time), contradictory output
  (extreme sentiment claimed with near-zero conviction), prompt injection
  (chat-template tokens and structural injection phrasing), validator
  timeouts (`timeout_seconds` per hook), and upstream provider failures.

## Promotion criteria (defined before evaluation)

A candidate is promoted only when ALL of the following hold:

1. At least TWO independent metric conditions from `MLRanker.compare`:
   expectancy delta above the minimum, Sharpe delta above the minimum, or
   drawdown improvement. One improved metric is never sufficient.
2. A non-LLM evidence source: historical LLM results do not authorize
   promotion.
3. The candidate is not already rejected; failing candidates are marked
   rejected by `MLCandidateRegistry.promote` instead of silently promoted.
4. `promotion_eligible` from the training pipeline (validation and test MSE
   within criteria) is necessary but not sufficient — the gate above is the
   enforcement point.

The deterministic baseline ranking always runs first; a trained model ranks
only when the gate approves it.

## Model provenance

Every `ModelArtifact` records: dataset hash (SHA-256 of canonical training
examples), code hash (the full git HEAD SHA supplied by the caller),
feature-schema hash (SHA-256 of the canonical schema with provenance
required), and model hash (SHA-256 of features, weights, bias and training
configuration). The `TrainableRanker` is deterministic gradient descent with
zero initialization — identical inputs produce identical hashes; there is a
deterministic fallback and it is always clearly labeled as fallback.

## Forward-evidence ceiling

`forward_test_feature` records forward dates and validation results with
LLM output SHA-256 provenance. No forward results exist in this repository;
forward operation and any LLM performance claim remain external gates.

## Boundary

ML and LLM modules import no broker modules, touch no credentials, and
expose no order-submission functions. The AI boundary is verified by tests
that assert the absence of broker imports and submission methods.
