# LoveEngine contract API

本文是 LoveEngine Witness Skill 的合约对接文档。它参考
`docs/reference/source-materials/current/UAS接口文档.md`、合约团队交付稿
`docs/reference/contracts/contract-team-v2/`，并按当前规格修正签名安全边界。
状态：M6 target ABI。

原接口到当前实现的完整映射、测试证据和待协作团队确认事项见
`docs/development/contract-team-handoff.md`。contract2 对照和修改建议见
`docs/development/contract2-comparison-and-recommendations.md`。

## 合约总览

| 合约 | 职责 | 状态 |
| --- | --- | --- |
| `WitnessDAO` | 见证者注册、提案、批量投票、失败 finalize、可选 relayer refund、执行下游状态变更 | M6 target |
| `CorporateSink` | 企业直播排期、结构化 Broadcast 查询、凭证/session/evidence hash 绑定、补偿记录 | M6 target |
| `StreamingEngine` | UTO 流账本计算、checkpoint 和用户数更新 | M6 target |
| `PublicSink` | 只读公共账本查询入口 | M6 target |
| `SkillRegistry` | Skill 版本、package hash、manifest hash 和 release 状态信任根 | M3+ auxiliary |

## 通用规则

- 所有签名使用 EIP-712。
- relayer 不需要特权；`batchRegister` 和 `batchVote` 可由任意地址提交。
- Agent 不读取、不保存、不打印私钥。
- 链上只保存证据和凭证 hash。
- `min_valid_votes`、`approval_threshold_bps`、`broadcast_window_seconds`、`min_broadcast_interval_seconds` 是部署或治理参数。

## WitnessDAO

### RegisterSignature

M2 local implemented:

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

M2 local implemented:

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
function finalizeProposal(uint256 proposalId) external;
function proposalCreatedAt(uint256 proposalId) external view returns (uint256);
function proposalVotingDeadline(uint256 proposalId) external view returns (uint256);
function setRelayerRefundPolicy(address relayer, bool allowed, uint256 maxWeiPerCall) external;
function withdrawRelayerRefund(address payable recipient) external;
function relayerRefundCredits(address relayer) external view returns (uint256);
function activeProposalId() external view returns (uint256);
```

### Expected events

```solidity
event WitnessRegistered(address indexed witness);
event ProposalCreated(uint256 indexed proposalId, uint8 proposalType, bytes32 evidenceBundleHash, bytes32 payloadHash);
event VoteAccepted(uint256 indexed proposalId, address indexed witness, bool support, bytes32 reasonHash);
event ProposalFinalized(uint256 indexed proposalId, bool passed, uint256 totalVotes, uint256 supportVotes);
event ProposalExecuted(uint256 indexed proposalId);
event ProposalFailed(uint256 indexed proposalId, uint256 totalVotes, uint256 supportVotes);
event RelayerRefundPolicySet(address indexed relayer, bool allowed, uint256 maxWeiPerCall);
event RelayerRefundCredited(address indexed relayer, uint256 amount);
event RelayerRefundWithdrawn(address indexed relayer, address indexed recipient, uint256 amount);
```

### Execution rule

M2 default:

- `totalVotes >= min_valid_votes`
- `supportVotes / totalVotes >= approval_threshold_bps`
- proposal still active
- `proposalId` equals the current active proposal before proposal state is read
- vote signatures valid
- nonce not used
- deadline not expired

When the threshold is reached, `batchVote` may finalize and execute in the same transaction.
If the voting deadline expires without passing, anyone may call `finalizeProposal`
to mark the proposal failed and clear the active proposal. Late votes for an
expired active proposal must revert before finalization.

M6 absorbs contract2's relayer gas refund idea only as optional bounded credit:
submitting relayers stay permissionless, but refund eligibility and per-call
cap are explicit policy. Credits are withdrawn separately to avoid mixing
signature submission with arbitrary value transfer.

## CorporateSink

### Methods

```solidity
function scheduleBroadcast(uint256 timestamp, bytes32 liveMetadataHash) external;
function uploadCertificate(uint256 index, bytes32 hash) external;
function uploadCertificate(uint256 index, bytes32 hash, bytes32 sessionHash, bytes32 evidenceBundleHash) external;
function broadcastCount() external view returns (uint256);
function broadcastAt(uint256 index) external view returns (Broadcast memory);
function broadcastMetadataHash(uint256 index) external view returns (bytes32);
function certificateSessionHash(uint256 index) external view returns (bytes32);
function certificateEvidenceBundleHash(uint256 index) external view returns (bytes32);
function nextBroadcastTime() external view returns (uint256);
function getCorporateCSR(uint256 index) external view returns (bytes32);
function getCompensation() external view returns (uint256);
```

### Expected events

```solidity
event BroadcastScheduled(uint256 indexed timestamp, bytes32 liveMetadataHash);
event CertificateUploaded(uint256 indexed index, bytes32 hash);
event CertificateBound(uint256 indexed index, bytes32 sessionHash, bytes32 evidenceBundleHash);
event CompensationRecorded(uint256 amount, bytes32 requestHash, bytes32 evidenceBundleHash);
```

### Rules

- `scheduleBroadcast` 受 `min_broadcast_interval_seconds` 限制，默认 2 周。
- `proposeUserCount` 和 `proposeCompensation` 只能在 `nextBroadcastTime` 后的 `broadcast_window_seconds` 内发起，默认 2 小时。
- `uploadCertificate` 存 bytes32 hash。M6 推荐使用四参数版本，把凭证绑定到
  broadcast index、sessionId hash 和 EvidenceBundle hash。原始凭证在链下
  证据包或外部存储中保留。

## StreamingEngine

### Methods

```solidity
function getCurrentBalance() external view returns (uint256);
function rate() external view returns (uint256);
function ratePerUserPerSecond() external view returns (uint256);
function updateUserCount(uint256 newUserCount) external;
```

### Expected event

```solidity
event UserCountUpdated(uint256 newUserCount, uint256 newRate, uint256 checkpointTime, uint256 baseBalance);
```

### Rule

`updateUserCount` 只能由 `WitnessDAO` 执行。更新前先结算历史累计值，再更新 `checkpointTime` 和 `rate`。

UTO 精度默认按 `1e18` 记账，时间单位为秒。`ratePerUserPerSecond` 必须来自
部署参数，不写死业务常量。

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
