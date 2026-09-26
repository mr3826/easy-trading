"""``trading-platform status`` — where the project actually stands.

Reports the deterministic MVP state (docs/MVP1.md) derived from configuration
and the latest persisted research evidence, and always re-states the permanent
live-trading boundary (including the legacy ``LIVE_TRADING_ENABLED`` /
``LIVE_STATUS`` lines for backward compatibility).
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict

from trading_platform.cli._common import EXIT_OK, env_path, research_output_root
from trading_platform.config import load_config
from trading_platform.promotion import load_approvals
from trading_platform.research.mvp import SUMMARY_JSON, latest_summary, resolve_mvp_state


def build_status_payload(output_root_arg: Any = None) -> Dict[str, Any]:
    output = output_root_arg or research_output_root()
    data = env_path("TRADING_DATA_DIR")
    membership = env_path("TRADING_MEMBERSHIP")
    cfg = load_config()
    state = resolve_mvp_state(output, data, membership)
    summary = latest_summary(output)
    payload: Dict[str, Any] = {
        "mvp_state": state.value,
        "live_trading_enabled": cfg.live_trading_enabled,
        "live_status": cfg.live_status,
        "environment": cfg.environment,
        "research_output": str(output),
        "data_dir": str(data) if data else None,
        "membership": str(membership) if membership else None,
        "latest_run": None,
        "current_policy_approvals": sorted(load_approvals()),
    }
    if summary:
        payload["latest_run"] = {
            "run_id": summary.get("run_id"),
            "data_quality": summary.get("data_quality", {}).get("verdict"),
            "final_verdict": summary.get("final_verdict"),
            "promoted": summary.get("promoted"),
            "families": {f.get("family"): f.get("verdict") for f in summary.get("families", [])},
            "file": str(output / str(summary.get("run_id")) / SUMMARY_JSON),
        }
    return payload


def cmd_status(args: argparse.Namespace) -> int:
    payload = build_status_payload(getattr(args, "output_dir", None))
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
        return EXIT_OK
    print(f"MVP_STATE={payload['mvp_state']}")
    print(f"LIVE_TRADING_ENABLED={'true' if payload['live_trading_enabled'] else 'false'}")
    print(f"LIVE_STATUS={payload['live_status']}")
    print(f"TRADING_ENV={payload['environment']}")
    print(f"research_output={payload['research_output']}")
    print(f"data_dir={payload['data_dir'] or 'NOT CONFIGURED (env TRADING_DATA_DIR or --data-dir)'}")
    print(f"membership={payload['membership'] or 'NOT CONFIGURED (env TRADING_MEMBERSHIP)'}")
    run = payload["latest_run"]
    if run:
        print(f"latest_run={run['run_id']}")
        print(f"latest_run_data_quality={run['data_quality']}")
        for family, verdict in sorted((run["families"] or {}).items()):
            print(f"  {family}: {verdict}")
        print(f"latest_run_verdict={run['final_verdict']}")
    else:
        print("latest_run=NONE (run `trading-platform research run-all ...`)")
    approvals = payload["current_policy_approvals"]
    print(f"approvals_current_policy={', '.join(approvals) if approvals else 'NONE (no strategy promoted)'}")
    print("shadow=REQUIRES_APPROVED_STRATEGY+FORWARD_EVIDENCE paper=REQUIRES_EXTERNAL_SETUP live=NOT_AUTHORIZED")
    return EXIT_OK
