from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


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
        "docs/specs/love-engine-local-witness-loop-spec.md",
        "UAS接口文档.md",
        "UAS 见证方案 2.0.md",
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


def test_current_manifest_is_local_loop_v2() -> None:
    manifest = load(MANIFEST)

    assert manifest["schema_version"] == "loveengine.skill-manifest/0.2"
    assert manifest["version"] == "0.2.0-local-loop"
    assert manifest["security"]["private_keys_in_agent_context"] is False


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
