"""Checksum-protected local pilot backup, restore, and retention."""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .jsonio import read_json, write_json
from .pilot_server import verify_audit_log
from .schema import validate_schema


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
REQUIRED_SNAPSHOT_FILES = {
    "manifest.json",
    "data/pilot.sqlite",
    "data/relay.sqlite",
    "data/audit.jsonl",
    "data/chain/deployment.json",
    "data/chain/state.json",
}


def _validate_run_id(run_id: Any) -> str:
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise LoveEngineError("invalid_snapshot_run_id", str(run_id))
    return run_id


def _safe_snapshot_relative(relative: Any) -> PurePosixPath:
    if not isinstance(relative, str):
        raise LoveEngineError("unsafe_snapshot_path", str(relative))
    path = PurePosixPath(relative)
    if (
        not relative
        or len(relative) > 512
        or "\x00" in relative
        or "\\" in relative
        or path.is_absolute()
        or path.as_posix() != relative
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(":" in part or len(part) > 255 for part in path.parts)
    ):
        raise LoveEngineError("unsafe_snapshot_path", relative)
    return path


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _resolve_restore_target(path: Path) -> Path:
    raw = Path(path)
    for candidate in (raw, *raw.parents):
        if candidate.is_symlink():
            raise LoveEngineError("snapshot_symlink_forbidden", str(candidate))
    return raw.resolve()


def _verify_sqlite(path: Path) -> None:
    try:
        with closing(sqlite3.connect(path)) as database:
            result = database.execute("PRAGMA quick_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise LoveEngineError("snapshot_database_invalid", str(path), 3) from exc
    if result != ("ok",):
        raise LoveEngineError("snapshot_database_invalid", str(path), 3)


def _apply_restore_item(item: dict[str, Any]) -> None:
    destination = item["destination"]
    if item["is_directory"]:
        if destination.exists():
            _remove_path(destination)
        os.replace(item["staged"], destination)
    elif item["is_sqlite"]:
        # Windows cannot rename over a database still held by a stopped-but-not-yet-
        # collected connection. A validated copy remains recoverable via the backup.
        shutil.copy2(item["staged"], destination)
    else:
        os.replace(item["staged"], destination)


def _rollback_restore_item(item: dict[str, Any]) -> None:
    destination = item["destination"]
    if not item["had_existing"]:
        _remove_path(destination)
        return
    if item["is_sqlite"]:
        shutil.copy2(item["backup"], destination)
        return
    _remove_path(destination)
    if item["backup"].is_dir():
        shutil.copytree(item["backup"], destination)
    else:
        shutil.copy2(item["backup"], destination)


def _backup_sqlite(source: Path, destination: Path) -> None:
    source = Path(source)
    if not source.is_file() or source.is_symlink():
        raise LoveEngineError("snapshot_input_missing", str(source), 3)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source)) as source_db, closing(
        sqlite3.connect(destination)
    ) as target:
        source_db.backup(target)
    _verify_sqlite(destination)


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
    run_id = _validate_run_id(run_id)
    output = Path(output).resolve()
    created_at = int(time.time())
    snapshot = (output / f"{run_id}-{created_at}").resolve()
    if output not in snapshot.parents:
        raise LoveEngineError("unsafe_snapshot_path", str(snapshot))
    data = snapshot / "data"
    created = False
    try:
        data.mkdir(parents=True, exist_ok=False)
        created = True
        _backup_sqlite(Path(database), data / "pilot.sqlite")
        _backup_sqlite(Path(relay_database), data / "relay.sqlite")
        artifact_root = Path(artifact_root)
        if artifact_root.exists():
            for entry in artifact_root.rglob("*"):
                if entry.is_symlink():
                    raise LoveEngineError("snapshot_symlink_forbidden", str(entry))
            shutil.copytree(artifact_root, data / "artifacts")
        else:
            (data / "artifacts").mkdir()
        audit_log = Path(audit_log)
        if not audit_log.is_file() or audit_log.is_symlink():
            raise LoveEngineError("snapshot_input_missing", str(audit_log), 3)
        shutil.copy2(audit_log, data / "audit.jsonl")
        verify_audit_log(data / "audit.jsonl")
        chain_target = data / "chain"
        chain_target.mkdir()
        for name in ("deployment.json", "state.json"):
            source = Path(chain_root) / name
            if not source.is_file() or source.is_symlink():
                raise LoveEngineError("snapshot_input_missing", str(source), 3)
            shutil.copy2(source, chain_target / name)
            if not isinstance(read_json(chain_target / name), dict):
                raise LoveEngineError("snapshot_chain_state_invalid", name)
        write_json(
            snapshot / "manifest.json",
            {
                "schema_version": "loveengine.pilot-snapshot/1",
                "run_id": run_id,
                "created_at": str(created_at),
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
        verify_system_snapshot(snapshot)
    except Exception:
        if created and snapshot.exists():
            shutil.rmtree(snapshot)
        raise
    return {
        "snapshot": str(snapshot),
        "file_count": len(checksums),
        "checksums_hash": sha256_prefixed((snapshot / "checksums.json").read_bytes()),
    }


def verify_system_snapshot(snapshot: Path) -> dict[str, Any]:
    raw_snapshot = Path(snapshot)
    if raw_snapshot.is_symlink():
        raise LoveEngineError("snapshot_symlink_forbidden", str(raw_snapshot))
    snapshot = raw_snapshot.resolve()
    if not snapshot.is_dir():
        raise LoveEngineError("snapshot_not_found", str(snapshot), 3)
    checksums = read_json(snapshot / "checksums.json")
    try:
        validate_schema(checksums, "pilot-snapshot-checksums-v1.schema.json")
    except LoveEngineError as exc:
        raise LoveEngineError("snapshot_checksums_invalid", exc.message) from exc
    files = checksums.get("files")
    if not isinstance(files, dict) or not files:
        raise LoveEngineError("snapshot_checksums_invalid", str(snapshot))
    if "checksums.json" in files:
        raise LoveEngineError(
            "snapshot_checksums_invalid", "checksums.json is self-referential"
        )
    actual_files: set[str] = set()
    for entry in snapshot.rglob("*"):
        if entry.is_symlink():
            raise LoveEngineError(
                "snapshot_symlink_forbidden", entry.relative_to(snapshot).as_posix()
            )
        if entry.is_file():
            actual_files.add(entry.relative_to(snapshot).as_posix())
    for relative, expected in files.items():
        safe_relative = _safe_snapshot_relative(relative)
        if not isinstance(expected, str) or not SHA256_PATTERN.fullmatch(expected):
            raise LoveEngineError("snapshot_checksums_invalid", relative)
        path = (snapshot / Path(*safe_relative.parts)).resolve()
        if snapshot not in path.parents:
            raise LoveEngineError("unsafe_snapshot_path", str(relative))
        if not path.is_file() or sha256_prefixed(path.read_bytes()) != expected:
            raise LoveEngineError("snapshot_checksum_mismatch", str(relative))
    expected_files = set(files) | {"checksums.json"}
    extra = actual_files - expected_files
    if extra:
        raise LoveEngineError("snapshot_unchecked_file", sorted(extra)[0])
    missing_checksums = (actual_files - {"checksums.json"}) - set(files)
    if missing_checksums:
        raise LoveEngineError("snapshot_unchecked_file", sorted(missing_checksums)[0])
    missing = REQUIRED_SNAPSHOT_FILES - set(files)
    if missing:
        raise LoveEngineError("snapshot_required_file_missing", sorted(missing)[0])
    if not (snapshot / "data" / "artifacts").is_dir():
        raise LoveEngineError("snapshot_required_file_missing", "data/artifacts")
    manifest = read_json(snapshot / "manifest.json")
    if isinstance(manifest, dict) and str(manifest.get("retention_days", "")).startswith("-"):
        raise LoveEngineError(
            "invalid_snapshot_retention", str(manifest.get("retention_days"))
        )
    try:
        validate_schema(manifest, "pilot-snapshot-v1.schema.json")
    except LoveEngineError as exc:
        raise LoveEngineError("snapshot_manifest_invalid", exc.message) from exc
    _validate_run_id(manifest.get("run_id"))
    if int(manifest["retention_days"]) < 0:
        raise LoveEngineError("invalid_snapshot_retention", manifest["retention_days"])
    _verify_sqlite(snapshot / "data" / "pilot.sqlite")
    _verify_sqlite(snapshot / "data" / "relay.sqlite")
    verify_audit_log(snapshot / "data" / "audit.jsonl")
    for name in ("deployment.json", "state.json"):
        if not isinstance(read_json(snapshot / "data" / "chain" / name), dict):
            raise LoveEngineError("snapshot_chain_state_invalid", name)
    return {
        "valid": True,
        "run_id": manifest["run_id"],
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
    snapshot_root = Path(snapshot).resolve()
    requested = [
        (data / "pilot.sqlite", _resolve_restore_target(Path(database)), False),
        (data / "relay.sqlite", _resolve_restore_target(Path(relay_database)), False),
        (data / "audit.jsonl", _resolve_restore_target(Path(audit_log)), False),
        (
            data / "chain" / "deployment.json",
            _resolve_restore_target(Path(chain_root) / "deployment.json"),
            False,
        ),
        (
            data / "chain" / "state.json",
            _resolve_restore_target(Path(chain_root) / "state.json"),
            False,
        ),
        (data / "artifacts", _resolve_restore_target(Path(artifact_root)), True),
    ]
    destinations = [item[1] for item in requested]
    for destination in destinations:
        if destination == destination.parent:
            raise LoveEngineError("unsafe_snapshot_restore_target", str(destination))
        if snapshot_root in destination.parents or destination in snapshot_root.parents:
            raise LoveEngineError("unsafe_snapshot_restore_target", str(destination))
        if destination.is_symlink():
            raise LoveEngineError("snapshot_symlink_forbidden", str(destination))
    for _, destination, is_directory in requested:
        if destination.exists() and destination.is_dir() != is_directory:
            raise LoveEngineError("snapshot_restore_target_type", str(destination))
    for index, destination in enumerate(destinations):
        for other in destinations[index + 1 :]:
            if destination == other or destination in other.parents or other in destination.parents:
                raise LoveEngineError(
                    "snapshot_restore_target_conflict", f"{destination} / {other}"
                )

    prepared: list[dict[str, Any]] = []
    try:
        # Stage all replacement data and complete rollback copies before touching live state.
        for source, destination, is_directory in requested:
            destination.parent.mkdir(parents=True, exist_ok=True)
            container = Path(
                tempfile.mkdtemp(
                    prefix=f".{destination.name}.restore-", dir=destination.parent
                )
            )
            staged = container / "staged"
            backup = container / "backup"
            item = {
                "destination": destination,
                "staged": staged,
                "backup": backup,
                "container": container,
                "had_existing": destination.exists(),
                "is_directory": is_directory,
                "is_sqlite": source.suffix == ".sqlite",
            }
            prepared.append(item)
            if is_directory:
                shutil.copytree(source, staged)
            else:
                shutil.copy2(source, staged)
            if item["had_existing"]:
                if destination.is_dir():
                    shutil.copytree(destination, backup)
                else:
                    shutil.copy2(destination, backup)

        _verify_sqlite(prepared[0]["staged"])
        _verify_sqlite(prepared[1]["staged"])
        verify_audit_log(prepared[2]["staged"])
        if not isinstance(read_json(prepared[3]["staged"]), dict) or not isinstance(
            read_json(prepared[4]["staged"]), dict
        ):
            raise LoveEngineError("snapshot_chain_state_invalid", "prepared restore")

        committed: list[dict[str, Any]] = []
        try:
            for item in prepared:
                needs_preemptive_rollback = item["is_directory"] or item["is_sqlite"]
                if needs_preemptive_rollback:
                    committed.append(item)
                _apply_restore_item(item)
                if not needs_preemptive_rollback:
                    committed.append(item)
        except Exception as exc:
            rollback_errors = []
            for item in reversed(committed):
                try:
                    _rollback_restore_item(item)
                except Exception as rollback_exc:  # pragma: no cover - catastrophic I/O
                    rollback_errors.append(str(rollback_exc))
            if rollback_errors:
                raise LoveEngineError(
                    "snapshot_restore_rollback_failed", "; ".join(rollback_errors), 3
                ) from exc
            raise LoveEngineError("snapshot_restore_failed", str(exc), 3) from exc
    finally:
        for item in prepared:
            if item["container"].exists():
                shutil.rmtree(item["container"])
    return {"restored": True, **verification}


def prune_snapshots(output: Path, *, older_than_days: int = 30) -> dict[str, Any]:
    if older_than_days < 0:
        raise LoveEngineError("invalid_snapshot_retention", str(older_than_days))
    cutoff = int(time.time()) - older_than_days * 86400
    removed = []
    output = Path(output).resolve()
    for path in output.iterdir() if output.exists() else []:
        if path.is_symlink():
            continue
        if not path.is_dir() or not (path / "manifest.json").is_file():
            continue
        resolved = path.resolve()
        if output not in resolved.parents:
            raise LoveEngineError("unsafe_snapshot_path", str(path))
        manifest = read_json(path / "manifest.json")
        if isinstance(manifest, dict) and str(manifest.get("retention_days", "")).startswith("-"):
            raise LoveEngineError(
                "invalid_snapshot_retention", str(manifest.get("retention_days"))
            )
        try:
            validate_schema(manifest, "pilot-snapshot-v1.schema.json")
        except LoveEngineError as exc:
            raise LoveEngineError("snapshot_manifest_invalid", exc.message) from exc
        _validate_run_id(manifest.get("run_id"))
        created = int(manifest["created_at"])
        if created < cutoff:
            verify_system_snapshot(resolved)
            shutil.rmtree(resolved)
            removed.append(str(resolved))
    return {"removed": len(removed), "paths": removed}
