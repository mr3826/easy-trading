"""Import every production module and fail if any import is broken."""

from __future__ import annotations

import importlib
from pathlib import Path

root = Path("trading-platform/src/trading_platform")
modules = [
    "trading_platform." + path.relative_to(root).with_suffix("").as_posix().replace("/", ".")
    for path in root.rglob("*.py")
    if path.name != "__init__.py"
]
for module in sorted(modules):
    importlib.import_module(module)
    print(module)
