"""Offline and RPC-backed verification for public-pilot transcripts."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import to_checksum_address
from web3 import HTTPProvider, Web3

from .canonical import canonical_json_bytes
from .dispute import aggregate_reviews, build_proposal_plan, build_review
from .errors import LoveEngineError
from .hashes import keccak256_hex, sha256_prefixed
from .live_evidence import evidence_bundle_hash
from .live_protocol import ZERO_HASH, verify_live_event
from .m4_network import (
    verify_bootstrap_v2,
    verify_receipt_v2,
    verify_task_v2,
)
from .observation import aggregate_observations, observation_set_hash
from .review_evidence import (
    is_current_review_payload,
    require_verified_review_result,
)
from .schema import validate_schema
from .secrets import reject_secret_fields
from .trust_policy import load_node_trust_policy, validate_node_trust_policy
from .typed_data import build_vote_typed_data


REGISTRY_ABI = [
    {
        "type": "function",
        "name": "getRelease",
        "stateMutability": "view",
        "inputs": [
            {"name": "publisher", "type": "address"},
            {"name": "skillId", "type": "bytes32"},
            {"name": "versionHash", "type": "bytes32"},
        ],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "packageHash", "type": "bytes32"},
                    {"name": "manifestHash", "type": "bytes32"},
                    {"name": "previousVersionHash", "type": "bytes32"},
                    {"name": "replacementVersionHash", "type": "bytes32"},
                    {"name": "status", "type": "uint8"},
                    {"name": "publishedAt", "type": "uint64"},
                ],
            }
        ],
    },
    {
        "type": "function",
        "name": "currentVersion",
        "stateMutability": "view",
        "inputs": [
            {"name": "publisher", "type": "address"},
            {"name": "skillId", "type": "bytes32"},
        ],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
]
PUBLIC_SINK_ABI = [
    {
        "type": "function",
        "name": "getTotalUTO",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    }
]
WITNESS_DAO_ABI = [
    {
        "type": "function",
        "name": "proposalExecuted",
        "stateMutability": "view",
        "inputs": [{"name": "proposalId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "proposalVoteCounts",
        "stateMutability": "view",
        "inputs": [{"name": "proposalId", "type": "uint256"}],
        "outputs": [
            {"name": "totalVotes", "type": "uint256"},
            {"name": "supportVotes", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "registeredWitnesses",
        "stateMutability": "view",
        "inputs": [{"name": "witness", "type": "address"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "proposalPayloadHash",
        "stateMutability": "view",
        "inputs": [{"name": "proposalId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
    {
        "type": "function",
        "name": "hasVoted",
        "stateMutability": "view",
        "inputs": [
            {"name": "proposalId", "type": "uint256"},
            {"name": "witness", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "minValidVotes",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

CONTRACT_POINTERS = {
    "registry": "SkillRegistry",
    "witness_dao": "WitnessDAO",
    "public_sink": "PublicSink",
}
TRANSACTION_ACTIONS = {
    "publishRelease": (
        "SkillRegistry",
        "publishRelease(bytes32,bytes32,bytes32,bytes32,bytes32)",
    ),
    "setCurrentVersion": (
        "SkillRegistry",
        "setCurrentVersion(bytes32,bytes32)",
    ),
    "batchRegister": (
        "WitnessDAO",
        "batchRegister((address,uint256,uint256,uint8,bytes32,bytes32)[])",
    ),
    "scheduleBroadcast": (
        "CorporateSink",
        "scheduleBroadcast(uint256,bytes32)",
    ),
    "proposeUserCount": (
        "WitnessDAO",
        "proposeUserCount(uint256,bytes32)",
    ),
    "batchVote": (
        "WitnessDAO",
        "batchVote((address,uint256,bool,bytes32,bytes32,uint256,uint256,"
        "uint8,bytes32,bytes32)[])",
    ),
}
VOTE_ACCEPTED_TOPIC = Web3.to_hex(
    Web3.keccak(text="VoteAccepted(uint256,address,bool,bytes32)")
).lower()

TrustPolicyInput = dict[str, Any] | str | Path


def pilot_transcript_hash(value: dict[str, Any]) -> str:
    view = dict(value)
    view.pop("transcript_hash", None)
    return sha256_prefixed(canonical_json_bytes(view))


def _same(left: Any, right: Any, label: str) -> None:
    if left != right:
        raise LoveEngineError("pilot_cross_reference_mismatch", label)


def _load_trust_policy(value: TrustPolicyInput | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return validate_node_trust_policy(value)
    return load_node_trust_policy(Path(value))


def verify_transcript_trust_policy(
    value: dict[str, Any], policy: dict[str, Any]
) -> None:
    """Bind a transcript to an independently supplied node trust policy."""

    release = value["release_anchor"]
    chain = value["chain"]
    package_hash = policy.get("package_hash", policy.get("expected_package_hash"))
    expected = {
        "chain_id": chain["chain_id"],
        "registry": chain["registry"],
        "publisher": chain["publisher"],
        "skill_id": release["skill_id"],
        "version": release["version"],
        "package_hash": release["package_hash"],
        "manifest_hash": release["manifest_hash"],
    }
    actual = {
        "chain_id": str(policy.get("chain_id", "")),
        "registry": policy.get("registry"),
        "publisher": policy.get("publisher"),
        "skill_id": policy.get("skill_id"),
        "version": policy.get("version"),
        "package_hash": package_hash,
        "manifest_hash": policy.get("manifest_hash"),
    }
    for name, expected_value in expected.items():
        actual_value = actual[name]
        if name in {"registry", "publisher"}:
            try:
                actual_value = to_checksum_address(actual_value)
                expected_value = to_checksum_address(expected_value)
            except (TypeError, ValueError) as exc:
                raise LoveEngineError("trust_policy_mismatch", name) from exc
        elif name in {"package_hash", "manifest_hash"}:
            actual_value = str(actual_value).lower()
            expected_value = str(expected_value).lower()
        if actual_value != expected_value:
            raise LoveEngineError("trust_policy_mismatch", name)

    allowed = policy.get("allowed_issuers")
    if not isinstance(allowed, list) or not allowed:
        raise LoveEngineError("trust_policy_mismatch", "allowed_issuers")
    try:
        allowed_issuers = {to_checksum_address(item) for item in allowed}
    except (TypeError, ValueError) as exc:
        raise LoveEngineError("trust_policy_mismatch", "allowed_issuers") from exc
    for task in value["network_tasks"]:
        issuer = to_checksum_address(task["issuer"])
        if issuer not in allowed_issuers:
            raise LoveEngineError("untrusted_issuer", issuer)


def _verify_event_chain(value: dict[str, Any], *, require_events: bool) -> None:
    events = value["events"]
    if require_events and not events:
        raise LoveEngineError("pilot_cross_reference_mismatch", "events empty")
    previous = ZERO_HASH
    for expected, event in enumerate(events, start=1):
        if int(event["sequence"]) != expected or event["previous_event_hash"] != previous:
            raise LoveEngineError("pilot_cross_reference_mismatch", "event chain")
        if not verify_live_event(event):
            raise LoveEngineError("event_hash_mismatch", event["event_id"])
        previous = event["event_hash"]
    if events:
        _same(previous, value["session"]["head_event_hash"], "event/session head")


def _verify_legacy(value: dict[str, Any]) -> dict[str, Any]:
    validate_schema(value, "pilot-transcript-v1.schema.json")
    if value["transcript_hash"] != pilot_transcript_hash(value):
        raise LoveEngineError("transcript_hash_mismatch", "PilotTranscriptV1")
    session = value["session"]
    bundle = value["evidence_bundle"]
    observations = value["observation_set"]
    _same(session["session_id"], bundle["session_id"], "session/bundle")
    _same(session["session_id"], observations["session_id"], "session/observations")
    _same(session["head_event_hash"], bundle["head_event_hash"], "session/bundle head")
    _same(
        session["head_event_hash"],
        observations["head_event_hash"],
        "session/observation head",
    )
    _same(bundle["bundle_hash"], observations["bundle_hash"], "bundle/observation hash")
    if bundle.get("schema_version") == "loveengine.evidence-bundle/2":
        _same(bundle["bundle_hash"], evidence_bundle_hash(bundle), "bundle hash")
    if observations.get("schema_version") == "loveengine.observation-set/1":
        _same(
            observations["observation_set_hash"],
            observation_set_hash(observations),
            "observation set hash",
        )
    _verify_event_chain(value, require_events=False)
    proposal = value["proposal"]
    for approval in value["vote_approvals"]:
        _same(approval["proposal_id"], proposal["proposal_id"], "vote proposal")
        _same(approval["payload_hash"], proposal["payload_hash"], "vote payload")
    if not value["proposal_gate"].get("ready"):
        raise LoveEngineError("pilot_cross_reference_mismatch", "gate not ready")
    if not value["final_state"].get("proposal_executed"):
        raise LoveEngineError("pilot_cross_reference_mismatch", "proposal not executed")
    return {
        "valid": True,
        "verification_level": "legacy_consistency",
        "chain_verified": False,
        "trust_bound": False,
        "run_id": value["run_id"],
        "event_count": len(value["events"]),
        "observation_receipts": len(observations.get("receipts", [])),
        "vote_approvals": len(value["vote_approvals"]),
        "total_uto": value["final_state"]["total_uto"],
    }


def _verify_release_anchor(value: dict[str, Any]) -> None:
    release = value["release_anchor"]
    package = value["package"]
    chain = value["chain"]
    validate_schema(release, "skill-release-v1.schema.json")
    _same(release["chain_id"], chain["chain_id"], "release/chain id")
    _same(
        to_checksum_address(release["registry"]),
        to_checksum_address(chain["registry"]),
        "release/registry",
    )
    _same(
        to_checksum_address(release["publisher"]),
        to_checksum_address(chain["publisher"]),
        "release/publisher",
    )
    _same(release["version"], value["version"], "release/version")
    _same(
        release["version_hash"].lower(),
        keccak256_hex(value["version"].encode("utf-8")),
        "release/version hash",
    )
    _same(release["package_hash"].lower(), package["archive_keccak256"].lower(), "release/package")
    _same(release["manifest_hash"].lower(), package["manifest_hash"].lower(), "release/manifest")
    if release["status"] != "active":
        raise LoveEngineError("release_not_active", release["status"])


def _bootstrap_members(value: dict[str, Any]) -> dict[str, set[str]]:
    chain = value["chain"]
    bootstrap = value["bootstrap"]
    verify_bootstrap_v2(
        bootstrap,
        chain["chain_id"],
        chain["registry"],
        now=int(value["verified_at"]),
    )
    _same(
        to_checksum_address(bootstrap["publisher"]),
        to_checksum_address(chain["publisher"]),
        "bootstrap/publisher",
    )
    return {
        to_checksum_address(item["profile"]["node"]): set(
            item["profile"]["capabilities"]
        )
        for item in bootstrap["directory"]
    }


def _verify_tasks(
    value: dict[str, Any],
    members: dict[str, set[str]],
    allowed_issuers: list[str] | tuple[str, ...] | None = None,
) -> dict[str, dict[str, Any]]:
    chain = value["chain"]
    expected_manifest = value["release_anchor"]["manifest_hash"]
    trusted_issuers = (
        {to_checksum_address(item) for item in allowed_issuers}
        if allowed_issuers is not None
        else {to_checksum_address(chain["publisher"])}
    )
    tasks: dict[str, dict[str, Any]] = {}
    for task in value["network_tasks"]:
        task_id = task.get("task_id")
        if not task_id or task_id in tasks:
            raise LoveEngineError("duplicate_task", str(task_id))
        recipient = to_checksum_address(task["recipient"])
        if recipient not in members:
            raise LoveEngineError("unexpected_recipient", recipient)
        if task["task_type"] not in members[recipient]:
            raise LoveEngineError("unsupported_capability", task["task_type"])
        issuer = to_checksum_address(task["issuer"])
        if issuer not in trusted_issuers:
            raise LoveEngineError("untrusted_issuer", issuer)
        verify_task_v2(
            task,
            expected_chain_id=chain["chain_id"],
            expected_registry=chain["registry"],
            expected_recipient=recipient,
            expected_issuer=issuer,
            expected_manifest_hash=expected_manifest,
            now=int(value["verified_at"]),
        )
        tasks[task_id] = task
    return tasks


def _verify_receipts(
    receipts: list[dict[str, Any]],
    tasks: dict[str, dict[str, Any]],
    value: dict[str, Any],
    expected_type: str,
    *,
    allow_legacy_review_payload: bool = False,
) -> None:
    seen: set[str] = set()
    for receipt in receipts:
        task_id = receipt.get("task_id")
        if task_id in seen or task_id not in tasks:
            raise LoveEngineError("receipt_task_mismatch", str(task_id))
        task = tasks[task_id]
        if task["task_type"] != expected_type:
            raise LoveEngineError("receipt_task_mismatch", task_id)
        signer = verify_receipt_v2(
            receipt,
            value["chain"]["chain_id"],
            value["chain"]["registry"],
        )
        _same(
            to_checksum_address(signer),
            to_checksum_address(task["recipient"]),
            "receipt/node",
        )
        _same(receipt["nonce"], task["nonce"], "receipt/nonce")
        if receipt["status"] != "completed" or int(receipt["completed_at"]) > int(
            task["deadline"]
        ):
            raise LoveEngineError("receipt_task_mismatch", task_id)
        if expected_type == "observe_live_text":
            validate_schema(
                receipt["result"], "live-observation-receipt-v1.schema.json"
            )
            _same(
                receipt["result"]["session_id"],
                task["payload"].get("session_id"),
                "observation receipt/task session",
            )
        elif expected_type == "review_dispute":
            result = receipt["result"]
            payload = task["payload"]
            if not allow_legacy_review_payload:
                require_verified_review_result(
                    payload, result, task_id=task_id
                )
            for field in ("dispute_id", "bundle_hash"):
                _same(
                    result.get(field),
                    payload.get(field),
                    f"review receipt/task {field}",
                )
        seen.add(task_id)


def _verify_artifacts(value: dict[str, Any]) -> None:
    inventory: dict[str, int] = {}
    for artifact in value["artifacts"]:
        digest = artifact["artifact_hash"]
        if digest in inventory:
            raise LoveEngineError("duplicate_artifact", digest)
        inventory[digest] = int(artifact["size"])
    expected: dict[str, int] = {}
    for event in value["events"]:
        raw = event["content"].encode("utf-8")
        digest = sha256_prefixed(raw)
        _same(digest, event["artifact_hash"], "event/artifact hash")
        expected[digest] = len(raw)
    _same(inventory, expected, "artifact inventory")


def _verify_dispute_and_gate(
    value: dict[str, Any],
    members: dict[str, set[str]],
    tasks: dict[str, dict[str, Any]],
) -> None:
    receipts = value["review_receipts"]
    if len(receipts) != 3:
        raise LoveEngineError("review_quorum_missing", "three receipts required")
    expected_nodes = {
        node for node, capabilities in members.items() if "review_dispute" in capabilities
    }
    reviews = []
    for receipt in receipts:
        task = tasks[receipt["task_id"]]
        _same(
            task["payload"].get("dispute_id"),
            value["dispute"]["dispute_id"],
            "review task/dispute",
        )
        _same(
            task["payload"].get("bundle_hash"),
            value["dispute"]["bundle_hash"],
            "review task/dispute bundle",
        )
        _same(
            receipt["result"].get("dispute_id"),
            value["dispute"]["dispute_id"],
            "review receipt/dispute",
        )
        _same(
            receipt["result"].get("bundle_hash"),
            value["evidence_bundle"]["bundle_hash"],
            "review receipt/evidence bundle",
        )
        reviews.append(
            build_review(
                receipt["task_id"],
                value["dispute"],
                receipt["node"],
                receipt["result"]["verdict"],
                receipt["result"]["reason_hash"],
                receipt["completed_at"],
                receipt["signature"],
            )
        )
    original = dict(value["dispute"])
    original["status"] = "open"
    original.pop("valid_review_count", None)
    original.pop("review_ids", None)
    resolved = aggregate_reviews(original, reviews, expected_nodes=expected_nodes)
    _same(resolved, value["dispute"], "dispute aggregation")
    gate = build_proposal_plan(
        session=value["session"],
        bundle=value["evidence_bundle"],
        disputes=[value["dispute"]],
        proposal=value["proposal_gate"]["proposal"],
    )
    _same(gate, value["proposal_gate"], "proposal gate")


def verify_witness_evidence_stages(
    value: dict[str, Any],
    *,
    allowed_issuers: list[str] | tuple[str, ...] | None = None,
    allow_legacy_review_payload: bool = False,
) -> tuple[dict[str, set[str]], dict[str, dict[str, Any]]]:
    """Verify the release-to-Gate stages shared by core and governance transcripts."""

    _verify_release_anchor(value)
    members = _bootstrap_members(value)
    tasks = _verify_tasks(value, members, allowed_issuers)
    _verify_receipts(value["observation_receipts"], tasks, value, "observe_live_text")
    _verify_receipts(
        value["review_receipts"],
        tasks,
        value,
        "review_dispute",
        allow_legacy_review_payload=allow_legacy_review_payload,
    )
    _verify_event_chain(value, require_events=True)
    _verify_artifacts(value)

    session = value["session"]
    bundle = value["evidence_bundle"]
    observations = value["observation_set"]
    _same(session["status"], "closed", "session status")
    for event in value["events"]:
        _same(event["session_id"], session["session_id"], "event/session")
    _same(session["session_id"], bundle["session_id"], "session/bundle")
    _same(session["head_event_hash"], bundle["head_event_hash"], "session/bundle head")
    _same(
        str(int(session["next_sequence"]) - 1),
        bundle["event_count"],
        "session/bundle event count",
    )
    _same(str(len(value["events"])), bundle["event_count"], "events/bundle count")
    bundle_events = bundle["events"]
    _same(len(bundle_events), len(value["events"]), "bundle/event inventory count")
    for event, bundled in zip(value["events"], bundle_events, strict=True):
        for field in (
            "event_id",
            "sequence",
            "category",
            "event_hash",
            "artifact_hash",
        ):
            _same(event[field], bundled[field], f"bundle/event {field}")
    _same(bundle["bundle_hash"], evidence_bundle_hash(bundle), "bundle hash")
    aggregated = aggregate_observations(
        value["observation_receipts"],
        expected_nodes={
            to_checksum_address(item["node"])
            for item in value["observation_receipts"]
        },
        expected_chain_id=value["chain"]["chain_id"],
        expected_registry=value["chain"]["registry"],
    )
    _same(aggregated, observations, "observation aggregation")
    _same(
        observations["session_id"],
        session["session_id"],
        "observation/session",
    )
    _same(
        observations["head_event_hash"],
        session["head_event_hash"],
        "observation/session head",
    )
    _same(
        observations["event_count"],
        bundle["event_count"],
        "observation/bundle event count",
    )
    _same(observations["bundle_hash"], bundle["bundle_hash"], "observation/bundle")
    _verify_dispute_and_gate(value, members, tasks)
    return members, tasks


def _required(mapping: dict[str, Any], names: tuple[str, ...], label: str) -> None:
    missing = [name for name in names if name not in mapping]
    if missing:
        raise LoveEngineError("pilot_cross_reference_mismatch", f"{label}: {missing[0]}")


def _verify_votes(value: dict[str, Any]) -> set[str]:
    proposal = value["proposal"]
    chain = value["chain"]
    _required(
        proposal,
        ("proposal_id", "payload_hash", "total_votes", "support_votes"),
        "proposal",
    )
    witnesses = {to_checksum_address(item) for item in chain["witnesses"]}
    recovered: set[str] = set()
    for approval in value["vote_approvals"]:
        _required(
            approval,
            (
                "witness", "proposal_id", "support", "reason_hash", "payload_hash",
                "nonce", "deadline", "v", "r", "s",
            ),
            "vote approval",
        )
        _same(approval["proposal_id"], proposal["proposal_id"], "vote proposal")
        _same(approval["payload_hash"].lower(), proposal["payload_hash"].lower(), "vote payload")
        witness = to_checksum_address(approval["witness"])
        if witness not in witnesses or witness in recovered:
            raise LoveEngineError("invalid_vote_membership", witness)
        if not approval["support"] or int(approval["deadline"]) < int(value["verified_at"]):
            raise LoveEngineError("invalid_vote_approval", witness)
        typed = build_vote_typed_data(
            {
                "chain_id": chain["chain_id"],
                "verifying_contract": chain["witness_dao"],
                "witness": witness,
                "proposal_id": approval["proposal_id"],
                "support": approval["support"],
                "reason_hash": approval["reason_hash"],
                "payload_hash": approval["payload_hash"],
                "nonce": approval["nonce"],
                "deadline": approval["deadline"],
            }
        )
        try:
            signer = Account.recover_message(
                encode_typed_data(full_message=typed),
                vrs=(
                    int(approval["v"]),
                    int(approval["r"], 16),
                    int(approval["s"], 16),
                ),
            )
        except (TypeError, ValueError) as exc:
            raise LoveEngineError("invalid_signature", "vote approval") from exc
        if to_checksum_address(signer) != witness:
            raise LoveEngineError("invalid_signature", "vote approval signer")
        recovered.add(witness)
    minimum = int(chain["min_valid_votes"])
    if len(recovered) < minimum:
        raise LoveEngineError("vote_quorum_missing", f"{len(recovered)} < {minimum}")
    _same(int(proposal["total_votes"]), len(recovered), "proposal total votes")
    _same(int(proposal["support_votes"]), len(recovered), "proposal support votes")
    return recovered


def _verify_transactions_and_code_inventory(value: dict[str, Any]) -> None:
    transactions = value["transaction_receipts"]
    hashes = [item["transaction_hash"].lower() for item in transactions]
    if len(hashes) != len(set(hashes)):
        raise LoveEngineError("duplicate_transaction", "transaction hash")
    for item in transactions:
        if item["action"] not in TRANSACTION_ACTIONS:
            raise LoveEngineError(
                "unsupported_transaction_action", item["action"]
            )
    _same(
        set(value["contract_code_hashes"]),
        set(value["chain"]["contracts"]),
        "contract code inventory",
    )
    contracts = value["chain"]["contracts"]
    for field, contract_name in CONTRACT_POINTERS.items():
        if contract_name not in contracts:
            raise LoveEngineError(
                "pilot_cross_reference_mismatch",
                f"missing {contract_name} contract",
            )
        _same(
            to_checksum_address(value["chain"][field]),
            to_checksum_address(contracts[contract_name]),
            f"chain {field}/{contract_name}",
        )


def _hex(value: Any) -> str:
    return Web3.to_hex(value).lower()


def _verify_transaction_call(
    transaction: Any,
    action: str,
    contracts: dict[str, str],
) -> None:
    """Bind a declared receipt action to the transaction target and selector."""

    try:
        contract_name, signature = TRANSACTION_ACTIONS[action]
        expected_target = contracts[contract_name]
    except KeyError as exc:
        raise LoveEngineError(
            "unsupported_transaction_action", action
        ) from exc
    target = transaction.get("to")
    if target is None:
        raise LoveEngineError(
            "pilot_cross_reference_mismatch", f"{action} transaction target"
        )
    _same(
        to_checksum_address(target),
        to_checksum_address(expected_target),
        f"{action} transaction target",
    )
    raw_input = transaction.get("input", b"")
    call_data = (
        raw_input.lower() if isinstance(raw_input, str) else _hex(raw_input)
    )
    expected_selector = Web3.to_hex(Web3.keccak(text=signature)[:4]).lower()
    if not call_data.startswith(expected_selector):
        raise LoveEngineError(
            "pilot_cross_reference_mismatch", f"{action} transaction input"
        )


def _verify_batch_vote_logs(
    w3: Web3,
    receipts: list[Any],
    value: dict[str, Any],
    vote_signers: set[str],
) -> None:
    expected = {
        to_checksum_address(item["witness"]): item
        for item in value["vote_approvals"]
    }
    recorded: dict[str, tuple[int, bool, str]] = {}
    dao_address = to_checksum_address(value["chain"]["witness_dao"])
    for receipt in receipts:
        for log in receipt["logs"]:
            if to_checksum_address(log["address"]) != dao_address:
                continue
            topics = log["topics"]
            if not topics or _hex(topics[0]) != VOTE_ACCEPTED_TOPIC:
                continue
            if len(topics) != 3:
                raise LoveEngineError(
                    "pilot_cross_reference_mismatch", "VoteAccepted topics"
                )
            proposal_id = int.from_bytes(bytes(topics[1]), "big")
            witness = to_checksum_address(bytes(topics[2])[-20:])
            if witness in recorded:
                raise LoveEngineError("duplicate_vote_event", witness)
            support, reason_hash = w3.codec.decode(
                ["bool", "bytes32"], bytes(log["data"])
            )
            recorded[witness] = (proposal_id, bool(support), _hex(reason_hash))

    if set(recorded) != vote_signers or set(expected) != vote_signers:
        raise LoveEngineError(
            "pilot_cross_reference_mismatch", "VoteAccepted witnesses"
        )
    for witness, approval in expected.items():
        proposal_id, support, reason_hash = recorded[witness]
        _same(proposal_id, int(approval["proposal_id"]), "VoteAccepted proposal")
        _same(support, bool(approval["support"]), "VoteAccepted support")
        _same(
            reason_hash,
            approval["reason_hash"].lower(),
            "VoteAccepted reason hash",
        )


def verify_release_anchor_rpc(
    value: dict[str, Any], rpc_url: str
) -> tuple[Web3, int]:
    """Verify the release at the transcript's immutable historical block."""

    chain = value["chain"]
    release = value["release_anchor"]
    w3 = Web3(HTTPProvider(rpc_url, request_kwargs={"timeout": 5}))
    try:
        if not w3.is_connected():
            raise LoveEngineError("rpc_unavailable", rpc_url, 4)
        _same(str(w3.eth.chain_id), chain["chain_id"], "RPC chain id")
        anchor = value["verification_anchor"]
        anchor_number = int(anchor["block_number"])
        block = w3.eth.get_block(anchor_number)
        _same(_hex(block["hash"]), anchor["block_hash"].lower(), "anchor block hash")
        _same(str(block["timestamp"]), anchor["timestamp"], "anchor timestamp")
        _same(value["verified_at"], anchor["timestamp"], "verified_at/anchor")
        registry = w3.eth.contract(
            address=to_checksum_address(chain["registry"]), abi=REGISTRY_ABI
        )
        skill_hash = Web3.keccak(text=release["skill_id"])
        version_hash = bytes.fromhex(release["version_hash"][2:])
        onchain = registry.functions.getRelease(
            to_checksum_address(chain["publisher"]), skill_hash, version_hash
        ).call(block_identifier=anchor_number)
        _same(_hex(onchain[0]), release["package_hash"].lower(), "Registry package hash")
        _same(_hex(onchain[1]), release["manifest_hash"].lower(), "Registry manifest hash")
        _same(int(onchain[4]), 1, "Registry release status")
        current = registry.functions.currentVersion(
            to_checksum_address(chain["publisher"]), skill_hash
        ).call(block_identifier=anchor_number)
        _same(_hex(current), release["version_hash"].lower(), "Registry current version")
        return w3, anchor_number
    except LoveEngineError:
        raise
    except Exception as exc:
        raise LoveEngineError("chain_verification_failed", str(exc), 4) from exc


def _verify_rpc(value: dict[str, Any], rpc_url: str, vote_signers: set[str]) -> None:
    chain = value["chain"]
    try:
        w3, anchor_number = verify_release_anchor_rpc(value, rpc_url)

        batch_vote_receipts = []
        for item in value["transaction_receipts"]:
            receipt = w3.eth.get_transaction_receipt(item["transaction_hash"])
            _same(str(receipt.status), item["status"], "transaction status")
            _same(str(receipt.blockNumber), item["block_number"], "transaction block")
            _same(_hex(receipt.blockHash), item["block_hash"].lower(), "transaction block hash")
            if int(receipt.blockNumber) > anchor_number:
                raise LoveEngineError(
                    "pilot_cross_reference_mismatch", "transaction after anchor"
                )
            w3.eth.get_block(receipt.blockHash)
            _verify_transaction_call(
                w3.eth.get_transaction(item["transaction_hash"]),
                item["action"],
                chain["contracts"],
            )
            if item["action"] == "batchVote":
                batch_vote_receipts.append(receipt)

        _verify_batch_vote_logs(w3, batch_vote_receipts, value, vote_signers)

        for name, address in chain["contracts"].items():
            actual = Web3.to_hex(
                Web3.keccak(
                    w3.eth.get_code(
                        to_checksum_address(address),
                        block_identifier=anchor_number,
                    )
                )
            )
            _same(actual.lower(), value["contract_code_hashes"][name].lower(), f"{name} code hash")

        proposal_id = int(value["proposal"]["proposal_id"])
        dao = w3.eth.contract(
            address=to_checksum_address(chain["witness_dao"]), abi=WITNESS_DAO_ABI
        )
        _same(
            bool(
                dao.functions.proposalExecuted(proposal_id).call(
                    block_identifier=anchor_number
                )
            ),
            True,
            "proposal executed",
        )
        counts = dao.functions.proposalVoteCounts(proposal_id).call(
            block_identifier=anchor_number
        )
        _same(int(counts[0]), int(value["proposal"]["total_votes"]), "onchain total votes")
        _same(int(counts[1]), int(value["proposal"]["support_votes"]), "onchain support votes")
        payload_hash = dao.functions.proposalPayloadHash(proposal_id).call(
            block_identifier=anchor_number
        )
        _same(
            _hex(payload_hash),
            value["proposal"]["payload_hash"].lower(),
            "onchain proposal payload hash",
        )
        _same(
            int(
                dao.functions.minValidVotes().call(
                    block_identifier=anchor_number
                )
            ),
            int(chain["min_valid_votes"]),
            "onchain minimum valid votes",
        )
        for witness in vote_signers:
            if not dao.functions.registeredWitnesses(witness).call(
                block_identifier=anchor_number
            ):
                raise LoveEngineError("witness_not_registered", witness)
            if not dao.functions.hasVoted(proposal_id, witness).call(
                block_identifier=anchor_number
            ):
                raise LoveEngineError("witness_vote_not_recorded", witness)
        sink = w3.eth.contract(
            address=to_checksum_address(chain["public_sink"]), abi=PUBLIC_SINK_ABI
        )
        _same(
            str(
                sink.functions.getTotalUTO().call(
                    block_identifier=anchor_number
                )
            ),
            value["final_state"]["total_uto"],
            "PublicSink total UTO",
        )
    except LoveEngineError:
        raise
    except Exception as exc:
        raise LoveEngineError("chain_verification_failed", str(exc), 4) from exc


def _uses_legacy_review_payload(value: dict[str, Any]) -> bool:
    payloads = [
        task.get("payload")
        for task in value["network_tasks"]
        if task.get("task_type") == "review_dispute"
    ]
    if not payloads:
        return False
    current = [is_current_review_payload(payload) for payload in payloads]
    if any(current) and not all(current):
        raise LoveEngineError(
            "mixed_review_evidence_payloads",
            "current and legacy review payloads cannot share a transcript",
        )
    return not all(current)


def _verify_v2(
    value: dict[str, Any],
    rpc_url: str | None,
    trust_policy: TrustPolicyInput | None,
) -> dict[str, Any]:
    validate_schema(value, "pilot-transcript-v2.schema.json")
    if value["transcript_hash"] != pilot_transcript_hash(value):
        raise LoveEngineError("transcript_hash_mismatch", "PilotTranscriptV2")
    legacy_review_payload = _uses_legacy_review_payload(value)
    policy = None if legacy_review_payload else _load_trust_policy(trust_policy)
    verify_witness_evidence_stages(
        value,
        allowed_issuers=(policy["allowed_issuers"] if policy is not None else None),
        allow_legacy_review_payload=legacy_review_payload,
    )
    vote_signers = _verify_votes(value)
    _verify_transactions_and_code_inventory(value)
    if not value["final_state"].get("proposal_executed"):
        raise LoveEngineError("pilot_cross_reference_mismatch", "proposal not executed")
    if legacy_review_payload:
        return {
            "valid": True,
            "verification_level": "legacy_consistency",
            "chain_verified": False,
            "trust_bound": False,
            "run_id": value["run_id"],
            "event_count": len(value["events"]),
            "observation_receipts": len(value["observation_receipts"]),
            "review_receipts": len(value["review_receipts"]),
            "vote_approvals": len(value["vote_approvals"]),
            "total_uto": value["final_state"]["total_uto"],
        }
    if policy is not None:
        verify_transcript_trust_policy(value, policy)
    if rpc_url:
        _verify_rpc(value, rpc_url, vote_signers)
    trust_bound = bool(rpc_url and policy is not None)
    verification_level = (
        "chain_verified"
        if trust_bound
        else "chain_consistency"
        if rpc_url
        else "offline_integrity"
    )
    return {
        "valid": True,
        "verification_level": verification_level,
        "chain_verified": bool(rpc_url),
        "trust_bound": trust_bound,
        "run_id": value["run_id"],
        "event_count": len(value["events"]),
        "observation_receipts": len(value["observation_receipts"]),
        "review_receipts": len(value["review_receipts"]),
        "vote_approvals": len(value["vote_approvals"]),
        "total_uto": value["final_state"]["total_uto"],
    }


def verify_pilot_transcript(
    value: dict[str, Any],
    rpc_url: str | None = None,
    trust_policy: TrustPolicyInput | None = None,
) -> dict[str, Any]:
    """Verify a historical V1 transcript or a V2 integrity/chain context."""

    reject_secret_fields(value)
    schema_version = value.get("schema_version")
    if schema_version == "loveengine.pilot-transcript/1":
        return _verify_legacy(value)
    if schema_version == "loveengine.pilot-transcript/2":
        return _verify_v2(value, rpc_url, trust_policy)
    raise LoveEngineError("unsupported_transcript_version", str(schema_version))
