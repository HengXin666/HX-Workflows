"""Advisory architecture checks for this existing mixed Python repository."""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "__pycache__", ".codex", ".claude", ".agents", "node_modules", "tests"}
MAX_LINES = 500


def python_files(roots: list[Path]) -> list[Path]:
    return sorted(
        path
        for root in roots
        for path in root.rglob("*.py")
        if not any(part in SKIP_DIRS for part in path.parts)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-output", type=int, default=20)
    parser.add_argument("roots", nargs="+")
    args = parser.parse_args()

    findings: list[str] = []
    files = python_files([Path(value) for value in args.roots])
    for path in files:
        text = path.read_text(encoding="utf-8")
        ast.parse(text, filename=str(path))
        lines = len(text.splitlines())
        if lines > MAX_LINES:
            findings.append(f"{path}: {lines} lines > {MAX_LINES}; split when next modified")

    if findings:
        print(f"WARN architecture baseline has {len(findings)} finding(s):")
        for finding in findings[: args.max_output]:
            print(f"  - {finding}")
    else:
        print(f"PASS architecture advisory ({len(files)} Python files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
