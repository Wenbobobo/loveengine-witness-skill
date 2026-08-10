# LoveEngine 0.7 Invited Public Pilot SPEC

状态：candidate implementation spec；本地接口已实现，外部验收证据待完成
目标版本：0.7.0-invited-public-pilot
兼容协议：loveengine-witness-net/0.6
更新日期：2026-08-10

## 1. 决策与定位

0.7 把已经通过本机和共享 Linux 实验的 Witness 核心推进到一个有边界的邀请制
试点：SkillRegistry 锚定 Sepolia，Agent 密钥留在外部 signer，参与者通过
Tailscale Serve 访问 tailnet 内的只读数据和 WebSocket Relay，管理写入口仍只在
loopback/SSH tunnel 内可达。

名称中的 “public” 指 release、hash、签名和 Sepolia 历史事实可由获准参与者独立
复核，不表示匿名公网访问。网络可达性仍限于被邀请进入 tailnet 且通过 ACL 的主体。

本轮只部署核心 SkillRegistry，不把 WitnessDAO、CorporateSink、StreamingEngine
或 PublicSink 部署到 Sepolia。默认流程仍在 ProposalGate 结束；Agent 负责验证、
见证和整理证据，不替人裁决事实、签投票或提交治理交易。

~~~text
Publisher external signer -> Sepolia SkillRegistry release
                           -> NodeTrustPolicyV1 (trusted side channel)

Admin loopback/SSH tunnel -> authenticated writes -> Pilot core
Tailnet HTTPS/WSS         -> read-only + Relay      -> invited nodes

invited input -> artifacts/events -> observation -> dispute review
-> ProposalGate -> WitnessCoreTranscriptV2
~~~

### 1.1 版本与兼容

- project/Skill 版本进入 `0.7.0-invited-public-pilot`。
- NetworkTaskV2、TaskReceiptV2、NodeTrustPolicyV1 和现有 EIP-712 domain 不因营销
  版本变化而改变；没有 wire-breaking 变更时协议继续为
  `loveengine-witness-net/0.6`。
- PilotInviteV1、WitnessCoreTranscriptV1、PilotTranscriptV1/V2、M0-M6 schema、codec
  和 reader 保留。新能力通过 PilotInviteV2、WitnessCoreTranscriptV2 和独立 schema
  增量表达，不放宽旧 schema。
- 未知 schema/version 必须失败为 `unsupported_schema_version`，不能猜测兼容。

### 1.2 上游发布依赖

本分支叠加在 PR #11 的候选树上，可以先完成接口设计和本机实现，但 0.7 发布必须
依赖以下事实：

1. PR #11 经人工 review 后合入 `main`，不得自动合并；
2. 0.6.1 收尾提交、tag 与 release asset 已由精确 commit 的发布门验证；
3. 本分支以该已发布 commit 为祖先，或在 rebase 后证明最终 tree 吸收了全部
   0.6.1 收尾改动；
4. rebase、manifest/package hash 改变或上游修复都使旧的 0.7 运行证据失效，必须
   对新精确 commit 重跑完整门。

PR #11 尚未合并不阻塞本规格和独立模块实现；它阻塞 0.7 release、tag、公开
验收结论以及“从已发布 0.6.1 升级”的表述。

### 1.3 当前实现与证据状态

| 能力 | 当前实现 | 当前证据上限 | 仍缺失 |
| --- | --- | --- | --- |
| WitnessCoreTranscriptV2 | schema、writer/verifier、本机固定门 | local_anvil / offline integrity | 真实 Sepolia 邀请 transcript |
| 双入口 Pilot | PilotConfigV2、admin/participant listener、route allowlist | 本机 contract tests | 真实 Tailscale Serve 会话 |
| InviteV2 + trust policy | schema、解析、连接前交叉校验 | 本机负向/集成测试 | 三名受邀远程节点 |
| External signer | Anvil/Clef adapter、scope/recovery checks、inspect 四层 | mock/本机 contract tests | 真实 Clef 1.17.3 人工确认签名 |
| Sepolia 发布 | deploy/publish plan、sign/submit 复核、双 RPC verifier | 本地 plan/负向测试 | 真实 deployment/release receipt |
| Tailscale Serve | 冲突/登录/Funnel preflight、自有映射与精确恢复 | 本机模拟测试 | 目标 tailnet/ACL/Serve 报告 |
| 15 分钟门 | core、900 秒、30 events、10 observers 的固定 V2 profile | 本机 runner contract | exact candidate 的完整终态报告 |

最新发布 tag 仍是 `v0.6.0-contract-public-pilot`。上述“已实现”只指当前 stacked
branch 的代码与本机测试面，不得当作 0.7 release 或外部试点已经完成。

## 2. 信任模型

### 2.1 信任根与非信任输入

节点只把通过可信旁路取得的 NodeTrustPolicyV1 当作发布授权边界。policy 固定
chain ID、Registry、Publisher、skill/version、actual ZIP Keccak、manifest Keccak
和 allowed issuers。invite、Relay、dashboard、Tailscale DNS 名称、RPC 响应和任务
自报字段均不是单独的信任根。

Sepolia 提供可公开复核的测试网链事实，但不证明 Publisher 的现实身份、参与者
独立性、artifact 内容为真或系统达到生产可用性。Tailscale ACL 只限制网络可达性，
不代替 profile/task/receipt 签名和 release policy 校验。

### 2.2 身份与密钥

- raw private key、mnemonic、keystore password、Clef master seed、Pilot write token、
  RPC credential 和 Tailscale auth key 不得进入 Agent context、CLI 参数、环境转储、
  日志、fixture、snapshot、transcript 或报告。
- Pilot、Relay 和 Agent runtime 只拿到 signer endpoint、预期地址、角色和可公开
  hash，不读取 signer 的密钥目录。
- Publisher、task issuer 和每个 observation node 使用可区分的 signer role；核心
  Agent signer 没有 vote 权限。
- signer 返回结果后，调用方必须本地恢复签名者或解码 signed transaction，确认
  地址、chain ID、nonce、to/data 和交易计划完全一致，再允许持久化或广播。

### 2.3 现实参与者声明

`ParticipantAttestationV1` 由参与者自己的 signer 签名，最少绑定 run ID、node
address、role、profile hash、assignment task/payload hash、PilotInviteV2/trust-policy
hash、package/manifest/service-config hash、ruleset/rules-attestation SHA-256、opaque
operator-group hash、opaque network-group hash、issuedAt 和 validUntil。group hash
只需在同一 run 内稳定，不记录真实姓名、IP、SSID、运营商账号或地理位置。

它能证明某个 signer 对分组声明负责，不能密码学证明两个人没有串通、两个网络
物理独立或声明真实。报告必须使用 `coordinator_confirmed_independence`，不得使用
`independence_proven`。

## 3. 外部 Signer 与 Sepolia

### 3.1 统一 SignerClient

把当前散落的 RPC 签名调用收敛到统一 `SignerClient` 端口：

~~~python
class SignerClient(Protocol):
    address: str
    chain_id: str
    role: str
    def sign_message(self, message: str) -> str: ...
    def sign_typed_data(self, typed_data: dict) -> str: ...
    def sign_transaction(self, transaction: dict, method_signature: str | None = None) -> str: ...
~~~

适配器边界：

| Adapter | 允许环境 | 边界 |
| --- | --- | --- |
| AnvilRpcSigner | chain ID 31337 的本机/CI 实验 | 非 31337 立即拒绝；不得用于 0.7 Sepolia 证据 |
| ClefSigner | IPC 或显式 loopback HTTP | external API 视为不可信输入；必须有严格规则和结果复核 |
| ReadOnlyRpc | 任意受支持链 | 只读；不能因节点支持 unlocked account 而升级为 signer |

`ExternalSignerConfigV1` 只保存 schema、role、expected address、chain ID、endpoint
kind、endpoint reference、ruleset SHA-256、rules attestation hash 和允许的 EIP-712
domain/type。endpoint reference 不得内嵌 credential；敏感 RPC URL 从权限受限配置
文件读取且输出时脱敏。

初始 Clef 兼容目标固定为经过来源和摘要核验的 Geth/Clef 1.17.3。Geth 1.17.4
已经移除内置 Clef，1.17.5 也不能被当作 Clef 1.17.3 的自动升级；独立
`ethereum/clef` 仓库或其他版本必须先通过 adapter contract tests，不能仅凭
`clef --version` 相似字符串放行。

`loveengine signer inspect` 把运行前检查拆成四个累积层级：

1. static：schema、role/address/chain、transport、manual-confirm、typed-data/transaction
   allowlist；
2. evidence：ruleset 与 rules attestation 两个文件的精确 SHA-256；
3. binary：指定 Clef binary 的精确 SHA-256 与兼容版本 1.17.3；
4. live probe：前三层完整后，只读核对运行 endpoint 的 external API 版本。

ruleset/attestation、binary/SHA 参数必须成对出现。live probe 不请求签名，也不能独立
证明 endpoint 对应已核验 binary 或加载了已核验 rules；真实试点还要把人工确认的
签名结果、恢复地址与随后链事实绑定到同一报告。

本候选的 transcript 仍只把 backend/rules/audit hashes 作为未签名运行声明。验证器
必须返回 `signer_backend_evidence_verified:false` 并列入 `does_not_prove`；不得因为
任务或交易签名地址正确，就声称已密码学证明 Clef 加载了指定规则。后续若要升级，需
新增由 signer 绑定 run、role、rules 与 audit digest 的独立证据格式，而不是放宽措辞。

### 3.2 Clef 严格规则

首轮 0.7 采用人工确认模式：LoveEngine adapter 在请求进入 Clef 前严格校验完整
request，Clef 只通过 IPC 或显式 loopback HTTP 接收，并由操作者逐次确认。不得把
尚未经过真实 Clef contract test 的动态 typed-data 规则标为自动批准。规则文件和
人工 attestation 仍需 hash；任何自动批准规则都属于后续独立安全变更。

adapter 默认拒绝，只允许角色对应的最小集合：

| Role | 可请求操作 | 必须绑定 |
| --- | --- | --- |
| Publisher | publishRelease transaction；一次已批准的 Registry deployment plan | Sepolia、预期 sender、bytecode/calldata hash、nonce、gas/fee cap、expiry |
| Task issuer | NetworkTaskV2 typed data | chain、Registry、recipient、task type、nonce、deadline、manifest hash |
| Observation node | profile、TaskReceiptV2、ParticipantAttestationV1 typed data | 自身 address、run/release、task/result hash、nonce、deadline |

以下请求在到达 Clef 前全部拒绝：任意文本盲签、`personal_sign` 风格不可解释 payload、未知 domain
或 type、无限 deadline、错误 chain/Registry、任意收款地址、value 非零、治理 vote、
合约任意调用和不在 plan 内的 deployment。

adapter 只映射 Clef 明确支持的 `account_signData`、`account_signTypedData` 和
`account_signTransaction`。签名前输出不含 secret 的 canonical request digest；
签后保存 request/result digest、Clef decision 和 rules attestation hash，不保存
密码或 raw key material。

### 3.3 Sepolia 交易计划

新增显式 plan/sign/submit 发布路径，禁止 CLI 暗中使用 unlocked RPC account：

1. `registry transaction deploy-plan` 或 `publish-plan` 生成 unsigned plan；
2. `registry transaction sign` 请求 Clef 对精确 allowlisted request 人工确认签名；
3. `registry transaction submit` 解码并复核 raw transaction/nonce 后广播；
4. `registry verify` 与 transcript verifier 在历史安全区块读取 release 与 code。

TransactionPlanV1 必须绑定：schema、plan ID、chain ID 11155111、sender、nonce、to
或 contract-creation marker、value=0、calldata/creation bytecode hash、expected
runtime code hash、gas limit cap、max fee cap、priority fee cap、issuedAt、expiresAt
和 release/package/manifest 引用。任何字段变化都产生新 plan 并需要重新签名。

只向主 RPC 广播同一 raw transaction；随后通过两个独立配置的 Sepolia RPC 在同一
`safe` 历史区块核对 block hash/timestamp、transaction receipt、Registry runtime
code hash、Publisher、skill/version、active status、ZIP Keccak 和 manifest Keccak。
两个 RPC 对区块、code 或 release 结果不一致时停止，不退回“取多数”或 latest head。

试点只部署 SkillRegistry。已有符合预期 code hash 的 Registry 可以复用，但必须由
policy 明确固定地址和 deployment transaction；不能仅因某地址 ABI 调用成功就信任。

## 4. 双入口 Pilot 与邀请

### 4.1 监听边界

同一持久状态上建立两个独立 aiohttp application/listener；不能用一套 app 加前端
菜单隐藏来模拟权限分离。

| Listener | 绑定 | 允许面 | 禁止面 |
| --- | --- | --- | --- |
| admin | `127.0.0.1`/`::1`，仅 SSH tunnel | operator UI、metrics、所有 Bearer 写入、snapshot 运维 | Tailscale Serve、tailnet 直接访问 |
| participant | 独立 loopback 端口，由 Tailscale Serve 反代 | allowlist GET、SSE、artifact/package、签名 WebSocket Relay | 所有 POST、operator UI、token 解析、snapshot/restore、task ingress、内部 metrics |

participant app 的 deny 必须发生在路由层和中间件层：即使请求携带正确 write token，
写方法和非 allowlist 路径仍返回固定拒绝；不能把请求转发给 admin app。两 app 共用
store 时仍通过现有事务、cursor 和 at-least-once 约束访问。

participant allowlist 初始仅包含：

~~~text
GET /healthz
GET /readyz
GET /demo/
GET /v1/bootstrap
GET /v1/releases/{publisher}/{skillId}/{version}
GET /v1/artifacts/{packageHash}
GET /v1/live/sessions/{sessionId}
GET /v1/live/sessions/{sessionId}/events
GET /v1/live/sessions/{sessionId}/stream
GET /v1/live/sessions/{sessionId}/evidence
GET /v1/live/artifacts/{sha256Digest}
GET /v1/dashboard/sessions
GET /v1/dashboard/sessions/{sessionId}
GET /v1/dashboard/disputes/{disputeId}
GET /v1/ws
~~~

`POST /v1/relay/tasks`、session/event/close/finalize、`/operator/`、`/v1/metrics` 和
全部 snapshot/restore 路径永远不在 participant listener。CLI WebSocket 可以没有
浏览器 Origin，但仍必须完成 challenge、bootstrap profile、release 和 policy 校验；
浏览器请求只允许精确 participant HTTPS origin，CORS 不使用 `*`。

### 4.2 PilotInviteV2

PilotInviteV2 只承担连接发现，字段为 schema、participant base URL、dashboard URL、
relay WSS URL、chain ID、Registry、Publisher、skill/version、package hash、issuedAt
和 expiresAt。所有 URL 必须是同一批准的 `https://<host>.<tailnet>.ts.net` origin，
relay 使用 `wss://`。

V2 不包含 operator/admin URL、SSH host、write token、RPC credential、signer endpoint
或 trust policy。NodeTrustPolicyV1 继续通过另一可信旁路分发。V1 reader 保留，
但 V1 invite 不能启动 0.7 invited-public 模式。

### 4.3 Tailscale Serve

0.7 只使用 tailnet 内的 Tailscale Serve，不启用 Funnel。部署工具必须：

1. 在任何修改前保存 `tailscale serve status --json` 的 canonical hash 和受影响
   handler 快照；
2. 验证 Tailscale 已登录目标 tailnet、Serve 可用、目标 hostname 与 participant
   origin 一致，且 ACL/用户邀请由操作者预先完成；
3. 发现现有 handler、端口或 hostname 冲突即停止，不覆盖或重置整机 Serve 配置；
4. 只把 participant loopback 端口映射到 HTTPS，不映射 admin/RPC/Clef/Anvil；
5. bounded run 结束后恢复本次拥有的精确前态，并再次读取状态核对；
6. 恢复结果未知或失败时把整个试点标记失败，并留下不含 auth key 的人工恢复说明。

自动流程不执行 `tailscale up`，不生成或持久化 reusable auth key，不改 ACL，不开
Funnel，也不以 `0.0.0.0` 暴露 Pilot。Serve 配置 hash 进入 transcript；原始状态中
可能含敏感 tailnet 信息的字段只保存在受限运维报告。

## 5. WitnessCoreTranscriptV2

WitnessCoreTranscriptV1 冻结，不把 `local_anvil`、`actors_simulated:true` 等历史
常量放宽成多义字段。新增 WitnessCoreTranscriptV2，最少包含：

- exact source commit、Skill version、protocol 和 package/manifest hashes；
- Sepolia chain ID、Registry/Publisher、release status、deployment transaction、
  runtime code hash；
- final verification block number/hash/timestamp 和两 RPC 的 canonical observation
  hashes；
- PilotInviteV2 hash、NodeTrustPolicyV1 hash、bootstrap/profile/member hashes；
- external signer configs、Clef ruleset/attestation/audit hashes，不含 endpoint secret；
- participant service allowlist hash、Tailscale Serve owned-config hash 与 restore result；
- signed tasks、ACK、receipts、receipt confirmations、artifacts、event chain、
  ObservationSet、EvidenceBundle、disputes、reviews、Gate 和 fault records；
- ParticipantAttestationV1 列表及 operator/network group counts；
- `input_mode: synthetic_fixture | invited_operator_input`、environment、
  actors_simulated 和明确的 `does_not_prove`；
- 900 秒门的资源、重启、重连和清理摘要。

所有链上读取固定到 transcript 的 final safe block。验证器先核对该 block 的 hash 和
timestamp，再以显式 block number 读取 Registry/code；链继续出块、release 后续
deprecated 或 RPC latest head 改变不能使历史 transcript 漂移。

邀请试点的 environment 固定为 `sepolia_invited_pilot`。只有事件不是 harness
生成、节点不由同一进程内 fixture 代演且参与者 attestation 完整时，才允许
`actors_simulated:false`；否则必须为 true 并列出具体模拟环节。

### 5.1 验证等级

| 输入 | verification_level | chain_verified | trust_bound | 能证明的上限 |
| --- | --- | --- | --- | --- |
| 历史 V1 | legacy_consistency | false | false | 旧字段与签名内部一致 |
| V2 offline | offline_integrity | false | false | hash、签名、成员、quorum、引用和声明内部一致 |
| V2 + 两 RPC，无 policy | chain_consistency | false | false | 记录区块的 Sepolia code/release/receipt 一致 |
| V2 + 两 RPC + trusted policy | chain_verified | true | true | policy 授权的 release 与记录区块链事实一致 |

V2 另返回 `chain_consistency_checked`：offline 为 false，两个 RPC 在同一历史块完成
一致性读取后为 true。这样“读到一致链事实”和“该 release 受可信 policy 授权”不会
共用一个布尔值。

`chain_verified` 不等于内容真实、参与者独立、服务长期可用、Tailscale 配置可信、
企业身份认证或生产安全。Serve/Clef/participant attestation 属于有签名或有 hash 的
运行证据，不会因为 Registry 验证通过而变成链上事实。

交易 plan 的 `expires_at` 只约束 LoveEngine `sign/submit`；普通 EIP-1559 raw
transaction 没有该 deadline。解码复核要求空 access list，但 raw bytes 一旦外流，
其他广播工具仍可在 plan 过期后提交。链上强制过期属于智能账户/合约层的后续设计。

## 6. 实施阶段与退出门

每阶段先完成单元/负向测试，再进入下一阶段；所有候选均在干净独立 worktree 执行。

### P0：0.6.1 基线吸收与规格冻结

输出：已人工合并的 0.6.1 release base、0.7 version plan、schema/CLI 变更清单、
threat model 和迁移表。

退出门：PR #11 人工合并事实可验证；0.6.1 tag 指向经过验证的 commit；0.7 分支
包含该基线；M0-M6 fixture 继续通过。设计和测试桩可以在此前并行，release 不能。

### P1：SignerClient 与 Clef

输出：统一 signer port、Anvil/Clef adapters、ExternalSignerConfigV1、人工确认的
ruleset/attestation/audit verifier 和 operator runbook。自动批准规则不在首轮范围。

退出门：错误地址、错误 chain、未知 domain/type、任意文本、治理 vote、过期请求、
非零 value、fee/gas 超限、tampered rule/audit、恶意 Clef response 全部拒绝；私钥和
credential secret scan 为零；现有本机任务/回执行为不变。

### P2：Sepolia Registry

输出：deploy/publish transaction plans、external sign/submit、双 RPC historical
verifier、code/release evidence 和只部署 SkillRegistry 的恢复/撤销说明。

退出门：测试网账户只持有有界测试 ETH；交易 plan 可离线审阅；signed raw tx
与 plan 字节级一致；两个 RPC 在同一 safe block 同意 receipt、code 和 release；
错误 nonce、reorg、RPC 分歧、inactive release 或 hash 不符均失败。

### P3：双入口与 InviteV2

输出：admin/participant apps、显式 route allowlist、PilotInviteV2、迁移兼容层和
端到端节点连接测试。

退出门：participant 对每个写/admin/metrics/snapshot 路径拒绝，即使携带正确
token；admin 仍要求 token+Origin；package/evidence/SSE/WSS 正常；invite 中无
operator URL 或 secret；V1 历史测试继续通过。

### P4：Tailscale Serve 编排

输出：只读 preflight、owned Serve apply/restore、冲突检测、bounded guardian 和
机器报告。

退出门：无 Funnel、无全接口监听、只代理 participant port；现有 Serve 配置冲突
必停；正常/失败/中断路径均核对恢复；未知进程或配置所有权时不发送破坏性命令。

### P5：Transcript V2 与参与者声明

输出：V2 schema/builder/verifier、ParticipantAttestationV1、历史 safe-block verifier、
tamper fixtures 和证据等级文档。

退出门：逐字段和跨阶段篡改、RPC head 漂移、wrong block hash、wrong policy、重复
成员、伪造 group、Serve/rules audit hash 变化均被发现；四种验证等级与布尔字段
严格一致；V1 reader 未改变。

### P6：本机和 CI 工程门

输出：完整 repository/release report，以及一次固定 900 秒 core acceptance report。

固定参数为 900 秒、30 个事件、10 个只读观察者。必须覆盖一次 Pilot restart、
一次 Anvil restart、至少一个节点断线重连、post-connection task、ACK-loss receipt
恢复、snapshot restore、zero secret finding、空 stderr diagnostics 和 transcript
离线/RPC/policy 验证。它是工程回归与恢复证据，不是四小时稳定性、SLA 或生产负载
证据。

退出门：精确 commit/manifest package hash/run ID 一致；子进程退出；所有 required
checks true；3 observation receipts、3 review receipts、3 ACK 和 3 receipt
confirmations；Gate ready；资源报告和 owned-process cleanup 完整。

### P7：远端邀请试点

输出：Sepolia release、tailnet-only participant service、三节点真实连接、机器报告、
WitnessCoreTranscriptV2 和受限运维日志。

参与门：至少 3 个 node signer、至少 2 名操作者、至少 2 个 network-group；没有
单一操作者控制全部节点，且每个 network-group 至少有一个节点。协调者只记录 opaque
group attestation。事件使用无敏感信息的测试输入，标记
`input_mode: invited_operator_input`；预设 verdict 和训练脚本必须披露。

远端共享主机继续执行 key-only SSH、pinned known_hosts、unique deployment、最多
两核、`nice +15`、内存/磁盘/load/process gate、loopback-only app、无 sudo/systemd/
Docker。Tailscale 已登录和 ACL 邀请由操作者预备；自动化不接管账号。

退出门：P6 在同一 exact candidate 通过；Sepolia 双 RPC+policy 返回
`chain_verified/trust_bound:true`；三个节点在连接后收到任务并完成 evidence
复算；参与者/Serve/signer hashes 完整；participant 写面探测为零；一次 backend
restart 和一次跨网络 node reconnect 成功；Serve 前态恢复和 postflight cleanup
可证。任一参与者掉线导致 quorum 不足时记录失败，不用本机模拟节点补位。

### P8：0.7 发布

输出：人工 review 的 PR、annotated tag、确定性 ZIP/checksums/SBOM、sanitized
acceptance report、迁移说明和 GitHub Release notes。

退出门：tag commit 与全部机器证据精确一致；确定性双构建相同；CI、Foundry、
non-integration/integration、900 秒门、远端邀请试点、secret scan 和
`git diff --check` 均通过；至少一名非作者开发者完成 review。禁止自动 merge。

## 7. 失败关闭矩阵

| 条件 | 必须动作 | 禁止降级 |
| --- | --- | --- |
| PR #11/0.6.1 未人工合并 | 可继续隔离开发；阻止 release/tag/public acceptance | 不把 stacked branch 称为已发布基线 |
| Clef config/rules/attestation 不匹配 | 拒绝签名和广播 | 不改用 unlocked RPC、raw key 或 blind sign |
| Sepolia RPC 分歧或 safe block 不可得 | 停止 chain verification | 不切 latest、不单 RPC 宣称 chain_verified |
| Registry/code/package/manifest 不匹配 | 节点不连接、任务不执行 | 不接受 invite/Relay 自报 hash |
| participant listener 暴露写入口 | 立即判定候选失败并停止 Serve | 不依赖 token 或 UI 隐藏补救 |
| Serve 有冲突或无法证明恢复 | 不修改或停止试点，保留人工恢复报告 | 不执行全局 reset、Funnel 或覆盖既有配置 |
| resource/preflight 不安全 | 不启动部署/构建/soak | 不降低阈值、不终止其他任务 |
| signer/RPC/Tailscale credential 出现在输出 | 终止、隔离 artifact、轮换 credential | 不把脱敏留到发布后 |
| receipt/task/artifact/event 绑定错误 | Gate blocked，transcript invalid | 不人工改报告或补写 receipt |
| 少于 3 节点/2 操作者/2 network-group | invited pilot 未通过 | 不用同机模拟 actor 填补现实参与门 |
| owned process/Serve cleanup 未知 | 整体失败并停止后续发布 | 不按裸 PID、模糊命令或全局配置清理 |

所有失败必须留下结构化 stage、error code、精确 commit/run ID 和不含 secret 的诊断。
失败报告是证据，不能通过删除输出目录把失败改写成“未运行”。

## 8. 明确非目标

- 公司直播、企业认证、业务调度、企业数据 adapter 和支付合作；
- 公网匿名访问、Tailscale Funnel、公共域名产品、mainnet 或生产钱包；
- 自动发现、P2P 网络、无限重连 daemon、开放注册或抗女巫共识；
- WitnessDAO 等四个治理实验合约的 Sepolia 部署或 ABI 改造；
- Agent 自动投票、事实裁决、自动执行 ProposalGate 计划；
- HA、多副本数据库、长期 artifact 保证、异地灾备、SLA 和生产监控；
- 对参与者现实身份、组织独立性、网络物理独立性或内容真实性的密码学证明；
- Clef 的生产灾备、企业 HSM、账户恢复和正式密钥轮换制度。

完成本规格只允许表述为：三个受邀节点在至少两个操作者和两个声明网络组中，通过
tailnet-only 服务验证了一个 Sepolia 锚定 release 的 Witness 核心闭环，并生成可
离线和链上复核的 transcript。它不表示公开互联网、企业接入或生产系统完成。

## 9. 验收清单

- [ ] PR #11 已人工 review/merge，0.6.1 精确 release base 可验证。
- [ ] SignerClient/Clef 默认拒绝规则及 audit/attestation 通过负向测试。
- [ ] SkillRegistry 在 Sepolia 的 deployment/release 由外部 signer 提交并由双 RPC
      在历史 safe block 验证。
- [ ] admin 与 participant 两个 listener 的路由和 credential 边界通过探测。
- [ ] PilotInviteV2 与 NodeTrustPolicyV1 分离分发，V1 reader 保持兼容。
- [ ] Tailscale Serve tailnet-only、无 Funnel、无配置冲突并恢复精确前态。
- [ ] WitnessCoreTranscriptV2 四种验证等级与 tamper matrix 通过。
- [ ] 900 秒、30 events、10 observers 的 exact-candidate 工程门通过。
- [ ] 3 nodes、2 operators、2 network-groups 的远端邀请试点通过。
- [ ] 3 observation/review receipts、ACK/confirmation、Gate、restart/reconnect 和
      cleanup 证据完整。
- [ ] deterministic build、CI、Foundry、全部测试、secret scan 和 diff check 通过。
- [ ] 文档没有把邀请试点升级为现实事实、社会独立性或生产可用性证明。
