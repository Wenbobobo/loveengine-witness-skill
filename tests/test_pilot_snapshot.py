from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.pilot_server import AuditLog, verify_audit_log
from loveengine_witness.pilot_snapshot import (
    create_system_snapshot,
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
