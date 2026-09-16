"""Independent state reconciliation primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ReconciliationReport:
    matched: bool
    differences: tuple[str, ...]
    blocks_new_orders: bool


def reconcile_positions(local: Mapping[str, int], broker: Mapping[str, int]) -> ReconciliationReport:
    """Compare independently-derived integer positions and fail closed."""
    symbols = sorted(set(local) | set(broker))
    differences = tuple(
        f"{symbol}: local={local.get(symbol, 0)} broker={broker.get(symbol, 0)}"
        for symbol in symbols
        if local.get(symbol, 0) != broker.get(symbol, 0)
    )
    return ReconciliationReport(not differences, differences, bool(differences))
