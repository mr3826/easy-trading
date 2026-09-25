# Monitoring and Alerting

System monitor: `monitor.py`. Dead man: `dead_man.py`, `chaos_engine.DeadManHeartbeat`.
Alert channels: webhook / Telegram / email / file. Setup: `alert-setup.md` —
**IMPLEMENTED, TESTED** (channels REQUIRES EXTERNAL SETUP for real endpoints).

Tracked: signal and risk-rejection rates, decision latency, data freshness (stale-data
alert), journal lag, DB health probe, storage, heartbeat freshness, exported snapshots.

Feeding safety: heartbeat staleness and data staleness are kill-switch inputs
([KILL_SWITCHES.md](KILL_SWITCHES.md)); strategy-level performance drift has its own monitor
([STRATEGY_DRIFT.md](STRATEGY_DRIFT.md)).
