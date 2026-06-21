"""JSON Schema validation adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .errors import LoveEngineError
from .jsonio import read_json


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas"


def validate_schema(value: Any, schema_name: str) -> None:
    schema = read_json(SCHEMA_DIR / schema_name)
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as exc:
        path = ".".join(str(part) for part in exc.absolute_path)
        detail = f"{path}: {exc.message}" if path else exc.message
        raise LoveEngineError("schema_validation_failed", detail) from exc
