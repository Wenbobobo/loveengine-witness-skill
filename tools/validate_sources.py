#!/usr/bin/env python3
"""Validate the LoveEngineSkill source inventory."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "docs" / "kb" / "sources.json"
REQUIRED_FIELDS = {
    "id",
    "title",
    "path",
    "source_group",
    "layer",
    "domain_tags",
    "status",
    "canonical_role",
    "summary",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    if not SOURCES_PATH.exists():
        fail("missing docs/kb/sources.json")

    try:
        records = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"invalid sources.json: {exc}")

    if not isinstance(records, list):
        fail("sources.json root must be a list")

    ids: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            fail(f"record {index} must be an object")
        missing = REQUIRED_FIELDS.difference(record)
        if missing:
            fail(f"record {index} missing fields: {sorted(missing)}")
        ids.append(record["id"])
        path = ROOT / record["path"]
        if not path.exists():
            fail(f"missing path for {record['id']}: {record['path']}")
        if not isinstance(record["domain_tags"], list):
            fail(f"domain_tags must be a list for {record['id']}")

    duplicates = [item for item, count in Counter(ids).items() if count > 1]
    if duplicates:
        fail(f"duplicate ids: {duplicates}")

    print(f"LoveEngine sources validation passed ({len(records)} records)")


if __name__ == "__main__":
    main()
