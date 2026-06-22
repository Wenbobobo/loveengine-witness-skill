# LoveEngine Witness Skill master plan

状态：`current`  
更新日期：2026-06-22

本文是唯一活动工程总规划。当前实施规格是 `love-engine-live-evidence-pilot-spec.md`；已完成规格保存在 `docs/archive/specs/implemented/`。

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
- **M3.1**：实施中，目标 `v0.3.1-demo-ready`。清理 Git、文档、演示入口和扩展接口。
- **M4**：实施中，目标 `v0.4.0-live-evidence-pilot`。通用文字流、证据、争议复核、ProposalGate 和只读面板。
- **M5**：未来。企业补偿申请、表决、记录和公开查询。

## M4 acceptance

- provider-neutral LiveSource 和 HTTP/NDJSON gateway。
- append-only hash-chained LiveEvent。
- 内容寻址 ArtifactStore 和 SQLite metadata。
- EvidenceBundleV2。
- 三个签名节点按简单多数复核争议。
- upheld/unresolved 阻断；只有 dismissed 放行。
- aiohttp 只读面板。
- LiveReviewTranscript 与完整 E2E。

## Active scope

包括 Skill 包、schemas、Python CLI、合约、Relay、直播文字证据、争议复核、只读面板和本地 Anvil demo。

当前不包括公共测试网、具体直播平台认证、视频处理、多模态推理、生产身份、密钥托管、P2P、HA、端到端加密、自动投票和链上争议门禁。

## Authority order

1. 本文。
2. `docs/specs/love-engine-live-evidence-pilot-spec.md`。
3. `docs/api/`。
4. `docs/reference/source-materials/current/`。
5. `docs/archive/specs/implemented/`。
6. `docs/archive/source-materials/`。
