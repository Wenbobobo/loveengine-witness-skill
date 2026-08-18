#!/usr/bin/env python3
"""Refresh current and M0 manifest hashes after intentional source changes."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from eth_hash.auto import keccak
from loveengine_witness.hashes import source_sha256_prefixed
from loveengine_witness.release_identity import PROTOCOL_VERSION, SKILL_VERSION


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "loveengine-witness"
CURRENT = SKILL / "skill-manifest.json"
M0 = SKILL / "skill-manifest.m0.json"

RELEASE_ROOT_REFS = [
    "LICENSE",
    "QA.md",
    "docs/kb/sources.json",
    "docs/specs/love-engine-pre-enterprise-remote-lab.md",
    "docs/specs/love-engine-invited-public-pilot.md",
    "docs/archive/specs/implemented/love-engine-witness-core-optimization.md",
    "docs/architecture/witness-core-and-data-flow.zh-CN.md",
    "docs/archive/specs/implemented/love-engine-lan-pilot-spec.md",
    "docs/api/lan-pilot-api.md",
    "docs/api/cli-reference.md",
    "docs/api/loveengine-contract-api.md",
    "docs/archive/development/2026-06-m3-m5/m5-acceptance-report.md",
    "docs/archive/planning/2026-06-23/m5-release-closeout-plan.md",
    "docs/development/contract2-comparison-and-recommendations.md",
    "docs/development/participant-runbook.zh-CN.md",
    "docs/development/runbooks/remote-lab-flow.zh-CN.md",
    "docs/archive/planning/2026-06-24/m6-demo-docs-publication-plan.md",
    "docs/development/runbooks/operator-flow.zh-CN.md",
    "docs/development/runbooks/observation-node-flow.zh-CN.md",
    "docs/development/runbooks/voting-witness-flow.zh-CN.md",
    "docs/development/runbooks/public-viewer-flow.zh-CN.md",
    "docs/development/runbooks/publisher-flow.zh-CN.md",
    "docs/development/runbooks/invited-public-pilot.zh-CN.md",
    "docs/articles/loveengine-technical-architecture.zh-CN.md",
    "docs/reference/contracts/contract-team-v2/README.md",
    "docs/reference/licenses/scc0-provenance.md",
    "skills/loveengine-witness/SKILL.md",
    "skills/loveengine-witness/agents/openai.yaml",
    "schemas/pilot-invite-v1.schema.json",
    "schemas/pilot-config-v1.schema.json",
    "schemas/observe-live-text-payload-v1.schema.json",
    "schemas/review-dispute-payload-v1.schema.json",
    "schemas/live-observation-receipt-v1.schema.json",
    "schemas/observation-set-v1.schema.json",
    "schemas/onchain-proposal-plan-v1.schema.json",
    "schemas/pilot-transcript-v1.schema.json",
    "schemas/pilot-transcript-v2.schema.json",
    "schemas/witness-core-transcript-v1.schema.json",
    "schemas/witness-core-transcript-v2.schema.json",
    "schemas/node-trust-policy-v1.schema.json",
    "schemas/pilot-config-v2.schema.json",
    "schemas/pilot-invite-v2.schema.json",
    "schemas/external-signer-config-v1.schema.json",
    "schemas/chain-transaction-plan-v1.schema.json",
    "schemas/signed-chain-transaction-v1.schema.json",
    "schemas/participant-attestation-v1.schema.json",
    "schemas/invited-pilot-service-config-v1.schema.json",
    "schemas/invited-public-pilot-plan-v1.schema.json",
    "schemas/pilot-snapshot-v1.schema.json",
    "schemas/pilot-snapshot-checksums-v1.schema.json",
    "contracts/dependency-lock.json",
    "src/loveengine_witness/release_identity.py",
    "src/loveengine_witness/cli_trust.py",
    "src/loveengine_witness/cli_pilot.py",
    "src/loveengine_witness/toolchain.py",
    "src/loveengine_witness/contract_dependency_bundle.py",
    "src/loveengine_witness/agent_session.py",
    "src/loveengine_witness/review_evidence.py",
    "src/loveengine_witness/secrets.py",
    "src/loveengine_witness/pilot_runtime.py",
    "src/loveengine_witness/pilot_config.py",
    "src/loveengine_witness/pilot_surfaces.py",
    "src/loveengine_witness/tailscale_serve.py",
    "src/loveengine_witness/pilot_auth.py",
    "src/loveengine_witness/pilot_audit.py",
    "src/loveengine_witness/pilot_ui.py",
    "src/loveengine_witness/pilot_task_ingress.py",
    "src/loveengine_witness/pilot_task_operator.py",
    "src/loveengine_witness/package.py",
    "src/loveengine_witness/pilot_server.py",
    "src/loveengine_witness/observation.py",
    "src/loveengine_witness/live_observation_node.py",
    "src/loveengine_witness/pilot_chain.py",
    "src/loveengine_witness/witness_vote.py",
    "src/loveengine_witness/pilot_snapshot.py",
    "src/loveengine_witness/pilot_transcript.py",
    "src/loveengine_witness/core_transcript.py",
    "src/loveengine_witness/signer_client.py",
    "src/loveengine_witness/registry_transactions.py",
    "src/loveengine_witness/participant_attestation.py",
    "src/loveengine_witness/invited_operator.py",
    "src/loveengine_witness/rpc_endpoints.py",
    "src/loveengine_witness/invited_pilot_plan.py",
    "src/loveengine_witness/trust_policy.py",
    "src/loveengine_witness/pilot_demo.py",
    "src/loveengine_witness/pilot_phases.py",
    "src/loveengine_witness/pilot_soak.py",
    "src/loveengine_witness/pilot_soak_process.py",
    "src/loveengine_witness/ui_fixture.py",
    "plugins/loveengine-witness/.codex-plugin/plugin.json",
    "plugins/loveengine-witness/skills/loveengine-witness/SKILL.md",
    "plugins/marketplace.example.json",
    "tools/refresh_manifests.py",
    "tools/check.py",
    "tools/scan_secrets.py",
    "tools/run_release_checks.ps1",
    "tools/run_engineering_acceptance.py",
    "tools/run_core_experiments.ps1",
    "tools/run_core_experiments.py",
    "tools/remote_host_preflight.py",
    "tools/enqueue_pilot_task.py",
    "tools/start_shared_quickstart.py",
    "tools/start_shared_core.py",
    "tools/run_remote_lab.py",
    "tools/validate_invited_pilot_plan.py",
    "config/examples/invited-public-pilot.sepolia.example.yaml",
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
    return source_sha256_prefixed(path)


def package_hash(value: dict) -> str:
    view = dict(value)
    view["package_hash"] = "sha256:SELF"
    raw = json.dumps(
        view, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def inventory_current_files() -> list[str]:
    inventory = load(ROOT / "docs" / "kb" / "sources.json")
    refs: list[str] = []
    for record in inventory:
        if record.get("status") != "current":
            continue
        ref = record.get("path")
        if (
            isinstance(ref, str)
            and ref not in UNPROTECTED_DOC_ASSETS
            and ref != CURRENT.relative_to(ROOT).as_posix()
            and (ROOT / ref).is_file()
        ):
            refs.append(ref)
    return refs


def refresh_current() -> None:
    value = load(CURRENT)
    moved_refs = {
        "docs/specs/love-engine-live-evidence-pilot-spec.md": "docs/archive/specs/implemented/love-engine-live-evidence-pilot-spec.md",
        "docs/specs/love-engine-lan-pilot-spec.md": "docs/archive/specs/implemented/love-engine-lan-pilot-spec.md",
        "docs/specs/love-engine-contract-public-pilot-spec.md": "docs/archive/specs/implemented/love-engine-contract-public-pilot-spec.md",
        "docs/specs/love-engine-witness-core-optimization.md": "docs/archive/specs/implemented/love-engine-witness-core-optimization.md",
        "docs/development/m5-release-closeout-plan.md": "docs/archive/planning/2026-06-23/m5-release-closeout-plan.md",
        "docs/development/m6-demo-docs-publication-plan.md": "docs/archive/planning/2026-06-24/m6-demo-docs-publication-plan.md",
        "docs/development/m3-demo-runbook.md": "docs/archive/development/2026-06-m3-m5/m3-demo-runbook.md",
        "docs/development/m3-acceptance-report.md": "docs/archive/development/2026-06-m3-m5/m3-acceptance-report.md",
        "docs/development/m4-skill-supervision-and-next-stage-gaps.md": "docs/archive/development/2026-06-m3-m5/m4-skill-supervision-and-next-stage-gaps.md",
        "docs/development/m5-acceptance-report.md": "docs/archive/development/2026-06-m3-m5/m5-acceptance-report.md",
    }
    refs = [
        moved_refs.get(ref, ref)
        for ref in value["source_refs"]
        if ref not in UNPROTECTED_DOC_ASSETS
    ]
    for ref in [*inventory_current_files(), *RELEASE_ROOT_REFS]:
        if ref not in refs:
            refs.append(ref)
    missing = [ref for ref in refs if not (ROOT / ref).is_file()]
    if missing:
        raise SystemExit(f"missing manifest refs: {missing}")
    value["version"] = SKILL_VERSION
    value["protocol"] = PROTOCOL_VERSION
    value["source_refs"] = refs
    value["source_hashes"] = {ref: sha(ROOT / ref) for ref in refs}
    binding = value["registry_binding"]
    binding["version_hash"] = "0x" + keccak(SKILL_VERSION.encode("utf-8")).hex()
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
    value["network"]["task_types"] = [
        "observe_live_text",
        "review_dispute",
    ]
    for legacy_command in ("local_loop", "network_demo", "live_demo"):
        value["commands"].pop(legacy_command, None)
    value["commands"].update(
        {
            "package_build": "loveengine package build --output <dir>",
            "package_verify": "loveengine package verify <archive> --expected-package-hash <registry-keccak>",
            "package_install": "loveengine package install <archive> --target <dir> --expected-package-hash <registry-keccak>",
            "package_self_check": "loveengine package self-check --root <dir> --expected-package-hash <registry-keccak>",
            "registry_publish": "loveengine registry publish --input <release> --dry-run",
            "registry_verify": "loveengine registry verify --artifact <archive> --rpc-url <url> --chain-id <id> --registry <address> --publisher <address>",
            "registry_transaction": "loveengine registry transaction deploy-plan|publish-plan|sign|submit",
            "signer_inspect": "loveengine signer inspect --config <file>",
            "node_connect": "loveengine node connect --invite <file> --trust-policy <policy> --package <archive> --profile <profile> --rpc-url <url> --address <node>",
            "pilot_serve": "loveengine pilot serve --config <path> [--tailscale-serve]",
            "network_task_sign": "loveengine network task sign --input <file> --signer-config <file> --output <file>",
            "pilot_task_enqueue": "loveengine pilot task enqueue --input <file> --admin-url <loopback> --origin <loopback> --token-file <file>",
            "participant_attest": "loveengine participant attest --input <file> --signer-config <file> --output <file>",
            "pilot_quickstart": "loveengine pilot quickstart --root <dir> --headless",
            "pilot_contracts_prepare": "loveengine pilot contracts prepare",
            "pilot_status": "loveengine pilot status --url <url>",
            "pilot_chain": "loveengine pilot chain init|start|status|snapshot|restore",
            "explicit_vote": "loveengine witness vote approve",
            "pilot_demo": "loveengine demo lan-pilot --stage core --events 12 --observers 10",
            "governance_demo": "loveengine demo lan-pilot --stage governance --events 12 --observers 10",
            "pilot_transcript_verify": "loveengine pilot transcript verify <path> [--rpc-url <url>] [--trust-policy <policy>]",
            "pilot_soak": "loveengine pilot soak --stage core --duration-seconds 900 --events 30 --observers 10 --core-transcript-version 2",
            "engineering_acceptance": "uv run python tools/run_engineering_acceptance.py --output <dir>",
            "core_experiment": "uv run python tools/run_core_experiments.py",
            "remote_lab": "uv run python tools/run_remote_lab.py preflight|run",
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
    refresh_m0()
    refresh_current()
    print("LoveEngine current and M0 manifest hashes refreshed")


if __name__ == "__main__":
    main()
