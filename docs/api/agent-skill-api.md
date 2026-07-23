# Agent and Skill API

本文定义 LoveEngine Witness Skill 面向 Agent adapter、本地 CLI、Codex Skill
和 Codex Plugin loader 的数据结构。M0-M6 格式保留历史兼容；当前
0.6.1 candidate 默认验证核心证据闭环，治理合约是可选实验。公网未实现。

## 标准 Skill 入口

`skills/loveengine-witness/SKILL.md` 是薄指令层，只包含触发条件、验证流程、命令导航和安全边界。`agents/openai.yaml` 提供 Codex UI metadata。协议行为继续由 manifest、Schema、Python 运行时、合约和 transcript 实现。

M5/M6 确定性 ZIP 是独立安装边界。它包含运行必需文件、`checksums.json`
与 `sbom.spdx.json`；SkillRegistry 的 package hash 绑定 ZIP 的 Keccak-256。
M6 还提供 `plugins/loveengine-witness/` 作为 Codex 分发包装。Plugin 不是
协议信任根，只负责让 Codex 发现工作流。

## 当前 M0 文件

| 文件 | 状态 |
| --- | --- |
| `skills/loveengine-witness/skill-manifest.m0.json` | M0 compatibility manifest |
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
  "version": "0.1.1-m0",
  "status": "draft",
  "spec_ref": "docs/specs/love-engine-master-plan.md",
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

M1 implemented additions:

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
  "skill_version": "0.1.1-m0",
  "address": "0x1111111111111111111111111111111111111111",
  "capabilities": [],
  "signer_type": "cast",
  "private_key_available_to_agent": false
}
```

M1 implemented fields include:

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
  "manifest_ref": "skills/loveengine-witness/skill-manifest.m0.json",
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

- `manifest_hash` and `payload_hash` must match the M0 compatibility manifest package hash.
- M0 does not preassign nodes.
- 这条 M0 fixture 不含 issuer/signature/deadline；当前 V1/V2 network task 已实现
  issuer、EIP-712 signature、deadline、recipient、nonce 和 replay protection。

## EvidenceBundle

M1 implemented:

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
  "artifact_hash": "sha256:...",
  "payload_hash": "0x<keccak256>",
  "created_at": "2026-06-15T00:00:00+08:00"
}
```

Rules:

- `artifact_hash` is the SHA-256 hash used for file integrity.
- `payload_hash` is the Keccak-256 bytes32 value bound into proposals and witness signatures.
- Raw evidence stays off-chain.
- If evidence is summarized by Agent, the summary must be marked as summary, not source.

## LocalLoopTranscript

M2 implemented:

```json
{
  "run_id": "local-loop-001",
  "manifest_hash": "sha256:...",
  "chain_id": "31337",
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

Implemented commands:

```text
loveengine manifest verify
loveengine node declare
loveengine fixture generate --witnesses 69
loveengine evidence build --session <id>
loveengine eip712 register-message
loveengine eip712 vote-message --proposal-id <id>
loveengine relayer batch-register --dry-run
loveengine relayer batch-vote --dry-run
loveengine transcript verify <path>
loveengine demo local-loop --output <dir>
loveengine package build --output <dir>
loveengine package verify <archive> --expected-package-hash <registry-keccak>
loveengine package install <archive> --target <dir> --expected-package-hash <registry-keccak>
loveengine package self-check --root <dir> --expected-package-hash <registry-keccak>
loveengine pilot quickstart --root <dir> [--headless]
loveengine pilot serve --config <path>
loveengine pilot status --url <url>
loveengine pilot chain init --root <dir>
loveengine pilot chain start --root <dir>
loveengine pilot chain status --root <dir> --rpc-url <url>
loveengine pilot chain snapshot --root <dir> --rpc-url <url>
loveengine pilot chain restore --root <dir> --rpc-url <url> --snapshot <path>
loveengine pilot snapshot create --config <path> --chain-root <dir> --output <dir>
loveengine pilot snapshot verify <path>
loveengine pilot snapshot restore <path> --config <path> --chain-root <dir>
loveengine pilot snapshot prune --output <dir> --older-than-days 30
loveengine witness vote approve --proposal-plan <path> --rpc-url <url> --address <address>
loveengine demo lan-pilot --stage core|governance --events 12 --observers 10 --output <dir>
loveengine pilot transcript verify <path> [--rpc-url <url>] [--trust-policy <policy>]
loveengine pilot soak --duration-seconds 14400 --events 240 --observers 10 --output <dir>
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

M3 network types and Relay Hub interfaces are defined separately in `docs/api/agent-network-api.md`.
