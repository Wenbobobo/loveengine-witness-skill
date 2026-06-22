"""Outbound-only M4 review Agent process backed by an RPC signer."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from web3 import HTTPProvider, Web3

from .jsonio import read_json, write_json
from .relay_server import run_v2_review_client


def _rpc_sign_typed_data(w3: Web3, address: str, typed_data: dict) -> str:
    response = w3.provider.make_request(
        "eth_signTypedData_v4",
        [address, json.dumps(typed_data, separators=(",", ":"))],
    )
    if "error" in response:
        raise RuntimeError(str(response["error"]))
    return str(response["result"])


def _rpc_sign_challenge(w3: Web3, address: str, challenge: str) -> str:
    response = w3.provider.make_request(
        "eth_sign", [address, Web3.to_hex(text=challenge)]
    )
    if "error" in response:
        raise RuntimeError(str(response["error"]))
    return str(response["result"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--address", required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--expected-tasks", type=int, required=True)
    parser.add_argument("--verdicts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    w3 = Web3(HTTPProvider(args.rpc_url))
    result = asyncio.run(
        run_v2_review_client(
            args.url,
            args.address,
            read_json(args.profile),
            args.expected_tasks,
            read_json(args.verdicts),
            lambda challenge: _rpc_sign_challenge(w3, args.address, challenge),
            lambda typed: _rpc_sign_typed_data(w3, args.address, typed),
        )
    )
    write_json(args.output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
