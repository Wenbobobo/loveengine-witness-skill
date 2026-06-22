# LoveEngine M4 阶段监督与下一阶段缺口

状态：`current supervision baseline`  
记录日期：2026-06-22  
适用基线：`v0.4.0-live-evidence-pilot`

本文记录 M1–M4 完成后的架构判断、当前缺口和下一阶段约束。它是后续 SPEC 的监督依据，不替代工程总规划，也不提前决定具体实现。

## 1. 当前完成度

LoveEngine 已达到可独立运行、可验证、可对外演示的本地协议原型阶段。

当前基线已经具备：

- 可复算的 Skill manifest、source hash 和 package hash。
- JSON Schema、canonical JSON、SHA-256 与 Keccak-256 规则。
- 四个 UAS 核心合约和 SkillRegistry。
- EIP-712 节点身份、任务、回执、注册与投票消息。
- Relay Hub、三个独立 Agent 进程和出站 WebSocket 连接。
- 文字直播摄取、连续事件哈希链、内容寻址 artifact 和 EvidenceBundleV2。
- 三节点争议复核、fail-closed ProposalGate 和只读面板。
- LocalLoopTranscript、NetworkTranscript 和 LiveReviewTranscript。

2026-06-22 的复核结果：

- 全新临时 Python 环境可从 `uv.lock` 完成安装。
- Python 测试 57/57 通过。
- Foundry 1.7.1 合约测试 19/19 通过。
- 仓库检查、M0 篡改拒绝、资料索引和活动文档检查通过。
- Local Loop、Agent Network 和 Live Evidence 三份 transcript 均可离线验证。

这里的“完整”仅指当前 M1–M4 本地 Demo 定义。它不等于标准 Agent Skill 已完成分发，也不等于生产系统已经就绪。

## 2. 当前 Skill 模型

LoveEngine 不是把所有实现写进一份命令文档。当前采用的是“薄指令层、厚协议运行时”模型：

```text
Agent 指令入口
    ↓
Skill Manifest / Onboarding
    ↓
Schema + CLI + Python 领域逻辑
    ↓
Relay / LiveGateway / Signer Adapter
    ↓
智能合约与 SkillRegistry
    ↓
证据、回执与 Transcript
```

各部分职责如下：

| 层 | 当前载体 | 职责 |
| --- | --- | --- |
| Skill 身份 | `skill-manifest.json` | 定义版本、协议、能力、命令、安全边界和受保护引用 |
| 信任清单 | `source_refs` / `source_hashes` | 将规范、Schema、合约和实现文件绑定到同一发布版本 |
| Agent 说明 | `agent-onboarding.md` | 说明验证、接入、签名请求和禁止事项 |
| 执行入口 | `loveengine` CLI | 将 Agent 意图转换成稳定、机器可读的命令 |
| 协议契约 | JSON Schema、EIP-712 | 固定节点、任务、回执、证据和 transcript 的数据格式 |
| 运行时 | Python 包 | 实现 Relay、直播摄取、证据、争议、门禁和验证逻辑 |
| 链上事实 | 四个核心合约和 SkillRegistry | 保存注册、排期、治理执行和 Skill 版本事实 |
| 审计产物 | Transcript | 让其他 Agent 离线复算过程和结果 |

Skill 负责描述、约束和调用，代码库负责实现，manifest 使用 hash 把两者绑定。大型协议型 Skill 不应把业务逻辑塞进提示词；Agent 指令应保持简短，复杂逻辑应由可测试的运行时和协议承担。

本节适合在下一轮 README 整理时加入项目说明。

## 3. Skill 如何完成当前整体目标

当前流程已经覆盖：

1. Agent 验证 Skill manifest、package hash 和受保护文件。
2. 节点通过签名 profile 与 bootstrap 加入网络。
3. SkillRegistry 提供 Publisher、版本和状态信任边界。
4. Relay 向只使用出站连接的 Agent 分发签名任务。
5. LiveGateway 摄取文字直播事件。
6. LiveEvent 形成连续、不可静默改写的哈希链。
7. ArtifactStore 保存内容寻址的原始证据。
8. EvidenceBundleV2 汇总可验证事件和 artifact 引用。
9. 三个节点对争议生成签名复核。
10. ProposalGate 阻断 `upheld` 和 `unresolved` 争议。
11. Transcript 将链上地址、事件、任务、回执、证据和最终状态组合成可复核产物。

M1–M4 已经证明核心协议可以运行。下一阶段的重点不是继续横向增加功能，而是把这些独立闭环组织成可安装、可分发、跨环境运行的完整 Skill。

本节也适合在下一轮 README 整理时加入全流程说明。

## 4. 当前缺口

### 4.1 尚不是通用 Agent Skill 安装包

`skills/loveengine-witness/` 当前没有通用 Skill loader 常见的 `SKILL.md`。因此它是 LoveEngine 协议意义上的 Skill 包，但不能直接被 Codex、Claude 或其他兼容 loader 当作标准 Skill 自动发现。

下一阶段需要新增一层很薄的 `SKILL.md`，只负责：

- 定义触发条件和适用场景。
- 指向 manifest 验证和安装流程。
- 列出必要 CLI 命令。
- 声明签名、私钥和源资料边界。
- 告诉 Agent 何时读取详细规范。

业务逻辑继续留在 Python 包、Schema 和合约中，不应复制进 `SKILL.md`。

### 4.2 链上 package hash 尚未代表完整发布包

当前 manifest 保护了大量核心引用，但没有覆盖完整安装输入，例如：

- `pyproject.toml`
- `uv.lock`
- 基础 hash、schema、security helper
- CI 和发布元数据
- 已生成的 transcript

Registry demo 中的 artifact/package hash 也不是一个真实可下载、可安装的完整 Skill archive。

下一阶段需要构建确定性发布包，例如：

```text
loveengine-witness-<version>.tar.zst
manifest.json
checksums.json
SBOM
```

Registry 的 `packageHash` 应绑定这个 archive。新 Agent 下载后必须能验证 archive hash、展开文件、验证 manifest，并从锁文件安装运行。

### 4.3 M1–M4 还不是单一连续 E2E

目前有三个经过验收的闭环：

- M2：注册、提案、投票、执行和 PublicSink 查询。
- M3：链上事件、Relay、Agent 任务和签名回执。
- M4：直播、证据、争议复核和 ProposalGate。

但还没有一次测试从直播开始，一直运行到链上执行：

```text
直播摄取
→ EvidenceBundleV2
→ 三节点复核
→ ProposalGate
→ WitnessDAO 提案
→ 见证者签名投票
→ 合约执行
→ PublicSink 查询
→ 单一 Transcript
```

下一阶段必须新增这个系统级 E2E。它应成为演示和发布的最高验收门槛。

### 4.4 `observe_live_text` 尚未形成真实网络任务闭环

节点 profile 已声明 `observe_live_text` 能力，LiveGateway 也能摄取文字事件，但当前 M4 transcript 中实际分发的是 `review_dispute`。

下一阶段需要让 Relay 真正派发 `observe_live_text`：

- 节点接收 session 和 cursor。
- 节点读取或订阅规范化文字流。
- 节点生成观察回执。
- 断线恢复、重复事件和 cursor 前进必须可测试。
- 观察结果应能追溯到 EvidenceBundleV2。

### 4.5 生产和真实试点能力尚未实现

当前缺少：

- 简单但真实的直播源 adapter。
- 生产鉴权、限流和审计日志。
- Clef、AA 或浏览器钱包的正式 signer 接入。
- 公共测试网部署和合约地址管理。
- Relay 多实例、备份和恢复。
- 数据库迁移和备份。
- 公共 RPC 链重组处理。
- 端到端加密、生产监控和告警。
- 链上争议门禁和受策略约束的自动化投票流程。

这些内容不应一次全部进入下一阶段。下一阶段先选择足够支持真实试点的最小集合：简单直播源、一个生产型 signer adapter、公共测试网和可观测部署。

### 4.6 文档仍有少量漂移

当前可见问题包括：

- DAism 根 README 仍引用已经归档的 Local Witness Loop SPEC。
- `docs/api/agent-skill-api.md` 的状态和命令列表主要停留在 M0–M2。
- M4 已完成，但部分入口仍使用“活动 SPEC”措辞。
- README 尚未完整解释“薄指令层、厚协议运行时”的 Skill 模型。

下一阶段开始前应先做一次小范围文档校准。校准只修正状态、入口和架构说明，不重写历史资料。

## 5. 路线判断与下一阶段方向

当前方向是正确的。LoveEngine 已经从概念说明发展成可运行、可审计的协议原型。继续把所有逻辑塞进一份 Skill 文档会降低可测试性，也会放大提示词和运行时之间的偏差。正确做法是保留薄的 Agent 指令入口，让 manifest、Schema、运行时、合约和 transcript 承担实际协议责任。

下一阶段应围绕 Skill 制作、分发和全流程验收推进：

1. 建立标准 `SKILL.md` 和兼容的安装目录。
2. 生成确定性发布 archive，并用 SkillRegistry 的 package hash 绑定。
3. 在全新环境中完成下载、验证、安装、能力声明和自检。
4. 接入一个简单直播源，完成 `observe_live_text` 网络任务闭环。
5. 在本地 Anvil 上打通直播到 PublicSink 的单一全流程 E2E。
6. 在公共测试网上重复同一流程，保留合约地址、交易、事件和 transcript。
7. 继续使用三个独立 Agent 进程和 Relay，验证断线、重放、过期任务、错误版本和 artifact 篡改。
8. 将 Skill 模型和完整流程补入中英文 README。

下一阶段的验收结果至少应包括：

- 可被标准 Skill loader 发现的包。
- 可下载、可复算的发布 archive。
- 简单直播源的可重复 fixture 和真实输入测试。
- 本地链完整 E2E。
- 公共测试网完整 E2E。
- 三节点 Agent 网络的任务、回执和恢复证据。
- 一份覆盖直播、证据、复核、提案、投票、执行和查询的最终 transcript。

在这些成果完成前，不把系统表述为生产可用。
