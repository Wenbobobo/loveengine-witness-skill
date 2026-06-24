# LoveEngine Witness Skill master plan

状态：`current`  
更新日期：2026-06-24

本文是唯一活动工程总规划。当前实施规格是
`love-engine-contract-public-pilot-spec.md`；已完成规格保存在
`docs/archive/specs/implemented/`。

## Direction

LoveEngine 首先是可被 Agent 网络验证、安装、传播和运行的 UAS 见证协议 Skill，不是中心化福利网站。

所有阶段遵守：

- 私钥不进入 Agent context、日志、fixture 或 transcript。
- 任务和签名绑定 chainId、verifying contract、主体、payload hash、nonce 和 deadline。
- 原始证据不上链，只保存 hash。
- Relay、直播平台、存储和展示层不是信任根。
- UTO 是公共记账单位，不是可交易资产。
- 治理值保持部署或治理可配置。

## Current baseline

- **M0**：完成，兼容版本 `0.1.1-m0`。manifest/source/package hash 可复算，篡改被拒绝。
- **M1**：完成。JSON Schema、canonical JSON、hash、CLI 和稳定错误码已实现。
- **M2**：完成，标签 `v0.2.0-local-loop`。四合约、五见证者 E2E 和 69 签名测试已实现。
- **M3**：完成，标签 `v0.3.0-network-pilot`。SkillRegistry、Relay、三节点任务与 transcript 已实现。
- **M3.1**：完成，标签 `v0.3.1-demo-ready`。Git、双语 README、演示入口和扩展接口已收口。
- **M4**：完成，标签 `v0.4.0-live-evidence-pilot`。通用文字流、证据、三节点争议复核、ProposalGate、只读面板和 E2E 已实现。
- **M5**：完成，标签 `v0.5.0-lan-pilot`。标准 Skill、确定性发布包、局域网真实文字直播、观察回执、统一链上 E2E、故障恢复与 soak 运行器已实现。
- **M6**：进行中，目标 `0.6.0-contract-public-pilot`。吸收 contract-team v2 的业务结构和中文 NatSpec，同时保留当前签名安全边界；补齐参与者上手、Plugin 分发、Tailscale/Debian 真实试点与公网/测试网预备配置。
- **M7**：未来。企业补偿申请、表决、记录和公开查询。

## M5 acceptance baseline

- Codex 可发现、跨平台可移植的薄 `SKILL.md`。
- 可重复构建并由 SkillRegistry package hash 绑定的确定性 ZIP。
- 带写入 token、操作页面、readiness、指标和审计日志的局域网 Pilot Server。
- 三个 Agent 通过 Relay 执行真实 `observe_live_text` 并签名观察回执。
- 持久化 Anvil、显式 CLI 投票批准和重启恢复。
- 从 Skill 安装、文字直播到 PublicSink 查询的 `PilotTranscriptV1`。
- 3 节点、10 观察者、4 小时正式试点运行器与验收报告。

## Active M6 scope

包括 contract2 preserved reference、合约融合、M6 target ABI、参与者运行手册、
双语 operator/dashboard、PilotInvite、Plugin 包装、Tailscale/Debian 运行配置、
公网 base URL 抽象、可选测试网 Gate 和统一 transcript 验收。

当前不包括真实直播平台 SDK、视频处理、多模态推理、生产身份、生产密钥托管、
P2P、HA、端到端加密、自动投票、主网部署和企业补偿深水区。

## Authority order

1. 本文。
2. `docs/specs/love-engine-contract-public-pilot-spec.md`。
3. `docs/api/`。
4. `docs/reference/source-materials/current/`。
5. `docs/archive/specs/implemented/`。
6. `docs/archive/source-materials/`。
