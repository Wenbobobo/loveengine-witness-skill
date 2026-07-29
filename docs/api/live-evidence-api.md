# Live evidence API

状态：本机实现
当前 wire protocol：loveengine-witness-net/0.6（历史 M4 domain version 2 保留）

## HTTP

| Method | Path | 语义 |
| --- | --- | --- |
| POST | /v1/live/sessions | 创建 LiveSessionV1 |
| POST | /v1/live/sessions/{sessionId}/events | 摄取 JSON、JSON array 或 NDJSON |
| POST | /v1/live/sessions/{sessionId}/close | 关闭 session；不 finalize |
| POST | /v1/live/sessions/{sessionId}/evidence/finalize | 重新读取 artifact 并创建 finalized revision |
| GET | /v1/live/sessions/{sessionId} | 读取 session |
| GET | /v1/live/sessions/{sessionId}/events?after=N | 按 sequence cursor 读取 |
| GET | /v1/live/sessions/{sessionId}/stream?after=N | SSE 只读事件流 |
| GET | /v1/live/sessions/{sessionId}/evidence | 只读已有 evidence；不存在则 404 |
| GET | /v1/live/artifacts/{sha256Digest} | 读取原始 artifact bytes |

Pilot Server 上全部 POST 要求 restricted-file Bearer token；带 Origin 的请求还要
匹配 allowed_origin。独立 loveengine live serve 只允许本机开发实验，不应暴露为
生产服务。

错误统一为：

~~~json
{"error":{"code":"sequence_gap","message":"3"}}
~~~

稳定错误包括 event_conflict、event_hash_mismatch、sequence_gap、
hash_chain_broken、session_closed、artifact_missing、evidence_not_finalized 和
bundle_revision_conflict。

## Event 与 artifact

- sequence 从 1 连续递增；previous_event_hash 必须等于 session head。
- SSE 是从持久 event log 按 cursor 重放的有限响应。读取到 `closed` 后，服务端会
  再读取一次该 cursor 之后的持久事件；关闭前已提交的最后事件不会被 keepalive 隐藏。
  节点仍必须逐项验证连续 sequence、previous_event_hash、event_hash 和 artifact。
- event_hash 是排除自身后的 canonical JSON Keccak-256。
- content_hash 与 artifact 地址使用 exact source bytes 的 SHA-256。
- artifact 路径是 artifacts/sha256/[前两位]/[完整 digest]。
- 完全相同 event_id 重试幂等成功；同 ID 不同内容拒绝。
- source、summary、derived 不可混写；视频只保存 URL/hash/time reference。
- finalize 逐一重新读取 artifact bytes，复算 SHA-256 并核对事件内容；调用方
  提供的 artifact hash 不能覆盖真实内容产生的 hash。

## Observation 与争议复核

NetworkTaskV2 当前只允许 observe_live_text 和 review_dispute。旧 V1 的
propagate_skill、observe_broadcast 保留历史兼容，但不属于 V2 执行集合。
TaskReceiptV2 对 task、node、status、result hash、nonce 和 completedAt 签名。

可执行 `review_dispute` 使用 ReviewDisputePayloadV1，绑定 finalized bundle 的
URL、events URL、artifact base URL、session、revision、event count 和 head hash。
节点只访问与 invite server 完全同源的 HTTP(S) URL，禁用 redirect 并限制响应
大小；随后复算 bundle hash、完整 event chain、artifact bytes、category counts
和 bundle event references。verdict map 只提供操作者判断，不能绕过这些证据检查。
历史 transcript 的 `{dispute_id,bundle_hash}` 最小 payload 只保留一致性校验，
Pilot task ingress 不执行它。

critical dispute 由 bootstrap 内三个不同节点复核：

- 至少两票 dismiss：dismissed；
- 至少两票 uphold：upheld；
- 缺失、重复、无多数或无效签名：unresolved。

只有 dismissed critical disputes 能通过 ProposalGate。Gate 只返回 plan 或阻断
原因，不裁决事实、不签投票、不提交交易，也不是 WitnessDAO 访问控制。

## Read-only dashboard

    GET /demo/
    GET /v1/dashboard/sessions
    GET /v1/dashboard/sessions/{sessionId}
    GET /v1/dashboard/disputes/{disputeId}

dashboard 展示 session、事件和 artifact integrity。它不执行 finalize、Gate、
投票或 PublicSink 查询；截图不能代替 transcript/RPC 验证。

## Local CLI

```powershell
uv run loveengine live serve --db .\var\live.sqlite --artifacts .\var\artifacts
uv run loveengine live session create --db .\var\live.sqlite --session-id demo --created-at 1770000000
uv run loveengine live ingest --db .\var\live.sqlite --artifacts .\var\artifacts --session-id demo --input .\examples\live\live-session.fixture.ndjson
uv run loveengine live close --db .\var\live.sqlite --session-id demo --closed-at 1770000020
uv run loveengine evidence finalize --db .\var\live.sqlite --artifacts .\var\artifacts --session-id demo --finalized-at 1770000021
```

这些直接数据库 CLI 是本机开发 adapter。服务运行时应使用鉴权 HTTP finalize，以
便统一审计和权限边界。
