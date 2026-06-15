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
| `docs/specs/love-engine-skill-spec.md` | current spec | Main LoveEngine Witness Skill spec |
| `docs/specs/love-engine-next-phase-spec.md` | current spec | M1/M2 Local Witness Loop spec |
| `docs/api/README.md` | current | API documentation entrypoint |
| `docs/api/loveengine-contract-api.md` | current | Four-contract API and EIP-712 constraints |
| `docs/api/agent-skill-api.md` | current | Skill and Agent adapter API |

## M0 implementation

| File | Status | Use |
| --- | --- | --- |
| `skills/loveengine-witness/skill-manifest.json` | M0 implemented | Manifest with source hashes, package hash, capabilities, permissions, governance parameters and signing boundaries |
| `skills/loveengine-witness/agent-onboarding.md` | M0 implemented | Minimal onboarding for a new Agent |
| `skills/loveengine-witness/fixtures/agent-node-profile.fixture.json` | M0 fixture | Node capability and signer-boundary declaration |
| `skills/loveengine-witness/fixtures/propagation-task.fixture.json` | M0 fixture | Skill propagation task fixture |
| `tools/validate_loveengine_m0.py` | M0 implemented | Manifest, source, fixture, safety and tamper validation |
| `tools/loveengine_m0_self_check.py` | M0 implemented | Capability self-check summary |
| `tools/validate_sources.py` | repository validation | Validates `docs/kb/sources.json` paths and duplicate IDs |

## Version priority

For development:

1. `docs/specs/love-engine-skill-spec.md`
2. `docs/specs/love-engine-next-phase-spec.md`
3. `docs/api/loveengine-contract-api.md`
4. `docs/api/agent-skill-api.md`
5. `UAS接口文档.md`
6. `UAS 见证方案 2.0.md`

If source files conflict, newer source notes beat older product drafts, but developer specs may add safety constraints that are not explicit in the original notes.
