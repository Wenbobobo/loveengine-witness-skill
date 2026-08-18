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
│   ├── presentations/
│   ├── reference/
│   └── specs/
├── examples/
├── schemas/
├── showcase/
├── skills/loveengine-witness/
├── src/loveengine_witness/
├── tests/
└── tools/
```

## Active development

- `docs/specs/`: the master plan, current invited-pilot SPEC, and retained
  pre-enterprise remote-lab evidence contract.
- `docs/architecture/`: canonical core/data-flow explanations.
- `docs/api/`: public protocol and replaceable port contracts.
- `src/loveengine_witness/`: domain/use cases, release trust, Agent session,
  local pilot runtime and adapters.
- `schemas/`: versioned wire contracts.
- `contracts/`: four UAS contracts and SkillRegistry.
- `examples/`: secret-free fixtures and transcripts.
- `tools/`: repository/release gates, cross-platform core experiments, and the
  key-only shared-host remote lab.
- `showcase/`: Chinese public presentation page and Sites build entrypoint.
- `docs/presentations/`: collaborator-facing introduction text and slide deck.

## Preserved provenance

- `docs/reference/source-materials/current/`: current source constraints.
- `docs/reference/source-materials/meeting-transcripts/`: preserved meeting
  transcripts; their header claims must be checked against the available body.
- `docs/archive/source-materials/`: historical original material.
- `docs/archive/specs/implemented/`: completed implementation specifications.
- `docs/archive/planning/`: superseded plans.
- `docs/archive/development/`: historical runbooks and acceptance reports.

Preserved materials are not rewritten. Their path and SHA-256 remain tracked in the source inventory.
