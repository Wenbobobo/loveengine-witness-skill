#!/usr/bin/env python3
"""Refresh current and M0 manifest hashes after intentional source changes."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from eth_hash.auto import keccak


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "loveengine-witness"
CURRENT = SKILL / "skill-manifest.json"
M0 = SKILL / "skill-manifest.m0.json"

M6_REFS = [
    "LICENSE",
    "docs/specs/love-engine-contract-public-pilot-spec.md",
    "docs/archive/specs/implemented/love-engine-lan-pilot-spec.md",
    "docs/api/lan-pilot-api.md",
    "docs/api/cli-reference.md",
    "docs/api/loveengine-contract-api.md",
    "docs/development/m5-acceptance-report.md",
    "docs/development/m5-release-closeout-plan.md",
    "docs/development/contract2-comparison-and-recommendations.md",
    "docs/development/participant-runbook.zh-CN.md",
    "docs/development/m6-demo-docs-publication-plan.md",
    "docs/development/runbooks/operator-flow.zh-CN.md",
    "docs/development/runbooks/observation-node-flow.zh-CN.md",
    "docs/development/runbooks/voting-witness-flow.zh-CN.md",
    "docs/development/runbooks/public-viewer-flow.zh-CN.md",
    "docs/development/runbooks/publisher-flow.zh-CN.md",
    "docs/articles/loveengine-technical-architecture.zh-CN.md",
    "docs/reference/contracts/contract-team-v2/README.md",
    "docs/reference/licenses/scc0-provenance.md",
    "skills/loveengine-witness/SKILL.md",
    "skills/loveengine-witness/agents/openai.yaml",
    "schemas/pilot-invite-v1.schema.json",
    "schemas/pilot-config-v1.schema.json",
    "schemas/observe-live-text-payload-v1.schema.json",
    "schemas/live-observation-receipt-v1.schema.json",
    "schemas/observation-set-v1.schema.json",
    "schemas/onchain-proposal-plan-v1.schema.json",
    "schemas/pilot-transcript-v1.schema.json",
    "src/loveengine_witness/package.py",
    "src/loveengine_witness/pilot_server.py",
    "src/loveengine_witness/observation.py",
    "src/loveengine_witness/live_observation_node.py",
    "src/loveengine_witness/pilot_chain.py",
    "src/loveengine_witness/witness_vote.py",
    "src/loveengine_witness/pilot_snapshot.py",
    "src/loveengine_witness/pilot_transcript.py",
    "src/loveengine_witness/pilot_demo.py",
    "src/loveengine_witness/pilot_soak.py",
    "src/loveengine_witness/pilot_soak_process.py",
    "src/loveengine_witness/ui_fixture.py",
    "plugins/loveengine-witness/.codex-plugin/plugin.json",
    "plugins/loveengine-witness/skills/loveengine-witness/SKILL.md",
    "plugins/marketplace.example.json",
    "tools/refresh_manifests.py",
]

# Documentation screenshots are tracked through the source inventory, but they
# are intentionally outside the protocol trust root. Git LFS checkouts expose
# pointer bytes before the asset fetch step, so protecting them in the manifest
# would make repository validation depend on checkout implementation details.
UNPROTECTED_DOC_ASSETS = {
    "docs/assets/operator-console.png",
    "docs/assets/read-only-dashboard.png",
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def package_hash(value: dict) -> str:
    view = dict(value)
    view["package_hash"] = "sha256:SELF"
    raw = json.dumps(
        view, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def refresh_current() -> None:
    value = load(CURRENT)
    moved_refs = {
        "docs/specs/love-engine-live-evidence-pilot-spec.md": "docs/archive/specs/implemented/love-engine-live-evidence-pilot-spec.md",
        "docs/specs/love-engine-lan-pilot-spec.md": "docs/archive/specs/implemented/love-engine-lan-pilot-spec.md",
    }
    refs = [
        moved_refs.get(ref, ref)
        for ref in value["source_refs"]
        if ref not in UNPROTECTED_DOC_ASSETS
    ]
    for ref in M6_REFS:
        if ref not in refs:
            refs.append(ref)
    missing = [ref for ref in refs if not (ROOT / ref).is_file()]
    if missing:
        raise SystemExit(f"missing manifest refs: {missing}")
    value["version"] = "0.6.0-contract-public-pilot"
    value["protocol"] = "loveengine-witness-net/0.6"
    value["source_refs"] = refs
    value["source_hashes"] = {ref: sha(ROOT / ref) for ref in refs}
    binding = value["registry_binding"]
    binding["version_hash"] = "0x" + keccak(b"0.6.0-contract-public-pilot").hex()
    binding.pop("artifact_package_hash", None)
    binding.pop("hash_algorithm", None)
    binding["package_hash_source"] = "SkillRegistry.Release.packageHash"
    binding["package_hash_algorithm"] = "keccak256"
    value["distribution"] = {
        "archive_format": "zip",
        "deterministic": True,
        "checksums": "checksums.json",
        "sbom": "sbom.spdx.json",
        "registry_hash": "keccak256(actual_zip_bytes)",
    }
    value["commands"].update(
        {
            "package_build": "loveengine package build --output <dir>",
            "package_verify": "loveengine package verify <archive>",
            "package_install": "loveengine package install <archive> --target <dir>",
            "pilot_serve": "loveengine pilot serve --config <path>",
            "pilot_quickstart": "loveengine pilot quickstart --root <dir> --headless",
            "pilot_status": "loveengine pilot status --url <url>",
            "pilot_chain": "loveengine pilot chain init|start|status|snapshot|restore",
            "explicit_vote": "loveengine witness vote approve",
            "pilot_demo": "loveengine demo lan-pilot --events 12 --observers 10",
            "pilot_transcript_verify": "loveengine pilot transcript verify <path>",
            "pilot_soak": "loveengine pilot soak --duration-seconds 14400 --events 240 --observers 10",
        }
    )
    value["package_hash"] = package_hash(value)
    save(CURRENT, value)


def refresh_m0() -> None:
    value = load(M0)
    value["source_hashes"] = {
        ref: sha(ROOT / ref) for ref in value["source_refs"]
    }
    value["spec_hash"] = value["source_hashes"][value["spec_ref"]]
    value["package_hash"] = package_hash(value)
    save(M0, value)

    onboarding = SKILL / "agent-onboarding.md"
    text = onboarding.read_text(encoding="utf-8")
    text = re.sub(r"`sha256:[0-9a-f]{64}`", f"`{value['spec_hash']}`", text)
    onboarding.write_text(text, encoding="utf-8")

    fixture = SKILL / "fixtures" / "propagation-task.fixture.json"
    task = load(fixture)
    task["manifest_hash"] = value["package_hash"]
    task["payload_hash"] = value["package_hash"]
    save(fixture, task)


def main() -> None:
    refresh_current()
    refresh_m0()
    print("LoveEngine current and M0 manifest hashes refreshed")


if __name__ == "__main__":
    main()
