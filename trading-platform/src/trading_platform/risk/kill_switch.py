"""Kill-switch coordinator for autonomous paper operation.

Aggregates independent block conditions and answers one question: is NEW
exposure currently permitted?

Semantics (deliberately distinct actions):
- blocked  -> reject NEW entries only. Cancel-open is a separate, explicit
  action. Liquidation is never implied by a block decision.
- Any unknown/unchecked condition fails closed (blocked).

Checks are deliberately conservative: stale market data, missing completed
bars, clock uncertainty, database/broker unavailability, reconciliation
mismatch, unknown broker positions, duplicate-order uncertainty, daily loss
and drawdown breaches, exposure breaches, excessive broker rejects, and
missing strategy approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

KILL_SWITCH_VERSION = "1.0.0"


@dataclass(frozen=True)
class BlockCondition:
    name: str
    blocked: bool
    detail: str = ""


@dataclass(frozen=True)
class BlockDecision:
    allow_new_exposure: bool
    blocked_conditions: Tuple[str, ...]
    checked_conditions: Tuple[str, ...]
    decided_at: str
    version: str = KILL_SWITCH_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allow_new_exposure": self.allow_new_exposure,
            "blocked_conditions": list(self.blocked_conditions),
            "checked_conditions": list(self.checked_conditions),
            "decided_at": self.decided_at,
            "version": self.version,
        }


@dataclass(frozen=True)
class HealthSnapshot:
    """Operator/monitor-supplied health inputs. None means UNKNOWN → block."""

    last_bar_timestamp: Optional[datetime] = None
    expected_bar_complete: Optional[bool] = None
    clock_skew_seconds: Optional[float] = None
    database_available: Optional[bool] = None
    broker_available: Optional[bool] = None
    reconciliation_mismatch: Optional[bool] = None
    unknown_broker_positions: Optional[bool] = None
    duplicate_order_uncertainty: Optional[bool] = None
    daily_pnl: Optional[float] = None
    portfolio_drawdown: Optional[float] = None
    gross_exposure_pct: Optional[float] = None
    broker_reject_count: int = 0
    heartbeat_fresh: Optional[bool] = None
    market_open: Optional[bool] = None


@dataclass(frozen=True)
class KillSwitchPolicy:
    """Thresholds for automatic blocking. POLICY CHOICES."""

    max_data_age_seconds: float = 2 * 24 * 3600  # daily bars
    max_clock_skew_seconds: float = 60.0
    max_daily_loss: float = 1000.0  # absolute currency
    max_portfolio_drawdown: float = 0.20  # 20%
    max_gross_exposure_pct: float = 1.0  # cash account: <= 100%
    max_broker_rejects: int = 3
    require_heartbeat: bool = True
    require_market_open: bool = True
    version: str = KILL_SWITCH_VERSION


class KillSwitchCoordinator:
    """Fail-closed aggregation of block conditions."""

    def __init__(self, policy: Optional[KillSwitchPolicy] = None) -> None:
        self.policy = policy or KillSwitchPolicy()
        self._latched: List[str] = []
        self._latch_reasons: Dict[str, str] = {}

    def latch(self, name: str, reason: str) -> None:
        """Manually latch a blocking condition until explicitly cleared."""
        if name not in self._latched:
            self._latched.append(name)
        self._latch_reasons[name] = reason

    def release(self, name: str) -> bool:
        if name in self._latched:
            self._latched.remove(name)
            self._latch_reasons.pop(name, None)
            return True
        return False

    def latched(self) -> Tuple[str, ...]:
        return tuple(self._latched)

    def evaluate(
        self,
        health: HealthSnapshot,
        *,
        now: Optional[datetime] = None,
        risk_engine_blocked: bool = False,
        strategy_approved: bool = True,
        strategy_drift_disabled: bool = False,
    ) -> BlockDecision:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        p = self.policy
        blocked: List[BlockCondition] = []
        checked: List[str] = []

        def check(name: str, value: Optional[bool], detail_when_blocked: str) -> None:
            """value None (unknown) blocks. value False blocks. True passes."""
            checked.append(name)
            if value is not True:
                detail = "unknown state" if value is None else detail_when_blocked
                blocked.append(BlockCondition(name, True, detail))

        # Data freshness.
        if health.last_bar_timestamp is None:
            check("stale_market_data", None, "")
        else:
            ts = health.last_bar_timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (current - ts).total_seconds()
            check(
                "stale_market_data",
                age <= p.max_data_age_seconds and age >= -p.max_clock_skew_seconds,
                f"last bar age {age:.0f}s exceeds {p.max_data_age_seconds}s",
            )
        check("missing_completed_bar", health.expected_bar_complete, "expected bar missing/incomplete")
        if health.clock_skew_seconds is None:
            check("clock_uncertainty", None, "")
        else:
            skew = abs(health.clock_skew_seconds)
            check("clock_uncertainty", skew <= p.max_clock_skew_seconds, f"clock skew {skew:.1f}s")
        check("database_unavailable", health.database_available, "database unavailable")
        check("broker_unavailable", health.broker_available, "broker unavailable")
        check("reconciliation_mismatch", health.reconciliation_mismatch is False, "reconciliation mismatch")
        check("unknown_broker_position", health.unknown_broker_positions is False, "unknown broker position")
        check(
            "duplicate_order_uncertainty",
            health.duplicate_order_uncertainty is False,
            "duplicate-order uncertainty",
        )
        if health.daily_pnl is None:
            check("daily_loss_breach", None, "")
        else:
            check(
                "daily_loss_breach",
                health.daily_pnl > -p.max_daily_loss,
                f"daily pnl {health.daily_pnl} breaches -{p.max_daily_loss}",
            )
        if health.portfolio_drawdown is None:
            check("drawdown_breach", None, "")
        else:
            dd = abs(min(health.portfolio_drawdown, 0.0))
            check("drawdown_breach", dd < p.max_portfolio_drawdown, f"drawdown {dd:.1%}")
        if health.gross_exposure_pct is None:
            check("exposure_breach", None, "")
        else:
            check(
                "exposure_breach",
                0.0 <= health.gross_exposure_pct <= p.max_gross_exposure_pct,
                f"gross exposure {health.gross_exposure_pct:.1%}",
            )
        check(
            "broker_reject_storm",
            health.broker_reject_count <= p.max_broker_rejects,
            f"{health.broker_reject_count} broker rejects",
        )
        if p.require_heartbeat:
            check("monitoring_heartbeat", health.heartbeat_fresh, "heartbeat stale")
        if p.require_market_open:
            check("market_closed", health.market_open, "market not open")
        if risk_engine_blocked:
            checked.append("risk_engine_block")
            blocked.append(BlockCondition("risk_engine_block", True, "hard risk engine blocks new positions"))
        else:
            checked.append("risk_engine_block")
        if not strategy_approved:
            checked.append("strategy_unapproved")
            blocked.append(BlockCondition("strategy_unapproved", True, "no current APPROVED promotion artifact"))
        else:
            checked.append("strategy_unapproved")
        if strategy_drift_disabled:
            checked.append("strategy_drift_disabled")
            blocked.append(BlockCondition("strategy_drift_disabled", True, "drift monitor DISABLED the strategy"))
        else:
            checked.append("strategy_drift_disabled")
        for name in self.latched():
            checked.append(f"latched:{name}")
            blocked.append(BlockCondition(f"latched:{name}", True, self._latch_reasons.get(name, "")))

        return BlockDecision(
            allow_new_exposure=not blocked,
            blocked_conditions=tuple(b.name for b in blocked),
            checked_conditions=tuple(checked),
            decided_at=current.isoformat(),
        )
