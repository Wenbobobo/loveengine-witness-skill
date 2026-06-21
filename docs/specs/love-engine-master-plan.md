# LoveEngine Witness Skill 工程总规划

状态：`current`  
版本：2026-06-21  
适用仓库：`Wenbobobo/loveengine-witness-skill`

本文是 LoveEngine Witness Skill 的唯一工程总规划。当前实施细节见 `docs/specs/love-engine-agent-network-pilot-spec.md`；已实现的 M1/M2 基线见 `docs/specs/love-engine-local-witness-loop-spec.md`；接口以 `docs/api/` 为准；原始材料保持在仓库根目录。

## 1. 核心目标

LoveEngine 首先是可被 Agent 网络验证、安装、传播和运行的 UAS 见证协议 Skill，不是中心化福利网站。

工程目标是形成可复核的见证链：

```text
Skill 校验
-> 节点声明
-> 排期
-> 证据包
-> 提案
-> 本地签名
-> 无特权 relayer
-> 链上投票与执行
-> PublicSink 查询
-> transcript
```

任何阶段都必须遵守：

- Agent 不读取、保存或打印私钥。
- `VoteSignature` 绑定 chainId、verifyingContract、proposalId、support、reasonHash、payloadHash、nonce 和 deadline。
- `PublicSink` 只读，无 owner 修改后门。
- UTO 是公共记账单位，不作为可交易资产。
- 原始证据不上链，只保存 hash。
- 治理值是部署或治理参数，不写成不可变叙事常量。

## 2. 当前进度

### M0 已完成

当前 M0 能够：

- 校验 Skill manifest、源资料、spec 和 package hash。
- 拒绝被篡改的 manifest。
- 提供 Agent onboarding、节点能力声明和传播任务 fixture。
- 检查 signer 边界、VoteSignature 绑定、relayer 权限和 PublicSink 只读规则。
- 通过 `uv run python tools/check.py` 完成仓库自检。

当前 M0 不能：

- 生成受 schema 约束的数据。
- 提供正式 `loveengine` CLI。
- 部署或运行四合约。
- 完成 EIP-712、relayer、证据、提案、投票和 transcript 闭环。

2026-06-21 的仓库基线是提交 `3b796ae`。当时远端无 open PR 或 issue，本机尚未安装 Foundry、Cast 和 Anvil。

## 3. 范围

当前工程包括：

- 可验证 Skill 包和版本规则。
- Agent 节点、证据、typed data、transcript 数据协议。
- Python CLI 与 adapter。
- WitnessDAO、CorporateSink、StreamingEngine、PublicSink 本地合约。
- 本地 signer、无特权 relayer 和可复跑 demo。

当前不包括：

- 完整 Web 产品。
- 真实直播平台与多模态审查。
- UHAH。
- 多企业、多地区和多币种结算。
- 生产身份系统和生产密钥托管。
- Agent 自动裁决争议。

## 4. 工程边界

```text
src/loveengine_witness/   Python domain、use case、adapter、CLI
schemas/                  JSON Schema Draft 2020-12
contracts/                Foundry 四合约与测试
tests/                    Python 单元、集成与端到端测试
examples/transcripts/     可提交的脱敏 transcript fixture
skills/loveengine-witness M0/M1 Skill 包
tools/                    兼容校验和仓库检查入口
docs/                     当前文档和历史归档
```

根目录的七份 Markdown 是源材料，默认只读。父目录 DAism 中的重复文件不是本仓库权威源。

## 5. 里程碑

### M0：可验证 Skill 包

状态：完成。

验收：

- source、spec、package hash 可复算。
- 篡改被拒绝。
- 私钥边界和签名约束可自动检查。
- 文档修订后发布 `0.1.1-m0`。

### M1：协议与 CLI 基础

状态：完成。

目标：固定数据形状、哈希规则和 Agent 可调用命令。

交付：

- SkillManifestV2、AgentNodeProfileV2、EvidenceBundleV1、LocalLoopTranscriptV1 schema。
- canonical JSON、SHA-256 和 Keccak-256 工具。
- `loveengine` CLI。
- 正反例 fixture 和 pytest。

验收：

- M0 manifest 继续兼容。
- 缺字段、错误 hash、非法权限、私钥字段均被拒绝。
- 命令成功输出 JSON，错误输出结构化 JSON。

### M2：Local Witness Loop

状态：完成。

目标：在 Anvil 上跑通最小见证闭环。

交付：

- 四合约和 Foundry 测试。
- EIP-712 register/vote typed data。
- 本地 signer 和无特权 relayer adapter。
- 部署、执行和 transcript demo。

验收：

- 快速 demo 使用 5 个见证者。
- 独立规模测试验证 69 个批量签名。
- 注册、排期、提案、投票、执行和查询完整通过。
- transcript 不包含私钥并可复核最终状态。

### M3：Agent 网络试点

状态：实施中。

目标：验证三个以上节点之间的 Skill 传播和任务协作。

验收：

- SkillRegistry 提供无全局 owner 的 Publisher 版本命名空间。
- 节点只通过出站连接接入 Relay Hub，并验证链上版本事实。
- 任务有发行者、签名、过期时间和重放保护。
- deprecated 版本不能接受新任务。
- 三个本地节点可验证同一 manifest、消费链上事件任务并提交签名回执。

### M4：证据与争议闭环

目标：接入文字直播和反对理由复核。

验收：

- 一场 fixture 直播形成 EvidenceBundle。
- 摘要与原文来源严格区分。
- 反对票关联 reasonHash，并触发三个独立复核任务。

### M5：企业补偿试点

目标：跑通补偿申请、表决、记录和公开查询。

验收：

- 结果可追溯到排期、证据、提案、投票和执行事件。
- CorporateSink 补偿账与 PublicSink 公共总账保持隔离。

## 6. 风险与控制

| 风险 | 当前控制 |
| --- | --- |
| Skill 传播污染 | manifest、source hash、package hash、版本废弃 |
| Agent 失控签名 | 本地 signer、策略门禁、私钥永不进入 Agent context |
| 签名重放 | chainId、verifyingContract、proposalId、nonce、deadline、payloadHash |
| 低参与率通过 | `minValidVotes` 与 90% 阈值分开配置 |
| 黑名单私权 | M2 不实现黑名单治理；后续必须有证据、复核和申诉 |
| 证据摘要替代原文 | EvidenceBundle 明确 source 与 summary 类型 |
| UTO 金融化误读 | 文档、API 和界面统一标注公共记账属性 |

## 7. 权威顺序

1. 本文：工程目标、边界、里程碑和当前状态。
2. `docs/specs/love-engine-agent-network-pilot-spec.md`：当前 M2 收口和 M3 实施要求。
3. `docs/specs/love-engine-local-witness-loop-spec.md`：已实现的 M1/M2 基线。
4. `docs/api/`：公开接口和状态。
5. `UAS接口文档.md`、`UAS 见证方案 2.0.md`：当前原始方案约束。
6. 其他根目录资料：历史和背景来源。
7. `docs/archive/`：仅用于追溯。
