# LoveEngine Witness Skill

LoveEngine Witness Skill is the development repository for the UAS witness protocol in the DAism / NaturalDAO workstream. The immediate goal is not to build a centralized welfare website. The first target is a Skill package that can be verified, installed, propagated, and run by an Agent network.

This repository keeps the current LoveEngine source notes in the root directory and puts developer-facing material under `docs/`, `skills/`, and `tools/`.

## Status

Current version: `0.1.0-m0`

M0 is a verifiable Skill package and handoff layer:

- `skills/loveengine-witness/skill-manifest.json` records source hashes, permissions, capabilities, governance parameters, and signing boundaries.
- `skills/loveengine-witness/agent-onboarding.md` gives a new Agent the minimum operating context.
- `skills/loveengine-witness/fixtures/` contains node and propagation task fixtures.
- `tools/validate_loveengine_m0.py` validates source integrity, manifest integrity, fixture consistency, safety rules, and tamper rejection.
- `tools/loveengine_m0_self_check.py` runs validation and prints the local node capability summary.

M0 does not include deployed contracts, real EIP-712 signatures, relayer code, live evidence ingestion, or a local chain demo. Those are M1/M2 work.

## Quick start

Install tooling with uv:

```powershell
uv sync
```

Run the M0 checks:

```powershell
uv run python .\tools\validate_loveengine_m0.py
uv run python .\tools\validate_loveengine_m0.py --tamper-check
uv run python .\tools\loveengine_m0_self_check.py
```

Equivalent Python commands:

```powershell
python .\tools\validate_loveengine_m0.py
python .\tools\validate_loveengine_m0.py --tamper-check
python .\tools\loveengine_m0_self_check.py
```

## Repository layout

```text
.
├── README.md
├── AGENTS.md
├── CONTRIBUTING.md
├── pyproject.toml
├── docs/
│   ├── api/
│   ├── development/
│   ├── kb/
│   └── specs/
├── skills/
│   └── loveengine-witness/
├── tools/
├── .github/
├── Love Engine.md
├── LoveEngine Skill.md
├── NaturalDAO 开发.md
├── Skill 模板.md
├── UAS 2.md
├── UAS 见证方案 2.0.md
└── UAS接口文档.md
```

Root Markdown files are source materials. Do not rewrite them in place unless the task explicitly asks for source editing. Developer-facing specs, APIs, and summaries belong under `docs/`.

## Read order

For auxiliary developers:

1. `README.md`
2. `CONTRIBUTING.md`
3. `docs/development/integration-guide.md`
4. `docs/specs/love-engine-skill-spec.md`
5. `docs/specs/love-engine-next-phase-spec.md`
6. `docs/api/README.md`

For contract work:

1. `docs/api/loveengine-contract-api.md`
2. `UAS接口文档.md`
3. `UAS 见证方案 2.0.md`
4. `docs/specs/love-engine-next-phase-spec.md`

For Agent / Harness / Hermes adapter work:

1. `skills/loveengine-witness/skill-manifest.json`
2. `skills/loveengine-witness/agent-onboarding.md`
3. `docs/api/agent-skill-api.md`
4. `docs/development/integration-guide.md`

## Development target

Next phase: M1/M2 Local Witness Loop.

The intended local loop:

```text
manifest verify
-> node declaration
-> local four-contract deployment
-> witness registration signatures
-> relayer batchRegister
-> corporate scheduleBroadcast
-> EvidenceBundle
-> user-count or compensation proposal
-> witness vote signatures
-> relayer batchVote
-> WitnessDAO finalize/execute
-> StreamingEngine update
-> PublicSink getTotalUTO
-> transcript
```

The detailed spec is `docs/specs/love-engine-next-phase-spec.md`.

## Interface docs

- `docs/api/loveengine-contract-api.md`: WitnessDAO, CorporateSink, StreamingEngine, PublicSink.
- `docs/api/agent-skill-api.md`: SkillManifest, AgentNodeProfile, PropagationTask, EvidenceBundle, LocalLoopTranscript, tool contract.

Important constraints:

- Agent never receives raw private keys.
- Relayers may submit transactions, but they do not sign on behalf of witnesses.
- `VoteSignature` must bind `chainId`, `verifyingContract`, `proposalId`, `support`, `reasonHash`, `payloadHash`, nonce, and deadline.
- Do not sign only against "latest active proposal".
- `PublicSink` remains read-only.

## Verification

Before a PR or handoff, run:

```powershell
uv run python .\tools\check.py
```

This runs M0 validation, tamper check, self-check, and source inventory validation.

## License

No repository-wide open-source license has been chosen yet. Do not assume the SCC0 materials from the broader DAism workspace apply to this repository.
