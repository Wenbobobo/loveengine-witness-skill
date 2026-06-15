# Agent and Skill API

本文定义 LoveEngine Witness Skill 面向 Agent、Harness/Hermes adapter 和本地 CLI 的数据结构。状态：M0 已有 manifest 和 fixture；M1/M2 需要把这些结构升级成 schema 和工具命令。

## 当前 M0 文件

| 文件 | 状态 |
| --- | --- |
| `skills/loveengine-witness/skill-manifest.json` | M0 implemented |
| `skills/loveengine-witness/agent-onboarding.md` | M0 implemented |
| `skills/loveengine-witness/fixtures/agent-node-profile.fixture.json` | M0 fixture |
| `skills/loveengine-witness/fixtures/propagation-task.fixture.json` | M0 fixture |
| `tools/validate_loveengine_m0.py` | M0 implemented |
| `tools/loveengine_m0_self_check.py` | M0 implemented |

## SkillManifest

M0 implemented fields:

```json
{
  "skill_id": "loveengine-witness",
  "name": "LoveEngine Witness Skill",
  "version": "0.1.0-m0",
  "status": "draft",
  "spec_ref": "docs/specs/love-engine-skill-spec.md",
  "spec_hash": "sha256:...",
  "package_hash": "sha256:...",
  "source_refs": [],
  "source_hashes": {},
  "network": {},
  "capabilities": [],
  "permissions": [],
  "tools": [],
  "governance_parameters": {},
  "vote_signature": {},
  "relayer_policy": {},
  "public_sink": {},
  "m0_artifacts": {}
}
```

M1 target additions:

```json
{
  "schema_version": "loveengine.skill-manifest/0.2",
  "protocol": "loveengine-witness-net/0.2",
  "commands": {},
  "contracts": {},
  "eip712": {},
  "evidence": {},
  "test_matrix": {}
}
```

Rules:

- `skill_id` is the stable primary key.
- `status` stays `draft` until there is a real implementation.
- `source_hashes` must cover all `source_refs`.
- `package_hash` is computed from canonical manifest JSON with `package_hash` replaced by `sha256:SELF`.
- `vote_signature.latest_active_proposal_only` must be `false`.

## AgentNodeProfile

M0 fixture:

```json
{
  "node_id": "loveengine-m0-codex-local-001",
  "operator_label": "local-m0-fixture",
  "agent_runtime": "codex",
  "skill_id": "loveengine-witness",
  "skill_version": "0.1.0-m0",
  "address": "0x1111111111111111111111111111111111111111",
  "capabilities": [],
  "signer_type": "cast",
  "private_key_available_to_agent": false
}
```

M1 target should add:

- `network_endpoints`
- `policy.can_request_signature`
- `policy.can_submit_transaction`
- `policy.can_modify_source_materials`
- `availability`
- `reputation`

Rules:

- `private_key_available_to_agent` must be `false`.
- `address` is an EVM address controlled outside Agent context.
- `signer_type` may be `clef`, `cast`, `browser_wallet`, or `aa`.

## PropagationTask

M0 fixture:

```json
{
  "task_id": "loveengine-m0-propagate-skill-fixture",
  "task_type": "propagate_skill",
  "manifest_ref": "skills/loveengine-witness/skill-manifest.json",
  "manifest_hash": "sha256:...",
  "payload_hash": "sha256:...",
  "required_capabilities": [
    "manifest_verification",
    "skill_propagation"
  ],
  "acceptance_checks": [
    "verify_manifest_hash",
    "verify_source_hashes",
    "run_capability_self_check"
  ]
}
```

Rules:

- `manifest_hash` and `payload_hash` must match the current manifest package hash.
- M0 does not preassign nodes.
- Future versions should include task issuer, signature, expiration policy, and replay protection.

## EvidenceBundle

M1 target:

```json
{
  "bundle_id": "uas-session-<date>-<seq>",
  "session_id": "uas-live-001",
  "proposal_type": "USER_COUNT",
  "subject": {
    "corporate": "0x...",
    "new_user_count": "1000000"
  },
  "source_refs": [],
  "attachments": [],
  "transcript_hash": "sha256:...",
  "payload_hash": "sha256:...",
  "created_at": "2026-06-15T00:00:00+08:00"
}
```

Rules:

- `payload_hash` is the hash signed by witnesses.
- Raw evidence stays off-chain.
- If evidence is summarized by Agent, the summary must be marked as summary, not source.

## LocalLoopTranscript

M2 target:

```json
{
  "run_id": "local-loop-001",
  "manifest_hash": "sha256:...",
  "chain_id": 31337,
  "contracts": {},
  "events": [],
  "evidence_bundle": {},
  "proposal": {},
  "votes": [],
  "final_state": {}
}
```

Transcript should be enough for another Agent to replay the local demo or diagnose where it diverged.

## Tool contract

M1 target commands:

```text
loveengine manifest verify
loveengine node declare
loveengine fixture generate --witnesses 69
loveengine evidence build --session <id>
loveengine eip712 register-message
loveengine eip712 vote-message --proposal <id>
loveengine relayer batch-register --dry-run
loveengine relayer batch-vote --dry-run
loveengine transcript build
```

Each command should support machine-readable output. Prefer JSON output by default for Agent adapters; human text can be added with `--pretty`.

## Adapter expectations

Harness/Hermes/Codex adapters should treat the Skill as a protocol package:

1. Read manifest.
2. Verify source and package hashes.
3. Read onboarding.
4. Declare node capability.
5. Build typed data.
6. Ask local signer for signature.
7. Pass signature to relayer.
8. Store transcript.

The adapter should not load raw private keys, rewrite original source files, or silently skip hash verification.
