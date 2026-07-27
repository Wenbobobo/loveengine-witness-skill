"""Append-only, hash-linked Pilot audit records."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from time import time
from typing import Any

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import sha256_prefixed


class AuditLog:
    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.previous_hash = "sha256:" + "0" * 64
        if self.path.exists() and self.path.stat().st_size:
            result = verify_audit_log(self.path)
            self.previous_hash = result["head_hash"]

    def write(self, event: str, **details: Any) -> None:
        safe = {
            key: value
            for key, value in details.items()
            if key.lower() not in {"authorization", "token", "write_token"}
        }
        record = {
            "timestamp": int(time()),
            "run_id": self.run_id,
            "event": event,
            "previous_record_hash": self.previous_hash,
            **safe,
        }
        record["record_hash"] = sha256_prefixed(canonical_json_bytes(record))
        self.previous_hash = record["record_hash"]
        line = json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
        print(line, file=sys.stdout, flush=True)


def verify_audit_log(path: Path) -> dict[str, Any]:
    previous = "sha256:" + "0" * 64
    count = 0
    try:
        handle = Path(path).open("r", encoding="utf-8")
    except FileNotFoundError as exc:
        raise LoveEngineError("audit_log_missing", str(path), 3) from exc
    with handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LoveEngineError("audit_log_invalid", f"line {line_number}") from exc
            if record.get("previous_record_hash") != previous:
                raise LoveEngineError("audit_chain_broken", f"line {line_number}")
            expected = record.get("record_hash")
            view = dict(record)
            view.pop("record_hash", None)
            if expected != sha256_prefixed(canonical_json_bytes(view)):
                raise LoveEngineError("audit_hash_mismatch", f"line {line_number}")
            previous = expected
            count += 1
    return {"valid": True, "record_count": count, "head_hash": previous}
