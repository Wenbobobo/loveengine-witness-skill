# LoveEngine 合约团队对接与讨论说明

状态：`M2 closeout`  
适用版本：`0.2.0-local-loop`  
原始接口来源：`docs/reference/source-materials/current/UAS接口文档.md`
当前实现：`contracts/src/`  
当前对外接口：`docs/api/loveengine-contract-api.md`

本文用于向协作合约团队说明 LoveEngine Witness Skill 已完成的本地合约工作、相对原接口的安全增强、现有测试证据，以及进入测试网前需要共同确认的事项。它不改写原始接口文档。

## 1. 当前实现结果

当前 Foundry 工程已实现：

- `WitnessDAO`：见证者 EIP-712 注册、用户数/补偿提案、批量投票、阈值判断和下游执行。
- `CorporateSink`：直播排期、凭证 hash、补偿累计和提案窗口查询。
- `StreamingEngine`：checkpoint、历史累计值和按用户数更新流速。
- `PublicSink`：只读 `getTotalUTO()` 查询。

部署顺序：

1. 部署 `StreamingEngine`。
2. 部署 `PublicSink` 并绑定 `StreamingEngine`。
3. 部署 `CorporateSink`。
4. 部署 `WitnessDAO` 并配置前三个合约和治理参数。
5. 由部署者一次性调用 `StreamingEngine.setWitnessDAO()` 和 `CorporateSink.setWitnessDAO()`。

`setWitnessDAO()` 只能成功执行一次。它是本地原型的 bootstrap 机制，不是长期治理升级接口。

## 2. 原接口到当前接口的映射

### RegisterSignature

原接口：

```solidity
struct RegisterSignature {
    address witness;
    uint8 v;
    bytes32 r;
    bytes32 s;
}
```

当前接口：

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

增加 `nonce` 和 `deadline`，用于阻止签名重放和无限期保存旧签名。EIP-712 domain 自动绑定 `chainId` 和 `verifyingContract`。

### VoteSignature

原接口只包含 `witness` 和 `support`，并描述为投给“最新活跃提案”。当前接口为：

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

安全增强：

- `proposalId`：旧签名不能被用于另一个提案。
- `payloadHash`：投票绑定具体提案参数和证据 hash。
- `reasonHash`：反对理由保留可审计引用。
- `nonce`：同一签名不能重放。
- `deadline`：过期签名不能提交。
- domain：绑定 chain 和 WitnessDAO 地址。

### 提案

原接口：

```solidity
proposeUserCount(uint256 newUserCount)
proposeCompensation(uint256 amount)
```

当前接口：

```solidity
proposeUserCount(uint256 newUserCount, bytes32 evidenceBundleHash)
proposeCompensation(
    uint256 amount,
    bytes32 requestHash,
    bytes32 evidenceBundleHash
)
```

链上只记录 hash。原始证据、成本明细和直播内容保留在链下 EvidenceBundle。

### 直播排期

原接口：

```solidity
scheduleBroadcast(uint256 timestamp)
```

当前接口：

```solidity
scheduleBroadcast(uint256 timestamp, bytes32 liveMetadataHash)
```

增加 `liveMetadataHash`，用于绑定直播入口、展示项目和 session 元数据。

## 3. 已验证的安全与业务行为

固定 Foundry `1.7.1` 测试覆盖：

- 五见证者完整用户数更新闭环。
- 69 个注册签名和 69 个投票签名批量规模。
- 注册过期、错误 signer 和重复注册保护。
- 错误 `proposalId`、错误 `payloadHash` 和重复投票保护。
- 低于 90% 时不执行。
- 排期最小间隔和提案窗口前/后拒绝。
- 补偿提案写入企业补偿账。
- StreamingEngine 更新流速前 checkpoint。
- PublicSink 不存在 owner 或 mutation surface。

Python/Anvil E2E 还验证：

- typed data 与合约 typehash/domain 匹配。
- 任意 relayer 可提交批量签名。
- 五个测试见证者完成注册、排期、提案、投票和执行。
- transcript 可复算且不包含私钥或 mnemonic。

## 4. 需要合约团队确认的事项

### 4.1 24 小时表决窗口

原方案描述提案在 24 小时后结算。当前 M2 在达到最低票数和赞成率后立即执行，没有：

- `votingDeadline`；
- 到期后失败关闭；
- 未达到阈值的 finalize 操作。

建议后续明确 `createdAt`、`votingDeadline`、`finalizeProposal()` 和失败事件。

### 4.2 单活跃提案

当前一个 WitnessDAO 只允许一个 active proposal。需要确认：

- 用户数与补偿提案是否必须串行；
- 是否允许多个企业或 session 并行；
- 并行时 payload 和窗口如何绑定。

### 4.3 首次排期

当前首次排期只要求不早于当前区块时间；第二次起才受最小间隔约束。需要确认首次直播是否也必须至少提前两周。

### 4.4 治理参数

`minValidVotes`、`approvalThresholdBps`、`minBroadcastInterval`、`broadcastWindow` 和 `ratePerUser` 当前均在部署时固定。需要决定：

- 保持部署不可变并通过新部署升级；
- 由 WitnessDAO 提案更新；
- 使用 timelock 或版本化配置合约。

### 4.5 Certificate 与 session

当前 `uploadCertificate(index, hash)` 的 index 没有链上 session 结构。需要明确：

- index 是否等于直播序号；
- 同一 session 可上传多少份凭证；
- 凭证与排期、提案和 EvidenceBundle 的关系。

### 4.6 签名账户类型

当前只验证 EOA ECDSA。Clef 可继续使用 EOA 地址，但 AA/智能合约钱包需要 EIP-1271 或其他 account abstraction 适配。

### 4.7 UTO 单位

合约只接受部署参数 `ratePerUser`，没有写死 365 或 52。需要共同固定：

- 最小精度；
- rate 的时间单位；
- UI/接口的换算规则；
- 初始 checkpoint 的来源。

### 4.8 尚未进入 M2 的治理能力

以下能力没有被当前代码宣称为已完成：

- 见证者主动退出；
- 随机抽样；
- 黑名单、证据和申诉；
- 反对票三 Agent 复核；
- 失败提案超时关闭；
- 多企业身份和权限。

这些能力需要独立规格，不能通过修改当前接口含义隐式加入。

## 5. 后续接口协作规则

- 原始 `docs/reference/source-materials/current/UAS接口文档.md` 保持只读。
- 已实现 ABI 以 `docs/api/loveengine-contract-api.md` 和 Solidity interface 为准。
- 任何 ABI、event、typehash 或治理语义变更必须同时更新：
  - Solidity interface 和测试；
  - Python typed-data builder；
  - API 文档和当前 SPEC；
  - Skill manifest source hash。
- 兼容性破坏必须提升协议/Skill 版本，并提供旧 transcript 的解释路径。
