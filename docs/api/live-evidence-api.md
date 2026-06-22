# Live evidence API

状态：M4 local implemented  
协议版本：`loveengine-witness-net/0.4`

## HTTP gateway

默认监听 `127.0.0.1:8780`。M4 仅用于本地试点，不提供生产鉴权。

| Method | Path | Result |
| --- | --- | --- |
| POST | `/v1/live/sessions` | 创建 `LiveSessionV1` |
| POST | `/v1/live/sessions/{sessionId}/events` | 摄取单条 JSON、JSON 数组或 NDJSON |
| POST | `/v1/live/sessions/{sessionId}/close` | 关闭 session，之后拒绝写入 |
| GET | `/v1/live/sessions/{sessionId}` | 查询 session |
| GET | `/v1/live/sessions/{sessionId}/events?after=N` | 从 sequence cursor 查询 |
| GET | `/v1/live/sessions/{sessionId}/stream?after=N` | SSE 只读事件流 |
| GET | `/v1/live/sessions/{sessionId}/evidence` | 获取或 finalize revision 1 |

错误统一为：

```json
{"error":{"code":"sequence_gap","message":"3"}}
```

稳定错误包括 `event_conflict`、`event_hash_mismatch`、`sequence_gap`、`hash_chain_broken`、`session_closed`、`artifact_missing` 和 `bundle_revision_conflict`。

## Event and artifact rules

- `sequence` 从 1 连续递增。
- `previous_event_hash` 必须等于 session 当前 head。
- `event_hash` 是排除自身后的 canonical JSON Keccak-256。
- `content_hash` 与 artifact 地址使用 SHA-256。
- artifact 路径是 `artifacts/sha256/<前两位>/<完整 digest>`。
- 完全相同的 `event_id` 重试是幂等成功；同 ID 不同内容拒绝。
- `source`、`summary`、`derived` 不可混写。
- 视频仅保存 URL、hash 和时间引用。

## Signed review protocol

M4 使用 EIP-712 domain version `2`。`NetworkTaskV2` 支持 `observe_live_text` 与 `review_dispute`，同时保留 M3 两种任务。`TaskReceiptV2` 对 result hash 签名。

critical dispute 必须由 bootstrap 内三个不同节点复核：

- 两票 `dismiss`：`dismissed`。
- 两票 `uphold`：`upheld`。
- 缺失、重复、无多数或无效签名：`unresolved`。

只有 `dismissed` 能通过服务层 ProposalGate。Gate 只返回 execution plan，不提交交易，也不请求投票签名。

## Dashboard

以下接口只读：

```text
GET /demo/
GET /v1/dashboard/sessions
GET /v1/dashboard/sessions/{sessionId}
GET /v1/dashboard/disputes/{disputeId}
```

session detail 同时返回 artifact 完整性状态。任何缺失 artifact 都必须显示为校验失败，不能静默降级。

## CLI

```powershell
uv run loveengine live serve --db .\var\live.sqlite --artifacts .\var\artifacts
uv run loveengine live session create --db .\var\live.sqlite --session-id demo --created-at 1770000000
uv run loveengine live ingest --db .\var\live.sqlite --artifacts .\var\artifacts --session-id demo --input .\examples\live\live-session.fixture.ndjson
uv run loveengine live close --db .\var\live.sqlite --session-id demo --closed-at 1770000020
uv run loveengine evidence finalize --db .\var\live.sqlite --artifacts .\var\artifacts --session-id demo --finalized-at 1770000021
uv run loveengine demo live-evidence --nodes 3 --input .\examples\live\live-session.fixture.ndjson --output .\examples\transcripts
uv run loveengine live transcript verify .\examples\transcripts\live-review.fixture.json
```
