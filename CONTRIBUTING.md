# Contributing

This repository is prepared for auxiliary development teams working on LoveEngine Witness Skill.

## Start here

```powershell
uv sync
uv run python .\tools\check.py
```

Then read:

- `docs/development/integration-guide.md`
- `docs/development/contract-team-handoff.md`
- `docs/specs/love-engine-master-plan.md`
- `docs/specs/love-engine-agent-network-pilot-spec.md`
- `docs/specs/love-engine-local-witness-loop-spec.md`
- `docs/api/loveengine-contract-api.md`
- `docs/api/agent-skill-api.md`
- `docs/api/agent-network-api.md`

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

Contract, network protocol, or adapter changes must update:

- `docs/api/loveengine-contract-api.md`
- `docs/api/agent-skill-api.md`
- `docs/api/agent-network-api.md` when M3 interfaces change
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
uv run pytest -m "not integration"
```

For a narrower check:

```powershell
uv run python .\tools\validate_loveengine_m0.py
uv run python .\tools\validate_loveengine_m0.py --tamper-check
uv run python .\tools\loveengine_m0_self_check.py
uv run python .\tools\validate_sources.py
```

For contracts and the end-to-end demo:

```powershell
cd contracts
forge test
cd ..
uv run pytest .\tests\test_demo.py
```

## Current priority

M1/M2 Local Witness Loop is implemented. The active plan is `docs/specs/love-engine-agent-network-pilot-spec.md`: M2 release closeout followed by the M3 signed three-node Relay Hub pilot.
