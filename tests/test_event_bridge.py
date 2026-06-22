from __future__ import annotations

from loveengine_witness.event_bridge import EventTaskBridge


def test_event_bridge_is_idempotent_by_chain_transaction_and_log_index() -> None:
    bridge = EventTaskBridge()
    event = {
        "event": "ReleasePublished",
        "chain_id": "31337",
        "tx_hash": "0x" + "1" * 64,
        "log_index": "0",
        "package_hash": "0x" + "2" * 64,
    }
    kwargs = {
        "event": event,
        "registry": "0x" + "3" * 40,
        "issuer": "0x" + "4" * 40,
        "recipients": ["0x" + "5" * 40, "0x" + "6" * 40],
        "manifest_hash": "0x" + "7" * 64,
        "nonce_start": 10,
        "deadline": "2000000000",
    }

    first = bridge.build_tasks(**kwargs)
    repeated = bridge.build_tasks(**kwargs)

    assert len(first) == 2
    assert {task["task_type"] for task in first} == {"propagate_skill"}
    assert [task["nonce"] for task in first] == ["10", "11"]
    assert repeated == []
