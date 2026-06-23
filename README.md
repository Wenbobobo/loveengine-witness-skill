# LoveEngine Witness Skill

[中文说明](README.zh-CN.md)

LoveEngine Witness Skill is an Agent-network-first protocol for verifiable public-interest witnessing. It packages source provenance, EIP-712 identities and tasks, local governance contracts, a relay network, evidence artifacts, and replayable transcripts without exposing private keys to an Agent.

Current stable demo: `0.4.0-live-evidence-pilot`.
Previous network-only demo: `0.3.1-demo-ready`.
Current release candidate: `0.5.0-lan-pilot` (`loveengine-witness-net/0.5`).

## Skill model

LoveEngine uses a thin instruction layer and a thick protocol runtime. The
Codex-facing `SKILL.md` only defines triggers, verification, command routing,
and safety boundaries. Versioned manifests, schemas, Python use cases,
adapters, contracts, and transcripts carry the executable protocol. This keeps
Agent context small while preserving deterministic, testable behavior.

## Architecture

```mermaid
flowchart LR
    Sources[Source materials] --> Manifest[Signed and hashed Skill package]
    Manifest --> Registry[SkillRegistry]
    Registry --> Relay[Relay Hub]
    Chain[UAS contracts and events] --> Relay
    Relay --> A[Agent A]
    Relay --> B[Agent B]
    Relay --> C[Agent C]
    A --> Receipts[Signed receipts]
    B --> Receipts
    C --> Receipts
    Live[LiveSource port] --> Gateway[Live Gateway]
    Gateway --> Artifacts[ArtifactStore port]
    Gateway --> Evidence[EvidenceBundle]
    Evidence --> Review[Review coordinator]
    Review --> Gate[ProposalGate]
    Registry --> Dashboard[Read-only dashboard]
    Receipts --> Dashboard
    Evidence --> Dashboard
    Gate --> Dashboard
```

Replaceable boundaries are documented in [extension interfaces](docs/api/extension-interfaces.md). The domain layer does not depend on a particular signer, live platform, object store, database, or transport.

## End-to-end flow

```mermaid
flowchart TD
    Verify[Verify manifest and source hashes] --> Deploy[Deploy four UAS contracts and SkillRegistry]
    Deploy --> Register[Register witnesses with EIP-712 signatures]
    Register --> Schedule[Schedule broadcast]
    Schedule --> Observe[Relay observe task to Agents]
    Observe --> Build[Build immutable evidence bundle]
    Build --> Review[Three-node dispute review]
    Review --> Gate{ProposalGate}
    Gate -->|dismissed disputes| Propose[Create proposal execution plan]
    Gate -->|upheld or unresolved| Block[Block proposal]
    Propose --> Vote[Witness vote signatures]
    Vote --> Execute[Execute approved proposal]
    Execute --> Query[Query PublicSink]
    Query --> Transcript[Verify transcript]
```

## M3 network sequence

```mermaid
sequenceDiagram
    participant Chain
    participant Relay
    participant A as Agent A
    participant B as Agent B
    participant C as Agent C
    Chain->>Relay: ReleasePublished / BroadcastScheduled
    Relay->>A: signed NetworkTask
    Relay->>B: signed NetworkTask
    Relay->>C: signed NetworkTask
    A-->>Relay: signed TaskReceipt
    B-->>Relay: signed TaskReceipt
    C-->>Relay: signed TaskReceipt
    Relay-->>Relay: ack, retry, deduplicate
    Relay-->>Chain: no signing authority
```

## Repository layout

```text
contracts/                 Foundry UAS contracts and SkillRegistry
docs/api/                  Public protocol and extension interfaces
docs/development/          Onboarding, runbooks and acceptance reports
docs/reference/            Current source constraints
docs/archive/              Historical sources and implemented specifications
docs/specs/                Current roadmap and active specification
examples/                  Secret-free fixtures and transcripts
schemas/                   JSON Schema Draft 2020-12 contracts
skills/loveengine-witness/ Verifiable Skill manifests and onboarding
src/loveengine_witness/    Python domain, use cases, ports, adapters and CLI
tests/                     Unit, contract and end-to-end tests
tools/                     Repository and compatibility validators
```

## Operator and read-only views

The Pilot Server exposes an authenticated host console and a separate
read-only evidence view. The token remains only in page memory; the public view
cannot write or sign. These screenshots are rendered from the repository's
secret-free local UI fixture.

![LoveEngine host operator console](docs/assets/operator-console.png)

![LoveEngine read-only evidence console](docs/assets/read-only-dashboard.png)

## Quick start

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and Foundry `1.7.1`.
The four commands below synchronize the locked environment, verify the
manifest, run the package-to-PublicSink pilot, and verify its transcript.

```powershell
uv sync --frozen
uv run loveengine manifest verify
uv run loveengine demo lan-pilot --events 12 --observers 10 --output .\pilot-output
uv run loveengine pilot transcript verify .\pilot-output\pilot.fixture.json
```

Full installation, server, chain, snapshot, voting, legacy demo, background
soak, and troubleshooting commands are in the
[CLI and operations reference](docs/api/cli-reference.md). Run the complete
automated quality matrix with `tools/run_release_checks.ps1`; the real
four-hour wall-clock soak remains a separate release gate.

## Security invariants

- Raw private keys, mnemonics, keystores, and tokens never enter Agent context, fixtures, logs, or transcripts.
- A relayer submits signatures but cannot sign for a witness or node.
- Every signed task binds chain ID, verifying contract, issuer, recipient, payload hash, nonce, and deadline.
- Raw evidence stays off-chain; only content hashes enter proposals or contracts.
- Agents never auto-sign votes. Each witness approval is a separate explicit
  `loveengine witness vote approve` command using an external RPC signer.
- `PublicSink` remains read-only.

## Documentation entrypoints

- Architecture and scope: [master plan](docs/specs/love-engine-master-plan.md)
  and [active M5 specification](docs/specs/love-engine-lan-pilot-spec.md).
- Interfaces and commands: [API index](docs/api/README.md) and
  [CLI reference](docs/api/cli-reference.md).
- Development and operations:
  [integration guide](docs/development/integration-guide.md) and
  [M5 acceptance report](docs/development/m5-acceptance-report.md).
- Provenance: [source inventory](docs/kb/source-inventory.md) and
  [SCC0 provenance](docs/reference/licenses/scc0-provenance.md).

## License

LoveEngineSkill is released under Smart Creative Commons Zero (SCC0). See
[LICENSE](LICENSE) and the exact [license provenance](docs/reference/licenses/scc0-provenance.md).
