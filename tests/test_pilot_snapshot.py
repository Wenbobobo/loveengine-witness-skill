from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import loveengine_witness.pilot_snapshot as snapshot_module
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.hashes import sha256_prefixed
from loveengine_witness.pilot_server import AuditLog, verify_audit_log
from loveengine_witness.pilot_snapshot import (
    create_system_snapshot,
    prune_snapshots,
    restore_system_snapshot,
    verify_system_snapshot,
)


def _seed(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True)
    database = root / "pilot.sqlite"
    relay = root / "relay.sqlite"
    for path in (database, relay):
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE state(value TEXT)")
            db.execute("INSERT INTO state VALUES ('committed')")
    artifacts = root / "artifacts" / "sha256" / "aa"
    artifacts.mkdir(parents=True)
    (artifacts / ("aa" * 32)).write_bytes(b"artifact")
    audit = root / "audit.jsonl"
    log = AuditLog(audit, "snapshot-test")
    log.write("seed", correlation_id="c1")
    chain = root / "chain"
    chain.mkdir()
    (chain / "deployment.json").write_text('{"chain_id":"31337"}\n', encoding="utf-8")
    (chain / "state.json").write_text(
        '{"format":"anvil_dumpState/v1","state":"0x00","checksum":"fixture"}\n',
        encoding="utf-8",
    )
    return {
        "database": database,
        "relay_database": relay,
        "artifact_root": root / "artifacts",
        "audit_log": audit,
        "chain_root": chain,
    }


def test_system_snapshot_detects_corruption_and_restores_state(tmp_path: Path) -> None:
    paths = _seed(tmp_path / "runtime")
    snapshot = create_system_snapshot(
        run_id="snapshot-test",
        output=tmp_path / "snapshots",
        **paths,
    )
    assert verify_system_snapshot(Path(snapshot["snapshot"]))["valid"] is True
    assert verify_audit_log(paths["audit_log"])["valid"] is True

    paths["database"].write_bytes(b"damaged")
    restore_system_snapshot(Path(snapshot["snapshot"]), **paths)
    with sqlite3.connect(paths["database"]) as db:
        assert db.execute("SELECT value FROM state").fetchone()[0] == "committed"

    copied = Path(snapshot["snapshot"]) / "data" / "pilot.sqlite"
    copied.write_bytes(b"tampered")
    with pytest.raises(LoveEngineError) as exc:
        verify_system_snapshot(Path(snapshot["snapshot"]))
    assert exc.value.code == "snapshot_checksum_mismatch"


def test_audit_hash_chain_rejects_modified_record(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, "run-1")
    log.write("first")
    log.write("second")
    assert verify_audit_log(path)["record_count"] == 2

    records = path.read_text(encoding="utf-8").splitlines()
    value = json.loads(records[0])
    value["event"] = "modified"
    records[0] = json.dumps(value)
    path.write_text("\n".join(records) + "\n", encoding="utf-8")
    with pytest.raises(LoveEngineError):
        verify_audit_log(path)


@pytest.mark.parametrize("run_id", ["", "../escape", "nested/run", "C:\\escape"])
def test_snapshot_create_rejects_unsafe_run_id(tmp_path: Path, run_id: str) -> None:
    paths = _seed(tmp_path / "runtime")
    with pytest.raises(LoveEngineError) as error:
        create_system_snapshot(
            run_id=run_id,
            output=tmp_path / "snapshots",
            **paths,
        )
    assert error.value.code == "invalid_snapshot_run_id"
    assert not (tmp_path / "escape").exists()


def test_snapshot_rejects_unchecked_and_traversal_paths(tmp_path: Path) -> None:
    paths = _seed(tmp_path / "runtime")
    result = create_system_snapshot(
        run_id="snapshot-test",
        output=tmp_path / "snapshots",
        **paths,
    )
    snapshot = Path(result["snapshot"])

    unexpected = snapshot / "data" / "unexpected.txt"
    unexpected.write_text("unchecked", encoding="utf-8")
    with pytest.raises(LoveEngineError) as error:
        verify_system_snapshot(snapshot)
    assert error.value.code == "snapshot_unchecked_file"
    unexpected.unlink()

    checksums_path = snapshot / "checksums.json"
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    checksums["files"]["../escape"] = "sha256:" + "0" * 64
    checksums_path.write_text(json.dumps(checksums), encoding="utf-8")
    with pytest.raises(LoveEngineError) as error:
        verify_system_snapshot(snapshot)
    assert error.value.code == "unsafe_snapshot_path"


def test_snapshot_rejects_negative_retention(tmp_path: Path) -> None:
    paths = _seed(tmp_path / "runtime")
    result = create_system_snapshot(
        run_id="snapshot-test",
        output=tmp_path / "snapshots",
        **paths,
    )
    snapshot = Path(result["snapshot"])
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["retention_days"] = "-1"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    checksums_path = snapshot / "checksums.json"
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    checksums["files"]["manifest.json"] = sha256_prefixed(
        manifest_path.read_bytes()
    )
    checksums_path.write_text(json.dumps(checksums), encoding="utf-8")
    with pytest.raises(LoveEngineError) as error:
        verify_system_snapshot(snapshot)
    assert error.value.code == "invalid_snapshot_retention"

    with pytest.raises(LoveEngineError) as error:
        prune_snapshots(tmp_path / "snapshots", older_than_days=-1)
    assert error.value.code == "invalid_snapshot_retention"


def test_snapshot_restore_rolls_back_all_replaced_state_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed(tmp_path / "runtime")
    result = create_system_snapshot(
        run_id="snapshot-test",
        output=tmp_path / "snapshots",
        **paths,
    )
    for database in (paths["database"], paths["relay_database"]):
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE state SET value = 'live-state'")

    real_apply = snapshot_module._apply_restore_item
    calls = 0

    def fail_second_replace(item: dict[str, object]) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected restore failure")
        real_apply(item)

    monkeypatch.setattr(snapshot_module, "_apply_restore_item", fail_second_replace)
    with pytest.raises(LoveEngineError) as error:
        restore_system_snapshot(Path(result["snapshot"]), **paths)
    assert error.value.code == "snapshot_restore_failed"
    for database in (paths["database"], paths["relay_database"]):
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT value FROM state").fetchone()[0] == "live-state"
