"""Reject secret-bearing protocol inputs before they enter Agent workflows."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .errors import LoveEngineError


FORBIDDEN_KEYS = {
    "privatekey",
    "mnemonic",
    "keystore",
    "authtoken",
    "accesstoken",
    "writetoken",
    "operatortoken",
    "pilottoken",
    "bearertoken",
}


def read_restricted_text_file(
    path: Path,
    *,
    label: str,
    minimum_length: int = 1,
    maximum_length: int = 8192,
) -> str:
    """Read one bounded secret-bearing value without returning it in errors."""

    path = Path(path).resolve()
    try:
        if path.is_symlink() or not path.is_file():
            raise OSError("not a regular file")
        value = path.read_text(encoding="utf-8").strip()
        mode = path.stat().st_mode
    except (OSError, UnicodeError) as exc:
        raise LoveEngineError(
            "restricted_file_unavailable", f"{label}:{exc.__class__.__name__}", 3
        ) from exc
    if not minimum_length <= len(value) <= maximum_length or "\x00" in value:
        raise LoveEngineError("restricted_file_invalid", label)
    if os.name != "nt" and mode & 0o077:
        raise LoveEngineError("restricted_file_permissions", label)
    return value


def reject_secret_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = "".join(
                character
                for character in str(key).casefold()
                if character.isalnum()
            )
            if normalized in FORBIDDEN_KEYS:
                raise LoveEngineError(
                    "forbidden_secret_field",
                    f"{path}.{key} is not allowed",
                )
            reject_secret_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_secret_fields(child, f"{path}[{index}]")
