from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.manifest import verify_manifest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "skills" / "loveengine-witness" / "skill-manifest.json"
M0_MANIFEST = ROOT / "skills" / "loveengine-witness" / "skill-manifest.m0.json"
NODE = (
    ROOT
    / "skills"
    / "loveengine-witness"
    / "fixtures"
    / "agent-node-profile.fixture.json"
)
PROPAGATION = (
    ROOT
    / "skills"
    / "loveengine-witness"
    / "fixtures"
    / "propagation-task.fixture.json"
)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_m0_revision_points_to_current_plan_and_spec() -> None:
    manifest = load(M0_MANIFEST)

    assert manifest["version"] == "0.1.1-m0"
    assert manifest["spec_ref"] == "docs/specs/love-engine-master-plan.md"
    assert set(manifest["source_refs"]) == {
        "docs/specs/love-engine-master-plan.md",
        "docs/archive/specs/implemented/love-engine-local-witness-loop-spec.md",
        "docs/reference/source-materials/current/UAS接口文档.md",
        "docs/reference/source-materials/current/UAS 见证方案 2.0.md",
        "docs/kb/source-inventory.md",
    }


def test_m0_fixtures_match_revision_and_are_static() -> None:
    manifest = load(M0_MANIFEST)
    node = load(NODE)
    propagation = load(PROPAGATION)

    assert node["skill_version"] == manifest["version"]
    assert propagation["status"] == "fixture"
    assert propagation["expires_at"] is None
    assert propagation["manifest_hash"] == manifest["package_hash"]
    assert propagation["payload_hash"] == manifest["package_hash"]


def test_current_manifest_is_lan_pilot() -> None:
    manifest = load(MANIFEST)

    assert manifest["schema_version"] == "loveengine.skill-manifest/0.3"
    assert manifest["version"] == "0.5.0-lan-pilot"
    assert manifest["registry_binding"]["chain_id"] == "31337"
    assert manifest["network"]["task_types"] == [
        "propagate_skill",
        "observe_broadcast",
        "observe_live_text",
        "review_dispute",
    ]
    assert manifest["eip712"]["domain_version"] == "2"
    assert manifest["security"]["private_keys_in_agent_context"] is False
    assert "docs/assets/operator-console.png" not in manifest["source_refs"]
    assert "docs/assets/read-only-dashboard.png" not in manifest["source_refs"]


def test_m0_validator_accepts_revision_and_rejects_tampering() -> None:
    validation = subprocess.run(
        [sys.executable, "tools/validate_loveengine_m0.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    tamper = subprocess.run(
        [sys.executable, "tools/validate_loveengine_m0.py", "--tamper-check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert validation.returncode == 0, validation.stdout + validation.stderr
    assert tamper.returncode == 0, tamper.stdout + tamper.stderr
    assert "tamper check rejected modified manifest" in tamper.stdout


def test_manifest_rejects_unknown_or_missing_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "skills" / "loveengine-witness" / "manifest.json"
    path.parent.mkdir(parents=True)
    manifest = load(MANIFEST)

    for schema_version in ("loveengine.skill-manifest/9.9", None):
        changed = dict(manifest)
        if schema_version is None:
            changed.pop("schema_version")
        else:
            changed["schema_version"] = schema_version
        path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(LoveEngineError) as error:
            verify_manifest(path)
        assert error.value.code == "unsupported_schema_version"
