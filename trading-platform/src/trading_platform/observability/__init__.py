"""Operational probes with fail-closed status semantics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class HealthCheck:
    name: str
    healthy: bool
    detail: str


def check_dependency(name: str, probe: Callable[[], bool]) -> HealthCheck:
    """Run a real dependency probe and convert exceptions to unhealthy."""
    try:
        healthy = bool(probe())
    except Exception as exc:  # dependency failures must not enable trading
        return HealthCheck(name, False, f"probe failed: {type(exc).__name__}")
    return HealthCheck(name, healthy, "ok" if healthy else "unhealthy")
