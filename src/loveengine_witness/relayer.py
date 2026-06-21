"""Relayer validation and dry-run planning."""

from __future__ import annotations

from typing import Any

from .errors import LoveEngineError
from .secrets import reject_secret_fields


def plan_batch(action: str, value: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    reject_secret_fields(value)
    if not dry_run:
        raise LoveEngineError(
            "live_submission_not_configured",
            "use the local-loop adapter for live transactions",
            4,
        )
    signatures = value.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise LoveEngineError("invalid_signature_batch", "signatures must be non-empty")
    call = {
        "batch-register": "batchRegister",
        "batch-vote": "batchVote",
    }[action]
    return {
        "call": call,
        "signature_count": len(signatures),
        "submitted": False,
    }
