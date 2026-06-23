"""Explicit witness approval using an external RPC signer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hexbytes import HexBytes
from web3 import HTTPProvider, Web3

from .demo import artifact
from .errors import LoveEngineError
from .jsonio import read_json, write_json
from .schema import validate_schema
from .typed_data import build_vote_typed_data


def approve_vote(
    proposal_plan: Path,
    rpc_url: str,
    address: str,
    output: Path | None = None,
) -> dict[str, Any]:
    plan = read_json(proposal_plan)
    validate_schema(plan, "onchain-proposal-plan-v1.schema.json")
    w3 = Web3(HTTPProvider(rpc_url, request_kwargs={"timeout": 3}))
    if not w3.is_connected():
        raise LoveEngineError("rpc_unavailable", rpc_url, 4)
    if str(w3.eth.chain_id) != plan["chain_id"]:
        raise LoveEngineError("wrong_chain_id", str(w3.eth.chain_id), 4)
    signer = Web3.to_checksum_address(address)
    dao = w3.eth.contract(
        address=Web3.to_checksum_address(plan["witness_dao"]),
        abi=artifact("WitnessDAO")["abi"],
    )
    proposal_id = int(plan["proposal_id"])
    if int(dao.functions.activeProposalId().call()) != proposal_id:
        raise LoveEngineError("proposal_id_mismatch", plan["proposal_id"])
    if Web3.to_hex(dao.functions.proposalPayloadHash(proposal_id).call()).lower() != plan[
        "payload_hash"
    ].lower():
        raise LoveEngineError("payload_hash_mismatch", plan["payload_hash"])
    if not dao.functions.registeredWitnesses(signer).call():
        raise LoveEngineError("witness_not_registered", signer)
    nonce = int(dao.functions.voteNonces(signer).call())
    if int(w3.eth.get_block("latest")["timestamp"]) > int(plan["deadline"]):
        raise LoveEngineError("signature_expired", plan["deadline"])
    typed = build_vote_typed_data(
        {
            "chain_id": plan["chain_id"],
            "verifying_contract": dao.address,
            "witness": signer,
            "proposal_id": plan["proposal_id"],
            "support": plan["support"],
            "reason_hash": plan["reason_hash"],
            "payload_hash": plan["payload_hash"],
            "nonce": str(nonce),
            "deadline": plan["deadline"],
        }
    )
    response = w3.provider.make_request(
        "eth_signTypedData_v4",
        [signer, json.dumps(typed, separators=(",", ":"))],
    )
    if "error" in response:
        raise LoveEngineError("signer_error", str(response["error"]), 4)
    signature = HexBytes(response["result"])
    if len(signature) != 65:
        raise LoveEngineError("signer_error", "expected 65-byte signature", 4)
    v = int(signature[64])
    if v < 27:
        v += 27
    result = {
        "schema_version": "loveengine.explicit-vote-approval/1",
        "witness": signer,
        "proposal_id": plan["proposal_id"],
        "support": plan["support"],
        "reason_hash": plan["reason_hash"].lower(),
        "payload_hash": plan["payload_hash"].lower(),
        "nonce": str(nonce),
        "deadline": plan["deadline"],
        "v": v,
        "r": Web3.to_hex(signature[:32]),
        "s": Web3.to_hex(signature[32:64]),
    }
    if output:
        write_json(output, result)
    return result
