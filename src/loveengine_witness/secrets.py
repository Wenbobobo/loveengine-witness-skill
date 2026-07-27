"""Reject secret-bearing protocol inputs before they enter Agent workflows."""

from __future__ import annotations

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
