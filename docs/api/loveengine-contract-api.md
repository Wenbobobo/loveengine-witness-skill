# LoveEngine contract API

本文是 LoveEngine Witness Skill 的合约对接文档。它参考 `LoveEngineSkill/UAS接口文档.md`，并按当前规格修正了签名安全边界。状态：M1/M2 target，尚未有完整合约实现。

## 合约总览

| 合约 | 职责 | 状态 |
| --- | --- | --- |
| `WitnessDAO` | 见证者注册、提案、批量投票、执行下游状态变更 | M2 target |
| `CorporateSink` | 企业直播排期、凭证 hash、补偿记录 | M2 target |
| `StreamingEngine` | UTO 流账本计算和用户数更新 | M2 target |
| `PublicSink` | 只读公共账本查询入口 | M2 target |

## 通用规则

- 所有签名使用 EIP-712。
- relayer 不需要特权；`batchRegister` 和 `batchVote` 可由任意地址提交。
- Agent 不读取、不保存、不打印私钥。
- 链上只保存证据和凭证 hash。
- `min_valid_votes`、`approval_threshold_bps`、`broadcast_window_seconds`、`min_broadcast_interval_seconds` 是部署或治理参数。

## WitnessDAO

### RegisterSignature

M2 target:

```solidity
struct RegisterSignature {
    address witness;
    uint256 nonce;
    uint256 deadline;
    uint8 v;
    bytes32 r;
    bytes32 s;
}
```

注册签名必须绑定：

- `chainId`
- `verifyingContract`
- `witness`
- `nonce`
- `deadline`

### VoteSignature

M2 target:

```solidity
struct VoteSignature {
    address witness;
    uint256 proposalId;
    bool support;
    bytes32 reasonHash;
    bytes32 payloadHash;
    uint256 nonce;
    uint256 deadline;
    uint8 v;
    bytes32 r;
    bytes32 s;
}
```

投票签名必须绑定：

- `chainId`
- `verifyingContract`
- `proposalId`
- `support`
- `reasonHash`
- `payloadHash`
- `nonce`
- `deadline`

不要实现“只对最新活跃提案签名”。这是旧接口文档里最容易误用的点。

### Methods

```solidity
function batchRegister(RegisterSignature[] calldata sigs) external;
function batchVote(VoteSignature[] calldata sigs) external;
function getDomainSeparator() external view returns (bytes32);
function proposeUserCount(uint256 newUserCount, bytes32 evidenceBundleHash) external returns (uint256 proposalId);
function proposeCompensation(uint256 amount, bytes32 requestHash, bytes32 evidenceBundleHash) external returns (uint256 proposalId);
function activeProposalId() external view returns (uint256);
```

### Expected events

```solidity
event WitnessRegistered(address indexed witness);
event WitnessExited(address indexed witness);
event ProposalCreated(uint256 indexed proposalId, uint8 proposalType, bytes32 evidenceBundleHash);
event VoteAccepted(uint256 indexed proposalId, address indexed witness, bool support, bytes32 reasonHash);
event ProposalFinalized(uint256 indexed proposalId, bool passed, uint256 totalVotes, uint256 supportVotes);
event ProposalExecuted(uint256 indexed proposalId);
```

### Execution rule

M2 default:

- `totalVotes >= min_valid_votes`
- `supportVotes / totalVotes >= approval_threshold_bps`
- proposal still active
- vote signatures valid
- nonce not used
- deadline not expired

When the threshold is reached, `batchVote` may finalize and execute in the same transaction.

## CorporateSink

### Methods

```solidity
function scheduleBroadcast(uint256 timestamp, bytes32 liveMetadataHash) external;
function uploadCertificate(uint256 index, bytes32 hash) external;
function nextBroadcastTime() external view returns (uint256);
function getCorporateCSR(uint256 index) external view returns (bytes32);
function getCompensation() external view returns (uint256);
```

### Expected events

```solidity
event BroadcastScheduled(uint256 indexed timestamp, bytes32 liveMetadataHash);
event CertificateUploaded(uint256 indexed index, bytes32 hash);
event CompensationRecorded(uint256 amount, bytes32 requestHash, bytes32 evidenceBundleHash);
```

### Rules

- `scheduleBroadcast` 受 `min_broadcast_interval_seconds` 限制，默认 2 周。
- `proposeUserCount` 和 `proposeCompensation` 只能在 `nextBroadcastTime` 后的 `broadcast_window_seconds` 内发起，默认 2 小时。
- `uploadCertificate` 存 bytes32 hash。原始凭证在链下证据包或外部存储中保留。

## StreamingEngine

### Methods

```solidity
function getCurrentBalance() external view returns (uint256);
function rate() external view returns (uint256);
function updateUserCount(uint256 newUserCount) external;
```

### Expected event

```solidity
event UserCountUpdated(uint256 newUserCount, uint256 newRate, uint256 checkpointTime, uint256 baseBalance);
```

### Rule

`updateUserCount` 只能由 `WitnessDAO` 执行。更新前先结算历史累计值，再更新 `checkpointTime` 和 `rate`。

UTO 单位仍是待决问题。实现时不要把 `365 UTO` 或 `52 UTO / 人 / 天` 写死为不可改常量。

## PublicSink

### Methods

```solidity
function getTotalUTO() external view returns (uint256);
```

### Rule

`PublicSink` 是只读代理，不设 owner 修改口。它可以持有 `StreamingEngine` 地址，但不应该暴露状态修改方法。

## 集成顺序

M2 local loop:

1. 部署 `StreamingEngine`。
2. 部署 `PublicSink`，指向 `StreamingEngine`。
3. 部署 `CorporateSink`。
4. 部署 `WitnessDAO`，配置下游合约和治理参数。
5. 生成 witness 注册 typed data。
6. 本地 signer 签名。
7. relayer 调用 `batchRegister`。
8. 企业调用 `scheduleBroadcast`。
9. 生成 EvidenceBundle，取得 `payloadHash` 和 `evidenceBundleHash`。
10. 企业调用 `proposeUserCount` 或 `proposeCompensation`。
11. 见证者签 vote typed data。
12. relayer 调用 `batchVote`。
13. 查询 `PublicSink.getTotalUTO()`。
14. 生成 transcript。

## 测试要求

合约测试至少覆盖：

- EIP-712 domain separator。
- register nonce/deadline。
- vote proposalId mismatch。
- vote payloadHash mismatch。
- duplicate vote。
- threshold 未达不得执行。
- 90% 未达不得执行。
- 窗口前、窗口内、窗口后提案。
- StreamingEngine checkpoint。
- PublicSink 无 owner mutation。
