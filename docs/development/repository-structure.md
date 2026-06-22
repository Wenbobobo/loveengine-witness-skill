# Repository structure

```text
.
├── README.md
├── README.zh-CN.md
├── AGENTS.md
├── CONTRIBUTING.md
├── contracts/
├── docs/
│   ├── api/
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

- `docs/specs/`: master plan and active M4 SPEC only.
- `docs/api/`: public protocol and replaceable port contracts.
- `src/loveengine_witness/`: framework-independent domain/use cases plus adapters.
- `schemas/`: versioned wire contracts.
- `contracts/`: four UAS contracts and SkillRegistry.
- `examples/`: secret-free fixtures and transcripts.

## Preserved provenance

- `docs/reference/source-materials/current/`: current source constraints.
- `docs/archive/source-materials/`: historical original material.
- `docs/archive/specs/implemented/`: completed implementation specifications.
- `docs/archive/planning/`: superseded plans.

Preserved materials are not rewritten. Their path and SHA-256 remain tracked in the source inventory.
