# LoveEngine Witness Core Optimization SPEC

状态：implemented candidate hardening
目标版本：0.6.1-contract-public-pilot
协议：loveengine-witness-net/0.6
更新日期：2026-07-23

## 目标

把 LoveEngine Witness 收缩为可解释、可运行、可篡改验证的一条证据闭环：

    deterministic package + Registry release
    -> trusted node policy + signed task/receipt
    -> event/artifact + observation
    -> critical dispute review
    -> ProposalGate
    -> verifiable core transcript

WitnessDAO、投票、CorporateSink、StreamingEngine 和 PublicSink 保留为可选治理
实验。公司直播 adapter、自动发现、SSH、公网和测试网部署不在本规格范围。本规格
的本机核心加固已完成；后续远程实验由活动的 Pre-enterprise Remote Lab SPEC
管理。

## 信任边界

- NodeTrustPolicyV1 必须通过可信旁路获得，固定 chain ID、Registry、Publisher、
  skill/version、ZIP hash、manifest hash 和 allowed issuers。
- invite 只包含连接位置和公开提示信息，不能充当信任根。
- live node connect 必须提供 trust policy；dry-run 可缺省，但必须返回
  trust_bound: false。
- NetworkTaskV2 只执行 observe_live_text 和 review_dispute。V1 历史 task
  继续可验证，不进入当前默认执行路径。
- receipt 必须属于当前鉴权 WebSocket 节点和它已接受的 pending task。
- evidence finalize 使用鉴权 POST，重新读取并复算 artifact；GET 纯读取。

## 两条实验路径

demo lan-pilot 默认 stage 为 core。core 阶段在 ProposalGate 停止，输出
WitnessCoreTranscriptV1，并标记 environment: local_anvil 和
actors_simulated: true。

stage governance 复用同一 runtime，在核心结果后执行显式 witness approval、
WitnessDAO 和 PublicSink，并输出 PilotTranscriptV2。治理实验必须披露：

- ProposalGate 是链下 advisory check，不是合约访问控制；
- CorporateSink 当前只支持一次干净部署中的单场实验；
- 本机预设 verdict 和 Anvil signer 不证明社会独立性或事实真实性。

## Transcript 语义

| 模式 | verification_level | chain_verified | trust_bound |
| --- | --- | --- | --- |
| V1 历史格式 | legacy_consistency | false | false |
| V2/Core offline | offline_integrity | false | false |
| V2/Core RPC，无 policy | chain_consistency | true | false |
| V2/Core RPC + policy | chain_verified | true | true |

链校验固定读取 transcript 的 final block number，并核对 block hash、timestamp、
Registry、交易、runtime code、DAO/PublicSink 状态。链继续出块不能改变旧记录的
验证结果。

## 文档与可解释性

- 根 QA.md 只回答能由代码、schema、测试或明确边界证明的问题。
- 架构文档集中维护角色、数据流、存储、hash 语义、合约权限和失败模式。
- README 只介绍核心流程；治理合约通过 optional lab 进入。
- M3-M6 阶段文档保留为历史证据，但退出活动索引。
- 原始会议资料移入 preserved source 区，不改正文。

## 验收

- 恶意自洽 invite、缺失/错误 policy、错误 issuer、非成员、错绑/重复 receipt
  和连接后新任务均有测试。
- core stage 在 Gate 结束；governance stage 仍能完整运行。
- offline、RPC 无 policy、RPC + policy 三种 transcript 等级可区分，历史区块
  验证不受新区块影响。
- README、QA、架构、runbook 中链接、路径、CLI 和版本状态由文档检查验证。
- repository checks、非集成/集成 pytest、Foundry 1.7.1、确定性双构建、soak、
  secret scan 和 git diff check 全部通过。

## 明确延期

- 公司直播平台认证、抓取、调度与长期 daemon；
- Tailscale、SSH、公网域名、TLS、公共测试网和生产 signer；
- CorporateSink 多场并发排期和链上强制 ProposalGate；
- 生产钱包托管、异地 artifact 复制、HA 和生产监控；
- 第二个通用 LoveEngine consensus Skill。
