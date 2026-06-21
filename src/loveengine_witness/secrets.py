"""Reject secret-bearing protocol inputs before they enter Agent workflows."""

from __future__ import annotations

from typing import Any

from .errors import LoveEngineError


FORBIDDEN_KEYS = {
    "private_key",
    "privatekey",
    "mnemonic",
    "keystore",
    "auth_token",
    "access_token",
}


def reject_secret_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized in FORBIDDEN_KEYS:
                raise LoveEngineError(
                    "forbidden_secret_field",
                    f"{path}.{key} is not allowed",
                )
            reject_secret_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_secret_fields(child, f"{path}[{index}]")
