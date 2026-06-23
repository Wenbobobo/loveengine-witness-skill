"""Outbound-only M5 live observation Agent process."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from web3 import HTTPProvider, Web3

from .jsonio import read_json, write_json
from .relay_server import run_v2_observation_client


def _sign_typed(w3: Web3, address: str, value: dict) -> str:
    response = w3.provider.make_request(
        "eth_signTypedData_v4",
        [address, json.dumps(value, separators=(",", ":"))],
    )
    if "error" in response:
        raise RuntimeError(str(response["error"]))
    return str(response["result"])


def _sign_challenge(w3: Web3, address: str, challenge: str) -> str:
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
    parser.add_argument("--cursor-db", type=Path, required=True)
    parser.add_argument("--expected-tasks", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    w3 = Web3(HTTPProvider(args.rpc_url))
    result = asyncio.run(
        run_v2_observation_client(
            args.url,
            args.address,
            read_json(args.profile),
            args.expected_tasks,
            str(args.cursor_db),
            lambda challenge: _sign_challenge(w3, args.address, challenge),
            lambda value: _sign_typed(w3, args.address, value),
        )
    )
    write_json(args.output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
