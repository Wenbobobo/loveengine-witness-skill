"""Replaceable live input adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Protocol


class LiveSource(Protocol):
    def events(self) -> Iterable[dict[str, Any]]: ...


class FixtureLiveSource:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def events(self) -> Iterable[dict[str, Any]]:
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


class HttpPushLiveSource:
    """Marker adapter for events accepted by the LiveGateway HTTP boundary."""

    @staticmethod
    def normalize(value: dict[str, Any]) -> dict[str, Any]:
        return dict(value)
