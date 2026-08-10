from __future__ import annotations

import copy

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

import loveengine_witness.pilot_transcript as pilot_transcript_module

from loveengine_witness.dispute import (
    aggregate_reviews,
    build_dispute,
    build_proposal_plan,
    build_review,
)
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.hashes import keccak256_hex, sha256_prefixed
from loveengine_witness.live_evidence import evidence_bundle_hash
from loveengine_witness.live_protocol import build_live_event, build_live_session
from loveengine_witness.m4_network import (
    build_bootstrap_v2,
    build_node_profile_v2,
    build_receipt_v2,
    build_task_v2,
)
from loveengine_witness.m4_typed_data import (
    build_bootstrap_v2_typed_data,
    build_node_profile_v2_typed_data,
    build_receipt_v2_typed_data,
    build_task_v2_typed_data,
)
from loveengine_witness.observation import aggregate_observations, observation_set_hash
from loveengine_witness.pilot_transcript import (
    pilot_transcript_hash,
    verify_pilot_transcript,
)
from loveengine_witness.typed_data import build_vote_typed_data


def _fixture() -> dict:
    value = {
        "schema_version": "loveengine.pilot-transcript/1",
        "run_id": "pilot-1",
        "version": "0.5.0-lan-pilot",
        "package": {"archive_keccak256": "0x" + "11" * 32},
        "chain": {
            "chain_id": "31337",
            "registry": "0x" + "12" * 20,
            "witness_dao": "0x" + "13" * 20,
            "public_sink": "0x" + "14" * 20,
        },
        "session": {
            "session_id": "s1",
            "status": "closed",
            "head_event_hash": "0x" + "21" * 32,
        },
        "events": [],
        "observation_set": {
            "session_id": "s1",
            "head_event_hash": "0x" + "21" * 32,
            "bundle_hash": "0x" + "22" * 32,
        },
        "evidence_bundle": {
            "session_id": "s1",
            "head_event_hash": "0x" + "21" * 32,
            "bundle_hash": "0x" + "22" * 32,
        },
        "dispute": {"status": "dismissed"},
        "proposal_gate": {"ready": True},
        "proposal": {
            "proposal_id": "1",
            "payload_hash": "0x" + "31" * 32,
        },
        "vote_approvals": [
            {"proposal_id": "1", "payload_hash": "0x" + "31" * 32}
            for _ in range(5)
        ],
        "transactions": [],
        "final_state": {"proposal_executed": True, "total_uto": "20"},
        "metrics": {},
        "faults": {},
        "snapshots": [],
    }
    value["transcript_hash"] = pilot_transcript_hash(value)
    return value


def test_pilot_transcript_cross_checks_every_phase() -> None:
    value = _fixture()
    result = verify_pilot_transcript(value)
    assert result["valid"] is True
    assert result["verification_level"] == "legacy_consistency"
    assert result["chain_verified"] is False
    assert result["trust_bound"] is False

    tampered = copy.deepcopy(value)
    tampered["vote_approvals"][0]["payload_hash"] = "0x" + "ff" * 32
    tampered["transcript_hash"] = pilot_transcript_hash(tampered)
    with pytest.raises(LoveEngineError) as exc:
        verify_pilot_transcript(tampered)
    assert exc.value.code == "pilot_cross_reference_mismatch"


def _sign(account: object, typed: dict) -> str:
    return "0x" + Account.sign_message(
        encode_typed_data(full_message=typed), account.key
    ).signature.hex()


def _v2_fixture(
    *,
    legacy_review_payload: bool = False,
    evidence_verified: bool | None = True,
) -> dict:
    chain_id = "31337"
    registry = Web3.to_checksum_address("0x" + "12" * 20)
    dao = Web3.to_checksum_address("0x" + "13" * 20)
    sink = Web3.to_checksum_address("0x" + "14" * 20)
    publisher = Account.create()
    nodes = [Account.create() for _ in range(3)]
    witnesses = [Account.create() for _ in range(5)]
    verified_at = "1770000000"
    deadline = "1770001000"
    manifest_hash = "0x" + "41" * 32
    archive_hash = "0x" + "42" * 32

    profiles = []
    for index, node in enumerate(nodes, start=1):
        profile = build_node_profile_v2(
            node.address,
            ["observe_live_text", "review_dispute"],
            str(index),
            deadline,
        )
        signed = {
            "schema_version": "loveengine.signed-agent-node-profile/2",
            "chain_id": chain_id,
            "registry": registry,
            "profile": profile,
            "signature": _sign(
                node,
                build_node_profile_v2_typed_data(chain_id, registry, profile),
            ),
        }
        profiles.append(signed)
    bootstrap = build_bootstrap_v2(
        chain_id=chain_id,
        registry=registry,
        publisher=publisher.address,
        nodes=profiles,
        sequence="1",
        valid_until=deadline,
    )
    bootstrap["signature"] = _sign(
        publisher, build_bootstrap_v2_typed_data(bootstrap)
    )

    content = "verifiable public feedback"
    artifact_hash = sha256_prefixed(content.encode("utf-8"))
    event = build_live_event(
        event_id="event-1",
        session_id="session-1",
        sequence="1",
        occurred_at="1769999901",
        category="source",
        source_type="operator",
        content=content,
        artifact_hash=artifact_hash,
        previous_event_hash="0x" + "00" * 32,
    )
    session = build_live_session("session-1", "operator", "1769999900")
    session.update(
        {
            "status": "closed",
            "closed_at": "1769999910",
            "next_sequence": "2",
            "head_event_hash": event["event_hash"],
        }
    )
    bundle = {
        "schema_version": "loveengine.evidence-bundle/2",
        "bundle_id": "session-1:r1",
        "session_id": "session-1",
        "revision": "1",
        "status": "finalized",
        "finalized_at": "1769999920",
        "event_count": "1",
        "head_event_hash": event["event_hash"],
        "category_counts": {"source": "1", "summary": "0", "derived": "0"},
        "events": [
            {
                "event_id": event["event_id"],
                "sequence": event["sequence"],
                "category": event["category"],
                "event_hash": event["event_hash"],
                "artifact_hash": event["artifact_hash"],
            }
        ],
    }
    bundle["bundle_hash"] = evidence_bundle_hash(bundle)

    tasks = []
    observation_receipts = []
    for index, node in enumerate(nodes, start=1):
        task = build_task_v2(
            chain_id=chain_id,
            registry=registry,
            task_id=f"observe-{index}",
            task_type="observe_live_text",
            issuer=publisher.address,
            recipient=node.address,
            manifest_hash=manifest_hash,
            payload={"session_id": "session-1"},
            nonce=str(index),
            deadline=deadline,
        )
        task["signature"] = _sign(publisher, build_task_v2_typed_data(task))
        tasks.append(task)
        receipt = build_receipt_v2(
            chain_id=chain_id,
            registry=registry,
            task_id=task["task_id"],
            node=node.address,
            status="completed",
            result={
                "schema_version": "loveengine.live-observation-receipt/1",
                "session_id": "session-1",
                "observed_from": "0",
                "last_sequence": "1",
                "event_count": "1",
                "head_event_hash": event["event_hash"],
                "bundle_hash": bundle["bundle_hash"],
                "artifact_count": "1",
                "recovered_from_cursor": False,
            },
            nonce=task["nonce"],
            completed_at="1769999950",
        )
        receipt["signature"] = _sign(node, build_receipt_v2_typed_data(receipt))
        observation_receipts.append(receipt)
    observation_set = aggregate_observations(
        observation_receipts,
        expected_nodes={node.address for node in nodes},
        expected_chain_id=chain_id,
        expected_registry=registry,
    )

    dispute = build_dispute(
        "dispute-1",
        bundle["bundle_hash"],
        "critical",
        keccak256_hex(b"review completeness"),
        deadline,
    )
    review_receipts = []
    reviews = []
    for index, node in enumerate(nodes, start=1):
        review_payload = {
            "schema_version": "loveengine.review-dispute-payload/1",
            "dispute_id": dispute["dispute_id"],
            "bundle_hash": bundle["bundle_hash"],
            "session_id": "session-1",
            "evidence_url": "http://127.0.0.1:8780/v1/live/sessions/session-1/evidence",
            "events_url": "http://127.0.0.1:8780/v1/live/sessions/session-1/events",
            "artifact_base_url": "http://127.0.0.1:8780/v1/live/artifacts",
            "revision": bundle["revision"],
            "event_count": bundle["event_count"],
            "head_event_hash": bundle["head_event_hash"],
        }
        if legacy_review_payload:
            review_payload = {
                "dispute_id": dispute["dispute_id"],
                "bundle_hash": bundle["bundle_hash"],
            }
        task = build_task_v2(
            chain_id=chain_id,
            registry=registry,
            task_id=f"review-{index}",
            task_type="review_dispute",
            issuer=publisher.address,
            recipient=node.address,
            manifest_hash=manifest_hash,
            payload=review_payload,
            nonce=str(100 + index),
            deadline=deadline,
        )
        task["signature"] = _sign(publisher, build_task_v2_typed_data(task))
        tasks.append(task)
        result = {
            "dispute_id": dispute["dispute_id"],
            "bundle_hash": bundle["bundle_hash"],
            "verdict": "dismiss" if index < 3 else "uphold",
            "reason_hash": keccak256_hex(f"review-{index}".encode("utf-8")),
        }
        if not legacy_review_payload:
            result.update(
                {
                    "session_id": "session-1",
                    "revision": bundle["revision"],
                    "event_count": bundle["event_count"],
                    "head_event_hash": bundle["head_event_hash"],
                }
            )
            if evidence_verified is not None:
                result["evidence_verified"] = evidence_verified
        receipt = build_receipt_v2(
            chain_id=chain_id,
            registry=registry,
            task_id=task["task_id"],
            node=node.address,
            status="completed",
            result=result,
            nonce=task["nonce"],
            completed_at="1769999960",
        )
        receipt["signature"] = _sign(node, build_receipt_v2_typed_data(receipt))
        review_receipts.append(receipt)
        reviews.append(
            build_review(
                receipt["task_id"],
                dispute,
                node.address,
                result["verdict"],
                result["reason_hash"],
                receipt["completed_at"],
                receipt["signature"],
            )
        )
    resolved = aggregate_reviews(
        dispute, reviews, expected_nodes={node.address for node in nodes}
    )
    gate = build_proposal_plan(
        session=session,
        bundle=bundle,
        disputes=[resolved],
        proposal={"action": "set_user_count", "value": "20"},
    )
    proposal = {
        "schema_version": "loveengine.onchain-proposal-plan/1",
        "chain_id": chain_id,
        "witness_dao": dao,
        "proposal_id": "1",
        "payload_hash": keccak256_hex(b"proposal-1"),
        "support": True,
        "reason_hash": "0x" + "00" * 32,
        "deadline": deadline,
        "total_votes": "5",
        "support_votes": "5",
    }
    approvals = []
    for witness in witnesses:
        vote = {
            "schema_version": "loveengine.explicit-vote-approval/1",
            "witness": witness.address,
            "proposal_id": proposal["proposal_id"],
            "support": True,
            "reason_hash": proposal["reason_hash"],
            "payload_hash": proposal["payload_hash"],
            "nonce": "0",
            "deadline": deadline,
        }
        signed = Account.sign_message(
            encode_typed_data(
                full_message=build_vote_typed_data(
                    {
                        "chain_id": chain_id,
                        "verifying_contract": dao,
                        **{key: vote[key] for key in (
                            "witness", "proposal_id", "support", "reason_hash",
                            "payload_hash", "nonce", "deadline",
                        )},
                    }
                )
            ),
            witness.key,
        )
        vote.update(
            {
                "v": signed.v,
                "r": Web3.to_hex(signed.r),
                "s": Web3.to_hex(signed.s),
            }
        )
        approvals.append(vote)

    version = "0.6.1-contract-public-pilot"
    release = {
        "schema_version": "loveengine.skill-release/1",
        "chain_id": chain_id,
        "registry": registry,
        "publisher": publisher.address,
        "skill_id": "loveengine-witness",
        "version": version,
        "version_hash": keccak256_hex(version.encode("utf-8")),
        "package_hash": archive_hash,
        "manifest_hash": manifest_hash,
        "previous_version_hash": "0x" + "00" * 32,
        "status": "active",
    }
    contracts = {
        "SkillRegistry": registry,
        "WitnessDAO": dao,
        "PublicSink": sink,
    }
    value = {
        "schema_version": "loveengine.pilot-transcript/2",
        "run_id": "pilot-v2",
        "version": version,
        "verified_at": verified_at,
        "environment": "local_anvil",
        "actors_simulated": True,
        "verification_anchor": {
            "block_number": "10",
            "block_hash": "0x" + "52" * 32,
            "timestamp": verified_at,
        },
        "package": {
            "archive": "loveengine.zip",
            "archive_sha256": "sha256:" + "43" * 32,
            "archive_keccak256": archive_hash,
            "manifest_hash": manifest_hash,
        },
        "release_anchor": release,
        "bootstrap": bootstrap,
        "chain": {
            "chain_id": chain_id,
            "registry": registry,
            "publisher": publisher.address,
            "witness_dao": dao,
            "public_sink": sink,
            "witnesses": [witness.address for witness in witnesses],
            "min_valid_votes": 5,
            "contracts": contracts,
        },
        "session": session,
        "events": [event],
        "artifacts": [{"artifact_hash": artifact_hash, "size": len(content)}],
        "network_tasks": tasks,
        "observation_receipts": observation_receipts,
        "observation_set": observation_set,
        "evidence_bundle": bundle,
        "dispute": resolved,
        "review_receipts": review_receipts,
        "proposal_gate": gate,
        "proposal": proposal,
        "vote_approvals": approvals,
        "transaction_receipts": [
            {
                "action": "batchVote",
                "transaction_hash": "0x" + "51" * 32,
                "block_number": "10",
                "block_hash": "0x" + "52" * 32,
                "status": "1",
            }
        ],
        "contract_code_hashes": {
            name: "0x" + f"{60 + index:02x}" * 32
            for index, name in enumerate(contracts)
        },
        "final_state": {"proposal_executed": True, "total_uto": "20"},
        "metrics": {},
        "faults": {},
        "snapshots": [],
    }
    value["transcript_hash"] = pilot_transcript_hash(value)
    return value


def test_v2_transcript_verifies_signed_membership_quorum_and_cross_references() -> None:
    value = _v2_fixture()

    result = verify_pilot_transcript(value)

    assert result["verification_level"] == "offline_integrity"
    assert result["chain_verified"] is False
    assert result["trust_bound"] is False


@pytest.mark.parametrize("evidence_verified", [False, None])
def test_v2_transcript_rejects_review_without_verified_evidence(
    evidence_verified: bool | None,
) -> None:
    value = _v2_fixture(evidence_verified=evidence_verified)

    with pytest.raises(LoveEngineError) as error:
        verify_pilot_transcript(value)

    assert error.value.code == "review_evidence_not_verified"


def test_v2_legacy_review_payload_is_consistency_only() -> None:
    value = _v2_fixture(legacy_review_payload=True)

    result = verify_pilot_transcript(value, rpc_url="http://rpc.invalid")

    assert result["verification_level"] == "legacy_consistency"
    assert result["chain_verified"] is False
    assert result["trust_bound"] is False


def _trust_policy(value: dict) -> dict:
    release = value["release_anchor"]
    return {
        "schema_version": "loveengine.node-trust-policy/1",
        "chain_id": value["chain"]["chain_id"],
        "registry": value["chain"]["registry"],
        "publisher": value["chain"]["publisher"],
        "skill_id": release["skill_id"],
        "version": release["version"],
        "package_hash": release["package_hash"],
        "manifest_hash": release["manifest_hash"],
        "allowed_issuers": [value["chain"]["publisher"]],
    }


def test_v2_rpc_requires_policy_for_trust_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _v2_fixture()
    monkeypatch.setattr(
        pilot_transcript_module,
        "_verify_rpc",
        lambda transcript, rpc_url, vote_signers: None,
    )

    consistency = verify_pilot_transcript(value, rpc_url="http://rpc.invalid")
    trusted = verify_pilot_transcript(
        value,
        rpc_url="http://rpc.invalid",
        trust_policy=_trust_policy(value),
    )

    assert consistency["verification_level"] == "chain_consistency"
    assert consistency["chain_verified"] is True
    assert consistency["trust_bound"] is False
    assert trusted["verification_level"] == "chain_verified"
    assert trusted["chain_verified"] is True
    assert trusted["trust_bound"] is True


def test_v2_rejects_mismatched_trust_policy() -> None:
    value = _v2_fixture()
    policy = _trust_policy(value)
    policy["manifest_hash"] = "0x" + "ff" * 32

    with pytest.raises(LoveEngineError) as exc:
        verify_pilot_transcript(value, trust_policy=policy)

    assert exc.value.code == "trust_policy_mismatch"


def test_v2_rejects_semantically_invalid_trust_policy() -> None:
    value = _v2_fixture()
    policy = _trust_policy(value)
    policy["allowed_issuers"] = [Account.create().address]

    with pytest.raises(LoveEngineError) as exc:
        verify_pilot_transcript(value, trust_policy=policy)

    assert exc.value.code == "invalid_trust_policy"


def test_v2_accepts_policy_authorized_delegated_task_issuer() -> None:
    value = _v2_fixture()
    delegate = Account.create()
    for task in value["network_tasks"]:
        task["issuer"] = delegate.address
        task["signature"] = _sign(delegate, build_task_v2_typed_data(task))
    value["transcript_hash"] = pilot_transcript_hash(value)
    policy = _trust_policy(value)
    policy["allowed_issuers"].append(delegate.address)

    with pytest.raises(LoveEngineError) as unbound:
        verify_pilot_transcript(value)
    assert unbound.value.code == "untrusted_issuer"

    result = verify_pilot_transcript(value, trust_policy=policy)
    assert result["valid"] is True
    assert result["trust_bound"] is False


def test_v2_rejects_legacy_task_type() -> None:
    value = _v2_fixture()
    value["network_tasks"][0]["task_type"] = "observe_broadcast"
    value["transcript_hash"] = pilot_transcript_hash(value)

    with pytest.raises(LoveEngineError) as exc:
        verify_pilot_transcript(value)

    assert exc.value.code == "unsupported_capability"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda value: value["network_tasks"][0].update(
                {"issuer": Account.create().address}
            ),
            "untrusted_issuer",
        ),
        (
            lambda value: value["review_receipts"][0]["result"].update(
                {"verdict": "uphold"}
            ),
            "result_hash_mismatch",
        ),
        (
            lambda value: value["artifacts"][0].update({"size": 999}),
            "pilot_cross_reference_mismatch",
        ),
    ],
)
def test_v2_transcript_rejects_stage_tampering(mutate: object, code: str) -> None:
    value = _v2_fixture()
    mutate(value)
    value["transcript_hash"] = pilot_transcript_hash(value)

    with pytest.raises(LoveEngineError) as exc:
        verify_pilot_transcript(value)

    assert exc.value.code == code


def test_v2_rejects_signed_reviews_rebound_to_another_dispute() -> None:
    value = _v2_fixture()
    value["dispute"]["dispute_id"] = "replayed-critical-dispute"
    value["proposal_gate"] = build_proposal_plan(
        session=value["session"],
        bundle=value["evidence_bundle"],
        disputes=[value["dispute"]],
        proposal=value["proposal_gate"]["proposal"],
    )
    value["transcript_hash"] = pilot_transcript_hash(value)

    with pytest.raises(LoveEngineError) as exc:
        verify_pilot_transcript(value)

    assert exc.value.code == "pilot_cross_reference_mismatch"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["observation_set"].update(
            {"session_id": "another-session"}
        ),
        lambda value: value["observation_set"].update(
            {"head_event_hash": "0x" + "99" * 32}
        ),
        lambda value: value["observation_set"].update({"event_count": "2"}),
    ],
)
def test_v2_observation_set_binds_session_head_and_count(mutate: object) -> None:
    value = _v2_fixture()
    mutate(value)
    value["observation_set"]["observation_set_hash"] = observation_set_hash(
        value["observation_set"]
    )
    value["transcript_hash"] = pilot_transcript_hash(value)

    with pytest.raises(LoveEngineError) as exc:
        verify_pilot_transcript(value)

    assert exc.value.code == "pilot_cross_reference_mismatch"


def test_v2_rejects_contract_pointer_outside_code_inventory() -> None:
    value = _v2_fixture()
    value["chain"]["contracts"]["WitnessDAO"] = Account.create().address
    value["transcript_hash"] = pilot_transcript_hash(value)

    with pytest.raises(LoveEngineError) as exc:
        verify_pilot_transcript(value)

    assert exc.value.code == "pilot_cross_reference_mismatch"


def test_v2_rejects_unknown_transaction_action() -> None:
    value = _v2_fixture()
    value["transaction_receipts"][0]["action"] = "notAProtocolAction"
    value["transcript_hash"] = pilot_transcript_hash(value)

    with pytest.raises(LoveEngineError) as exc:
        verify_pilot_transcript(value)

    assert exc.value.code == "unsupported_transaction_action"


def test_v2_transaction_action_binds_target_and_selector() -> None:
    value = _v2_fixture()
    contracts = value["chain"]["contracts"]
    signature = (
        "batchVote((address,uint256,bool,bytes32,bytes32,uint256,uint256,"
        "uint8,bytes32,bytes32)[])"
    )
    selector = Web3.to_hex(Web3.keccak(text=signature)[:4])
    transaction = {
        "to": contracts["WitnessDAO"],
        "input": selector + "00" * 32,
    }

    pilot_transcript_module._verify_transaction_call(
        transaction, "batchVote", contracts
    )

    wrong_target = {**transaction, "to": contracts["PublicSink"]}
    with pytest.raises(LoveEngineError) as target_error:
        pilot_transcript_module._verify_transaction_call(
            wrong_target, "batchVote", contracts
        )
    assert target_error.value.code == "pilot_cross_reference_mismatch"

    wrong_selector = {**transaction, "input": "0xdeadbeef" + "00" * 32}
    with pytest.raises(LoveEngineError) as input_error:
        pilot_transcript_module._verify_transaction_call(
            wrong_selector, "batchVote", contracts
        )
    assert input_error.value.code == "pilot_cross_reference_mismatch"


def test_v2_batch_vote_logs_bind_recorded_witness_decisions() -> None:
    value = _v2_fixture()
    codec = Web3().codec
    event_topic = bytes.fromhex(
        pilot_transcript_module.VOTE_ACCEPTED_TOPIC.removeprefix("0x")
    )
    logs = []
    for approval in value["vote_approvals"]:
        witness = bytes.fromhex(approval["witness"].removeprefix("0x"))
        reason_hash = bytes.fromhex(
            approval["reason_hash"].removeprefix("0x")
        )
        logs.append(
            {
                "address": value["chain"]["witness_dao"],
                "topics": [
                    event_topic,
                    int(approval["proposal_id"]).to_bytes(32, "big"),
                    bytes(12) + witness,
                ],
                "data": codec.encode(
                    ["bool", "bytes32"],
                    [approval["support"], reason_hash],
                ),
            }
        )
    vote_signers = {
        Web3.to_checksum_address(item["witness"])
        for item in value["vote_approvals"]
    }

    pilot_transcript_module._verify_batch_vote_logs(
        Web3(), [{"logs": logs}], value, vote_signers
    )

    tampered = copy.deepcopy(logs)
    tampered[0]["data"] = codec.encode(
        ["bool", "bytes32"],
        [False, bytes.fromhex(value["vote_approvals"][0]["reason_hash"][2:])],
    )
    with pytest.raises(LoveEngineError) as exc:
        pilot_transcript_module._verify_batch_vote_logs(
            Web3(), [{"logs": tampered}], value, vote_signers
        )
    assert exc.value.code == "pilot_cross_reference_mismatch"
