# Security Model

See `threat-model.md` for the full model. Summary of enforced boundaries:

- **Live trading**: NOT AUTHORIZED. `config.load_config` force-resets enablement attempts;
  `authorization.authorize_live` rejects unconditionally even when every gate is passed.
  No CLI/env/fallback path can bypass this (tested in `test_safety_boundaries.py`).
- **Credentials**: broker/alert credentials come from the environment only; never logged
  (`redact_secrets`), never committed (gitleaks in CI).
- **LLM/news inputs**: strictly validated schema + injection rejection
  (`features.__init__.parse_news_feature`, `ml_ranking.LLMSentimentFeature`); these inputs
  can rank nothing and authorize nothing.
- **Backups**: encrypted at rest (`backup.py`), checksum-verified.
- **Dependency hygiene**: pip-audit in CI.
