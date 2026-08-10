#!/usr/bin/env python3
"""Fail closed on likely raw secrets in the LoveEngine repository."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "cache",
    "lib",
    "out",
}
# These are transient output roots produced by local validation commands.  They
# are deliberately checked only at repository depth one: source directories
# such as ``src/tmp`` remain part of the release surface and must be scanned.
TRANSIENT_OUTPUT_ROOTS = {".tmp", "tmp", "temp"}
FORBIDDEN_NAMES = {".env", "id_rsa", "id_ed25519", "mnemonic", "keystore"}
FORBIDDEN_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}
MAX_TEXT_BYTES = 5 * 1024 * 1024
PEM_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
PRIVATE_VALUE_RE = re.compile(
    r"(?i)(?:private[_ -]?key|secret[_ -]?key|mnemonic|seed[_ -]?phrase)"
    r"[\"']?\s*[:=]\s*[\"']?(0x[0-9a-f]{64}|[0-9a-f]{64}|(?:[a-z]+\s+){11,23}[a-z]+)"
)
TOKEN_RE = re.compile(
    r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9_-]{32,})"
)


def _files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and not any(part in SKIP_DIRECTORIES for part in path.relative_to(root).parts)
        and not (
            len(path.relative_to(root).parts) > 1
            and path.relative_to(root).parts[0].casefold()
            in TRANSIENT_OUTPUT_ROOTS
        )
    )


def scan_repository(root: Path = ROOT) -> list[str]:
    root = Path(root).resolve()
    findings: list[str] = []
    for path in _files(root):
        relative = path.relative_to(root).as_posix()
        lowered = path.name.lower()
        if (
            lowered in FORBIDDEN_NAMES
            or lowered.startswith(".env.")
            or path.suffix.lower() in FORBIDDEN_SUFFIXES
            or "keystore" in lowered
        ):
            findings.append(f"forbidden secret file: {relative}")
            continue
        if path.stat().st_size > MAX_TEXT_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in (
            ("private key material", PEM_RE),
            ("secret-valued field", PRIVATE_VALUE_RE),
            ("credential token", TOKEN_RE),
        ):
            match = pattern.search(text)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{label}: {relative}:{line}")
    return findings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    findings = scan_repository(args.root)
    if findings:
        for finding in findings:
            print(f"FAIL: {finding}", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({"valid": True, "secret_findings": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
