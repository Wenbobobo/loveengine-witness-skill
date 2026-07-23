# Repository structure

```text
.
├── README.md
├── README.zh-CN.md
├── QA.md
├── AGENTS.md
├── CONTRIBUTING.md
├── contracts/
├── docs/
│   ├── api/
│   ├── architecture/
│   ├── archive/
│   ├── decisions/
│   ├── development/
│   ├── kb/
│   ├── reference/
│   └── specs/
├── examples/
├── schemas/
├── skills/loveengine-witness/
├── src/loveengine_witness/
├── tests/
└── tools/
```

## Active development

- `docs/specs/`: the master plan and active Witness core optimization SPEC.
- `docs/architecture/`: canonical core/data-flow explanations.
- `docs/api/`: public protocol and replaceable port contracts.
- `src/loveengine_witness/`: domain/use cases, release trust, Agent session,
  local pilot runtime and adapters.
- `schemas/`: versioned wire contracts.
- `contracts/`: four UAS contracts and SkillRegistry.
- `examples/`: secret-free fixtures and transcripts.

## Preserved provenance

- `docs/reference/source-materials/current/`: current source constraints.
- `docs/reference/source-materials/meeting-transcripts/`: preserved meeting
  transcripts; their header claims must be checked against the available body.
- `docs/archive/source-materials/`: historical original material.
- `docs/archive/specs/implemented/`: completed implementation specifications.
- `docs/archive/planning/`: superseded plans.

Preserved materials are not rewritten. Their path and SHA-256 remain tracked in the source inventory.
