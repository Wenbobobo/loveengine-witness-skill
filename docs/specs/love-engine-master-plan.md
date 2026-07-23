# LoveEngine engineering master plan

状态：current
工作目标：0.6.1-contract-public-pilot candidate
最新 Git tag：v0.6.0-contract-public-pilot
更新日期：2026-07-23

本文是唯一活动工程总规划。当前实施规格是
[Witness Core Optimization SPEC](love-engine-witness-core-optimization.md)。
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
- 0.6.1 candidate：修复信任边界、拆分核心/治理实验、建立可解释实验和文档。
  wire protocol 保持 loveengine-witness-net/0.6。

当前代码能验证 package/manifest 与 Registry release、一组受 policy 约束的节点、
签名 task/receipt、artifact 与事件链、争议 quorum 和跨阶段引用。可选治理实验
还能验证本机交易、code hash 和 PublicSink 状态。

当前代码不能证明远程网络已部署、生产身份可信、公众陈述为真、Agent 在社会关系
上独立、artifact 长期可用，或公司直播已接入。

## 默认主路径

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

## 不可破坏的边界

- 私钥、助记词、keystore 和写 token 不进入 Agent context、CLI 参数、日志、
  fixture、snapshot 或 transcript。
- invite 不是信任根；节点执行任务前必须用外部 NodeTrustPolicyV1 和 RPC
  Registry 事实绑定 release。
- Relay 只接受 bootstrap 成员，并把 receipt 绑定到鉴权连接及 pending task。
- finalize 必须重新读取 artifact；GET 接口不改变 evidence。
- Observation Agent 不签 vote；治理投票只能由 witness 通过外部 RPC signer
  显式批准。
- PublicSink 只读；UTO 不是可交易资产。
- contract-team v2 preserved copy 不原地修改；融合判断记录在开发文档。

## 后续路线

完成 0.6.1 本机核心实验后，下一阶段仍需逐项决策和验收：

- 公众反馈闭环：把表达、证据、异议、回复和处置结果连成可追溯记录。
- PoL 教育：把协议边界、证据素养和公共协作训练转成课程和实践材料。
- UHAH：只在真实需求和独立安全审计成立后评估。
- 单支付公司真实合作：先明确合规、数据来源、调度和 signer，再接一个受约束
  合作方；本轮不实现。
- 部署门：SSH/Tailscale、外部 signer、公共测试网、TLS、生产身份、监控、备份
  和 HA 分别验收，不能由本机 demo 推断。
- 治理协议：多场排期和链上强制 Gate 需要独立合约设计与审计，不在 0.6.1 修改
  ABI。

## Authority order

1. 本文。
2. [Witness Core Optimization SPEC](love-engine-witness-core-optimization.md)。
3. [核心架构与数据流](../architecture/witness-core-and-data-flow.zh-CN.md)。
4. ../api/ 下的当前接口文档。
5. ../development/contract2-comparison-and-recommendations.md。
6. ../reference/source-materials/current/。
7. ../archive/specs/implemented/ 和 ../archive/source-materials/。
