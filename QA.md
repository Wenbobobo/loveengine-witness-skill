# LoveEngine Witness Skill 问答

本文回答 2026-06-25 道易程社区共识大会中开发成员提出的问题。当前保存的
[会议逐字稿](docs/reference/source-materials/meeting-transcripts/2026-06-25-daoyicheng-consensus-full.md)
正文实际只有 `33:42-51:06` 的 63 个发言片段；文件头声称覆盖整场会议，但正文
并不支持该说法。因此本文只引用现有片段，不把它称为完整会议记录。

状态标签含义：`[已实现]` 表示有当前代码和自动测试；`[仅本机实验]` 表示只在
loopback/Anvil 场景验证；`[治理实验]` 表示不属于 Witness Skill 默认核心；
`[未实现]` 表示不能对外承诺。

## 1. 现在到底有几个 LoveEngine Skill？（33:42）

**状态：`[已实现]`**

**当前已证明：** 仓库只有一个正式 Skill，即 `loveengine-witness`。它负责验证
发布包、接收见证任务、校验证据、签署任务回执和整理争议结果。此前讨论的“Agent
之间形成更一般共识的第二个 LoveEngine Skill”没有实现，也不是本轮目标。

**当前未证明：** 本仓库不是完整 LoveEngine，也没有实现第二个“文明传播”或
通用共识 Skill。

**验证入口：** [Skill manifest](skills/loveengine-witness/skill-manifest.json)、
[Skill 说明](skills/loveengine-witness/SKILL.md)。

## 2. 我们做的是 Skill，还是直播平台？（38:46-40:44）

**状态：`[已实现] [仅本机实验]`**

**当前已证明：** 核心是一个 Agent-network-first Witness Skill。`/operator/`、
`/demo/` 和文字输入只是本机实验 adapter，用来产生可重复的输入并观察状态，
不是产品定位，也不是中心化直播平台。Skill 文档很薄，真正的协议校验由可测试的
Python runtime、schema、Relay 和合约完成。

**当前未证明：** 当前 UI 不提供真实音视频直播、平台账号体系、内容推荐或商业
直播运营能力。

**验证入口：** [核心与数据流](docs/architecture/witness-core-and-data-flow.zh-CN.md)、
[扩展接口](docs/api/extension-interfaces.md)。

## 3. Skill 什么时候“命中”，任务怎样开始？（35:20、41:21）

**状态：`[已实现] [未实现]`**

**当前已证明：** 节点通过显式 `loveengine node connect` 建立出站 WebSocket；
经过信任策略、Registry release、节点 profile 和任务签名校验后，Relay 才能向该
连接推送 `observe_live_text` 或 `review_dispute`。触发来自主持人或实验 runner
创建并分配任务，不依赖 Agent 猜测用户意图。

**当前未证明：** 没有自动搜索互联网直播、长期链事件监听服务、日历调度 daemon
或“每天自行询问”的发现机制。公司直播接入已明确延期。

**验证入口：** [Agent network API](docs/api/agent-network-api.md)、
[CLI 参考](docs/api/cli-reference.md#5-observation-node)。

## 4. 主持人、Agent、见证者分别做什么？（39:00-40:44）

**状态：`[已实现] [治理实验]`**

**当前已证明：** 主持人负责鉴权写入、关闭 session 和显式 finalize；观察 Agent
验证事件/artifact 或复核争议并签任务回执；ProposalGate 只检查证据完整性和
关键争议状态；投票见证者在可选治理实验中亲自通过外部 RPC signer 批准投票。
Relay 只传递任务，不能替任何角色签名。

**当前未证明：** Agent 回执不是投票，也不代表 Agent 已裁决现实事实。主持人
页面不会自动执行 Gate 或链上治理。

**验证入口：** [参与者手册](docs/development/participant-runbook.zh-CN.md)、
[主持人流程](docs/development/runbooks/operator-flow.zh-CN.md)。

## 5. 数据保存在哪里，推送和获取的数据流怎样走？（42:33-43:14）

**状态：`[已实现] [仅本机实验]`**

**当前已证明：** 主持人的鉴权 HTTP POST 把 session、事件、bundle、dispute、
review 和 cursor 元数据写入 `pilot.sqlite`；原文 bytes 按 SHA-256 写入
`artifacts/sha256/<prefix>/<digest>`；Relay 的队列、投递和 ACK 状态写入
`relay.sqlite`；关键写入另记入 hash-linked `audit.jsonl`。观察节点用 SSE 读取
事件，用 HTTP 读取 artifact，并通过 WebSocket 返回签名 receipt。链上只保存
release、治理和会计所需的 hash/状态，不存文字原文。

**当前未证明：** 这些本机文件不是多副本对象存储、灾备系统或长期可用性承诺。
SHA-256 能证明取回的 bytes 是否一致，不能证明文件永远可取。

**验证入口：** [核心与数据流](docs/architecture/witness-core-and-data-flow.zh-CN.md)、
[Live evidence API](docs/api/live-evidence-api.md)。

## 6. 原文、证据、hash 和 transcript 分别证明什么？

**状态：`[已实现]`**

**当前已证明：** 事件使用 canonical JSON Keccak-256 组成连续 hash chain；原文
artifact 使用 SHA-256 内容寻址；finalize 会重新读取 bytes 并复算 hash；签名
回执绑定任务和结果；transcript 交叉检查这些引用。这样能发现内容、顺序、签名
或引用被篡改。

**当前未证明：** 完整性不等于真实性。系统能证明“保存后没有悄悄改掉”，不能仅
凭 hash 证明发言本身为真、转写无误或观察者在社会关系上独立。

**验证入口：** [Live evidence API](docs/api/live-evidence-api.md)、
[核心与数据流中的 hash 表](docs/architecture/witness-core-and-data-flow.zh-CN.md#hash-语义)。

## 7. 三个 Agent 已经证明了什么？（45:38-45:46）

**状态：`[本机已验证] [远端待验收]`**

**当前已证明：** 固定实验可以启动三个独立进程/连接，验证成员身份、任务签名、
事件连续性、artifact 完整性、三份不同地址的回执，以及争议复核的多数结果。

**当前未证明：** 三个进程仍可运行在同一台电脑、由同一实验 runner 和 Anvil
测试账户控制，所以不能证明现实中的组织独立性、抗串谋性或生产网络可靠性。

**验证入口：** [开发实验指南](docs/development/integration-guide.md)、
`tests/integration/test_network_demo.py`。

## 8. 为什么使用 Python、uv 和 Foundry？它会生成生产钱包吗？（44:04-44:50）

**状态：`[已实现] [仅本机实验]`**

**当前已证明：** Python runtime 负责协议、存储、HTTP/WebSocket 和 CLI；`uv`
固定 Python 依赖；Foundry 1.7.1 负责编译、测试和启动本机 Anvil。quickstart
使用 Anvil 内置测试账户完成可重复实验。生产投票命令只请求外部 RPC signer，
CLI 不接受私钥参数。

**当前未证明：** 没有实现生产钱包生成、托管、恢复、硬件钱包策略或组织级密钥
管理；Anvil 测试账户不能用于真实资产或公网部署。

**验证入口：** [CLI 参考](docs/api/cli-reference.md)、
[投票见证者流程](docs/development/runbooks/voting-witness-flow.zh-CN.md)。

## 9. 五个合约各自起什么作用？（47:46-50:30）

**状态：`[已实现] [治理实验]`**

**当前已证明：** `SkillRegistry` 是核心信任合约，记录 Publisher 命名空间下的
skill/version、ZIP hash、manifest hash 和 release 状态。其余四个是可选治理
实验：`WitnessDAO` 管理见证者、提案、签名投票及受限退款；`CorporateSink`
记录单场实验的排期、certificate hash 和补偿；`StreamingEngine` 按时间累计
UTO 公共会计；`PublicSink` 只读返回累计值。

**当前未证明：** `CorporateSink` 目前不适合并行多场排期；新排期会改变全局
`nextBroadcastTime`。ProposalGate 是链下 advisory 检查，具有 proposer 权限的
`corporateAdmin` 仍可直接调用 WitnessDAO。因此四合约路径只作为治理实验，不能
宣称链上强制执行了 Gate。

**验证入口：** [合约 API](docs/api/loveengine-contract-api.md)、
[SkillRegistry 决策](docs/decisions/0001-onchain-skill-registry.md)。

## 10. 合约实际记录和返回什么，怎样知道真的提交了？（48:35-49:12）

**状态：`[已实现] [仅本机实验]`**

**当前已证明：** 实验保存部署交易、合约地址和 runtime code hash；治理阶段保存
proposal/vote/执行交易 receipt、区块信息和最终 `PublicSink.getTotalUTO()`。
带可信策略的 RPC transcript 校验会在记录的历史区块复核 Registry、交易、code、
WitnessDAO 和 PublicSink，而不是只相信截图或 runner 自报成功。

**当前未证明：** 离线 transcript 只能证明内部 hash、签名和交叉引用一致；没有
可信 policy 与 RPC 链事实时 `trust_bound` 必须是 `false`。本轮没有公共测试网
交易可供外部浏览器查看。

**验证入口：** [LAN Pilot API](docs/api/lan-pilot-api.md)、
[合约 API](docs/api/loveengine-contract-api.md)。

## 11. ProposalGate 是否决定事实或自动发交易？

**状态：`[已实现]`**

**当前已证明：** Gate 只接受 finalized EvidenceBundle，并检查所有 critical
dispute 是否已 `dismissed`。通过后返回 proposal plan；失败则给出阻断原因。

**当前未证明：** Gate 不判定公众陈述真假、不签投票、不提交交易，也不是合约
权限控制。Operator 和 Viewer 页面目前只展示状态/说明，没有 Gate 执行按钮。

**验证入口：** [Live evidence API](docs/api/live-evidence-api.md)、
`src/loveengine_witness/dispute.py`。

## 12. 现在能不能让开发成员直接运行和测试？（46:01-47:43）

**状态：`[仅本机实验]`**

**当前已证明：** 开发成员可在 Python 3.11+、uv 和固定 Foundry 1.7.1 环境中
执行分层实验：包信任、Relay/receipt、证据/finalize、争议/Gate，以及可选的
治理合约流程。每层都有机器可读结果和自动测试；远端 runner 还会用公开
`node connect` 经 SSH tunnel 验证连接后任务和绑定 receipt。

**当前未证明：** 当前最新 Git tag 是 `v0.6.0-contract-public-pilot`；工作树中的
`0.6.1-contract-public-pilot` 是 candidate，不是已发布版本。工具实现不等于
指定主机已经通过 SSH/resource/core/tunnel 验收，也没有完成公网、公共测试网、
TLS、生产身份或 HA 验收。

**验证入口：** [开发实验指南](docs/development/integration-guide.md)、
[中文 README](README.zh-CN.md)。

## 13. 为什么这次不先接公司直播或公共测试网？（41:21、49:47-50:02）

**状态：`[未实现]`**

**当前已证明：** 本机 fixture 和 HTTP push adapter 足以验证核心证据协议，而不
需要把平台认证、调度、限流、视频格式和运维问题同时引入。先把信任、数据流和
可复核结果讲清楚，能降低开发成员测试时的歧义。

**当前未证明：** 会议中“之后会部署公共测试网或主机”的表述只是当时计划，不是
完成事实。远程实验需要明确 host、端口、身份、部署目录和 signer 方案后单独验收。

**验证入口：** [活动远程实验规格](docs/specs/love-engine-pre-enterprise-remote-lab.md)、
[工程总规划](docs/specs/love-engine-master-plan.md)。

## 14. 远程主机测试是否等于可以接企业？

**状态：`[已实现] [未实现]`**

**当前已证明：** 仓库提供只读 shared-host preflight、跨平台 core runner 和
key-only SSH 编排。它固定 host key、拒绝 password 参数、要求干净 commit，
限制为 nice +15 和最多两核，并保持远端 Pilot/Anvil loopback。短实验还覆盖
持久 Anvil 重启、Quickstart Relay、snapshot 恢复，以及公开 node CLI 经 SSH
tunnel 收到连接后任务并返回绑定 receipt。

**当前未证明：** 工具存在不等于某台主机已经验收。只有目标主机 preflight、
远端 core report、下载 transcript 的本机复验都通过后，才能声称共享 Linux
实验成功；还必须看到 tunnel smoke、receipt 和本次进程组清理结果。即使成功，
也不证明生产 signer、TLS、HA、公共测试网或企业系统已经接入。

**验证入口：**
[远程实验规格](docs/specs/love-engine-pre-enterprise-remote-lab.md)、
[共享主机 runbook](docs/development/runbooks/remote-lab-flow.zh-CN.md)、
`tests/test_remote_lab.py`。
