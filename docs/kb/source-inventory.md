# Source inventory

本清单区分当前工程 authority、preserved source 和历史记录。机器可读清单在
[sources.json](sources.json)；发布文件集合由该清单与 manifest source inventory
派生，不在文档中维护第三份硬编码列表。

## Current engineering authority

| Path | Role |
| --- | --- |
| README.md / README.zh-CN.md | 中英文仓库入口和真实成熟度 |
| QA.md | 会议问题的代码事实回答 |
| docs/specs/love-engine-master-plan.md | 唯一工程总规划 |
| docs/specs/love-engine-invited-public-pilot.md | 当前 0.7 candidate 的接口、阶段和退出门 |
| docs/specs/love-engine-pre-enterprise-remote-lab.md | 0.6.1 共享 Linux 历史验收规格 |
| docs/architecture/witness-core-and-data-flow.zh-CN.md | 核心/治理边界、数据流和存储 |
| docs/api/ | 当前接口、ABI 和 CLI |
| docs/development/integration-guide.md | 分层开发实验 |
| docs/development/runbooks/ | 当前角色操作手册 |
| skills/loveengine-witness/skill-manifest.json | 当前 source refs/hashes 与 package identity |

最新 Git tag 是 `v0.6.0-contract-public-pilot`；0.6.1 PR #11 尚未人工合并。
`0.7.0-invited-public-pilot` 是叠加在该 PR 上的工作 candidate，协议仍为
`loveengine-witness-net/0.6`。M3-M6 阶段报告不再属于 current authority。

## Preserved current source constraints

以下文件只作为来源/约束，不原地改写：

| Path | Role |
| --- | --- |
| docs/reference/source-materials/current/UAS接口文档.md | 合约协作者接口来源 |
| docs/reference/source-materials/current/UAS 见证方案 2.0.md | Witness 方案来源 |
| docs/reference/source-materials/meeting-transcripts/2026-06-25-daoyicheng-consensus-full.md | 会议自动转写原文；正文实际覆盖 33:42-51:06 |
| docs/reference/contracts/contract-team-v2/ | contract-team v2 preserved input |

会议文件迁移前后 SHA-256 均为
2c5416dc9b9dc02e6c9a90cd374e5d71d9766b5b3525fdb99becabf30a0878a2；文件头的
“全程覆盖”声明与当前正文不一致，QA 只使用实际存在的片段。

## Historical documentation

已完成规格保存在 docs/archive/specs/implemented/，包括 M1-M6 与 0.6.1
hardening 历史规格。旧规划和源材料分别保存在 docs/archive/planning/ 和
docs/archive/source-materials/。

以下 development 文件保留验收/决策历史，但退出活动索引：

- docs/development/m3-demo-runbook.md
- docs/development/m3-acceptance-report.md
- docs/development/m4-skill-supervision-and-next-stage-gaps.md
- docs/development/m5-acceptance-report.md
- docs/archive/planning/2026-06-23/m5-release-closeout-plan.md
- docs/archive/planning/2026-06-24/m6-demo-docs-publication-plan.md

## Implementation layers

- Core trust：deterministic ZIP、SkillRegistry、NodeTrustPolicyV1、profile/bootstrap、
  signed task/receipt、鉴权 task ingress 和 Relay。
- Core evidence：session/event、artifact、ObservationSet、EvidenceBundle、critical
  dispute、独立 review evidence 复算和 ProposalGate。
- Runtime recovery：节点 task journal、issuer nonce 去重、有界重连和 receipt
  ACK-loss confirmation。
- Core verification：WitnessCoreTranscriptV1 的 offline/RPC/policy 模式。
- Invited pilot contract：WitnessCoreTranscriptV2、PilotConfig/InviteV2、双入口、
  ExternalSignerConfigV1、Sepolia transaction plan、受限文件加载的双 RPC、
  assignment-bound participant attestation 和 Tailscale Serve preflight/自有配置
  恢复；目前只有本机实现与测试。
- Historical remote lab：只读资源门、key-only SSH、loopback 服务、低优先级 core/recovery，
  以及经 SSH tunnel 使用公开 node CLI 的连接后签名任务与回执验证。
- Governance lab：WitnessDAO、CorporateSink、StreamingEngine、PublicSink、显式
  vote 和 PilotTranscriptV2。
- Historical compatibility：M0-M6 schema、codec、fixture 和 transcript reader。

## Hash update rule

修改任何 manifest source_ref 后，统一运行 refresh 工具更新 current/M0 manifest
source hash 和 package hash；只有 M0 package hash 变化时才更新 propagation fixture。
新增或移动公共 artifact 时先更新 sources.json，再刷新 manifest。不要手工猜 hash。

## Authority order

1. docs/specs/love-engine-master-plan.md
2. docs/specs/love-engine-invited-public-pilot.md
3. docs/architecture/witness-core-and-data-flow.zh-CN.md
4. docs/api/
5. docs/specs/love-engine-pre-enterprise-remote-lab.md
6. docs/reference/source-materials/current/
7. preserved meeting/contract sources and historical archive
