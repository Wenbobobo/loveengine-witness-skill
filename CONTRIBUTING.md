# Contributing

This repository is prepared for auxiliary development teams working on LoveEngine Witness Skill.

## Start here

```powershell
uv sync
uv run python .\tools\check.py
```

Then read:

- `docs/development/integration-guide.md`
- `docs/specs/love-engine-skill-spec.md`
- `docs/specs/love-engine-next-phase-spec.md`
- `docs/api/loveengine-contract-api.md`
- `docs/api/agent-skill-api.md`

## Branch naming

Use focused branches:

```text
docs/<topic>
spec/<topic>
tool/<topic>
contract/<topic>
agent/<topic>
```

## Pull request expectations

Every PR should state:

- Which layer changed: docs, skill package, tools, contracts, relayer, or Agent adapter.
- Whether any source material was edited.
- Whether manifest-protected files changed.
- Which verification commands were run.
- What remains incomplete.

Use `.github/pull_request_template.md`.

## Interface rules

Contract or adapter changes must update:

- `docs/api/loveengine-contract-api.md`
- `docs/api/agent-skill-api.md`
- the relevant spec under `docs/specs/`

Do not weaken these constraints:

- Agent does not hold private keys.
- Relayer submits signatures but does not sign for witnesses.
- `VoteSignature` binds `chainId`, `verifyingContract`, `proposalId`, `support`, `reasonHash`, `payloadHash`, nonce, and deadline.
- `PublicSink` exposes read-only public accounting.

## Verification

Run:

```powershell
uv run python .\tools\check.py
```

For a narrower check:

```powershell
uv run python .\tools\validate_loveengine_m0.py
uv run python .\tools\validate_loveengine_m0.py --tamper-check
uv run python .\tools\loveengine_m0_self_check.py
uv run python .\tools\validate_sources.py
```

## Current priority

The next implementation slice is M1/M2 Local Witness Loop:

1. JSON schemas for manifest, node profile, evidence bundle, and transcript.
2. `tools/loveengine/` CLI.
3. Foundry four-contract prototype.
4. EIP-712 typed data generation.
5. local relayer dry-run.
6. local-loop transcript.
