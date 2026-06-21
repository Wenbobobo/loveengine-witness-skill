"""Convert idempotent chain events into signed-task candidates."""

from __future__ import annotations

from typing import Any

from .errors import LoveEngineError
from .network_protocol import build_task
from .network_typed_data import id_hash


EVENT_TASK_TYPES = {
    "ReleasePublished": "propagate_skill",
    "BroadcastScheduled": "observe_broadcast",
}


def event_key(chain_id: str, tx_hash: str, log_index: str) -> str:
    return id_hash(f"{chain_id}:{tx_hash.lower()}:{log_index}")


class EventTaskBridge:
    def __init__(self) -> None:
        self.seen: set[str] = set()

    def build_tasks(
        self,
        *,
        event: dict[str, Any],
        registry: str,
        issuer: str,
        recipients: list[str],
        manifest_hash: str,
        nonce_start: int,
        deadline: str,
    ) -> list[dict[str, Any]]:
        event_type = event["event"]
        task_type = EVENT_TASK_TYPES.get(event_type)
        if task_type is None:
            raise LoveEngineError("unsupported_chain_event", event_type)
        key = event_key(
            event["chain_id"],
            event["tx_hash"],
            event["log_index"],
        )
        if key in self.seen:
            return []
        self.seen.add(key)
        return [
            build_task(
                chain_id=event["chain_id"],
                registry=registry,
                task_id=f"{key}:{index}",
                task_type=task_type,
                issuer=issuer,
                recipient=recipient,
                manifest_hash=manifest_hash,
                payload=event,
                nonce=str(nonce_start + index),
                deadline=deadline,
            )
            for index, recipient in enumerate(recipients)
        ]
