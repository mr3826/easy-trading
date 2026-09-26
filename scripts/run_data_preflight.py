"""Dataset point-in-time preflight: is this universe trustworthy for research?

Backward-compatible wrapper around the shared service used by
``trading-platform data preflight`` — identical output and exit-code contract:
0=PASS, 1=PASS_WITH_WARNINGS, 2=FAIL (do not research), 3=REQUIRES_EXTERNAL_SETUP.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--membership", required=True, type=Path, help="PIT constituent manifest JSON")
    parser.add_argument("--output", default=None, type=Path)
    args = parser.parse_args()

    from trading_platform.cli.data_cmd import cmd_preflight

    return cmd_preflight(args)


if __name__ == "__main__":
    sys.exit(main())
