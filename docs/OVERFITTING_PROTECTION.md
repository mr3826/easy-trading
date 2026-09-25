# Overfitting Protection

Module: `trading_platform.validation.statistics` — **IMPLEMENTED, TESTED, CI-VERIFIED**.

## Multiple-testing accounting

- **Every tried configuration is recorded** in the family report (`trial_count` = all
  configurations including failures). Failed experiments are data; nothing is dropped after
  the fact.
- **Deflated Sharpe Ratio** (`deflated_sharpe_ratio`): PSR evaluated against the expected
  maximum Sharpe under the null across `n_trials` configurations (Bailey & Lopez de Prado).
  More trials ⇒ higher bar.
- **Probabilistic Sharpe Ratio** (`probabilistic_sharpe_ratio`): confidence that true Sharpe
  exceeds the benchmark, corrected for return skew/kurtosis. Benchmark for promotion is 0.
- **PBO via CSCV** (`cscv_pbo`): 16-block combinatorially-symmetric cross-validation;
  fraction of IS-best configs landing below the OOS median. PBO >= 0.5 ⇒ coin flip, reject.
- **White reality check** (`white_reality_check`): stationary-bootstrap data-snooping test
  of the best configuration's excess return vs benchmark across the whole family.

## Distributional robustness

- **Stationary bootstrap** (Politis-Romano, deterministic under seed) gives distributions of
  total return, Sharpe, max drawdown, loss streaks, terminal equity — not point estimates.
  IID resampling is deliberately avoided for serially dependent returns.

## Anti-cherry-picking process rules

- Parameters are never optimized iteratively against the promotion report. A retune = a new
  strategy version = a new family trial set.
- Promotion thresholds are pinned in `PromotionPolicy` (versioned + hashed). They are not
  changed to make a specific strategy pass.
- If no strategy passes, the correct output is **NO STRATEGY PROMOTED**.
