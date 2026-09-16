"""Standalone dead-man heartbeat check independent of the trading process."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def heartbeat_is_fresh(path: Path, max_age_seconds: float, now: datetime | None = None) -> bool:
    """Return false when heartbeat is missing, malformed, or stale."""
    try:
        value = datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    if value.tzinfo is None:
        return False
    current = now or datetime.now(timezone.utc)
    return 0 <= (current - value.astimezone(timezone.utc)).total_seconds() <= max_age_seconds


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="dead-man-monitor")
    parser.add_argument("heartbeat", type=Path)
    parser.add_argument("--max-age-seconds", type=float, default=120.0)
    args = parser.parse_args()
    return 0 if heartbeat_is_fresh(args.heartbeat, args.max_age_seconds) else 1


if __name__ == "__main__":
    raise SystemExit(main())
