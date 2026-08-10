"""RPC endpoint loading and validation without leaking credential-bearing URLs."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import SplitResult, urlsplit

from .errors import LoveEngineError
from .secrets import read_restricted_text_file


SEPOLIA_CHAIN_ID = "11155111"
LOCAL_ANVIL_CHAIN_ID = "31337"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _parsed_endpoint(value: str, *, chain_id: str, label: str) -> SplitResult:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except (TypeError, ValueError) as exc:
        raise LoveEngineError("rpc_endpoint_invalid", label) from exc
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise LoveEngineError("rpc_endpoint_invalid", label)
    if str(chain_id) == SEPOLIA_CHAIN_ID:
        if parsed.scheme != "https":
            raise LoveEngineError("rpc_endpoint_invalid", f"{label}:https required")
    elif str(chain_id) == LOCAL_ANVIL_CHAIN_ID:
        if (
            parsed.scheme != "http"
            or parsed.hostname not in LOOPBACK_HOSTS
            or parsed.query
        ):
            raise LoveEngineError("rpc_endpoint_invalid", f"{label}:loopback required")
    elif parsed.scheme not in {"http", "https"}:
        raise LoveEngineError("rpc_endpoint_invalid", label)
    return parsed


def resolve_rpc_endpoint(
    *,
    direct_url: str | None,
    url_file: Path | None,
    chain_id: str,
    label: str,
    required: bool = False,
) -> str | None:
    if direct_url and url_file:
        raise LoveEngineError(
            "rpc_endpoint_ambiguous", f"{label} URL and file are mutually exclusive"
        )
    if str(chain_id) == SEPOLIA_CHAIN_ID and direct_url:
        raise LoveEngineError(
            "rpc_url_file_required",
            f"Sepolia {label} RPC must be loaded from a restricted file",
        )
    value = (
        read_restricted_text_file(url_file, label=f"{label}_rpc_url")
        if url_file is not None
        else direct_url
    )
    if required and not value:
        raise LoveEngineError("rpc_endpoint_required", label)
    if value:
        _parsed_endpoint(value, chain_id=str(chain_id), label=label)
    return value


def validate_rpc_pair(
    primary: str | None,
    secondary: str | None,
    *,
    chain_id: str,
    require_secondary: bool,
) -> None:
    if not primary:
        raise LoveEngineError("rpc_endpoint_required", "primary")
    first = _parsed_endpoint(primary, chain_id=str(chain_id), label="primary")
    if require_secondary and not secondary:
        raise LoveEngineError("two_rpc_endpoints_required", "secondary")
    if not secondary:
        return
    second = _parsed_endpoint(secondary, chain_id=str(chain_id), label="secondary")
    if str(chain_id) == SEPOLIA_CHAIN_ID:
        if first.hostname.casefold() == second.hostname.casefold():
            raise LoveEngineError(
                "distinct_rpc_origins_required",
                "Sepolia RPC endpoints must use different hostnames",
            )
    elif primary == secondary:
        raise LoveEngineError(
            "distinct_rpc_origins_required", "RPC endpoints must differ"
        )
