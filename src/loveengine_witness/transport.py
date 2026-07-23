"""Transport boundary for Agent network connections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from .agent_session import run_agent_session


@runtime_checkable
class Transport(Protocol):
    async def connect(self) -> dict[str, Any]:
        """Connect outbound and consume the configured task batch."""


@dataclass
class RelayTransport:
    url: str
    node_address: str
    signed_profile: dict[str, Any]
    expected_tasks: int
    expected_issuer: str
    expected_manifest_hash: str
    sign_challenge: Callable[[str], str]
    sign_typed_data: Callable[[dict[str, Any]], str]

    async def connect(self) -> dict[str, Any]:
        return await run_agent_session(
            url=self.url,
            node_address=self.node_address,
            signed_profile=self.signed_profile,
            expected_tasks=self.expected_tasks,
            expected_issuer=self.expected_issuer,
            expected_manifest_hash=self.expected_manifest_hash,
            sign_challenge=self.sign_challenge,
            sign_typed_data=self.sign_typed_data,
        )
