"""Canonical JSON serialization used by LoveEngine protocol hashes."""

from __future__ import annotations

import json
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a JSON-compatible value using LoveEngine canonical JSON v1."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
