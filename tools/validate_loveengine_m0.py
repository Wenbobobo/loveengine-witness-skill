#!/usr/bin/env python3
"""Validate the LoveEngine Witness Skill M0 package."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "loveengine-witness"
MANIFEST_PATH = SKILL_DIR / "skill-manifest.m0.json"
ONBOARDING_PATH = SKILL_DIR / "agent-onboarding.md"
FIXTURE_DIR = SKILL_DIR / "fixtures"
NODE_PROFILE_PATH = FIXTURE_DIR / "agent-node-profile.fixture.json"
PROPAGATION_TASK_PATH = FIXTURE_DIR / "propagation-task.fixture.json"


REQUIRED_CAPABILITIES = {
    "explain_pol",
    "register_witness",
    "watch_live_text",
    "build_evidence_bundle",
    "sign_vote",
    "sync_evidence",
    "propagate_skill",
    "verify_manifest",
}

REQUIRED_PERMISSIONS = {
    "read_chain",
    "read_live_text",
    "read_local_sources",
    "request_local_signature",
    "submit_transaction",
}

REQUIRED_SOURCE_REFS = {
    "docs/specs/love-engine-master-plan.md",
    "docs/archive/specs/implemented/love-engine-local-witness-loop-spec.md",
    "docs/reference/source-materials/current/UAS接口文档.md",
    "docs/reference/source-materials/current/UAS 见证方案 2.0.md",
    "docs/kb/source-inventory.md",
}
REQUIRED_VERSION = "0.1.1-m0"
REQUIRED_SPEC_REF = "docs/specs/love-engine-master-plan.md"

REQUIRED_CONTRACT_PARAMS = {
    "min_valid_votes",
    "max_selected_witnesses",
    "approval_threshold_bps",
    "broadcast_window_seconds",
    "min_broadcast_interval_seconds",
}

VOTE_BINDINGS = {
    "chain_id",
    "verifying_contract",
    "proposal_id",
    "support",
    "reason_hash",
    "payload_hash",
    "nonce",
    "deadline",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path: Path) -> dict:
    if not path.exists():
        fail(f"missing required file: {path.relative_to(ROOT).as_posix()}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path.relative_to(ROOT).as_posix()}: {exc}")
    if not isinstance(data, dict):
        fail(f"JSON root must be an object: {path.relative_to(ROOT).as_posix()}")
    return data


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_manifest_bytes(manifest: dict) -> bytes:
    package_hash = manifest.get("package_hash")
    manifest["package_hash"] = "sha256:SELF"
    try:
        return json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    finally:
        manifest["package_hash"] = package_hash


def validate_manifest(manifest: dict, root: Path = ROOT) -> None:
    if manifest.get("skill_id") != "loveengine-witness":
        fail("manifest skill_id must be loveengine-witness")
    if manifest.get("status") != "draft":
        fail("M0 manifest status must be draft")
    if manifest.get("version") != REQUIRED_VERSION:
        fail(f"M0 manifest version must be {REQUIRED_VERSION}")

    source_refs = manifest.get("source_refs")
    if not isinstance(source_refs, list):
        fail("manifest source_refs must be a list")
    missing_refs = REQUIRED_SOURCE_REFS.difference(source_refs)
    if missing_refs:
        fail(f"manifest missing source refs: {sorted(missing_refs)}")

    source_hashes = manifest.get("source_hashes")
    if not isinstance(source_hashes, dict):
        fail("manifest source_hashes must be an object")
    for rel_path in source_refs:
        path = root / rel_path
        if not path.exists():
            fail(f"source ref does not exist: {rel_path}")
        expected = f"sha256:{sha256_file(path)}"
        if source_hashes.get(rel_path) != expected:
            fail(f"source hash mismatch for {rel_path}")

    spec_ref = manifest.get("spec_ref")
    if spec_ref != REQUIRED_SPEC_REF:
        fail(f"manifest spec_ref must point to {REQUIRED_SPEC_REF}")
    if manifest.get("spec_hash") != source_hashes.get(spec_ref):
        fail("manifest spec_hash must match source_hashes[spec_ref]")

    capabilities = set(manifest.get("capabilities", []))
    if not REQUIRED_CAPABILITIES.issubset(capabilities):
        fail(f"manifest missing capabilities: {sorted(REQUIRED_CAPABILITIES - capabilities)}")

    permissions = set(manifest.get("permissions", []))
    if not REQUIRED_PERMISSIONS.issubset(permissions):
        fail(f"manifest missing permissions: {sorted(REQUIRED_PERMISSIONS - permissions)}")

    forbidden_permissions = {"store_private_key", "sign_without_policy", "write_source_materials"}
    present_forbidden = forbidden_permissions.intersection(permissions)
    if present_forbidden:
        fail(f"manifest contains forbidden permissions: {sorted(present_forbidden)}")

    governance = manifest.get("governance_parameters")
    if not isinstance(governance, dict):
        fail("manifest governance_parameters must be an object")
    missing_params = REQUIRED_CONTRACT_PARAMS.difference(governance)
    if missing_params:
        fail(f"manifest missing governance parameters: {sorted(missing_params)}")
    if governance.get("min_valid_votes", {}).get("default") == 69 and not governance.get("min_valid_votes", {}).get("deploy_time_parameter"):
        fail("69-vote default must be marked as a deploy-time governance parameter")

    vote_signature = manifest.get("vote_signature")
    if not isinstance(vote_signature, dict):
        fail("manifest vote_signature must be an object")
    bindings = set(vote_signature.get("required_bindings", []))
    if not VOTE_BINDINGS.issubset(bindings):
        fail(f"VoteSignature missing bindings: {sorted(VOTE_BINDINGS - bindings)}")
    if vote_signature.get("latest_active_proposal_only") is not False:
        fail("VoteSignature must explicitly reject latest-active-proposal-only signing")

    relayer_policy = manifest.get("relayer_policy")
    if not isinstance(relayer_policy, dict):
        fail("manifest relayer_policy must be an object")
    if relayer_policy.get("batchRegister") != "any_relayer":
        fail("batchRegister must allow any relayer")
    if relayer_policy.get("batchVote") != "any_relayer":
        fail("batchVote must allow any relayer")
    if relayer_policy.get("private_keys_in_agent_context") is not False:
        fail("private keys must be excluded from Agent context")

    public_sink = manifest.get("public_sink")
    if public_sink != {"readonly_methods": ["getTotalUTO"], "owner_mutation_backdoor": False}:
        fail("PublicSink policy must be exactly readonly getTotalUTO with no owner backdoor")

    package_hash = manifest.get("package_hash")
    expected_package_hash = "sha256:" + hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()
    if package_hash != expected_package_hash:
        fail("manifest package_hash mismatch")


def validate_onboarding(manifest: dict, onboarding_path: Path = ONBOARDING_PATH) -> None:
    if not onboarding_path.exists():
        fail("missing agent onboarding summary")
    text = onboarding_path.read_text(encoding="utf-8")
    required_phrases = [
        "LoveEngine Witness Skill",
        "not a centralized web product",
        "private keys never enter Agent context",
        "VoteSignature binds proposal_id, nonce, deadline, and payload_hash",
        manifest["spec_hash"],
    ]
    missing = [phrase for phrase in required_phrases if phrase not in text]
    if missing:
        fail(f"onboarding missing required phrases: {missing}")


def validate_node_profile(manifest: dict, node_profile_path: Path = NODE_PROFILE_PATH) -> None:
    node = load_json(node_profile_path)
    if node.get("skill_id") != manifest["skill_id"]:
        fail("node profile skill_id mismatch")
    if node.get("skill_version") != manifest["version"]:
        fail("node profile skill_version mismatch")
    if not node.get("address", "").startswith("0x") or len(node.get("address", "")) != 42:
        fail("node profile must contain an EVM address")
    if node.get("signer_type") not in {"clef", "cast", "browser_wallet", "aa"}:
        fail("node profile signer_type is not supported")
    if "vote_signing" in node.get("capabilities", []) and node.get("private_key_available_to_agent") is not False:
        fail("vote-signing node must declare private_key_available_to_agent=false")


def validate_propagation_task(manifest: dict, propagation_task_path: Path = PROPAGATION_TASK_PATH) -> None:
    task = load_json(propagation_task_path)
    if task.get("task_type") != "propagate_skill":
        fail("propagation fixture task_type must be propagate_skill")
    if task.get("manifest_ref") != "skills/loveengine-witness/skill-manifest.m0.json":
        fail("propagation fixture manifest_ref mismatch")
    if task.get("manifest_hash") != manifest["package_hash"]:
        fail("propagation fixture manifest_hash must match manifest package_hash")
    if task.get("payload_hash") != manifest["package_hash"]:
        fail("propagation fixture payload_hash must match manifest package_hash")
    required_steps = {"verify_manifest_hash", "verify_source_hashes", "run_capability_self_check"}
    if not required_steps.issubset(set(task.get("acceptance_checks", []))):
        fail("propagation fixture missing acceptance checks")
    if task.get("assigned_nodes") != []:
        fail("M0 propagation fixture should not preassign nodes")
    if task.get("status") != "fixture" or task.get("expires_at") is not None:
        fail("M0 propagation fixture must be static and non-expiring")


def validate_package(
    root: Path = ROOT,
    manifest_path: Path = MANIFEST_PATH,
    onboarding_path: Path = ONBOARDING_PATH,
    node_profile_path: Path = NODE_PROFILE_PATH,
    propagation_task_path: Path = PROPAGATION_TASK_PATH,
) -> None:
    manifest = load_json(manifest_path)
    validate_manifest(manifest, root=root)
    validate_onboarding(manifest, onboarding_path=onboarding_path)
    validate_node_profile(manifest, node_profile_path=node_profile_path)
    validate_propagation_task(manifest, propagation_task_path=propagation_task_path)


def run_tamper_check() -> None:
    with tempfile.TemporaryDirectory(prefix="loveengine-m0-tamper-") as tmp:
        tmp_root = Path(tmp)
        for rel_path in REQUIRED_SOURCE_REFS:
            target = tmp_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel_path, target)

        tmp_skill = tmp_root / "skills" / "loveengine-witness"
        tmp_fixtures = tmp_skill / "fixtures"
        tmp_fixtures.mkdir(parents=True, exist_ok=True)
        shutil.copy2(MANIFEST_PATH, tmp_skill / "skill-manifest.json")
        shutil.copy2(ONBOARDING_PATH, tmp_skill / "agent-onboarding.md")
        shutil.copy2(NODE_PROFILE_PATH, tmp_fixtures / "agent-node-profile.fixture.json")
        shutil.copy2(PROPAGATION_TASK_PATH, tmp_fixtures / "propagation-task.fixture.json")

        tampered_manifest_path = tmp_skill / "skill-manifest.json"
        manifest = json.loads(tampered_manifest_path.read_text(encoding="utf-8"))
        removed_ref = sorted(REQUIRED_SOURCE_REFS)[0]
        manifest["source_refs"] = [
            ref for ref in manifest["source_refs"] if ref != removed_ref
        ]
        tampered_manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        expected_failure = (
            f"FAIL: manifest missing source refs: {[removed_ref]!r}\n"
        )
        captured_stderr = io.StringIO()
        try:
            with redirect_stderr(captured_stderr):
                validate_package(
                    root=tmp_root,
                    manifest_path=tampered_manifest_path,
                    onboarding_path=tmp_skill / "agent-onboarding.md",
                    node_profile_path=tmp_fixtures / "agent-node-profile.fixture.json",
                    propagation_task_path=tmp_fixtures / "propagation-task.fixture.json",
                )
        except SystemExit as exc:
            if exc.code == 1 and captured_stderr.getvalue() == expected_failure:
                print("LoveEngine Witness M0 tamper check rejected modified manifest")
                return
            sys.stderr.write(captured_stderr.getvalue())
            raise
        fail("tamper check accepted a modified manifest")


def main() -> None:
    if "--tamper-check" in sys.argv[1:]:
        run_tamper_check()
        return
    validate_package()
    print("LoveEngine Witness M0 validation passed")


if __name__ == "__main__":
    main()
