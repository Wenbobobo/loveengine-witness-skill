"""Checksum-protected local pilot backup, restore, and retention."""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .jsonio import read_json, write_json
from .pilot_server import verify_audit_log


def _backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as target:
        source_db.backup(target)


def _checksums(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_prefixed(path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "checksums.json"
    }


def create_system_snapshot(
    *,
    run_id: str,
    database: Path,
    relay_database: Path,
    artifact_root: Path,
    audit_log: Path,
    chain_root: Path,
    output: Path,
) -> dict[str, Any]:
    output = Path(output).resolve()
    snapshot = output / f"{run_id}-{int(time.time())}"
    data = snapshot / "data"
    data.mkdir(parents=True, exist_ok=False)
    _backup_sqlite(Path(database), data / "pilot.sqlite")
    _backup_sqlite(Path(relay_database), data / "relay.sqlite")
    if Path(artifact_root).exists():
        shutil.copytree(artifact_root, data / "artifacts")
    else:
        (data / "artifacts").mkdir()
    shutil.copy2(audit_log, data / "audit.jsonl")
    verify_audit_log(data / "audit.jsonl")
    chain_target = data / "chain"
    chain_target.mkdir()
    for name in ("deployment.json", "state.json"):
        source = Path(chain_root) / name
        if not source.is_file():
            raise LoveEngineError("snapshot_input_missing", str(source), 3)
        shutil.copy2(source, chain_target / name)
    write_json(
        snapshot / "manifest.json",
        {
            "schema_version": "loveengine.pilot-snapshot/1",
            "run_id": run_id,
            "created_at": str(int(time.time())),
            "retention_days": "30",
        },
    )
    checksums = _checksums(snapshot)
    write_json(
        snapshot / "checksums.json",
        {
            "schema_version": "loveengine.pilot-snapshot-checksums/1",
            "files": checksums,
        },
    )
    return {
        "snapshot": str(snapshot),
        "file_count": len(checksums),
        "checksums_hash": sha256_prefixed((snapshot / "checksums.json").read_bytes()),
    }


def verify_system_snapshot(snapshot: Path) -> dict[str, Any]:
    snapshot = Path(snapshot).resolve()
    checksums = read_json(snapshot / "checksums.json")
    files = checksums.get("files")
    if not isinstance(files, dict):
        raise LoveEngineError("snapshot_checksums_invalid", str(snapshot))
    for relative, expected in files.items():
        path = (snapshot / relative).resolve()
        if snapshot not in path.parents:
            raise LoveEngineError("unsafe_snapshot_path", relative)
        if not path.is_file() or sha256_prefixed(path.read_bytes()) != expected:
            raise LoveEngineError("snapshot_checksum_mismatch", relative)
    required = {
        "manifest.json",
        "data/pilot.sqlite",
        "data/relay.sqlite",
        "data/audit.jsonl",
        "data/chain/deployment.json",
        "data/chain/state.json",
    }
    missing = required - set(files)
    if missing:
        raise LoveEngineError("snapshot_required_file_missing", sorted(missing)[0])
    verify_audit_log(snapshot / "data" / "audit.jsonl")
    return {
        "valid": True,
        "run_id": read_json(snapshot / "manifest.json")["run_id"],
        "file_count": len(files),
    }


def restore_system_snapshot(
    snapshot: Path,
    *,
    database: Path,
    relay_database: Path,
    artifact_root: Path,
    audit_log: Path,
    chain_root: Path,
) -> dict[str, Any]:
    verification = verify_system_snapshot(snapshot)
    data = Path(snapshot).resolve() / "data"
    for source, destination in (
        (data / "pilot.sqlite", Path(database)),
        (data / "relay.sqlite", Path(relay_database)),
        (data / "audit.jsonl", Path(audit_log)),
        (data / "chain" / "deployment.json", Path(chain_root) / "deployment.json"),
        (data / "chain" / "state.json", Path(chain_root) / "state.json"),
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    artifact_root = Path(artifact_root).resolve()
    if artifact_root.exists():
        shutil.rmtree(artifact_root)
    shutil.copytree(data / "artifacts", artifact_root)
    return {"restored": True, **verification}


def prune_snapshots(output: Path, *, older_than_days: int = 30) -> dict[str, Any]:
    cutoff = int(time.time()) - older_than_days * 86400
    removed = []
    for path in Path(output).iterdir() if Path(output).exists() else []:
        if not path.is_dir() or not (path / "manifest.json").is_file():
            continue
        created = int(read_json(path / "manifest.json")["created_at"])
        if created < cutoff:
            shutil.rmtree(path)
            removed.append(str(path))
    return {"removed": len(removed), "paths": removed}
