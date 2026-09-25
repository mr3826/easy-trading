# Experiment Registry

Persistence: `persistence/experiment.py` (+ PostgreSQL store) — **IMPLEMENTED, TESTED**.
Schema details: `experiment-registry-schema.md`.

## What every run records

- hypothesis/parameters (including the MA-cross baseline unchanged), strategy version
- dataset hash, universe, code commit, dependency lock hash, policy hash
- cost/slippage assumptions (incl. stress multiplier), seed, per-fold and aggregate metrics
- family ID + **trial count** (every configuration tried, winners and losers alike)

## Research reports

`run_family_research` writes versioned artifacts (`<family>--<UTC>.json|.md`) containing, per
configuration: metrics, PSR/DSR, PBO, bootstrap distributions, walk-forward fold results,
cost stresses, parameter sensitivity, regime breakdown, concentration, promotion result,
rejection reasons, known biases, and evidence ceiling. Family-level: parameter stability and
White reality check. Nothing is written only for winners.

## Rules

- Experiment families are the unit of multiple-testing accounting; trial counts feed DSR.
- Records are immutable; malformed provenance hashes are rejected
  (`MalformedExperimentRecord`).
