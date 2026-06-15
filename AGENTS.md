# AGENTS.md

These instructions apply to the LoveEngineSkill repository.

## Read first

1. `README.md`
2. `docs/development/integration-guide.md`
3. `docs/specs/love-engine-skill-spec.md`
4. `docs/specs/love-engine-next-phase-spec.md`
5. `docs/api/loveengine-contract-api.md`
6. `docs/api/agent-skill-api.md`
7. `skills/loveengine-witness/skill-manifest.json`

## Source material boundary

The root Markdown files are source materials:

- `Love Engine.md`
- `LoveEngine Skill.md`
- `NaturalDAO 开发.md`
- `Skill 模板.md`
- `UAS 2.md`
- `UAS 见证方案 2.0.md`
- `UAS接口文档.md`

Do not move, delete, rename, or rewrite these files unless the user explicitly asks. New summaries, specs, and interface documents go under `docs/`.

## Current direction

Keep LoveEngine framed as an Agent-network-first Witness Skill. Do not drift back to a centralized web-product framing.

## Hash-protected files

`skills/loveengine-witness/skill-manifest.json` records hashes for:

- `docs/specs/love-engine-skill-spec.md`
- `UAS接口文档.md`
- `UAS 见证方案 2.0.md`
- `docs/kb/source-inventory.md`

If any of these files change, update:

- `skills/loveengine-witness/skill-manifest.json`
- `skills/loveengine-witness/fixtures/propagation-task.fixture.json`

Then run:

```powershell
uv run python .\tools\check.py
```

## Safety invariants

- Raw private keys never enter Agent context, prompts, logs, fixtures, or transcripts.
- `VoteSignature` must bind `proposalId`, nonce, deadline, and payload hash.
- `PublicSink` stays read-only.
- Governance values such as min valid votes, broadcast window, and interval stay configurable.
- UTO is treated as public accounting, not a tradable asset.
