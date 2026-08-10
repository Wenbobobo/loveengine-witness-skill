"""Standalone outbound-only Agent node process for the M3 pilot."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

from web3 import HTTPProvider, Web3

from .agent_session import relay_challenge_signing_text, run_agent_session
from .errors import LoveEngineError
from .jsonio import read_json, write_json
from .signer_client import SignerClient


def _rpc_sign_typed_data(
    w3: Web3,
    address: str,
    typed_data: dict[str, Any],
) -> str:
    response = w3.provider.make_request(
        "eth_signTypedData_v4",
        [address, json.dumps(typed_data, separators=(",", ":"))],
    )
    if "error" in response:
        raise LoveEngineError("signer_error", str(response["error"]), 4)
    return str(response["result"])


def _rpc_sign_challenge(
    w3: Web3, address: str, challenge: dict[str, Any]
) -> str:
    signing_text = relay_challenge_signing_text(challenge, node=address)
    response = w3.provider.make_request(
        "eth_sign",
        [address, Web3.to_hex(text=signing_text)],
    )
    if "error" in response:
        raise LoveEngineError("signer_error", str(response["error"]), 4)
    return str(response["result"])


def connect_node(
    *,
    url: str,
    rpc_url: str,
    address: str,
    profile_path: Path,
    expected_tasks: int,
    expected_issuer: str | None,
    expected_manifest_hash: str,
    allowed_issuers: list[str] | tuple[str, ...] | None = None,
    cursor_database: Path | None,
    verdicts_path: Path | None,
    output: Path | None,
    allowed_http_origin: str | None = None,
    reconnect_attempts: int = 0,
    idle_timeout_seconds: float = 30,
    before_connect: Callable[[], None] | None = None,
    signer_client: SignerClient | None = None,
) -> dict[str, Any]:
    if signer_client is not None:
        if Web3.to_checksum_address(signer_client.address) != Web3.to_checksum_address(
            address
        ):
            raise LoveEngineError("wrong_signer_address", signer_client.address)
        sign_challenge = lambda challenge: signer_client.sign_message(
            relay_challenge_signing_text(challenge, node=address)
        )
        sign_typed_data = signer_client.sign_typed_data
    else:
        w3 = Web3(HTTPProvider(rpc_url))
        if not w3.is_connected():
            raise LoveEngineError("rpc_unavailable", rpc_url, 4)
        sign_challenge = lambda challenge: _rpc_sign_challenge(
            w3,
            address,
            challenge,
        )
        sign_typed_data = lambda typed: _rpc_sign_typed_data(w3, address, typed)
    result = asyncio.run(
        run_agent_session(
            url=url,
            node_address=address,
            signed_profile=read_json(profile_path),
            expected_tasks=expected_tasks,
            expected_issuer=expected_issuer,
            allowed_issuers=allowed_issuers,
            expected_manifest_hash=expected_manifest_hash,
            cursor_database=cursor_database,
            verdicts=read_json(verdicts_path) if verdicts_path else None,
            allowed_http_origin=allowed_http_origin,
            reconnect_attempts=reconnect_attempts,
            idle_timeout_seconds=idle_timeout_seconds,
            before_connect=before_connect,
            sign_challenge=sign_challenge,
            sign_typed_data=sign_typed_data,
        )
    )
    if output:
        write_json(output, result)
    return result
