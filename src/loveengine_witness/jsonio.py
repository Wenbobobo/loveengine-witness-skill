"""JSON filesystem adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import LoveEngineError


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LoveEngineError("file_not_found", str(path), 3) from exc
    except json.JSONDecodeError as exc:
        raise LoveEngineError("invalid_json", f"{path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
