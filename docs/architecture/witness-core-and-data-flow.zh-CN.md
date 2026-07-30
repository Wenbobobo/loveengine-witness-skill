# LoveEngine Witness 核心与数据流

状态：0.6.1-contract-public-pilot candidate
协议：loveengine-witness-net/0.6
定位：开发成员理解和复核当前实现的规范性架构说明

## 系统边界

本仓库只实现一个 loveengine-witness Skill。默认主线在 ProposalGate 结束；
WitnessDAO、投票和 PublicSink 属于可选治理实验。

| 层 | 责任 | 不是 |
| --- | --- | --- |
| Skill 文档 | 告诉 Agent 何时调用哪些受约束接口 | 独立运行的服务或信任根 |
| Witness runtime | 校验发布、节点、任务、证据、争议和 transcript | 事实裁判或自动投票者 |
| Relay | 持久化并至少一次投递任务，接收 ACK/receipt | 签名者或 release 信任根 |
| Operator / Viewer UI | 本机输入和只读观察 adapter | 直播产品或 Gate 执行器 |
| SkillRegistry | 锚定 Publisher release 的 ZIP/manifest hash 与状态 | 保存完整 ZIP 或证据原文 |
| 治理实验 | 演示显式投票和 UTO 公共记账 | Witness Skill 的默认完成条件 |

公司直播 adapter、自动发现、长期 scheduler、公网、测试网、生产身份、TLS、HA
和多副本证据存储当前都未实现。共享 Linux SSH 短实验已在 ARM64 主机上通过，
但它只运行 loopback Anvil/Pilot、core/recovery 和 tunnel 节点验证，不是远程
生产服务。

## 组件与角色

~~~mermaid
flowchart LR
    Publisher["Publisher"] --> Package["deterministic ZIP"]
    Publisher --> Registry["SkillRegistry"]
    Policy["NodeTrustPolicyV1 via trusted side channel"] --> Node["Observation Agent"]
    Package --> Node
    Registry --> Node
    Operator["Operator"] -->|authenticated HTTP POST| Server["Pilot Server"]
    Server --> Store["SQLite + artifact store + audit log"]
    Server -->|SSE / artifact HTTP| Node
    Server --> Relay["outbound WebSocket Relay"]
    Relay --> Node
    Node -->|ACK + signed receipt + receipt confirmation| Relay
    Store --> Review["dispute aggregation"]
    Review --> Gate["ProposalGate"]
    Gate -. optional .-> DAO["WitnessDAO governance lab"]
    DAO -.-> Sink["CorporateSink / StreamingEngine / PublicSink"]
~~~

- Publisher 构建发布包并通过外部 signer 发布 Registry release；仓库 CLI 只生成
  发布交易计划和执行只读验证。
- Operator 持有 Pilot 写 token，创建/关闭 session、写事件并显式 finalize。
- Observation Agent 使用自己的外部 signer 身份，只签任务回执，不签投票。
- Voting Witness 只在治理实验中显式审阅并批准 vote typed data。
- Public Viewer 无 token、无钱包，只读查看 session/evidence；页面不是信任根。

## 核心时序

### 1. 发布与节点接入

~~~mermaid
sequenceDiagram
    participant P as Publisher
    participant R as SkillRegistry
    participant O as Operator
    participant N as Observation Agent
    participant H as Relay
    P->>P: build deterministic ZIP
    P->>R: external signer publishes ZIP Keccak + manifest Keccak
    P-->>N: distribute ZIP and NodeTrustPolicyV1 separately
    H-->>N: distribute secret-free invite
    N->>R: read active release
    N->>N: verify policy, ZIP, manifest and signed profile
    N->>H: outbound WebSocket connect as bootstrap member
    P-->>O: provide signed NetworkTaskV2
    O->>H: authenticated POST signed task
    H->>H: verify release, member, issuer, recipient and signature
    H->>N: signed observe/review task
    N->>N: verify issuer, recipient, nonce and deadline
    N-->>H: acceptance ACK
    N-->>H: signed TaskReceiptV2 after completion
    H-->>N: receipt stored ACK
    N-->>H: receipt ACK confirmation
~~~

invite 只提供连接位置和方便核对的公开元数据，不是信任根。NodeTrustPolicyV1
必须通过可信旁路获得，并固定 chain ID、Registry、Publisher、skill/version、ZIP
hash、manifest hash 和允许的 issuer。Relay 或任务即使自洽地伪造另一套 invite，
也不能改变 policy。

### 2. 事件、证据与争议

~~~mermaid
sequenceDiagram
    participant O as Operator
    participant S as Pilot Server
    participant D as SQLite / artifacts
    participant A as Observation Agents
    O->>S: POST session and text events with bearer token
    S->>D: store metadata, raw bytes and audit record
    A->>S: follow SSE from durable cursor
    A->>S: fetch artifact bytes by SHA-256
    A-->>S: signed observation receipts
    O->>S: POST close
    O->>S: POST evidence/finalize
    S->>D: reread bytes and recompute every hash
    S->>A: signed review tasks bind bundle/events/artifact URLs
    A->>S: refetch and recompute bundle, event chain and artifacts
    A-->>S: signed review receipts
    S->>S: aggregate ObservationSet and critical reviews
    S->>S: evaluate ProposalGate
    S-->>O: ready plan or explicit blocking reasons
~~~

写入失败不能静默降级。关闭 session 不会隐式 finalize；GET evidence 永远只读。
finalize 后 bundle 不可原地修改，需要新 revision。critical dispute 由三个不同的
bootstrap 成员复核：至少两票 dismiss 才能 dismissed，至少两票 uphold 才能
upheld，其余为 unresolved。Gate 只在 bundle finalized 且所有 critical dispute
均 dismissed 时返回 ready plan。verdict map 只表达节点操作者的结论；节点在签名
前还必须从 invite 绑定的同一 HTTP origin 取回 finalized bundle、完整事件链和
每个 artifact，复算 hash、顺序、category count 与 bundle 引用。

### 3. 可选治理实验

~~~mermaid
sequenceDiagram
    participant C as corporateAdmin
    participant D as WitnessDAO
    participant W as Voting Witnesses
    participant E as StreamingEngine
    participant P as PublicSink
    C->>D: propose with payload/evidence hashes
    W->>W: independently review on-chain proposal plan
    W-->>D: explicit EIP-712 vote approvals via relayer
    D->>E: execute accepted user-count update
    P->>E: getCurrentBalance
~~~

这是本机治理机制实验，不是核心证据闭环。ProposalGate 是链下 advisory check，
不是 WitnessDAO 的访问控制；具有 proposer 权限的 corporateAdmin 可以绕过 Gate
直接提案。当前 CorporateSink 以单个 nextBroadcastTime 计算提案窗口，新排期
会覆盖该全局时间，因此只支持一次干净部署中的单场实验，不能宣传为并发排期方案。

## 数据落点

| 数据 | 默认位置 | 持久性与边界 |
| --- | --- | --- |
| session、event 元数据、bundle、dispute、review、cursor | pilot.sqlite | 单机 SQLite；可 snapshot，不是 HA |
| Relay queue、delivery、ACK、receipt 状态 | relay.sqlite | at-least-once；消费者必须幂等 |
| 节点 task journal 与 observation cursor | node cursor.sqlite | taskId + issuer/nonce、signed receipt、cursor；用于断线恢复 |
| 文字原文 bytes | artifacts/sha256/[prefix]/[digest] | 内容寻址；存在性仍依赖单机磁盘 |
| 关键写入和拒绝 | audit.jsonl | hash-linked append log |
| Anvil deployment/state、交易和 code hash | Pilot chain root | 本机实验链；不是公共测试网 |
| Foundry dependency/cache 与 deployment artifacts | `contracts/lib`、`contracts/cache`、`contracts/out` | 被 Git 忽略的显式准备产物；报告保留摘要，不是发布信任根 |
| ZIP/manifest hash 和 release 状态 | SkillRegistry | 链上 hash/状态，不存 ZIP bytes |
| proposal、vote、UTO 会计 | 四个治理实验合约 | 可选层，只存结构化值和 hash |
| 原文、token、私钥 | 不上链 | token 只从受限文件读取；私钥只在外部 signer |

snapshot 对数据库、artifact、audit 与链状态做完整 checksum 覆盖；restore 先验证并
准备 rollback，再替换正式状态。它解决单机恢复，不等于异地灾备。

Unix-like 系统会检查 token 文件的 group/other 权限；当前 Windows quickstart 只把
token 隔离在本机文件中，尚未显式配置或验收 NTFS ACL。因此它是 loopback 实验
边界，不是已完成的多用户主机凭据隔离方案。

所有真实 Pilot 路径都先要求 `loveengine pilot contracts prepare`。它严格检查
Forge/Anvil `1.7.1`、版本化 dependency lock、受控 Foundry profile/remapping，并以
单线程构建。缺失依赖可按固定 commit 安装；已有依赖的规范树摘要失配则失败关闭，只有
显式 `--refresh-dependencies` 才会在 staging 核验后切换。成功后在
`contracts/cache/loveengine-contract-preparation.json` 保存本地 attestation；package、
quickstart、demo、chain init 与 soak 会重新计算它，因此不在执行过程中隐式编译。这样
计时实验的资源指标不混入下载/编译副作用。prepare 会先把受管依赖中 UTF-8 文本规范化为
LF，避免宿主 Git 行尾策略进入编译 metadata；package build 也会以同一 LF 表示写入所有
文本 archive 条目，避免等价 CRLF checkout 产生不同 release hash；缺产物或 attestation 失配时也会明确 fail
closed。该本地 attestation 只能发现未经重新准备的后续改动，不单独证明上游源码来源；
发布信任仍来自包、Registry 与外部 policy。

每份部署 artifact 的 compiler metadata 还必须只列出 `src/` 或两棵锁定的 `lib/` 依赖树；
因此相对 import 不能把 `test/`、`script/` 或工作区外文件悄悄带入部署字节码。

## Hash 语义

“package hash”在不同上下文中曾被混称，开发和评审必须使用下表中的完整名称。

| 名称 | 算法与输入 | 用途 |
| --- | --- | --- |
| manifest source hash | SHA-256 of each referenced file | 检测 manifest 引用源变更 |
| manifest package hash | SHA-256 of canonical manifest view with self marker | 检测 manifest 内部集合变更；格式为 sha256:... |
| manifest hash | Keccak-256 of canonical manifest JSON | 绑定 Registry release 与 ZIP 内 manifest |
| archive SHA-256 | SHA-256 of actual ZIP bytes | 普通文件摘要和诊断 |
| Registry package hash | Keccak-256 of actual deterministic ZIP bytes | SkillRegistry.Release.packageHash；可信安装的 expected hash |
| checksums entry | SHA-256 of each non-checksum ZIP member | 要求非空并完整覆盖包内文件 |
| content/artifact hash | SHA-256 of exact source bytes | artifact 地址和取回校验 |
| event hash | Keccak-256 of canonical event without its own hash | 构建事件连续链 |
| payload/bundle/proposal hash | Keccak-256 of canonical structured data | 跨阶段引用和 EIP-712 绑定 |
| code hash | Keccak-256 of deployed runtime bytecode | transcript/RPC 合约身份复核 |

## 传输、重试与失败模式

- Agent 只建立出站 WebSocket；Relay 可以在连接建立后继续推送新任务。
- Pilot 的鉴权 task ingress 只接受已经签名的 NetworkTaskV2；write token 只授权
  入队，不赋予 Pilot 代替 Publisher 签名的能力。入口要求完整可执行 payload；
  字节完全相同的 signed task 重试幂等成功，同 ID/nonce 不同内容拒绝。
- Relay 使用 at-least-once 语义。节点以 taskId 和 issuer+nonce 双重去重，并在
  发回前把 signed receipt 落入本地 SQLite journal。
- 接收 ACK 延迟和任务完成延迟分别记录；长观察任务期间继续处理 heartbeat。
- observation cursor 持久化，SSE 重连携带 Last-Event-ID 或 after。
- receipt 必须属于当前鉴权连接和该节点已接受的 pending task；伪造、错绑或重复
  receipt 均拒绝。
- Relay 保存 receipt 后返回 ACK，节点再回 receipt confirmation。若第一份 ACK
  丢失，重连后 Relay 发送 `receipt_state`；节点只有在它与本地 journal 完全一致
  时才确认，Relay 再返回 `receipt_confirmed`，整个过程不重复执行。
- observation 单次执行最多处理 10,000 个新事件和 64 MiB artifact；review
  最多处理 1,000 个事件和 32 MiB artifact。JSON/SSE 单响应上限 1 MiB，单个
  artifact 上限 8 MiB，所有取回都禁用 redirect。
- 当前 node connect 默认最多重连 3 次、idle timeout 60 秒；每次重连重验 challenge
  和 profile，首次之后还重验 Registry release。它仍是有界会话，不是常驻
  scheduler 或生产级 daemon。
- 共享远程实验固定 host key、只接受 SSH key，并通过 tunnel 保持 Pilot/Anvil
  的 loopback 边界；本机先同时连接 3 个公开 node CLI 进程，再让每个节点经
  tunnel 完成自己的连接后任务、evidence 复算和 receipt 验证，但不开放
  Tailscale 或公网监听。改进前 baseline `2fd3a29` 的机器报告只验证单节点基础
  路径；增强路径必须以每次机器报告中的精确 source_commit、3 个 ACK 和 3 个
  receipt confirmation 逐次复验。三个本机进程不等于三个现实组织；长 soak 和
  生产网络仍未验证。
- NetworkTaskV2 只执行 observe_live_text 和 review_dispute。旧 V1 的
  propagate_skill、observe_broadcast 只保留历史兼容验证。
- 历史 V2 transcript 的最小 observe/review payload 仍可由兼容 reader 校验签名和
  交叉引用，但只返回 `legacy_consistency`，不再获得 release/chain 信任结论。当前
  Core/V2 路径要求完整 payload，review receipt 必须带 `evidence_verified:true`，并把
  dispute、bundle、session、revision、count、head hash 全部回绑；公开任务入口同样只接收
  带 URL、cursor、revision、count 和 head hash 的完整 payload。

## Transcript 能与不能证明什么

| 格式/模式 | 能证明 | 不能证明 |
| --- | --- | --- |
| PilotTranscriptV1 | 历史字段一致性 | 发布信任或链事实；返回 legacy_consistency |
| WitnessCoreTranscriptV1 offline | 核心阶段的 hash、签名、成员、quorum 与引用一致 | Registry/链事实；trust_bound:false |
| WitnessCoreTranscriptV1 RPC + policy | 核心 release anchor 与记录区块的链事实 | 现实陈述真实性、成员社会独立性 |
| PilotTranscriptV2 legacy review payload | 历史签名与字段一致性 | release/chain 信任；返回 legacy_consistency |
| PilotTranscriptV2 current offline | 完整 review 证据绑定和治理实验内部一致性 | 外部信任绑定；返回 offline_integrity |
| PilotTranscriptV2 current RPC, no policy | 记录区块的链状态一致 | 谁授权这套 release；返回 chain_consistency |
| PilotTranscriptV2 current RPC + policy | policy、Registry、交易、区块、code 与最终状态一致 | 公共网络部署或现实事实；返回 chain_verified |

RPC verifier 读取 transcript 记录的最终区块号，并核对区块 hash 和 timestamp；
链继续出块不会让旧 transcript 因“当前状态变化”失效。

## 合约权限与返回值

| 合约 | 主要写权限 | 主要公开结果 |
| --- | --- | --- |
| SkillRegistry | 每个 Publisher 管理自己的命名空间 | getRelease、current version、status |
| WitnessDAO | corporateAdmin 提案；任何 relayer 可提交有效签名 | proposal 状态、vote counts、refund credit |
| CorporateSink | corporate admin 排期/上传；DAO 记补偿 | timestamp、metadata/certificate/session/evidence hash |
| StreamingEngine | 仅配置后的 WitnessDAO 更新用户数 | 当前累计余额和 rate |
| PublicSink | 无写入口 | getTotalUTO |

精确 ABI、事件和构造器约束见[合约 API](../api/loveengine-contract-api.md)。
