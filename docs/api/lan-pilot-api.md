# LAN Pilot API

状态：M5 implemented release candidate  
协议：`loveengine-witness-net/0.5`

## PilotConfigV1

Schema：`schemas/pilot-config-v1.schema.json`。

配置固定数据库、Relay 数据库、artifact 根目录、audit 日志、RPC、
chainId、监听地址和允许的 Origin。`bootstrap_file`、`release_file` 和
`package_archive` 为同进程 Relay 提供节点目录、发布元数据和实际 ZIP；
启动时必须复算 ZIP Keccak 并匹配 release。写入 token 只从
`token_file` 读取。
绑定 `0.0.0.0` 或 `::` 必须显式设置 `allow_all_interfaces=true`。

## HTTP

只读：

```text
GET /healthz
GET /readyz
GET /v1/metrics
GET /operator/
GET /demo/
GET /v1/live/sessions/{sessionId}
GET /v1/live/sessions/{sessionId}/events
GET /v1/live/sessions/{sessionId}/stream
GET /v1/live/sessions/{sessionId}/evidence
GET /v1/live/artifacts/{sha256Digest}
GET /v1/bootstrap
GET /v1/ws
```

`stream` 使用最长一秒的 SSE long-poll：有新事件时立即返回，空闲时返回
keepalive，客户端继续携带 `Last-Event-ID`/`after` 重连。这样保留 cursor
恢复语义，同时避免空 session 上的高频 busy polling。

写入：

```text
POST /v1/live/sessions
POST /v1/live/sessions/{sessionId}/events
POST /v1/live/sessions/{sessionId}/close
```

写入必须同时满足：

- `Authorization: Bearer <token>`。
- 浏览器请求的 `Origin` 与配置一致。
- token 不写日志、不进入页面存储、不进入 transcript。

每个响应带 `X-Correlation-ID`。调用方可传入同名 header 关联请求。

`/operator/` 是主持人写入控制台，token 只保存在页面内存；
`/demo/` 是只读证据面板，不包含 POST、签名或交易操作。

## Observation protocol

`ObserveLiveTextPayloadV1` 绑定 session、SSE URL、session URL、artifact
URL、起始 cursor、初始 head hash 和可选 `max_duration_seconds`。该时长
进入签名 payload，最大为 14,700 秒，允许四小时 session 加五分钟收口；
缺省值 30 秒仅用于兼容早期短任务。`NetworkTaskV2` 继续绑定 chainId、
Registry、issuer、recipient、nonce、deadline 和 payload hash。

Agent 必须依次验证：

1. sequence 单调连续；
2. `previousEventHash`；
3. canonical event Keccak；
4. content SHA-256；
5. artifact bytes 与 artifact SHA-256；
6. 关闭后的 EvidenceBundleV2 head、数量和 bundle hash。

三个不同签名节点的结果聚合为 `ObservationSetV1`。节点结果不一致时
聚合失败。

Relay 将任务接收与任务完成分开观测：

- Agent 校验 task 的 chainId、Registry、recipient、nonce、deadline 和
  payload 后，立即发送 `ack`/`accepted`。
- `latency_ms` 只计算下发到接受 ACK 的延迟。
- session 关闭、证据校验和签名回执完成后再发送 `TaskReceiptV2`；
  该总耗时记录在 `completion_latency_ms`。
- `observe_live_text` 运行期间客户端持续服务 WebSocket heartbeat，不能因
  长直播阻塞 Relay 连接。

## Chain and voting

`pilot chain init` 部署四个核心合约和 SkillRegistry，并保存地址、部署
交易和 code hash。`start` 从校验后的 Anvil state 恢复；`status` 重新
计算全部 code hash。

`witness vote approve` 读取 `OnchainProposalPlanV1`，查询链上 active
proposal、payload hash、witness 注册状态和 nonce，再通过
`eth_signTypedData_v4` 请求显式签名。该命令不接受私钥。

## Snapshot and transcript

系统 snapshot 包含 SQLite online backup、artifact、hash-chained audit
JSONL、Anvil deployment/state 与 checksums。恢复前必须验证所有文件。

Audit JSONL 记录写操作、拒绝和关键状态变化；成功的只读 GET 只计入 metrics，
不逐请求写入审计链。审计校验逐行流式执行，长时间试点不会把完整日志载入内存。

`PilotTranscriptV1` 交叉校验 package、session/event chain、
ObservationSet、EvidenceBundle、dispute、ProposalGate、proposal、五个
显式批准、交易和 PublicSink 最终状态。

## Stable CLI

```text
loveengine version
loveengine package build|verify|install|self-check
loveengine pilot serve|status
loveengine pilot chain init|start|status|snapshot|restore
loveengine pilot snapshot create|verify|restore|prune
loveengine pilot transcript verify
loveengine pilot soak [--background]
loveengine pilot soak-status <state>
loveengine witness vote approve
loveengine demo lan-pilot
```

完整参数、后台 soak 和故障排查见 `docs/api/cli-reference.md`。
