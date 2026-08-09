from __future__ import annotations

import json

from loveengine_witness import network_node


ADDRESS = "0x" + "12" * 20
REGISTRY = "0x" + "34" * 20


def test_connect_node_routes_challenge_and_typed_data_through_external_signer(
    tmp_path, monkeypatch
) -> None:
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"fixture": True}), encoding="utf-8")
    calls: list[tuple[str, object]] = []

    class Signer:
        address = ADDRESS
        chain_id = "11155111"
        role = "observation_node"

        def sign_message(self, message: str) -> str:
            calls.append(("message", message))
            return "0x" + "11" * 65

        def sign_typed_data(self, typed_data: dict) -> str:
            calls.append(("typed", typed_data))
            return "0x" + "22" * 65

    async def run_agent_session(**kwargs):
        challenge = {
            "type": "challenge",
            "schema_version": "loveengine.relay-challenge/1",
            "chain_id": "11155111",
            "registry": REGISTRY,
            "nonce": "ab" * 32,
        }
        assert kwargs["sign_challenge"](challenge).startswith("0x")
        assert kwargs["sign_typed_data"]({"primaryType": "TaskReceiptV2"}).startswith(
            "0x"
        )
        return {"connected": True, "tasks": 1}

    monkeypatch.setattr(network_node, "run_agent_session", run_agent_session)
    result = network_node.connect_node(
        url="wss://witness.example.ts.net/v1/ws",
        rpc_url="https://must-not-be-contacted.invalid",
        address=ADDRESS,
        profile_path=profile,
        expected_tasks=1,
        expected_issuer=None,
        expected_manifest_hash="0x" + "55" * 32,
        cursor_database=None,
        verdicts_path=None,
        output=None,
        signer_client=Signer(),
    )
    assert result["connected"] is True
    assert calls[0][0] == "message"
    assert "LoveEngine Relay Authentication" in calls[0][1]
    assert calls[1] == ("typed", {"primaryType": "TaskReceiptV2"})
