"""Transport boundary for Agent network connections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from .relay_server import run_node_client


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
    sign_challenge: Callable[[str], str]
    sign_typed_data: Callable[[dict[str, Any]], str]

    async def connect(self) -> dict[str, Any]:
        return await run_node_client(
            self.url,
            self.node_address,
            self.signed_profile,
            self.expected_tasks,
            self.sign_challenge,
            self.sign_typed_data,
        )
