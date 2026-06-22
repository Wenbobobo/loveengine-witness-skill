# LoveEngine M4 Live Evidence Pilot SPEC

状态：`active implementation`  
目标版本：`0.4.0-live-evidence-pilot`  
依赖版本：`0.3.1-demo-ready`

## 1. 核心目标

M4 在 M3 三节点网络上增加通用文字直播、内容寻址证据、三节点争议复核、服务层 ProposalGate 和只读演示面板。

```text
LiveSource
-> LiveGateway
-> append-only LiveEvent hash chain
-> ArtifactStore + MetadataStore
-> EvidenceBundleV2
-> DisputeCase
-> three signed review_dispute tasks
-> majority aggregation
-> ProposalGate
-> read-only dashboard and LiveReviewTranscript
```

本阶段不绑定具体直播平台，不下载或分析视频，不修改四个核心业务合约，不实现自动投票。

## 2. M4.1 协议与存储

- [ ] 固定 LiveSessionV1、LiveEventV1、EvidenceBundleV2。
- [ ] 固定 DisputeCaseV1、DisputeReviewV1。
- [ ] 新增 V2 node/bootstrap/task/receipt 和 LiveReviewTranscriptV1。
- [ ] M3 V1 schema 保持不可变；M4 EIP-712 domain version 使用 `2`。
- [ ] LiveEvent 使用连续 sequence、previousEventHash 和 canonical Keccak hash。
- [ ] artifact 保存为 `artifacts/sha256/<prefix>/<digest>`。
- [ ] SQLite 保存 session、event、bundle、dispute、review 和 cursor。

验收：精确重复事件幂等；同 ID 不同内容、sequence 缺口、hash-chain 断裂和关闭后写入被拒绝。

## 3. M4.2 通用文字流网关

```text
POST /v1/live/sessions
POST /v1/live/sessions/{sessionId}/events
POST /v1/live/sessions/{sessionId}/close
GET  /v1/live/sessions/{sessionId}
GET  /v1/live/sessions/{sessionId}/events
GET  /v1/live/sessions/{sessionId}/stream
GET  /v1/live/sessions/{sessionId}/evidence
```

- [ ] 支持单条 JSON 和 NDJSON 输入。
- [ ] SSE 只用于实时观察，并支持 cursor 恢复。
- [ ] 默认监听 `127.0.0.1`。
- [ ] 实现 FixtureLiveSource 和 HttpPushLiveSource。
- [ ] 视频只记录 URL、hash 和 timestamp 引用。

验收：断线恢复不丢事件；重复输入不重复生成证据；错误使用稳定 JSON error code。

## 4. M4.3 EvidenceBundleV2

- [ ] 聚合原始文字事件、附件引用、来源类型和内容 hash。
- [ ] 严格区分 source、summary 和 derived。
- [ ] finalize 前验证事件连续性和 artifact 完整性。
- [ ] finalized bundle 不可修改，只能创建新 revision。
- [ ] 原始 artifact 不上链。

CLI：

```text
loveengine live serve
loveengine live session create
loveengine live ingest
loveengine live close
loveengine evidence finalize
```

验收：相同输入生成稳定 bundle hash；任意事件、附件、顺序或来源修改都会失败。

## 5. M4.4 三节点争议复核

新增 task type：

- `observe_live_text`
- `review_dispute`

每个 critical dispute 必须收到三个不同签名节点的有效复核。verdict 为 `uphold`、`dismiss` 或 `unable_to_determine`：

- 两票 uphold：`upheld`。
- 两票 dismiss：`dismissed`。
- 缺失、超时、无多数或无效签名：`unresolved`。
- upheld 和 unresolved 阻断；只有 dismissed 放行。

CLI：

```text
loveengine dispute open
loveengine review dispatch
loveengine review aggregate
loveengine proposal gate
```

验收：错误节点、重复复核、错误 bundle、过期任务和 Relay 篡改均被拒绝。

## 6. M4.5 ProposalGate

- [ ] session 必须关闭。
- [ ] EvidenceBundleV2 必须 finalized。
- [ ] 所有 critical dispute 必须 dismissed。
- [ ] 输出只读 proposal execution plan。
- [ ] 不提交交易，不请求投票签名。

验收：通过路径生成稳定 payload；upheld、unresolved、缺失复核和 bundle hash mismatch 均阻断。

## 7. M4.6 只读演示面板

```text
GET /demo/
GET /v1/dashboard/sessions
GET /v1/dashboard/sessions/{sessionId}
GET /v1/dashboard/disputes/{disputeId}
```

面板由 aiohttp 提供静态 HTML/CSS/JS，不使用 Node 构建链，不提供写操作或生产鉴权。

展示 session 时间线、source 类型、EvidenceBundle、三节点复核、ProposalGate 和网络/流延迟指标。缺失 artifact 或校验失败必须明确显示。

## 8. M4.7 固定端到端场景

1. Anvil 部署四核心合约和 SkillRegistry。
2. 启动 Relay Hub、LiveGateway 和只读面板。
3. 启动三个 V2 Agent 进程。
4. 摄取 12 条有序 NDJSON 文字事件。
5. 验证重复事件幂等和冲突事件拒绝。
6. 关闭 session 并生成 EvidenceBundleV2。
7. 创建一个 critical dispute。
8. 三节点返回两票 dismiss、一票 uphold。
9. 聚合为 dismissed，ProposalGate 放行。
10. 另一个缺少第三份复核的争议为 unresolved 并阻断。
11. 生成并验证 LiveReviewTranscript。

## 9. 完成定义

```powershell
uv run python .\tools\check.py
uv run pytest -m "not integration" -q
cd contracts
forge test
cd ..
uv run pytest .\tests\integration\test_live_evidence_demo.py
uv run loveengine demo live-evidence --nodes 3 --input .\examples\live\live-session.fixture.ndjson --output <temp>
uv run loveengine live transcript verify <temp>\live-review.fixture.json
```

全部日志、artifact、fixture 和 transcript 必须通过秘密扫描。
