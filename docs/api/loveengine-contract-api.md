# LoveEngine contract API

状态：v0.6.0-contract-public-pilot 本机 ABI；0.6.1 不修改治理 ABI
编译器：Solidity 0.8.26
用途：SkillRegistry 属于核心信任；其余四合约属于可选治理实验

源码 ABI 是最终事实源。本文不把 contract-team v2 preserved copy 当成当前合约。

## SkillRegistry

每个 msg.sender 只管理自己的 Publisher namespace。

~~~solidity
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
function currentVersion(address publisher, bytes32 skillId)
    external view returns (bytes32);
function getRelease(address publisher, bytes32 skillId, bytes32 versionHash)
    external view returns (Release memory);
~~~

packageHash 是 actual deterministic ZIP bytes 的 Keccak-256；manifestHash 是
canonical manifest JSON 的 Keccak-256。release 状态为 Active、Deprecated 或
Revoked；revoked 不能恢复。Registry 不保存 ZIP bytes。

事件：ReleasePublished、CurrentVersionSet、ReleaseStatusChanged。

## WitnessDAO（治理实验）

### Constructor

~~~solidity
constructor(
    address streamingEngine,
    address corporateSink,
    address corporateAdmin,
    uint256 minValidVotes,
    uint256 approvalThresholdBps
);
~~~

三个地址不得为 zero address；minValidVotes 必须大于 0；
approvalThresholdBps 必须在 1 到 10,000 之间。

### EIP-712

domain 为 LoveEngine WitnessDAO / version 1。Register 绑定 witness、nonce、
deadline；Vote 绑定 witness、proposalId、support、reasonHash、payloadHash、nonce
和 deadline。

~~~solidity
function getDomainSeparator() external view returns (bytes32);
function hashRegister(address witness, uint256 nonce, uint256 deadline)
    public view returns (bytes32);
function hashVote(
    address witness,
    uint256 proposalId,
    bool support,
    bytes32 reasonHash,
    bytes32 payloadHash,
    uint256 nonce,
    uint256 deadline
) public view returns (bytes32);
function batchRegister(RegisterSignature[] calldata signatures) external;
function batchVote(VoteSignature[] calldata signatures) external;
~~~

空 batch 拒绝。batchVote 只接受当前 active proposal，验证 witness 注册、payload、
nonce、deadline、签名和 duplicate vote；达到 minValidVotes 和
approvalThresholdBps 时同一交易执行下游动作。

### Proposal 与退款

~~~solidity
function proposeUserCount(uint256 newUserCount, bytes32 evidenceBundleHash)
    external returns (uint256);
function proposeCompensation(
    uint256 amount,
    bytes32 requestHash,
    bytes32 evidenceBundleHash
) external returns (uint256);
function finalizeProposal(uint256 proposalId) external;
function proposalPayloadHash(uint256 proposalId) external view returns (bytes32);
function proposalActive(uint256 proposalId) external view returns (bool);
function proposalExecuted(uint256 proposalId) external view returns (bool);
function proposalCreatedAt(uint256 proposalId) external view returns (uint256);
function proposalVotingDeadline(uint256 proposalId) external view returns (uint256);
function proposalVoteCounts(uint256 proposalId)
    external view returns (uint256 totalVotes, uint256 supportVotes);
function setRelayerRefundPolicy(
    address relayer,
    bool allowed,
    uint256 maxWeiPerCall
) external;
function withdrawRelayerRefund() external;
~~~

只有 corporateAdmin 能在 CorporateSink 的当前 proposal window 内提案。合约一次
只允许一个 active proposal。过期 proposal 可由任何地址 finalize。

退款只计算实际处理项；每次受 allowlist 和 cap 限制，总 credit 受合约余额减去
既有未提取 credit 约束。withdrawRelayerRefund 没有 recipient 参数，只向
msg.sender 提取自己的 credit。

事件：WitnessRegistered、ProposalCreated、VoteAccepted、ProposalFinalized、
ProposalExecuted、ProposalFailed、RelayerRefundPolicySet、
RelayerRefundCredited、RelayerRefundWithdrawn(address indexed relayer,
uint256 amount)。

## CorporateSink（治理实验）

~~~solidity
constructor(
    address corporateAdmin,
    uint256 minBroadcastInterval,
    uint256 broadcastWindow
);
function setWitnessDAO(address witnessDAO) external;
function scheduleBroadcast(uint256 timestamp, bytes32 liveMetadataHash) external;
function uploadCertificate(uint256 index, bytes32 hash) external;
function uploadCertificate(
    uint256 index,
    bytes32 hash,
    bytes32 sessionIdHash,
    bytes32 evidenceBundleHash
) public;
function broadcastCount() external view returns (uint256);
function broadcastAt(uint256 index) external view returns (uint256);
function broadcastMetadataHash(uint256 index) external view returns (bytes32);
function certificateSessionHash(uint256 index) external view returns (bytes32);
function certificateEvidenceBundleHash(uint256 index) external view returns (bytes32);
function getCorporateCSR(uint256 index) external view returns (bytes32);
function getCompensation() external view returns (uint256);
function isProposalWindowOpen(uint256 timestamp) external view returns (bool);
function recordCompensation(
    uint256 amount,
    bytes32 requestHash,
    bytes32 evidenceBundleHash
) external;
~~~

setWitnessDAO 只能由部署 bootstrapper 调用一次。scheduleBroadcast 和
uploadCertificate 只有 corporateAdmin 可调用；recordCompensation 只有设置后的
WitnessDAO 可调用。certificate 是否可上传按目标 broadcasts[index].timestamp
判断，不使用当前 nextBroadcastTime。

broadcastAt 返回 uint256 timestamp，不返回 Broadcast struct。当前事件只有
WitnessDAOSet、BroadcastScheduled、CertificateUploaded 和
CompensationRecorded；不存在 CertificateBound 事件。

限制：proposal window 使用单个 nextBroadcastTime。后续 schedule 会更新该值，
所以当前治理实验只承诺一次干净部署中的单场流程，不承诺多场并发排期。

## StreamingEngine（治理实验）

~~~solidity
constructor(uint256 ratePerUser, uint256 initialUserCount);
function setWitnessDAO(address witnessDAO) external;
function getCurrentBalance() public view returns (uint256);
function updateUserCount(uint256 newUserCount) external;
~~~

部署者只能设置一次非零 WitnessDAO；之后只有该 DAO 能更新用户数。更新先把历史
累计结算到 baseBalance，再改变 checkpointTime 和 rate。公开 immutable/view
包括 ratePerUser、ratePerUserPerSecond、checkpointTime、baseBalance 和 rate。

事件：WitnessDAOSet、UserCountUpdated。

## PublicSink（治理实验）

~~~solidity
constructor(address streamingEngine);
function getTotalUTO() external view returns (uint256);
~~~

PublicSink 没有写入口，只转发 StreamingEngine.getCurrentBalance。UTO 在本仓库中
是公共会计值，不是可交易资产。

## ProposalGate 与链上权限

ProposalGate 位于 Python service/domain 层。它检查 finalized EvidenceBundle 和
critical dispute 状态，然后生成 plan；它不调用合约。WitnessDAO 不验证
ProposalGate proof，corporateAdmin 可以直接提案。因此“Gate 已放行”只能作为
治理实验的人工流程要求，不能描述为合约强制安全属性。

## 部署顺序

1. 部署 StreamingEngine、PublicSink 和 CorporateSink。
2. 部署 WitnessDAO，传入前三者/管理员和治理参数。
3. bootstrapper 分别设置 StreamingEngine、CorporateSink 的 WitnessDAO。
4. 单独部署 SkillRegistry；它不依赖四个治理合约。
5. Registry 发布由外部 signer 提交，节点只读验证 active release。
