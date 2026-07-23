#!/usr/bin/env python3
"""Validate active LoveEngine documentation entrypoints and path hygiene."""

from __future__ import annotations

import sys
import re
import shlex
import tomllib
from pathlib import Path

from loveengine_witness.cli import build_parser
from loveengine_witness.jsonio import read_json
from loveengine_witness.release_identity import PROJECT_VERSION, SKILL_VERSION


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "docs" / "archive"

REQUIRED_CURRENT_DOCS = {
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "QA.md",
    "docs/README.md",
    "docs/architecture/witness-core-and-data-flow.zh-CN.md",
    "docs/development/integration-guide.md",
    "docs/development/repository-structure.md",
    "docs/specs/love-engine-master-plan.md",
    "docs/specs/love-engine-witness-core-optimization.md",
    "README.zh-CN.md",
    "docs/api/extension-interfaces.md",
    "docs/api/live-evidence-api.md",
    "docs/api/lan-pilot-api.md",
    "docs/development/contract2-comparison-and-recommendations.md",
    "docs/development/participant-runbook.zh-CN.md",
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
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
INLINE_PATH_RE = re.compile(
    r"`((?:(?:README(?:\.zh-CN)?\.md)|(?:docs|schemas|skills|src|tests|tools|contracts|examples|plugins)/[^`\s]+))`"
)
POWERSHELL_BLOCK_RE = re.compile(r"```powershell\s*\n(.*?)```", re.DOTALL)
README_SCREENSHOT_LIMIT = 3
VERSION_STATUS_DOCS = {
    "README.md",
    "README.zh-CN.md",
    "docs/api/cli-reference.md",
    "docs/development/participant-runbook.zh-CN.md",
}
LATEST_TAG = "v0.6.0-contract-public-pilot"


def require_text(
    rel_path: str,
    required: set[str],
    forbidden: set[str],
    errors: list[str],
) -> None:
    text = (ROOT / rel_path).read_text(encoding="utf-8")
    for value in sorted(required):
        if value not in text:
            errors.append(f"required text missing from {rel_path}: {value}")
    for value in sorted(forbidden):
        if value in text:
            errors.append(f"stale text in {rel_path}: {value}")


def validate_interface_docs(errors: list[str]) -> None:
    require_text(
        "docs/api/loveengine-contract-api.md",
        {
            "withdrawRelayerRefund()",
            "RelayerRefundWithdrawn(address indexed relayer,",
            "broadcastAt(uint256 index) external view returns (uint256)",
            "不存在 CertificateBound 事件",
        },
        {
            "withdrawRelayerRefund(address payable recipient)",
            "returns (Broadcast memory)",
        },
        errors,
    )
    require_text(
        "docs/api/live-evidence-api.md",
        {
            "POST | /v1/live/sessions/{sessionId}/evidence/finalize",
            "只读已有 evidence；不存在则 404",
        },
        set(),
        errors,
    )
    qa = (ROOT / "QA.md").read_text(encoding="utf-8")
    if "33:42-51:06" not in qa:
        errors.append("QA.md must disclose the actual meeting excerpt range")
    if qa.count("**状态：") < 10:
        errors.append("QA.md questions must retain explicit status labels")


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


def validate_markdown_links(path: Path, text: str, errors: list[str]) -> None:
    rel_path = path.relative_to(ROOT).as_posix()
    for match in MARKDOWN_LINK_RE.finditer(text):
        target = resolve_markdown_link(path, match.group(1))
        if target is not None and not target.exists():
            errors.append(f"missing Markdown link in {rel_path}: {match.group(1)}")


def validate_inline_paths(path: Path, text: str, errors: list[str]) -> None:
    rel_path = path.relative_to(ROOT).as_posix()
    for raw in INLINE_PATH_RE.findall(text):
        target = raw.rstrip(".,;:").replace("\\", "/")
        if any(character in target for character in "<>*{}"):
            continue
        candidates = [(ROOT / target).resolve(), (path.parent / target).resolve()]
        in_repo = []
        for candidate in candidates:
            try:
                candidate.relative_to(ROOT)
            except ValueError:
                continue
            in_repo.append(candidate)
        if not in_repo:
            errors.append(f"escaping inline path in {rel_path}: {raw}")
            continue
        if not any(candidate.exists() for candidate in in_repo):
            errors.append(f"missing inline path in {rel_path}: {raw}")


def _command_paths() -> dict[tuple[str, ...], set[str]]:
    import argparse

    result: dict[tuple[str, ...], set[str]] = {}

    def visit(parser: argparse.ArgumentParser, prefix: tuple[str, ...]) -> None:
        options = {
            option
            for action in parser._actions
            for option in action.option_strings
            if option.startswith("--")
        }
        result[prefix] = options
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    visit(child, (*prefix, name))

    visit(build_parser(), ())
    return result


def validate_cli_examples(path: Path, text: str, errors: list[str]) -> None:
    rel_path = path.relative_to(ROOT).as_posix()
    command_paths = _command_paths()
    for block in POWERSHELL_BLOCK_RE.findall(text):
        normalized = re.sub(r"`\s*\n", " ", block)
        for line in normalized.splitlines():
            if "loveengine" not in line:
                continue
            try:
                tokens = shlex.split(line.strip(), posix=False)
            except ValueError:
                errors.append(f"unparseable CLI example in {rel_path}: {line.strip()}")
                continue
            try:
                start = tokens.index("loveengine") + 1
            except ValueError:
                continue
            arguments = [token.strip('"\'') for token in tokens[start:]]
            prefix: tuple[str, ...] = ()
            for token in arguments:
                candidate = (*prefix, token)
                if candidate in command_paths:
                    prefix = candidate
                else:
                    break
            if not prefix:
                errors.append(f"unknown loveengine command in {rel_path}: {line.strip()}")
                continue
            valid_options = command_paths[prefix]
            for option in (token for token in arguments if token.startswith("--")):
                name = option.split("=", 1)[0]
                if name not in valid_options:
                    errors.append(
                        f"unknown option for {' '.join(prefix)} in {rel_path}: {name}"
                    )


def validate_version_status(errors: list[str]) -> None:
    manifest = read_json(ROOT / "skills/loveengine-witness/skill-manifest.json")
    if manifest.get("version") != SKILL_VERSION:
        errors.append(
            f"manifest version drift: {manifest.get('version')} != {SKILL_VERSION}"
        )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    if project["project"]["version"] != PROJECT_VERSION:
        errors.append("pyproject version drift")
    for rel_path in VERSION_STATUS_DOCS:
        text = (ROOT / rel_path).read_text(encoding="utf-8")
        if SKILL_VERSION not in text:
            errors.append(f"current version missing from status document: {rel_path}")
        if LATEST_TAG not in text:
            errors.append(f"latest tag missing from status document: {rel_path}")
        if "candidate" not in text.lower():
            errors.append(f"candidate status missing from status document: {rel_path}")


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
        validate_markdown_links(path, text, errors)
        validate_inline_paths(path, text, errors)
        validate_cli_examples(path, text, errors)
        validate_readme_screenshot_count(path, text, errors)

    validate_version_status(errors)
    validate_interface_docs(errors)

    if errors:
        fail(errors)

    print(
        "LoveEngine documentation validation passed "
        f"({len(active_markdown_files())} active Markdown files)"
    )


if __name__ == "__main__":
    main()
