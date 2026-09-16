# Remaining Execution Plan

## Dependency order

1. Tooling and test discovery: repair `pyproject.toml`, package entry point, parse every retained Python file, and inventory node IDs. Risk: high; acceptance is clean locked install, ruff, format, mypy, collection. Commit boundary: tooling.
2. Domain and simulator: enforce UTC/validation/state invariants and next-event execution. Risk: critical; tests first are property tests and explicit no-lookahead scenarios. Rollback: retain prior simulator behind no production path until tests pass.
3. OMS/risk/reconciliation: hard gates, idempotency, independent state comparison, fail-closed incidents. Risk: critical; acceptance requires falsification tests.
4. PostgreSQL: migrations, repositories, append-only journal, transactional transitions, startup recovery and isolated PostgreSQL integration. Risk: high; migration rollback scripts and clean-room restore are required.
5. Adapter boundaries/shadow/monitoring: unmistakable fake/shadow/IBKR-paper classes, no shadow submission path, independent alerts and dead-man. Risk: critical; external IBKR and forward shadow remain gated.
6. Chaos/backup and ML/AI: dependency mutation, encrypted checksums, deterministic ML registry, strict `FEATURE_REJECTED` AI boundary. Risk: high.
7. Live authorization: multi-factor software gate only, defaults false, independent confirmation and expiry. Risk: critical; status ceiling `NOT_AUTHORIZED`.
8. CI/evidence/review: PostgreSQL service, uv commands, coverage thresholds, secret scan, machine-readable artifacts, complete diff/security/architecture review. Risk: high.

Every workstream must specify tests before implementation, exact changed files, acceptance command, rollback, and evidence. No infrastructure beyond the modular Python/PostgreSQL/Parquet V1 is introduced without a requirement.
