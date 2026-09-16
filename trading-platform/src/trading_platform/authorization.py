"""Fail-closed authorization for the disabled live execution boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class AuthorizationRequest:
    environment: str
    account_id: str
    policy_version: str
    reconciled: bool
    critical_incident: bool
    operator_authorized: bool
    independent_confirmation: bool
    capital_limit: float
    position_limit: int
    loss_limit: float
    expires_at: datetime


@dataclass(frozen=True)
class AuthorizationDecision:
    approved: bool
    reason: str


def authorize_live(request: AuthorizationRequest, *, now: datetime | None = None) -> AuthorizationDecision:
    """Evaluate all independent gates; any uncertainty rejects execution."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return AuthorizationDecision(False, "authorization time must be UTC-aware")
    checks = (
        (request.environment == "live", "environment is not live"),
        (
            request.account_id.startswith("LIVE-"),
            "account is not recognized live account",
        ),
        (bool(request.policy_version), "risk policy is not versioned"),
        (request.reconciled, "reconciliation has not succeeded"),
        (not request.critical_incident, "critical incident is unresolved"),
        (request.operator_authorized, "operator authorization is missing"),
        (request.independent_confirmation, "independent confirmation is missing"),
        (request.capital_limit > 0, "capital limit is invalid"),
        (request.position_limit > 0, "position limit is invalid"),
        (request.loss_limit > 0, "loss limit is invalid"),
        (
            request.expires_at.tzinfo is not None and current < request.expires_at,
            "authorization expired",
        ),
    )
    for passed, reason in checks:
        if not passed:
            return AuthorizationDecision(False, reason)
    return AuthorizationDecision(False, "live trading is disabled by permanent platform policy")
