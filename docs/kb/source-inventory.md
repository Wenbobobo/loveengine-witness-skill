# Source inventory

This inventory describes the LoveEngineSkill repository. It does not rewrite the original source materials.

## Source materials

| File | Status | Use |
| --- | --- | --- |
| `UAS接口文档.md` | current source | Collaborator contract API notes; calibrates WitnessDAO, CorporateSink, PublicSink, StreamingEngine |
| `UAS 见证方案 2.0.md` | current source | Latest witness plan: open registration, Agent-assisted witness process, vote-count-based 90% threshold |
| `UAS 2.md` | previous source | Earlier product spec for UAS / UHAH and four-contract architecture |
| `LoveEngine Skill.md` | early source | Early notes on LoveEngine Skill as witness and public-opinion collector |
| `Love Engine.md` | early source | LoveEngine, UAS, UHAH, UTO and transitional implementation ideas |
| `NaturalDAO 开发.md` | background source | NaturalDAO, EAP, Skills, Memory Lib and upper-level engineering background |
| `Skill 模板.md` | working source | Early LoveEngine Witness module template |

## Developer docs

| File | Status | Use |
| --- | --- | --- |
| `README.md` | current | Repository entrypoint |
| `AGENTS.md` | current | Agent working rules |
| `CONTRIBUTING.md` | current | Developer contribution guide |
| `docs/README.md` | current | Documentation entrypoint |
| `docs/development/integration-guide.md` | current | Auxiliary development handoff guide |
| `docs/development/repository-structure.md` | current | Repository layout and source boundary |
| `docs/development/contract-team-handoff.md` | current | Contract implementation mapping, evidence, and open integration decisions |
| `docs/specs/love-engine-master-plan.md` | current plan | Engineering goals, boundaries, progress and milestones |
| `docs/specs/love-engine-local-witness-loop-spec.md` | implemented spec | M1/M2 protocol, CLI, contracts and local-loop acceptance |
| `docs/specs/love-engine-agent-network-pilot-spec.md` | active spec | M2 release closeout and M3 three-node Relay Hub pilot |
| `docs/api/README.md` | current | API documentation entrypoint |
| `docs/api/loveengine-contract-api.md` | current | Four-contract API and EIP-712 constraints |
| `docs/api/agent-skill-api.md` | current | Skill and Agent adapter API |
| `docs/api/agent-network-api.md` | M3 target | SkillRegistry, signed network messages, and Relay Hub API |
| `docs/decisions/0001-onchain-skill-registry.md` | accepted | On-chain Skill version trust-root decision |
| `docs/archive/planning/2026-06-21/README.md` | archive index | Superseded planning documents and milestone mapping |

## M0 implementation

| File | Status | Use |
| --- | --- | --- |
| `skills/loveengine-witness/skill-manifest.m0.json` | M0 implemented | Preserved `0.1.1-m0` manifest and source protection |
| `skills/loveengine-witness/agent-onboarding.md` | M0 implemented | Minimal onboarding for a new Agent |
| `skills/loveengine-witness/fixtures/agent-node-profile.fixture.json` | M0 fixture | Node capability and signer-boundary declaration |
| `skills/loveengine-witness/fixtures/propagation-task.fixture.json` | M0 fixture | Skill propagation task fixture |
| `tools/validate_loveengine_m0.py` | M0 implemented | Manifest, source, fixture, safety and tamper validation |
| `tools/loveengine_m0_self_check.py` | M0 implemented | Capability self-check summary |
| `tools/validate_sources.py` | repository validation | Validates `docs/kb/sources.json` paths and duplicate IDs |

## M1/M2 implementation

| File | Status | Use |
| --- | --- | --- |
| `skills/loveengine-witness/skill-manifest.json` | M1/M2 implemented | Current `0.2.0-local-loop` manifest |
| `schemas/` | M1 implemented | Manifest, node, evidence and transcript JSON Schema |
| `src/loveengine_witness/` | M1/M2 implemented | Protocol domain, CLI, typed data, relayer and Anvil demo |
| `contracts/src/` | M2 implemented | Four-contract local implementation |
| `contracts/test/LocalWitnessLoop.t.sol` | M2 implemented | Security, threshold, window and 69-signature tests |
| `examples/transcripts/local-loop.fixture.json` | M2 fixture | Verifiable five-witness Anvil transcript |
| `tests/` | current | Python unit, CLI, compatibility and integration tests |

## Version priority

For development:

1. `docs/specs/love-engine-master-plan.md`
2. `docs/specs/love-engine-agent-network-pilot-spec.md`
3. `docs/specs/love-engine-local-witness-loop-spec.md`
4. `docs/api/loveengine-contract-api.md`
5. `docs/api/agent-skill-api.md`
6. `docs/api/agent-network-api.md`
7. `UAS接口文档.md`
8. `UAS 见证方案 2.0.md`

If source files conflict, newer source notes beat older product drafts, but developer specs may add safety constraints that are not explicit in the original notes.
