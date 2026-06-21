# LoveEngine Witness Skill

LoveEngine Witness Skill is the development repository for the UAS witness protocol in the DAism / NaturalDAO workstream. The immediate goal is not to build a centralized welfare website. The first target is a Skill package that can be verified, installed, propagated, and run by an Agent network.

This repository keeps the current LoveEngine source notes in the root directory and puts developer-facing material under `docs/`, `skills/`, and `tools/`.

## Status

Current version: `0.3.0-network-pilot`

M0 remains available as the verifiable package baseline. M1/M2 now add:

- JSON Schema for manifest, node profile, EvidenceBundle and transcript.
- An installable `loveengine` machine-readable CLI.
- EIP-712 register/vote typed-data builders and relayer dry-run validation.
- Foundry implementations of WitnessDAO, CorporateSink, StreamingEngine and PublicSink.
- A real five-witness Anvil demo and a 69-signature Foundry scale test.
- A verifiable, secret-free LocalLoopTranscript fixture.

M3 adds:

- Publisher-scoped on-chain `SkillRegistry` release facts.
- Signed node profiles, Publisher bootstrap, tasks, and receipts.
- HTTP/WebSocket Relay Hub with SQLite at-least-once delivery.
- Chain-event idempotency by chainId, transaction hash, and log index.
- A real three-process Agent pilot for `propagate_skill` and `observe_broadcast`.
- A verifiable, secret-free NetworkTranscript fixture with delivery metrics.

## Quick start

Install tooling with uv:

```powershell
uv sync
```

Run repository and Python checks:

```powershell
uv run python .\tools\check.py
uv run pytest -m "not integration"
uv run loveengine manifest verify
```

With Foundry v1.7.1 installed:

```powershell
cd contracts
forge install foundry-rs/forge-std@v1.9.7 --no-git
forge install OpenZeppelin/openzeppelin-contracts@v5.3.0 --no-git
forge test
cd ..
uv run loveengine demo local-loop --output .\examples\transcripts
uv run loveengine transcript verify .\examples\transcripts\local-loop.fixture.json
uv run loveengine network demo --nodes 3 --output .\examples\transcripts
uv run loveengine network transcript verify .\examples\transcripts\network-pilot.fixture.json
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
├── schemas/
├── src/loveengine_witness/
├── tests/
├── contracts/
├── examples/transcripts/
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
4. `docs/specs/love-engine-master-plan.md`
5. `docs/specs/love-engine-agent-network-pilot-spec.md`
6. `docs/specs/love-engine-local-witness-loop-spec.md`
7. `docs/api/README.md`

For contract work:

1. `docs/api/loveengine-contract-api.md`
2. `UAS接口文档.md`
3. `UAS 见证方案 2.0.md`
4. `docs/specs/love-engine-local-witness-loop-spec.md`

For Agent / Harness / Hermes adapter work:

1. `skills/loveengine-witness/skill-manifest.json`
2. `skills/loveengine-witness/agent-onboarding.md`
3. `docs/api/agent-skill-api.md`
4. `docs/development/integration-guide.md`

## Implemented local loop

The current local loop:

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

The engineering roadmap is `docs/specs/love-engine-master-plan.md`.
The implemented M1/M2 baseline is specified by `docs/specs/love-engine-local-witness-loop-spec.md`.
The implemented M2 closeout and M3 pilot spec is `docs/specs/love-engine-agent-network-pilot-spec.md`.

## Interface docs

- `docs/api/loveengine-contract-api.md`: WitnessDAO, CorporateSink, StreamingEngine, PublicSink.
- `docs/api/agent-skill-api.md`: SkillManifest, AgentNodeProfile, PropagationTask, EvidenceBundle, LocalLoopTranscript, tool contract.
- `docs/api/agent-network-api.md`: SkillRegistry, signed node/bootstrap messages, network tasks, receipts, Relay Hub, and M3 CLI.

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

This runs M0 validation, tamper check, self-check, source inventory validation, and active-document path validation.

## License

No repository-wide open-source license has been chosen yet. Do not assume the SCC0 materials from the broader DAism workspace apply to this repository.
