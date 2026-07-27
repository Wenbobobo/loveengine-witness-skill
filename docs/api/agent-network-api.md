# LoveEngine Agent Network API

状态：M3 V1 历史兼容；0.6.1 candidate 使用 V2 + NodeTrustPolicyV1
M3 稳定版本：`0.3.1-demo-ready`（兼容 `0.3.0-network-pilot`）。

本文先保留 M3 V1 wire format，再说明当前共用 Relay 行为。V2 的 signed profile、
bootstrap、task 和 receipt 使用 EIP-712 domain version 2；当前 V2 只执行
`observe_live_text` 和 `review_dispute`。P2P 和节点直连未实现。

## 1. SkillRegistry

```solidity
enum ReleaseStatus {
    NONE,
    ACTIVE,
    DEPRECATED,
    REVOKED
}

struct Release {
    bytes32 packageHash;
    bytes32 manifestHash;
    bytes32 previousVersionHash;
    bytes32 replacementVersionHash;
    ReleaseStatus status;
    uint64 publishedAt;
}

function publishRelease(
    bytes32 skillId,
    bytes32 versionHash,
    bytes32 packageHash,
    bytes32 manifestHash,
    bytes32 previousVersionHash
) external;

function setCurrentVersion(bytes32 skillId, bytes32 versionHash) external;

function setReleaseStatus(
    bytes32 skillId,
    bytes32 versionHash,
    ReleaseStatus status,
    bytes32 replacementVersionHash
) external;

function getRelease(
    address publisher,
    bytes32 skillId,
    bytes32 versionHash
) external view returns (Release memory);
```

约束：

- `msg.sender` 即 Publisher，不能修改其他 Publisher 的 release。
- 同一 Publisher/skill/version 只能发布一次。
- revoked 状态不可恢复。
- current version 必须存在且为 active。
- package/manifest hash 不能为零。

## 2. 签名节点身份

`SignedAgentNodeProfileV1` 包含：

```json
{
  "schema_version": "loveengine.signed-agent-node-profile/1",
  "chain_id": "31337",
  "registry": "0x...",
  "profile": {
    "node": "0x...",
    "capabilities": ["propagate_skill", "observe_broadcast"],
    "sequence": "1",
    "valid_until": "4102444800"
  },
  "signature": "0x..."
}
```

NodeProfile EIP-712 message：

```text
NodeProfile(
  address node,
  bytes32 profileHash,
  uint256 sequence,
  uint256 validUntil
)
```

domain 绑定 `chainId` 和 `SkillRegistry` 地址。

## 3. BootstrapBundle

Bootstrap 由测试 Publisher 签名，列出 Relay Hub 和节点目录：

```json
{
  "schema_version": "loveengine.bootstrap-bundle/1",
  "chain_id": "31337",
  "registry": "0x...",
  "publisher": "0x...",
  "directory": [],
  "directory_hash": "0x...",
  "sequence": "1",
  "valid_until": "1780000000",
  "signature": "0x..."
}
```

旧 sequence、过期 bundle、错误 Publisher 或目录 hash 必须被拒绝。

## 4. NetworkTask

允许的 M3 task type：

- `propagate_skill`
- `observe_broadcast`

EIP-712 message：

```text
NetworkTask(
  bytes32 taskId,
  bytes32 taskType,
  address issuer,
  address recipient,
  bytes32 manifestHash,
  bytes32 payloadHash,
  uint256 nonce,
  uint256 deadline
)
```

约束：

- 绑定 chainId 和 SkillRegistry。
- M3 每条任务绑定一个具体 recipient；广播由 Relay 为每个节点生成独立任务。
- 节点以 issuer+nonce 和 taskId 双重去重。
- `observe_broadcast` payload 必须引用 chainId、CorporateSink、txHash 和 logIndex。
- M3 不允许 `sign_vote` 或自动投票任务。

## 5. TaskReceipt

```text
TaskReceipt(
  bytes32 taskId,
  address node,
  bytes32 status,
  bytes32 resultHash,
  uint256 nonce,
  uint256 completedAt
)
```

状态：

- `completed`
- `rejected`

回执只提交结果 hash 和结构化摘要，不包含私钥、token 或原始敏感证据。

## 6. Relay Hub

HTTP：

```text
GET /v1/health
GET /v1/bootstrap
GET /v1/releases/{publisher}/{skillId}/{version}
GET /v1/artifacts/{packageHash}
```

Pilot Server 另提供受 write token 和 Origin 保护的任务入口：

```text
POST /v1/relay/tasks
```

该入口只接受已经签名的 NetworkTaskV2，并在持久化前检查 active release、
BootstrapBundleV2 成员、chainId、Registry、recipient、Publisher issuer、
manifest hash、deadline、签名和完整可执行 payload schema。字节完全相同的
签名 task 重试返回幂等成功；相同 taskId 或 issuer+nonce 对应不同内容时冲突。
它不替 issuer 签名。独立 `relay serve` 不提供该 Operator 写入口。

当前可执行 payload：

- `observe_live_text` 必须使用
  `loveengine.observe-live-text-payload/1`，绑定 session、SSE、session API、
  artifact origin 和 durable cursor；
- `review_dispute` 必须使用
  `loveengine.review-dispute-payload/1`，绑定 dispute、finalized bundle、
  session、evidence/events/artifact URL、revision、event count 和 head hash。

历史 V2 transcript 中只含 sessionId 或 disputeId/bundleHash 的最小 payload
仍可做签名与一致性验证，但公开入口不再把它们视为可执行任务。

WebSocket：

```text
GET /v1/ws
```

消息：

- `challenge`
- `authenticate`
- `task`
- `receipt`
- `receipt_state`
- `receipt_ack`
- `ack`
- `heartbeat`
- `error`

投递语义：

- Agent 主动建立出站连接。
- Relay SQLite 保存离线消息、ACK、receipt 与重试次数；节点自己的 SQLite
  journal 保存 taskId、issuer+nonce、完整签名 task、签名 receipt 和确认状态。
- at-least-once delivery。
- taskId 和 issuer+nonce 双重约束节点端幂等。
- Relay 只认证 signed bootstrap directory 中完全匹配的 profile。
- Receipt 必须来自当前 WebSocket 节点，并对应本连接已经投递且 ACK accepted 的
  pending task；未分配、重复和错绑回执被拒绝。
- 连接保持期间新增的任务会继续推送，不要求节点重连。
- 节点在发送前持久化 signed receipt。若 Relay 已保存 receipt 但确认 ACK 丢失，
  下次认证后 Relay 发送 `receipt_state`；节点只在它与本地 journal 完全一致时
  回 `receipt_ack`，Relay 再返回 `ack/status=receipt_confirmed`，节点不再次执行
  任务。认证 ACK 的 `receipt_state_count` 声明本次恢复批次数量，节点拒绝非法
  或过大的计数。
- Relay Hub 不替 issuer 或 node 签名。
- 本地 pilot 的节点通过 Anvil RPC signer 完成签名；私钥不进入 CLI 参数、环境变量、日志或 transcript。
- `loveengine node connect` 是统一的 V1/V2 出站 session 入口。V1/V2 codec 独立，
  连接、鉴权、ACK、keepalive 和 receipt 确认共用同一 session engine。
- live node 默认最多重连 3 次、每次 idle timeout 60 秒。每次重连都重新执行
  Relay challenge/profile 校验；首次连接后每次重连还重新查询 Registry release
  并与 trust policy 比较。达到上限后返回结构化错误，不作为常驻 daemon 无限重试。
- observation 单次执行上限是 10,000 个新事件、64 MiB artifact 总量；review
  上限是 1,000 个事件、32 MiB artifact 总量。两者的 JSON/SSE 响应上限为
  1 MiB，单个 artifact 上限为 8 MiB，HTTP redirect 被拒绝。
- 当前 live connect 还必须取得独立 `NodeTrustPolicyV1`，绑定 chain ID、
  Registry、Publisher、skill/version、ZIP/manifest hash 和 allowed issuers。
  invite 只负责连接，不是信任根。

## 7. CLI

```text
loveengine registry publish --input <release> --dry-run
loveengine registry verify --rpc-url <url> --artifact <zip> --chain-id <id> --registry <address> --publisher <address>
loveengine node profile sign
loveengine bootstrap build
loveengine bootstrap verify
loveengine relay serve
loveengine node connect --invite <file> --trust-policy <policy> --package <zip> --profile <profile> --rpc-url <url> --address <node> --reconnect-attempts 3 --idle-timeout-seconds 60
loveengine network demo --nodes 3
loveengine network transcript verify <path>
```

成功输出 JSON；错误使用现有结构化 stderr 和稳定退出码。
