from __future__ import annotations

from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.m4_network import (
    build_bootstrap_v2,
    build_node_profile_v2,
    build_task_v2,
)
from loveengine_witness.m4_typed_data import (
    build_bootstrap_v2_typed_data,
    build_node_profile_v2_typed_data,
    build_task_v2_typed_data,
)
from loveengine_witness.network_typed_data import payload_hash
from loveengine_witness.pilot_task_ingress import enqueue_signed_task
from loveengine_witness.relay import RelayStore
from loveengine_witness.relay_server import RelayHub


CHAIN_ID = "31337"
REGISTRY = "0x" + "1" * 40
MANIFEST_HASH = "0x" + "2" * 64
FUTURE = "4102444700"


def _sign(account: object, typed_data: dict) -> str:
    signature = Account.sign_message(
        encode_typed_data(full_message=typed_data),
        account.key,
    ).signature.hex()
    return signature if signature.startswith("0x") else "0x" + signature


def _signed_profile(account: object) -> dict:
    profile = build_node_profile_v2(
        account.address,
        ["observe_live_text", "review_dispute"],
        "1",
        FUTURE,
    )
    return {
        "schema_version": "loveengine.signed-agent-node-profile/2",
        "chain_id": CHAIN_ID,
        "registry": REGISTRY,
        "profile": profile,
        "signature": _sign(
            account,
            build_node_profile_v2_typed_data(CHAIN_ID, REGISTRY, profile),
        ),
    }


def _hub(tmp_path: Path) -> tuple[RelayHub, object, object]:
    publisher = Account.create()
    node = Account.create()
    profile = _signed_profile(node)
    bootstrap = build_bootstrap_v2(
        chain_id=CHAIN_ID,
        registry=REGISTRY,
        publisher=publisher.address,
        nodes=[profile],
        sequence="1",
        valid_until=FUTURE,
    )
    bootstrap["signature"] = _sign(
        publisher,
        build_bootstrap_v2_typed_data(bootstrap),
    )
    release = {
        "schema_version": "loveengine.skill-release/1",
        "chain_id": CHAIN_ID,
        "registry": REGISTRY,
        "publisher": publisher.address,
        "skill_id": "loveengine-witness",
        "version": "0.6.1-contract-public-pilot",
        "version_hash": "0x" + "3" * 64,
        "package_hash": "0x" + "4" * 64,
        "manifest_hash": MANIFEST_HASH,
        "previous_version_hash": "0x" + "0" * 64,
        "status": "active",
    }
    key = "/".join(
        (
            publisher.address.lower(),
            release["skill_id"],
            release["version"],
        )
    )
    return (
        RelayHub(
            RelayStore(tmp_path / "relay.sqlite"),
            bootstrap,
            {key: release},
            {},
        ),
        publisher,
        node,
    )


def _task(
    signer: object,
    recipient: str,
    *,
    task_id: str = "task-1",
    nonce: str = "1",
    chain_id: str = CHAIN_ID,
    registry: str = REGISTRY,
    manifest_hash: str = MANIFEST_HASH,
    deadline: str = FUTURE,
) -> dict:
    task = build_task_v2(
        chain_id=chain_id,
        registry=registry,
        task_id=task_id,
        task_type="review_dispute",
        issuer=signer.address,
        recipient=recipient,
        manifest_hash=manifest_hash,
        payload={
            "schema_version": "loveengine.review-dispute-payload/1",
            "dispute_id": "dispute-1",
            "bundle_hash": "0x" + "5" * 64,
            "session_id": "session-1",
            "evidence_url": "http://127.0.0.1:8080/v1/live/sessions/session-1/evidence",
            "events_url": "http://127.0.0.1:8080/v1/live/sessions/session-1/events",
            "artifact_base_url": "http://127.0.0.1:8080/v1/live/artifacts",
            "revision": "1",
            "event_count": "1",
            "head_event_hash": "0x" + "7" * 64,
        },
        nonce=nonce,
        deadline=deadline,
    )
    task["signature"] = _sign(signer, build_task_v2_typed_data(task))
    return task


def test_signed_v2_task_is_queued_once(tmp_path: Path) -> None:
    hub, publisher, node = _hub(tmp_path)
    task = _task(publisher, node.address)

    result = enqueue_signed_task(hub, task)

    assert result == {
        "queued": True,
        "task_id": "task-1",
        "recipient": node.address,
    }
    pending = hub.store.pending(node.address)
    assert len(pending) == 1
    assert pending[0].task_id == "task-1"
    duplicate = enqueue_signed_task(hub, task)
    assert duplicate == {
        "queued": True,
        "idempotent_replay": True,
        "task_id": "task-1",
        "recipient": node.address,
    }
    assert len(hub.store.pending(node.address)) == 1


def test_task_nonce_cannot_be_reused_for_same_issuer_and_recipient(
    tmp_path: Path,
) -> None:
    hub, publisher, node = _hub(tmp_path)
    enqueue_signed_task(hub, _task(publisher, node.address))

    with pytest.raises(LoveEngineError) as duplicate:
        enqueue_signed_task(
            hub,
            _task(publisher, node.address, task_id="task-2", nonce="1"),
        )
    assert duplicate.value.code == "task_conflict"


@pytest.mark.parametrize(
    ("task_factory", "error_code"),
    (
        (
            lambda publisher, node: _task(
                Account.create(),
                node.address,
            ),
            "wrong_issuer",
        ),
        (
            lambda publisher, node: _task(
                publisher,
                Account.create().address,
            ),
            "profile_not_in_directory",
        ),
        (
            lambda publisher, node: _task(
                publisher,
                node.address,
                manifest_hash="0x" + "6" * 64,
            ),
            "release_context_not_found",
        ),
        (
            lambda publisher, node: _task(
                publisher,
                node.address,
                chain_id="31338",
            ),
            "wrong_chain_id",
        ),
        (
            lambda publisher, node: _task(
                publisher,
                node.address,
                registry="0x" + "7" * 40,
            ),
            "wrong_registry",
        ),
        (
            lambda publisher, node: _task(
                publisher,
                node.address,
                deadline="1",
            ),
            "task_expired",
        ),
    ),
)
def test_task_trust_binding_is_checked_before_enqueue(
    tmp_path: Path,
    task_factory: object,
    error_code: str,
) -> None:
    hub, publisher, node = _hub(tmp_path)

    with pytest.raises(LoveEngineError) as rejected:
        enqueue_signed_task(hub, task_factory(publisher, node))

    assert rejected.value.code == error_code
    assert hub.store.metrics()["queued"] == 0


def test_task_ingress_rejects_non_v2_codec(tmp_path: Path) -> None:
    hub, _, _ = _hub(tmp_path)

    with pytest.raises(LoveEngineError) as rejected:
        enqueue_signed_task(
            hub,
            {"schema_version": "loveengine.network-task/1"},
        )

    assert rejected.value.code == "unsupported_task_schema"


def test_task_ingress_rejects_invalid_signature(tmp_path: Path) -> None:
    hub, publisher, node = _hub(tmp_path)
    task = _task(publisher, node.address)
    task["signature"] = "0x" + "0" * 130

    with pytest.raises(LoveEngineError) as rejected:
        enqueue_signed_task(hub, task)

    assert rejected.value.code == "invalid_signature"
    assert hub.store.metrics()["queued"] == 0


def test_task_ingress_rejects_historical_minimal_payload(
    tmp_path: Path,
) -> None:
    hub, publisher, node = _hub(tmp_path)
    task = _task(publisher, node.address)
    task["payload"] = {
        "dispute_id": "dispute-1",
        "bundle_hash": "0x" + "5" * 64,
    }
    task["payload_hash"] = payload_hash(task["payload"])
    task["signature"] = _sign(
        publisher,
        build_task_v2_typed_data(task),
    )

    with pytest.raises(LoveEngineError) as rejected:
        enqueue_signed_task(hub, task)

    assert rejected.value.code == "executable_task_payload_required"
    assert hub.store.metrics()["queued"] == 0
