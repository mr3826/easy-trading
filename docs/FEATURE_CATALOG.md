# Feature Catalog

Engine: `trading_platform.features.indicators` — **IMPLEMENTED, TESTED, CI-VERIFIED**.

## Guarantees

- Every feature at row `t` depends only on bars with index `<= t` (verified by
  `assert_point_in_time`, which recomputes each indicator on prefixes and requires
  exact equality at the cut).
- Warmup rows are NaN; NaN means *unavailable*, never forward-filled.
- Indicators marked *shifted* (donchian, relative_volume, volume_zscore) exclude the
  current bar from their reference window by construction.
- Each feature has a `FeatureSpec` (name, params, lookback, source columns, version)
  with a content fingerprint for research provenance.

## Registered features

| Name | Params (default) | Notes |
|---|---|---|
| `sma` | window=20 | close |
| `ema` | window=20 | adjust=False, warmup masked |
| `rsi` | window=14 | Wilder smoothing; 100 when avg loss = 0 |
| `atr` | window=14 | Wilder |
| `normalized_atr` | window=14 | ATR/close |
| `realized_volatility` | window=20 | annualized std of simple returns |
| `return` / `roc` | window=20 / 12 | trailing simple return |
| `rolling_high` / `rolling_low` | window=20 | |
| `donchian_upper` / `donchian_lower` | window=20 | excludes current bar |
| `volume_sma` | window=20 | |
| `relative_volume` | window=20 | vs **prior** window mean |
| `volume_zscore` | window=60 | vs **prior** window |
| `distance_from_ma` | window=50 | (close-SMA)/SMA |
| `rolling_drawdown` | window=252 | from rolling peak |
| `relative_strength` | window=63 | symbol minus benchmark return (needs benchmark) |
| `rolling_beta` | window=63 | needs benchmark |
| `rolling_correlation` | window=63 | needs benchmark |
| `gap_pct` | — | open/prior close - 1 |
| `price_percentile` | window=252 | |
| `volatility_percentile` | window=20, rank_window=252 | |
| `distance_from_52w_high` | window=252 | |

## Non-goals

- No revised/corrected history may retroactively change a feature value (tested).
- No data not in the point-in-time store may feed features. Benchmark-relative
  features require a benchmark series *aligned in length*; misalignment raises.
