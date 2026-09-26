# Strategy Drift

Module: `monitoring/drift.py` (`DriftMonitor`) — **IMPLEMENTED, TESTED, CI-VERIFIED**.

## Model

At promotion, research expectations are frozen (`DriftExpectations`: expectancy, win rate,
Sharpe, profit factor, max DD, typical frequency). Forward (shadow/paper) trades are compared
against them.

States: `HEALTHY -> WATCH -> DEGRADED -> DISABLED` (monotone in severity; WATCH allows new
positions with alerting, DEGRADED/DISABLED block **new** positions via the kill-switch gate
`strategy_drift_disabled`; exits are never blocked).

Escalation triggers (versioned `DriftPolicy`, policy choices):
- forward expectancy ratio vs research (watch < 0.5, degraded <= 0)
- absolute win-rate drop (watch >= 10pp, degraded >= 20pp)
- severe consecutive-loss streaks (count threshold)
- operator manual disable; unregistered strategy = DISABLED (fail closed)

Early-life rule: below `min_forward_trades` only severe signals escalate — no knee-jerk
disables on tiny samples.

## Non-negotiable

The system never retunes itself against recent losses. Any retune is a **new strategy
version** that must pass the full research → validation → promotion path.
