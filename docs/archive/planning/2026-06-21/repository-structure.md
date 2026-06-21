# Repository structure

本仓库按“GitHub 协作入口 + 原始资料区 + LoveEngine 原型区”组织。这样做是为了让辅助开发团队能快速进入开发，同时保留 DD 原始讨论和早期资料的溯源。

## 顶层目录

```text
.
├── README.md
├── AGENTS.md
├── CONTRIBUTING.md
├── .github/
├── docs/
├── skills/
├── tools/
├── LoveEngineSkill/
├── TG-5.22/
├── Discuss/
├── NaturalDAO/
├── daism-tg-forwarder/
├── DAism LOGO/
├── Hackerhouse/
└── Slidev/
```

## 可维护区

这些目录面向协作开发，可以正常新增文档、规格、fixture 和工具：

| 路径 | 内容 |
| --- | --- |
| `.github/` | GitHub issue 和 PR 模板 |
| `docs/api/` | 接口文档 |
| `docs/development/` | 协作、结构、开发流程 |
| `docs/specs/` | 开发规格 |
| `docs/kb/` | 知识库索引和 Telegram 整理层 |
| `skills/loveengine-witness/` | LoveEngine Witness Skill M0 包 |
| `tools/` | 校验脚本和未来 CLI |

## 源材料区

这些目录默认只读。不要为了整理仓库而移动、删除或原地重写：

| 路径 | 内容 |
| --- | --- |
| `LoveEngineSkill/` | LoveEngine、UAS、UHAH、NaturalDAO 早期开发记录 |
| `TG-5.22/` | Telegram 原始导出和附件 |
| `Discuss/` | EAP、Alignment、SCC0 早期资料 |
| `NaturalDAO/` | NaturalDAO 规划、PoL 分章和书稿工作区 |

如需摘要或改写，在 `docs/` 下新建整理文档，并标注来源。

## 独立子项目

`daism-tg-forwarder/` 是独立代码项目，已有自己的 `AGENTS.md`、测试和 Python 包配置。处理它时按该目录规则来，不要把 LoveEngine 的仓库化改动套进去。

## LoveEngine 原型边界

当前 LoveEngine 原型由两部分组成：

```text
skills/loveengine-witness/
tools/validate_loveengine_m0.py
tools/loveengine_m0_self_check.py
```

下一阶段可以新增：

```text
tools/loveengine/
LoveEngineSkill/prototype/
```

如果建立 Foundry 合约工程，建议优先放在 `LoveEngineSkill/prototype/` 或单独仓库。当前仓库还承担资料库职责，不宜把原始材料和生产代码混在一起。

## Hash 敏感文件

`skills/loveengine-witness/skill-manifest.json` 记录下列文件的 hash：

- `docs/specs/love-engine-skill-spec.md`
- `LoveEngineSkill/UAS接口文档.md`
- `LoveEngineSkill/UAS 见证方案 2.0.md`
- `docs/kb/source-inventory.md`

修改这些文件后，必须更新 manifest 的 `source_hashes`、`package_hash`，并同步更新 `skills/loveengine-witness/fixtures/propagation-task.fixture.json`。
