"""Agent node profile construction."""

from __future__ import annotations

from typing import Any

from eth_utils import to_checksum_address

from .schema import validate_schema
from .secrets import reject_secret_fields


def build_node_profile(config: dict[str, Any]) -> dict[str, Any]:
    reject_secret_fields(config)
    profile = {
        "schema_version": "loveengine.agent-node-profile/0.2",
        "node_id": config["node_id"],
        "agent_runtime": config["agent_runtime"],
        "skill_id": "loveengine-witness",
        "skill_version": config.get("skill_version", "0.2.0-local-loop"),
        "address": to_checksum_address(config["address"]),
        "signer_type": config["signer_type"],
        "capabilities": config.get("capabilities", []),
        "network_endpoints": config.get("network_endpoints", []),
        "availability": config.get("availability", {}),
        "private_key_available_to_agent": False,
        "policy": {
            "can_request_signature": config.get("can_request_signature", True),
            "can_submit_transaction": config.get("can_submit_transaction", True),
            "can_modify_source_materials": False,
        },
    }
    validate_schema(profile, "agent-node-profile-v2.schema.json")
    return profile
