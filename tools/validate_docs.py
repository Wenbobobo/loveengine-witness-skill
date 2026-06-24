#!/usr/bin/env python3
"""Validate active LoveEngine documentation entrypoints and path hygiene."""

from __future__ import annotations

import sys
import re
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
    "docs/specs/love-engine-contract-public-pilot-spec.md",
    "README.zh-CN.md",
    "docs/api/extension-interfaces.md",
    "docs/api/live-evidence-api.md",
    "docs/api/lan-pilot-api.md",
    "docs/development/m3-demo-runbook.md",
    "docs/development/m3-acceptance-report.md",
    "docs/development/m4-skill-supervision-and-next-stage-gaps.md",
    "docs/development/m5-acceptance-report.md",
    "docs/development/contract2-comparison-and-recommendations.md",
    "docs/development/participant-runbook.zh-CN.md",
    "docs/development/m6-demo-docs-publication-plan.md",
    "docs/development/runbooks/operator-flow.zh-CN.md",
    "docs/development/runbooks/observation-node-flow.zh-CN.md",
    "docs/development/runbooks/voting-witness-flow.zh-CN.md",
    "docs/development/runbooks/public-viewer-flow.zh-CN.md",
    "docs/development/runbooks/publisher-flow.zh-CN.md",
    "docs/articles/loveengine-technical-architecture.zh-CN.md",
    "docs/api/README.md",
    "docs/api/loveengine-contract-api.md",
    "docs/api/agent-skill-api.md",
    "docs/kb/source-inventory.md",
}

FORBIDDEN_ACTIVE_TEXT = {
    "docs/specs/love-engine-skill-spec.md",
    "docs/specs/love-engine-next-phase-spec.md",
    "docs/specs/love-engine-lan-pilot-spec.md",
    "LoveEngine" + "Skill/",
    "D:" + "\\zWenbo\\AI\\DAism",
}

MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
README_SCREENSHOT_LIMIT = 3


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


def resolve_markdown_link(path: Path, raw: str) -> Path | None:
    target = raw.strip()
    if target.startswith(("http://", "https://", "#", "mailto:")):
        return None
    if " " in target and not target.startswith("<"):
        target = target.split()[0]
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split("#", 1)[0]
    if not target:
        return None
    return (path.parent / target).resolve()


def validate_image_links(path: Path, text: str, errors: list[str]) -> None:
    rel_path = path.relative_to(ROOT).as_posix()
    for match in MARKDOWN_IMAGE_RE.finditer(text):
        image_path = resolve_markdown_link(path, match.group(1))
        if image_path is None:
            continue
        if not image_path.is_file():
            errors.append(
                f"missing Markdown image in {rel_path}: {match.group(1)}"
            )
            continue
        if image_path.suffix.lower() != ".png":
            errors.append(f"non-PNG Markdown image in {rel_path}: {match.group(1)}")
        if image_path.stat().st_size <= 1024:
            errors.append(f"empty or tiny Markdown image in {rel_path}: {match.group(1)}")


def validate_readme_screenshot_count(path: Path, text: str, errors: list[str]) -> None:
    if path.name not in {"README.md", "README.zh-CN.md"}:
        return
    count = len(MARKDOWN_IMAGE_RE.findall(text))
    if count > README_SCREENSHOT_LIMIT:
        errors.append(
            f"too many README screenshots in {path.relative_to(ROOT).as_posix()}: {count}"
        )


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
        validate_image_links(path, text, errors)
        validate_readme_screenshot_count(path, text, errors)

    if errors:
        fail(errors)

    print(
        "LoveEngine documentation validation passed "
        f"({len(active_markdown_files())} active Markdown files)"
    )


if __name__ == "__main__":
    main()
