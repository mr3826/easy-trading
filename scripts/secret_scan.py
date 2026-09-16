"""Conservative local secret scan for tracked source and configuration."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA|OPENSSH|EC|PGP) PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"][^'\"]{12,}"),
)
IGNORED = {".git", ".venv", "artifacts"}


def main() -> int:
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.splitlines()
    findings: list[str] = []
    for name in files:
        path = Path(name)
        if any(part in IGNORED for part in path.parts) or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in PATTERNS:
            if pattern.search(text):
                findings.append(name)
                break
    if findings:
        print("secret candidates:")
        print("\n".join(sorted(findings)))
        return 1
    print("no high-confidence secret candidates found in tracked files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
