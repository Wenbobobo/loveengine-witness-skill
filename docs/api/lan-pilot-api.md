# Local Pilot API

状态：0.6.1-contract-public-pilot candidate；仅本机
协议：loveengine-witness-net/0.6

名称中的历史 “LAN Pilot” 不表示 Tailscale、公网或远程部署已完成。当前默认实验
只绑定 loopback；governance stage 是可选层。

## Runtime files

PilotConfigV1 固定 pilot.sqlite、relay.sqlite、artifact root、audit log、RPC、
chain ID、监听地址、Origin、bootstrap/release/ZIP 和 restricted token file。
绑定 0.0.0.0 或 :: 必须显式 allow_all_interfaces=true，但这本身不构成安全部署。

Quickstart 生成：

- pilot-invite.json：server/operator/dashboard/relay URL 和公开 release 提示；
- pilot-trust-policy.json：独立信任策略；
- release/ 下的 actual deterministic ZIP 和 release metadata；
- profiles/ 下的本机实验 signed profiles；
- token file、config、SQLite、artifact、audit 与持久 Anvil 状态。

## Invite 与 trust policy

PilotInviteV1 实际字段是 schema_version、server_url、operator_url、
dashboard_url、relay_url、chain_id、registry、publisher、skill_id、version 和
package_hash。它没有 issued_at、expires_at 或 public_base_url 字段，也不包含
token、私钥、助记词或 keystore。

NodeTrustPolicyV1 必须通过可信旁路获得，字段为：

- chain_id、registry、publisher；
- skill_id、version；
- actual ZIP Keccak package_hash；
- canonical manifest Keccak manifest_hash；
- 非空、唯一 allowed_issuers。

live node connect 要求 policy。节点比较 invite/policy/profile/RPC release/actual
ZIP，随后才连接；任务 issuer 必须属于 allowed_issuers。dry-run 可以不带 policy，
但只输出 connection_plan 且 trust_bound: false。

## HTTP 与 Relay

只读：

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
    GET /v1/artifacts/{packageHash}
    GET /v1/ws

鉴权写入：

    POST /v1/live/sessions
    POST /v1/live/sessions/{sessionId}/events
    POST /v1/live/sessions/{sessionId}/close
    POST /v1/live/sessions/{sessionId}/evidence/finalize
    POST /v1/relay/tasks

写请求需要 Authorization: Bearer [token]；浏览器请求还必须匹配 allowed Origin。
响应带 X-Correlation-ID。token 只来自受限文件和页面内存，不进入 URL、
localStorage、日志或 transcript。

`POST /v1/relay/tasks` 只接受完整签名的 NetworkTaskV2。Pilot 会在写入
`relay.sqlite` 前验证 active release、bootstrap 成员、chainId、Registry、
recipient、Publisher issuer、manifest hash、deadline 和 EIP-712 签名。成功返回
202。完全相同的 signed task 重试仍返回 202 并标记 idempotent replay；相同
task ID 或 recipient/issuer/nonce 对应不同内容返回 409。observe/review 还必须
携带完整可执行 payload schema；历史 transcript 的最小 payload 不在此执行。
端点不创建签名，也不接受 V1 task。

SSE 通过 Last-Event-ID/after 恢复。Relay 只接受 bootstrap profile，区分接收 ACK
和最终 receipt；它可在连接建立后继续推送任务。receipt 必须属于当前连接、当前
节点和已接受 pending task，伪造、重复或错绑均拒绝。节点发送前将 receipt 写入
本地 journal；Relay 保存后 ACK，节点再确认。ACK 丢失时重连恢复同一 receipt，
不重复执行。

## Observation

ObserveLiveTextPayloadV1 绑定 session、SSE/session/artifact URL、起始 cursor、
initial head 和 max_duration_seconds。节点验证：

1. sequence 与 previous_event_hash；
2. canonical event Keccak；
3. content/artifact SHA-256 和 exact bytes；
4. 关闭后 bundle 的 head、event count 和 bundle hash；
5. 任务的 policy/issuer/recipient/nonce/deadline。

当前核心实验聚合三个不同签名节点；结果不一致则 ObservationSet 失败。固定三节点
是实验 quorum，不是通用共识算法。

ReviewDisputePayloadV1 绑定 dispute/bundle、session、evidence/events/artifact
URL、revision、event count 和 head hash。全部 URL 必须与 invite 的 server origin
一致。节点重新获取 finalized bundle 和事件，限制响应/事件/总 artifact 大小，
复算事件链、每个 artifact、category counts 与 bundle references，成功后才根据
操作者显式 verdict 签回执。

## Core 与 governance stages

~~~text
core:
release -> policy/bootstrap -> public node connect -> event/artifact
-> ObservationSet -> EvidenceBundle -> dispute review -> ProposalGate
-> WitnessCoreTranscriptV1

governance:
core -> witness registration -> explicit approvals -> WitnessDAO execution
-> PublicSink -> PilotTranscriptV2
~~~

core 是 demo lan-pilot 默认 stage。两个 stage 均标记 local_anvil 和
actors_simulated。Operator/Viewer UI 不执行 finalize、Gate、vote 或 PublicSink
读取；这些由 API/CLI 和 transcript 验证。

## Transcript verification levels

| 输入 | verification_level | chain_verified | trust_bound |
| --- | --- | --- | --- |
| PilotTranscriptV1 | legacy_consistency | false | false |
| Core/V2 offline | offline_integrity | false | false |
| Core/V2 + RPC | chain_consistency | true | false |
| Core/V2 + RPC + trust policy | chain_verified | true | true |

Core transcript 在 ProposalGate 结束；V2 额外包含 proposal、独立 witness approvals、
transactions、contract code 和 PublicSink final state。RPC 模式在 transcript
记录的 final block number 读取并核对 block hash/timestamp，避免使用易漂移的
“当前状态”验证历史记录。

## Snapshot

snapshot 包含 SQLite online backup、artifact、hash-linked audit JSONL、Anvil
deployment/state 和完整 checksums。verify 拒绝空清单、缺失文件、symlink、路径
逃逸和非法 run ID；negative retention 拒绝。restore 先验证并准备 rollback，再
替换正式状态。该机制只解决单机恢复，不是跨主机 HA。

## Stable CLI

~~~text
loveengine package build|verify|install|self-check
loveengine registry publish|verify
loveengine pilot contracts prepare
loveengine pilot quickstart|serve|status
loveengine pilot chain init|start|status|snapshot|restore
loveengine pilot snapshot create|verify|restore|prune
loveengine pilot transcript verify
loveengine node connect
loveengine witness vote approve
loveengine demo lan-pilot --stage core|governance
~~~

完整参数见 [CLI reference](cli-reference.md)。
