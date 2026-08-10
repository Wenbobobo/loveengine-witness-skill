# LoveEngine engineering master plan

状态：current
工作目标：0.7.0-invited-public-pilot candidate implementation
最新 Git tag：v0.6.0-contract-public-pilot
更新日期：2026-08-10

本文是唯一活动工程总规划。当前实施规格是
[0.7 Invited Public Pilot SPEC](love-engine-invited-public-pilot.md)。0.6.1 的共享主机
基线和验收事实保留在
[Pre-enterprise Remote Lab SPEC](love-engine-pre-enterprise-remote-lab.md)。
已完成规格在 ../archive/specs/implemented/，原始资料在 ../reference/ 和
../archive/source-materials/，不得原地改写。

## 三层关系

1. **NaturalDAO / Proof of Love（PoL）**是总体社会协作愿景，关注公共善意、
   教育和协作怎样形成可持续制度。
2. **LoveEngine / UAS**是愿景中的公共见证、治理输入和会计协议层。UTO 是公共
   会计单位，不是可交易资产。
3. **LoveEngine Witness Skill**是本仓库实现的第一条可验证纵向切片。Agent
   见证、存证和整理公众反馈，不替用户执行、投票或裁决事实。

本仓库当前只有一个正式 Skill。第二个通用 LoveEngine consensus Skill 仍是愿景，
不属于已实现能力。

## 当前基线

- M0-M4：兼容 schema、codec、fixture 和 transcript reader 保留，默认文档不再
  以阶段历史组织使用流程。
- M5 / v0.5.0-lan-pilot：确定性包、Relay、证据、三 Agent 观察、snapshot 和
  package-to-chain 实验基线。
- M6 / v0.6.0-contract-public-pilot：最新发布 tag，是本机 Anvil/LAN 合约融合
  基线，不代表 Tailscale、公网或测试网部署。
- 0.6.1 candidate：信任边界、核心/治理拆分、可解释实验和 CI 已完成加固；精确
  candidate `9e5058e` 已通过完整本机门、四小时 core 运行和共享 ARM64 Linux
  loopback/tunnel 验收。增强路径包含真实 review evidence 复算、durable task
  journal/ACK-loss 重连、精确依赖 commit、失败报告和 postflight 清理。wire
  protocol 保持 loveengine-witness-net/0.6。
- 0.7 candidate implementation：PR #11 与 PR #12 已于 2026-08-10 经项目所有者
  授权的自审合入 `main`，当前源码已实现本地 V2、双入口、外部 signer adapter、
  Sepolia transaction plan、双 RPC 与
  Tailscale Serve preflight，但不能据此宣称 0.6.1/0.7 已发布。0.7 release 必须以
  已合并的 0.6.1 代码基线为祖先，并对最终 commit 重跑全部门；0.6.1/0.7 tag
  和 release asset 仍未发布。

当前代码能验证 package/manifest 与 Registry release、一组受 policy 约束的节点、
签名 task/receipt、artifact 与事件链、争议 quorum 和跨阶段引用。可选治理实验
还能验证本机交易、code hash 和 PublicSink 状态。

当前代码不能证明生产网络已部署、生产身份可信、公众陈述为真、Agent 在社会关系
上独立、artifact 长期可用，或公司直播已接入。已通过的远程短实验只增加相同
commit 在受约束共享 Linux 主机上可复跑的证据。

## 当前 0.6.1 默认主路径

    source inventory -> deterministic ZIP -> SkillRegistry release
    -> NodeTrustPolicyV1 + bootstrap
    -> signed Agent tasks/receipts
    -> content-addressed evidence
    -> critical dispute review
    -> ProposalGate
    -> WitnessCoreTranscriptV1

默认路径在 ProposalGate 结束。ProposalGate 只检查 finalized evidence 和未解决的
critical dispute；它不裁决事实、不签名、不提交交易，也不是链上权限控制。

可选 governance stage 才进入 explicit witness approvals、WitnessDAO 和 PublicSink，
并生成 PilotTranscriptV2。四个 UAS 业务合约不再代表 Witness Skill 本体。

0.7 不改变这条核心边界，只替换运行环境和可复核证据：SkillRegistry 锚定
Sepolia；Publisher、task issuer 和节点使用外部 signer；Pilot 分离 loopback 管理
入口与 Tailscale Serve tailnet-only 参与者入口；WitnessCoreTranscriptV2 记录历史
safe block、服务配置和参与者声明。治理合约仍留在本机可选实验。

## 不可破坏的边界

- 私钥、助记词、keystore 和写 token 不进入 Agent context、CLI 参数、日志、
  fixture、snapshot 或 transcript。
- invite 不是信任根；节点执行任务前必须用外部 NodeTrustPolicyV1 和 RPC
  Registry 事实绑定 release。
- Relay 只接受 bootstrap 成员，并把 receipt 绑定到鉴权连接及 pending task。
- 公开 task ingress 只接受完整可执行 payload；节点以 taskId 和 issuer+nonce
  持久去重，发送前保存 signed receipt，并用有界重连恢复 ACK-loss。
- finalize 必须重新读取 artifact；GET 接口不改变 evidence。
- Observation Agent 不签 vote；治理投票只能由 witness 通过外部 RPC signer
  显式批准。
- PublicSink 只读；UTO 不是可交易资产。
- contract-team v2 preserved copy 不原地修改；融合判断记录在开发文档。

## 当前实施路线

0.7 按依赖顺序实施，任何后段证据不能替代前段信任门：

- 0.6.1 依赖：PR #11/PR #12 merge 与 0.7 ancestry 已完成；精确 tag/release asset
  仍待发布。后者继续阻塞 0.7 tag 和“已发布”结论，但不阻塞受邀试点准备。
- Signer/Sepolia：统一 SignerClient，以 Clef strict rules 约束 Publisher、task
  issuer 和节点角色；只把 SkillRegistry 部署到 Sepolia，并由两个 RPC 在相同
  historical safe block 核对交易、code 和 release。
- 双入口/Tailscale：管理写面只在 loopback/SSH tunnel；参与者 listener 只暴露
  allowlist GET/SSE/WSS，经 Tailscale Serve 提供 tailnet HTTPS/WSS；不使用 Funnel，
  不覆盖既有 Serve 配置。
- Transcript/参与者：新增 WitnessCoreTranscriptV2 和 ParticipantAttestationV1，
  区分 offline integrity、chain consistency、policy-bound chain verification 与
  只能由协调者确认的操作者/网络分组声明。
- 工程验收：每个新 candidate 运行 900 秒、30 个事件、10 个只读观察者，并覆盖
  Pilot/Anvil restart、节点重连和 ACK-loss 恢复。它是回归/恢复门，不证明长期
  可用性；既有四小时 `9e5058e` 结果只属于该历史 candidate。
- 邀请试点：至少 3 个 node signer、2 名操作者和 2 个声明 network-group，在
  Sepolia release 和 tailnet-only participant surface 上完成观察、复核、Gate、
  cleanup 与 V2 transcript 验证。参与者声明不证明现实独立性。

详细输入、输出、失败关闭矩阵和逐阶段退出门见
[0.7 Invited Public Pilot SPEC](love-engine-invited-public-pilot.md)。

## 0.7 之后的路线

以下方向不进入本轮实现：

- 公众反馈闭环：把表达、证据、异议、回复和处置结果连成可追溯记录。
- PoL 教育：把协议边界、证据素养和公共协作训练转成课程和实践材料。
- UHAH：只在真实需求和独立安全审计成立后评估。
- 单支付公司真实合作：先明确合规、数据来源、调度和 signer，再接一个受约束
  合作方；本轮不实现。
- 生产部署门：邀请试点不能推断生产身份、开放公网、长期监控、备份、HA、SLA
  或企业合规；这些能力必须分别设计和验收。
- 治理协议：多场排期和链上强制 Gate 需要独立合约设计与审计，不在 0.7 修改
  ABI。

## Authority order

1. 本文。
2. [0.7 Invited Public Pilot SPEC](love-engine-invited-public-pilot.md)。
3. [Pre-enterprise Remote Lab SPEC](love-engine-pre-enterprise-remote-lab.md)，作为
   0.6.1 基线事实与共享主机安全约束。
4. [核心架构与数据流](../architecture/witness-core-and-data-flow.zh-CN.md)。
5. ../api/ 下的当前接口文档。
6. ../development/contract2-comparison-and-recommendations.md。
7. ../reference/source-materials/current/。
8. ../archive/specs/implemented/ 和 ../archive/source-materials/，其中包括已完成的
   Witness Core Optimization SPEC。
