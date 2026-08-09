from __future__ import annotations

import json
from types import SimpleNamespace
from time import time

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from loveengine_witness import invited_operator
from loveengine_witness.canonical import canonical_json_bytes
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.hashes import sha256_prefixed
from loveengine_witness.m4_network import build_task_v2
from loveengine_witness.participant_attestation import build_participant_attestation


REGISTRY = "0x" + "12" * 20
MANIFEST_HASH = "0x" + "34" * 32
PACKAGE_HASH = "0x" + "35" * 32
SERVICE_HASH = "0x" + "36" * 32
PROFILE_HASH = "0x" + "37" * 32
RULESET_HASH = "sha256:" + "38" * 32
RULES_ATTESTATION_HASH = "sha256:" + "39" * 32


class _AccountSigner:
    def __init__(self, account: object, role: str) -> None:
        self.account = account
        self.address = account.address
        self.chain_id = "11155111"
        self.role = role

    def sign_typed_data(self, typed_data: dict) -> str:
        return "0x" + Account.sign_message(
            encode_typed_data(full_message=typed_data), self.account.key
        ).signature.hex()


def _patch_signer(monkeypatch: pytest.MonkeyPatch, signer: _AccountSigner) -> None:
    config = SimpleNamespace(
        role=signer.role,
        chain_id=signer.chain_id,
        address=signer.address,
        kind="clef",
        ruleset_sha256=RULESET_HASH,
        rules_attestation_sha256=RULES_ATTESTATION_HASH,
    )
    monkeypatch.setattr(
        invited_operator, "load_external_signer_config", lambda _path: config
    )
    monkeypatch.setattr(
        invited_operator,
        "_build_verified_signer_client",
        lambda _config, **_kwargs: (signer, None),
    )


def test_sign_network_task_uses_external_task_issuer(tmp_path, monkeypatch) -> None:
    account = Account.create()
    signer = _AccountSigner(account, "task_issuer")
    _patch_signer(monkeypatch, signer)
    recipient = Account.create().address
    policy = {
        "chain_id": "11155111",
        "registry": REGISTRY,
        "publisher": account.address,
        "package_hash": PACKAGE_HASH,
        "manifest_hash": MANIFEST_HASH,
        "allowed_issuers": [account.address],
    }
    monkeypatch.setattr(
        invited_operator, "load_node_trust_policy", lambda _path: policy
    )
    monkeypatch.setattr(
        invited_operator,
        "_load_bootstrap_context",
        lambda *_args, **_kwargs: ({"valid_until": str(int(time()) + 600)}, {recipient: PROFILE_HASH}),
    )
    task = build_task_v2(
        chain_id="11155111",
        registry=REGISTRY,
        task_id="observe-after-connect",
        task_type="observe_live_text",
        issuer=account.address,
        recipient=recipient,
        manifest_hash=MANIFEST_HASH,
        payload={
            "schema_version": "loveengine.observe-live-text-payload/1",
            "session_id": "public-pilot-session",
            "stream_url": "https://pilot.example.ts.net/v1/live/sessions/s/stream",
            "session_url": "https://pilot.example.ts.net/v1/live/sessions/s",
            "artifact_base_url": "https://pilot.example.ts.net/v1/live/artifacts",
            "start_cursor": "0",
            "initial_head_hash": "0x" + "00" * 32,
            "max_duration_seconds": 900,
        },
        nonce="1",
        deadline=str(int(time()) + 300),
    )
    input_path = tmp_path / "unsigned-task.json"
    output_path = tmp_path / "signed-task.json"
    input_path.write_text(json.dumps(task), encoding="utf-8")

    result = invited_operator.sign_network_task(
        input_path,
        tmp_path / "signer.json",
        output_path,
        trust_policy_path=tmp_path / "policy.json",
        bootstrap_path=tmp_path / "bootstrap.json",
    )

    signed = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["signed"] is True
    assert signed["signature"].startswith("0x")
    assert "signature" not in result


def test_sign_network_task_rejects_excessive_deadline(tmp_path, monkeypatch) -> None:
    account = Account.create()
    signer = _AccountSigner(account, "task_issuer")
    _patch_signer(monkeypatch, signer)
    recipient = Account.create().address
    now = int(time())
    policy = {
        "chain_id": "11155111",
        "registry": REGISTRY,
        "publisher": account.address,
        "package_hash": PACKAGE_HASH,
        "manifest_hash": MANIFEST_HASH,
        "allowed_issuers": [account.address],
    }
    monkeypatch.setattr(
        invited_operator, "load_node_trust_policy", lambda _path: policy
    )
    monkeypatch.setattr(
        invited_operator,
        "_load_bootstrap_context",
        lambda *_args, **_kwargs: (
            {"valid_until": str(now + 7_200)},
            {recipient: PROFILE_HASH},
        ),
    )
    task = build_task_v2(
        chain_id="11155111",
        registry=REGISTRY,
        task_id="too-long",
        task_type="observe_live_text",
        issuer=account.address,
        recipient=recipient,
        manifest_hash=MANIFEST_HASH,
        payload={
            "schema_version": "loveengine.observe-live-text-payload/1",
            "session_id": "public-pilot-session",
            "stream_url": "https://pilot.example.ts.net/v1/live/sessions/s/stream",
            "session_url": "https://pilot.example.ts.net/v1/live/sessions/s",
            "artifact_base_url": "https://pilot.example.ts.net/v1/live/artifacts",
            "start_cursor": "0",
            "initial_head_hash": "0x" + "00" * 32,
            "max_duration_seconds": 900,
        },
        nonce="1",
        deadline=str(now + 3_601),
    )
    input_path = tmp_path / "unsigned-task.json"
    input_path.write_text(json.dumps(task), encoding="utf-8")

    with pytest.raises(LoveEngineError) as error:
        invited_operator.sign_network_task(
            input_path,
            tmp_path / "signer.json",
            tmp_path / "signed-task.json",
            trust_policy_path=tmp_path / "policy.json",
            bootstrap_path=tmp_path / "bootstrap.json",
            now=now,
        )

    assert error.value.code == "task_ttl_exceeded"


def test_sign_participant_attestation_binds_profile_and_ruleset(
    tmp_path, monkeypatch
) -> None:
    account = Account.create()
    signer = _AccountSigner(account, "observation_node")
    _patch_signer(monkeypatch, signer)
    now = int(time())
    issuer = Account.create().address
    policy = {
        "chain_id": "11155111",
        "registry": REGISTRY,
        "publisher": issuer,
        "skill_id": "loveengine-witness",
        "version": "0.7.0-invited-public-pilot",
        "package_hash": PACKAGE_HASH,
        "manifest_hash": MANIFEST_HASH,
        "allowed_issuers": [issuer],
    }
    invite = {
        "schema_version": "loveengine.pilot-invite/2",
        "chain_id": "11155111",
        "registry": REGISTRY,
        "publisher": issuer,
        "skill_id": "loveengine-witness",
        "version": policy["version"],
        "package_hash": PACKAGE_HASH,
        "issued_at": str(now - 10),
        "expires_at": str(now + 600),
    }
    assignment = {
        "task_id": "observe-1",
        "task_type": "observe_live_text",
        "issuer": issuer,
        "recipient": account.address,
        "payload": {"session_id": "invited-pilot"},
        "payload_hash": "0x" + "43" * 32,
        "deadline": str(now + 500),
    }
    service = {
        "schema_version": "loveengine.invited-pilot-service-config/1",
        "transport": "loopback",
        "participant_origin": "http://127.0.0.1:8780",
        "relay_url": "ws://127.0.0.1:8780/v1/ws",
        "tailnet_only": False,
        "funnel_enabled": False,
        "admin_surface": "loopback_only",
        "participant_allowlist_hash": "0x" + "44" * 32,
        "tailscale_serve_config_hash": "0x" + "45" * 32,
        "restore_verified": True,
    }
    service["config_hash"] = invited_operator.public_service_config_hash(service)
    value = build_participant_attestation(
        chain_id="11155111",
        registry=REGISTRY,
        run_id="invited-pilot",
        node=account.address,
        role="observation_node",
        profile_hash=PROFILE_HASH,
        assignment_task_id=assignment["task_id"],
        assignment_payload_hash=assignment["payload_hash"],
        pilot_invite_hash=sha256_prefixed(canonical_json_bytes(invite)),
        trust_policy_hash=sha256_prefixed(canonical_json_bytes(policy)),
        package_hash=PACKAGE_HASH,
        manifest_hash=MANIFEST_HASH,
        service_config_hash=service["config_hash"],
        ruleset_sha256=RULESET_HASH,
        rules_attestation_sha256=RULES_ATTESTATION_HASH,
        operator_group_hash="0x" + "41" * 32,
        network_group_hash="0x" + "42" * 32,
        issued_at=str(now - 1),
        valid_until=str(now + 300),
    )
    input_path = tmp_path / "unsigned-attestation.json"
    output_path = tmp_path / "signed-attestation.json"
    input_path.write_text(json.dumps(value), encoding="utf-8")
    invite_path = tmp_path / "invite.json"
    assignment_path = tmp_path / "assignment.json"
    service_path = tmp_path / "service.json"
    invite_path.write_text(json.dumps(invite), encoding="utf-8")
    assignment_path.write_text(json.dumps(assignment), encoding="utf-8")
    service_path.write_text(json.dumps(service), encoding="utf-8")
    monkeypatch.setattr(
        invited_operator, "load_node_trust_policy", lambda _path: policy
    )
    monkeypatch.setattr(
        invited_operator,
        "_load_bootstrap_context",
        lambda *_args, **_kwargs: (
            {"valid_until": str(now + 600)},
            {account.address: PROFILE_HASH},
        ),
    )
    monkeypatch.setattr(
        invited_operator,
        "validate_pilot_invite_v2",
        lambda loaded, **_kwargs: loaded,
    )
    monkeypatch.setattr(invited_operator, "verify_invite_against_policy", lambda *_: None)
    monkeypatch.setattr(invited_operator, "verify_task_v2", lambda *_args, **_kwargs: issuer)

    result = invited_operator.sign_participant_attestation(
        input_path,
        tmp_path / "signer.json",
        output_path,
        trust_policy_path=tmp_path / "policy.json",
        bootstrap_path=tmp_path / "bootstrap.json",
        invite_path=invite_path,
        assignment_task_path=assignment_path,
        service_config_path=service_path,
    )

    signed = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["node"] == account.address
    assert signed["profile_hash"] == PROFILE_HASH
    assert signed["signature"].startswith("0x")

    for field, wrong in (
        ("chain_id", "1"),
        ("registry", "0x" + "99" * 20),
    ):
        changed = dict(value)
        changed[field] = wrong
        input_path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(LoveEngineError) as error:
            invited_operator.sign_participant_attestation(
                input_path,
                tmp_path / "signer.json",
                output_path,
                trust_policy_path=tmp_path / "policy.json",
                bootstrap_path=tmp_path / "bootstrap.json",
                invite_path=invite_path,
                assignment_task_path=assignment_path,
                service_config_path=service_path,
            )
        assert error.value.code == "participant_attestation_mismatch"


def test_task_enqueue_reads_token_file_and_returns_no_token(
    tmp_path, monkeypatch
) -> None:
    task = {"schema_version": "loveengine.network-task/2"}
    input_path = tmp_path / "task.json"
    input_path.write_text(json.dumps(task), encoding="utf-8")
    token_file = tmp_path / "operator.token"
    token_file.write_text("safe-test-token-123456", encoding="utf-8")
    token_file.chmod(0o600)
    monkeypatch.setattr(invited_operator, "validate_schema", lambda *_args: None)
    seen = {}

    class Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {"queued": True, "task_id": "task-1", "recipient": "node-1"}
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        seen["authorization"] = request.headers["Authorization"]
        seen["origin"] = request.headers["Origin"]
        seen["url"] = request.full_url
        assert timeout == 15
        return Response()

    monkeypatch.setattr(invited_operator, "urlopen", fake_urlopen)
    result = invited_operator.enqueue_network_task(
        input_path,
        admin_url="http://127.0.0.1:8780",
        origin="http://127.0.0.1:8780",
        token_file=token_file,
    )

    assert result["queued"] is True
    assert "token" not in json.dumps(result).lower()
    assert seen["authorization"] == "Bearer safe-test-token-123456"
    assert seen["origin"] == "http://127.0.0.1:8780"
    assert seen["url"].endswith("/v1/relay/tasks")


def test_task_enqueue_rejects_non_loopback_admin_url(tmp_path) -> None:
    with pytest.raises(LoveEngineError) as error:
        invited_operator.enqueue_network_task(
            tmp_path / "missing.json",
            admin_url="https://pilot.example.ts.net",
            origin="http://127.0.0.1:8780",
            token_file=tmp_path / "token",
        )
    assert error.value.code == "invalid_admin_origin"
