# AGENTS.md

These instructions apply to the LoveEngineSkill repository.

## Read first

1. `README.md`
2. `docs/development/integration-guide.md`
3. `docs/specs/love-engine-master-plan.md`
4. `docs/architecture/witness-core-and-data-flow.zh-CN.md`
5. `docs/specs/love-engine-witness-core-optimization.md`
6. `docs/api/loveengine-contract-api.md`
7. `docs/api/agent-skill-api.md`
8. `docs/api/agent-network-api.md`
9. `docs/api/extension-interfaces.md`
10. `docs/api/cli-reference.md`
11. `skills/loveengine-witness/skill-manifest.json`
12. `QA.md`

## Source material boundary

Preserved source materials live under `docs/reference/source-materials/` and
`docs/archive/source-materials/`. Do not rewrite them in place. New summaries,
specs, and interface documents go under the active `docs/` directories.

## Current direction

Keep LoveEngine framed as an Agent-network-first Witness Skill. Do not drift back to a centralized web-product framing.

## Hash-protected files

The current manifest is `skills/loveengine-witness/skill-manifest.json`.
The M0 compatibility manifest is `skills/loveengine-witness/skill-manifest.m0.json`.
Their `source_refs` and `source_hashes` are authoritative; do not maintain a separate hard-coded list here.

If any referenced file changes, update:

- the affected manifest source hash and package hash;
- the M0 propagation fixture only when the M0 package hash changes;
- `docs/kb/sources.json` when a public artifact is added or moved.

Then run:

```powershell
uv run python .\tools\check.py
uv run pytest -m "not integration"
uv run pytest tests/integration/test_network_demo.py
```

## Safety invariants

- Raw private keys never enter Agent context, prompts, logs, fixtures, or transcripts.
- Network tasks are limited to `propagate_skill` and `observe_broadcast`; M3 never requests vote signatures.
- M4 tasks may use `observe_live_text` and `review_dispute`, but never request vote signatures.
- M5 observation Agents may reconnect from durable cursors, but still never
  sign votes. Vote approval is an explicit witness CLI action using an external
  RPC signer.
- M5 measures task acceptance ACK latency separately from long-running task
  completion latency.
- M6 treats `docs/reference/contracts/contract-team-v2/` as preserved
  collaborator input. Do not rewrite that reference copy; record fusion
  decisions in `docs/development/contract2-comparison-and-recommendations.md`.
- Pilot write tokens are read from restricted files and never enter CLI
  arguments, logs, fixtures, snapshots, or transcripts.
- Agent nodes verify chainId, SkillRegistry, Publisher, release status, package hash, recipient, nonce, and deadline.
- `VoteSignature` must bind `proposalId`, nonce, deadline, and payload hash.
- `PublicSink` stays read-only.
- Governance values such as min valid votes, broadcast window, and interval stay configurable.
- UTO is treated as public accounting, not a tradable asset.
