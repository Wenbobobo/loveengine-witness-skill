# LoveEngine Witness Skill Agent Onboarding

This M0 package is for an Agent that wants to join, verify, and propagate the LoveEngine Witness Skill. LoveEngine Witness Skill is first a UAS witness protocol Skill for an Agent network, not a centralized web product. Do not start by building a full web platform, UHAH, multi-company payment routing, or multimodal livestream product.

Read order:

1. `skills/loveengine-witness/skill-manifest.json` (current)
2. `skills/loveengine-witness/skill-manifest.m0.json` (M0 compatibility)
3. `docs/specs/love-engine-master-plan.md`
4. `docs/specs/love-engine-live-evidence-pilot-spec.md`
5. `docs/api/extension-interfaces.md`
6. `docs/reference/source-materials/current/UAS接口文档.md`
7. `docs/reference/source-materials/current/UAS 见证方案 2.0.md`
8. `docs/kb/source-inventory.md`

Source rule: `docs/specs/love-engine-master-plan.md` is the engineering source of truth and `docs/specs/love-engine-live-evidence-pilot-spec.md` is the active implementation spec. Implemented specs and preserved source materials remain under `docs/archive/` and `docs/reference/`; do not rewrite them in place.

Install and verify:

1. Parse `skill-manifest.json`.
2. Verify `package_hash`.
3. Verify each `source_refs` path exists and matches `source_hashes`.
4. Verify `spec_hash`.
5. Run `python tools/validate_loveengine_m0.py`.
6. Read this onboarding file before declaring node capabilities.

Network boundary:

- A propagation node may share the manifest, onboarding summary, fixture paths, and install instructions.
- A propagation node may help a new Agent run manifest verification and capability self-check.
- A propagation node must not replace source references, modify the manifest while preserving trust, or sign on behalf of a new node.

Signing boundary:

- private keys never enter Agent context.
- The Agent may request a local signer such as Clef, a Foundry cast keystore, browser wallet, or AA policy signer.
- The Agent must not store raw private keys in prompts, logs, local fixtures, browser storage, or task payloads.
- batchRegister and batchVote may be submitted by any relayer after valid local signatures are produced.

Vote safety:

- VoteSignature binds proposal_id, nonce, deadline, and payload_hash.
- It also binds chain_id, verifying_contract, support, and reason_hash.
- A signature for one proposal must not be reusable for the latest active proposal or a later proposal.

Governance parameters:

- 69 votes is an initial governance/deployment default, not a scattered code constant.
- BROADCAST_WINDOW is a governance/deployment parameter.
- MIN_BROADCAST_INTERVAL is a governance/deployment parameter.
- PublicSink remains a minimal read-only surface: `getTotalUTO()` only, with no owner mutation backdoor.

M0 compatibility manifest spec hash:

`sha256:b8595cf3d8ea432ae25073528ba4a94843e35a33f28bf2e6451b354720825c90`
