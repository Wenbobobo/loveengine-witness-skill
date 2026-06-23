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

## Observation protocol

`ObserveLiveTextPayloadV1` 绑定 session、SSE URL、session URL、artifact
URL、起始 cursor 和初始 head hash。`NetworkTaskV2` 继续绑定 chainId、
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

`PilotTranscriptV1` 交叉校验 package、session/event chain、
ObservationSet、EvidenceBundle、dispute、ProposalGate、proposal、五个
显式批准、交易和 PublicSink 最终状态。

## Stable CLI

```text
loveengine package build|verify|install|self-check
loveengine pilot serve|status
loveengine pilot chain init|start|status|snapshot|restore
loveengine pilot snapshot create|verify|restore|prune
loveengine pilot transcript verify
loveengine pilot soak
loveengine witness vote approve
loveengine demo lan-pilot
```
