"""Outbound-only M4 review Agent process backed by an RPC signer."""

from __future__ import annotations

import argparse
from pathlib import Path

from .network_node import connect_node


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--rpc-url", required=True)
    parser.add_argument("--address", required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--expected-issuer", required=True)
    parser.add_argument("--expected-manifest-hash", required=True)
    parser.add_argument("--expected-tasks", type=int, required=True)
    parser.add_argument("--evidence-origin", required=True)
    parser.add_argument("--verdicts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    connect_node(
        url=args.url,
        rpc_url=args.rpc_url,
        address=args.address,
        profile_path=args.profile,
        expected_tasks=args.expected_tasks,
        expected_issuer=args.expected_issuer,
        expected_manifest_hash=args.expected_manifest_hash,
        cursor_database=None,
        verdicts_path=args.verdicts,
        output=args.output,
        allowed_http_origin=args.evidence_origin,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
