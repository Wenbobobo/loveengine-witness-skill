"""Locate the pinned local Foundry toolchain."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .errors import LoveEngineError


FOUNDRY_VERSION = "1.7.1"


def foundry_binary(name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    executable = name + suffix
    configured = os.environ.get("FOUNDRY_BIN")
    candidates = [
        Path(configured) / executable if configured else None,
        Path.home()
        / ".codex"
        / "tools"
        / f"foundry-v{FOUNDRY_VERSION}"
        / executable,
    ]
    discovered = shutil.which(name)
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise LoveEngineError(
        "foundry_not_found",
        f"Foundry {FOUNDRY_VERSION} is required; set FOUNDRY_BIN",
        3,
    )
