# LoveEngine Agent Network API

状态：`M3 local implemented`
M3 稳定版本：`0.3.1-demo-ready`（兼容 `0.3.0-network-pilot`）；M4 V2 扩展见 `live-evidence-api.md`。

本文定义 SkillRegistry、节点身份、Bootstrap、网络任务、任务回执和中心 Relay Hub 的公开接口。M3 只实现中心 Relay；P2P 和节点直连不在本阶段范围内。

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

WebSocket：

```text
GET /v1/ws
```

消息：

- `challenge`
- `authenticate`
- `task`
- `receipt`
- `ack`
- `heartbeat`
- `error`

投递语义：

- Agent 主动建立出站连接。
- SQLite 保存离线消息、cursor、ack 和重试次数。
- at-least-once delivery。
- taskId 和 nonce 保证节点端幂等。
- Relay Hub 不替 issuer 或 node 签名。
- 本地 pilot 的节点通过 Anvil RPC signer 完成签名；私钥不进入 CLI 参数、环境变量、日志或 transcript。
- `loveengine node connect` 是可单独启动的出站节点进程入口。

## 7. CLI

```text
loveengine registry publish
loveengine registry verify
loveengine node profile sign
loveengine bootstrap build
loveengine bootstrap verify
loveengine relay serve
loveengine node connect
loveengine network demo --nodes 3
loveengine network transcript verify <path>
```

成功输出 JSON；错误使用现有结构化 stderr 和稳定退出码。
