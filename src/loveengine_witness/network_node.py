"""Standalone outbound-only Agent node process for the M3 pilot."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from web3 import HTTPProvider, Web3

from .errors import LoveEngineError
from .jsonio import read_json, write_json
from .transport import RelayTransport


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


def _rpc_sign_challenge(w3: Web3, address: str, challenge: str) -> str:
    response = w3.provider.make_request(
        "eth_sign",
        [address, Web3.to_hex(text=challenge)],
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
    output: Path | None,
) -> dict[str, Any]:
    w3 = Web3(HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise LoveEngineError("rpc_unavailable", rpc_url, 4)
    transport = RelayTransport(
        url=url,
        node_address=address,
        signed_profile=read_json(profile_path),
        expected_tasks=expected_tasks,
        sign_challenge=lambda challenge: _rpc_sign_challenge(
            w3,
            address,
            challenge,
        ),
        sign_typed_data=lambda typed: _rpc_sign_typed_data(w3, address, typed),
    )
    result = asyncio.run(transport.connect())
    if output:
        write_json(output, result)
    return result
