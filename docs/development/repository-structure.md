# Repository structure

本文描述独立仓库 `loveengine-witness-skill` 的当前结构。父目录 DAism 中的重复文件不是权威开发源。

## 当前目录

```text
.
├── README.md
├── AGENTS.md
├── CONTRIBUTING.md
├── docs/
│   ├── api/
│   ├── archive/
│   ├── decisions/
│   ├── development/
│   ├── kb/
│   └── specs/
├── skills/loveengine-witness/
├── tools/
├── schemas/
├── src/loveengine_witness/
├── tests/
├── contracts/
└── examples/transcripts/
```

`schemas/`、`src/`、`tests/`、`contracts/` 和 `examples/` 随 M1/M2 实现建立。

## 可维护区

- `docs/`：当前规格、API、指南和历史归档。
- `skills/loveengine-witness/`：可传播 Skill 包。
- `tools/`：仓库兼容检查入口。
- `src/loveengine_witness/`：Python protocol、use case、adapter 和 CLI。
- `schemas/`：机器可读协议。
- `contracts/`：Foundry 合约工程。
- `tests/`：Python 测试。
- `examples/transcripts/`：脱敏、可复跑的示例。

## 默认只读源材料

- `Love Engine.md`
- `LoveEngine Skill.md`
- `NaturalDAO 开发.md`
- `Skill 模板.md`
- `UAS 2.md`
- `UAS 见证方案 2.0.md`
- `UAS接口文档.md`

摘要、推断和新规格必须写入 `docs/`，不能原地覆盖源材料。

## 权威入口

1. `docs/specs/love-engine-master-plan.md`
2. `docs/specs/love-engine-agent-network-pilot-spec.md`
3. `docs/specs/love-engine-local-witness-loop-spec.md`
4. `docs/api/`
5. `docs/kb/source-inventory.md`

`docs/archive/` 只用于历史追溯。
