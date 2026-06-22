#!/usr/bin/env python3
"""Validate active LoveEngine documentation entrypoints and path hygiene."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "docs" / "archive"

REQUIRED_CURRENT_DOCS = {
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "docs/README.md",
    "docs/development/integration-guide.md",
    "docs/development/repository-structure.md",
    "docs/specs/love-engine-master-plan.md",
    "docs/specs/love-engine-live-evidence-pilot-spec.md",
    "README.zh-CN.md",
    "docs/api/extension-interfaces.md",
    "docs/api/live-evidence-api.md",
    "docs/development/m3-demo-runbook.md",
    "docs/development/m3-acceptance-report.md",
    "docs/development/m4-skill-supervision-and-next-stage-gaps.md",
    "docs/api/README.md",
    "docs/api/loveengine-contract-api.md",
    "docs/api/agent-skill-api.md",
    "docs/kb/source-inventory.md",
}

FORBIDDEN_ACTIVE_TEXT = {
    "docs/specs/love-engine-skill-spec.md",
    "docs/specs/love-engine-next-phase-spec.md",
    "LoveEngine" + "Skill/",
    "D:" + "\\zWenbo\\AI\\DAism",
}


def fail(messages: list[str]) -> None:
    for message in messages:
        print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def active_markdown_files() -> list[Path]:
    files = [
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        ROOT / "CONTRIBUTING.md",
    ]
    files.extend((ROOT / "docs").rglob("*.md"))
    return sorted(path for path in files if ARCHIVE not in path.parents)


def main() -> None:
    errors: list[str] = []
    for rel_path in sorted(REQUIRED_CURRENT_DOCS):
        if not (ROOT / rel_path).is_file():
            errors.append(f"missing current document: {rel_path}")

    for path in active_markdown_files():
        text = path.read_text(encoding="utf-8")
        rel_path = path.relative_to(ROOT).as_posix()
        for forbidden in FORBIDDEN_ACTIVE_TEXT:
            if forbidden in text:
                errors.append(f"stale path in {rel_path}: {forbidden}")

    if errors:
        fail(errors)

    print(
        "LoveEngine documentation validation passed "
        f"({len(active_markdown_files())} active Markdown files)"
    )


if __name__ == "__main__":
    main()
