# M3 demo runbook

## Preparation

```powershell
uv sync --frozen
cd contracts
forge install foundry-rs/forge-std@v1.9.7 --no-git
forge install OpenZeppelin/openzeppelin-contracts@v5.3.0 --no-git
forge test
cd ..
```

## Demonstration

```powershell
uv run loveengine manifest verify
uv run loveengine demo local-loop --output .\examples\transcripts
uv run loveengine transcript verify .\examples\transcripts\local-loop.fixture.json
uv run loveengine network demo --nodes 3 --output .\examples\transcripts
uv run loveengine network transcript verify .\examples\transcripts\network-pilot.fixture.json
```

Explain the result in this order:

1. Skill and protected source hashes are verified.
2. Five witnesses complete the local governance loop.
3. SkillRegistry publishes an active version.
4. Three outbound-only Agent processes authenticate.
5. Release and broadcast events create signed tasks.
6. SQLite demonstrates at-least-once delivery and idempotency.
7. Six signed receipts and a secret-free transcript are independently verified.

Do not expose Anvil account keys, describe M3 as production-ready, or imply that Relay can sign for nodes.
