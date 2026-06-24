# 合约团队 v2 版本对比与建议

状态：活跃交接说明  
基线版本：`v0.5.0-lan-pilot` / 分支 `feat/loveengine-m6-contract-public-pilot`  
合约团队快照：`docs/reference/contracts/contract-team-v2/contracts/`  
当前目标实现：`contracts/src/`

本说明旨在连同额外的 `SkillRegistry` 合约一并发回给合约团队。它不会修改已保留的 `contract-team-v2` 源文件。

## 执行建议

将合约团队的版本作为业务语言参考，而非直接替换使用。

v2 合约编译成功并包含有价值的改进：

- 更清晰的中文 NatSpec；
- `CorporateSink` 中的显式 `Broadcast[]` 历史记录；
- 中继者 gas 退款概念；
- 具体的 24 小时投票窗口概念；
- 更清晰的补偿和实时证书公共 getter 名称。

然而，直接替换将移除已在 LoveEngine M2-M5 中验证的协议安全性：

- 注册签名需要 `nonce` 和 `deadline`；
- 投票签名需要 `proposalId`、`payloadHash`、`reasonHash`、`nonce` 和 `deadline`；
- 中继者必须能够提交签名而不必成为受信任签名者；
- 提案 payload 必须绑定链下证据哈希；
- 治理阈值和时间值应保持可配置；
- 即使 M6 ABI 演变，旧的记录仍必须可解释。

## 各合约对比

| 合约 | 建议 | 原因 |
| --- | --- | --- |
| `WitnessDAO` | 修改后使用 | 结构良好且有投票窗口意图，但当前 v2 签名对于 Agent/中继者操作来说太弱。 |
| `CorporateSink` | 采纳选定部分 | 索引化的广播列表很有用。保留 LoveEngine 的 `liveMetadataHash`、证据哈希和可配置窗口。 |
| `StreamingEngine` | 保留当前核心，增加命名清晰度 | 两个版本都在速率变更前进行检查点。LoveEngine 应保留可配置的 `ratePerUserPerSecond`。 |
| `PublicSink` | 保留当前基于接口的实现 | v2 仍为只读，但接口调用比字符串 `staticcall` 更安全、更清晰。 |
| `SkillRegistry` | 作为辅助协议合约添加 | 不属于四个 UAS 业务合约；用于技能发布信任。 |

## 采用 `WitnessDAO` 前需进行的修改

### EIP-712 注册签名

v2 快照签名：

```solidity
Register(address witness)
```

推荐目标：

```solidity
Register(address witness,uint256 nonce,uint256 deadline)
```

原因：没有 nonce/deadline，有效的注册签名可以在任何未来的中继者提交中被重放，只要域仍然有效。

### EIP-712 投票签名

v2 快照签名：

```solidity
Vote(bool support,uint256 proposalId)
```

推荐目标：

```solidity
Vote(
    address witness,
    uint256 proposalId,
    bool support,
    bytes32 reasonHash,
    bytes32 payloadHash,
    uint256 nonce,
    uint256 deadline
)
```

原因：

- `proposalId` 防止用于另一个提案。
- `payloadHash` 绑定确切的提案参数和证据。
- `reasonHash` 保留可审计的反对背景。
- `nonce` 防止重放。
- `deadline` 防止签名无限期保存。
- EIP-712 域绑定 chainId 和 verifyingContract。

### 重复投票

v2 快照跳过重复投票：

```solidity
if (hasVoted[pid][sig.witness]) {
    continue;
}
```

推荐目标：对重复投票进行回退。

原因：跳过的重复投票会隐藏中继者或客户端的错误。对于可审计的见证协议，错误提交应可被观察到。

### 活跃提案状态

v2 快照使用 `activeProposalId = 0` 同时作为有效数组索引和"无活跃提案"的哨兵值。

推荐目标：

- 提案 ID 从 `1` 开始，或
- 保留显式 `active` 字段，且不使用 `0` 作为有效的活跃 ID。

### 投票窗口与失败结算

v2 快照定义了 24 小时间隔，但尚未提供完整的失败结算流程。

推荐目标：

- `createdAt`；
- `votingDeadline`；
- `finalizeProposal(proposalId)`；
- `ProposalFinalized(proposalId, passed, totalVotes, supportVotes)`；
- `ProposalFailed(proposalId, totalVotes, supportVotes)`；
- 失败后，清除活跃提案，以便后续有效提案可以开始。

### 治理常量

v2 快照硬编码：

- `MIN_VOTES_REQUIRED = 69`；
- 90% 批准率；
- 两周广播间隔；
- 两小时提案窗口。

推荐目标：将这些作为部署或治理参数保留。

原因：M2-M6 测试使用小型本地见证集，而生产环境可能使用 69 或其他批准的法定人数。硬编码值使得使用相同代码路径验证本地、测试网和生产配置更加困难。

### DAO 初始化

v2 快照在子合约上暴露了 `initializeDAO(address)`，但未限制为引导者/管理员。

推荐目标：

- 仅初始化一次；
- 拒绝零地址；
- 调用者必须是引导者或配置的管理员；
- 发出初始化事件。

### 中继者 gas 退款

v2 快照在 `batchRegister` / `batchVote` 期间使用直接的 ETH 转账。

推荐目标：

- 中继者仍可无许可提交；
- 退款资格与提交权限分离；
- 将退款记入可提取余额；
- 每次调用设置退款上限；
- 避免在投票处理期间进行易受重入攻击的推送支付。

## `CorporateSink` 建议

采纳：

- 结构化的 `Broadcast[]`；
- 基于索引的广播查询；
- 每个广播的证书状态。

保留 LoveEngine 的：

- `scheduleBroadcast(uint256 timestamp, bytes32 liveMetadataHash)`；
- 可配置的最小广播间隔和提案窗口；
- `recordCompensation(uint256 amount, bytes32 requestHash, bytes32 evidenceBundleHash)`；
- EvidenceBundle 哈希绑定。

推荐的证书形状：

```solidity
function uploadCertificate(
    uint256 index,
    bytes32 certificateHash,
    bytes32 sessionIdHash,
    bytes32 evidenceBundleHash
) external;
```

原始双参数形式可作为兼容性重载保留。

## `StreamingEngine` 建议

保留检查点模型。当前 M6 目标暴露了：

- `ratePerUser()`；
- `ratePerUserPerSecond()`。

实际单位应记录为类似 wei 的 UTO 精度每秒。不要将年度或日度的业务常量写入合约。

## `PublicSink` 建议

保留当前只读接口：

```solidity
function getTotalUTO() external view returns (uint256);
```

不应有 owner 修改接口。优先使用类型化接口而非基于字符串的 `staticcall`。

## SkillRegistry 添加

LoveEngine 添加一个辅助协议合约：

```text
contracts/src/SkillRegistry.sol
```

`SkillRegistry` 不是四个 UAS 业务合约之一。它记录技能发布事实：

- `publisher`；
- `skillId`；
- `versionHash`；
- `packageHash`；
- `manifestHash`；
- `previousVersionHash`；
- `status`；
- `replacementVersionHash`。

为什么必要：

1. Agent 可以从 Relay、GitHub、S3、IPFS 或其他镜像下载技能 ZIP。
2. 镜像不被信任。
3. Agent 在安装或执行前，将 `packageHash` 与链上记录进行验证。
4. 已弃用/撤销的版本可以在不依赖中央网络服务器的情况下被拒绝。

发布者命名空间是独立的；没有全局所有者。每个发布者只管理自己的发布。

## 当前 M6 集成状态

M6 分支保留了当前安全的 ABI，并已开始吸收 v2 功能：

- 提案 `createdAt` 和 `votingDeadline`；
- `finalizeProposal`；
- 可观察的失败提案结算；
- 结构化广播元数据 getter；
- 证书绑定到会话和证据哈希；
- 安全的中继者退款记账及显式提款；
- `ratePerUserPerSecond` 命名别名。

Foundry 测试在 ABI 被视为稳定之前涵盖了新的失败和兼容性行为。