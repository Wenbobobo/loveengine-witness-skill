# UAS Smart Contracts API 文档

本文档整理了 UAS 流支付系统中各个智能合约对外的核心接口，以便前端应用、Relayer（中继节点）以及支付公司后台进行对接集成。

---

## 1. 见证者治理入口 (`WitnessDAO.sol`)

负责处理见证者的 712 签名注册、投票，以及支付公司的提案申请。

### EIP-712 签名数据结构
```solidity
// 注册签名结构 (由想成为见证者的 Agent 私钥离线签名)
struct RegisterSignature {
    address witness; // Agent 的地址
    uint8 v; bytes32 r; bytes32 s;
}

// 投票签名结构 (由见证者私钥离线签名)
struct VoteSignature {
    address witness; // 见证者地址
    bool support;    // 是否赞成 (true / false)
    uint8 v; bytes32 r; bytes32 s;
}
```

### Relayer (公共) 调用接口
- **`batchRegister(RegisterSignature[] calldata sigs)`**
  - **说明**：批量提交见证者的注册签名。
  - **权限**：无限制（任何人可调用代付 Gas）。
  - **业务逻辑**：合约会使用 EIP-712 校验签名是否来自 `witness` 本人，验证通过即注册。

- **`batchVote(VoteSignature[] calldata sigs)`**
  - **说明**：批量提交对**最新活跃提案**的投票。
  - **权限**：无限制。
  - **业务逻辑**：如果该批投票使得总票数达到 69 票以上，且赞成率 >= 90%，提案将在该笔交易中被自动执行。

- **`getDomainSeparator() returns (bytes32)`**
  - **说明**：获取 EIP-712 的 `DOMAIN_SEPARATOR`，用于前端或后端组装离线签名的哈希结构。

### 支付公司调用接口
- **`proposeUserCount(uint256 newUserCount)`**
  - **说明**：发起“更新用户数”的提案。
  - **权限**：仅限 `corporateAdmin`。
  - **限制**：只能在直播排期时间到达后的 2 小时窗口期（`BROADCAST_WINDOW`）内发起。

- **`proposeCompensation(uint256 amount)`**
  - **说明**：发起“申请成本补偿”的提案。
  - **权限**：仅限 `corporateAdmin`。
  - **限制**：同上，必须在排期窗口内。

---

## 2. 企业与直播账本 (`CorporateSink.sol`)

负责管理直播排期、凭证上传以及追踪已获批的补偿。

### 支付公司调用接口
- **`scheduleBroadcast(uint256 _timestamp)`**
  - **说明**：登记下一次直播的时间戳。
  - **权限**：仅限 `corporateAdmin`。
  - **限制**：两次排期之间必须至少间隔 2 周 (`MIN_BROADCAST_INTERVAL`)。

- **`uploadCertificate(uint256 _index, bytes32 _hash)`**
  - **说明**：上传某次直播的证明/放弃商业利润协议的哈希值。
  - **参数**：`_index` 是直播记录的索引（第一次排期为 0，第二次为 1），`_hash` 是凭证哈希。
  - **限制**：仅能在排期时间到达**之后**上传。

### 公共查询接口
- **`nextBroadcastTime() returns (uint256)`**
  - **说明**：查询下一次（最新一次）预告的直播时间戳。
  
- **`getCorporateCSR(uint256 _index) returns (bytes32)`**
  - **说明**：根据索引查询对应排期上传的凭证哈希值。
  
- **`getCompensation() returns (uint256)`**
  - **说明**：获取支付公司**累计被 90% 见证者投票批准**的总补偿 UTO 额度。


---

## 3. 公共只读代理 (`PublicSink.sol`)

面向全网公开的、最纯粹的查询入口。

- **`getTotalUTO() returns (uint256)`**
  - **说明**：实时计算并返回全系统自基准时间以来，每一秒累加出的 UTO 总额。
  - **特点**：此合约没有 Owner，不可篡改，是官网大屏前端首选的数据抓取地址。

---

## 4. 底层流计算引擎 (`StreamingEngine.sol`)

系统的心脏，通常不需要外部应用直接交互，完全由 `WitnessDAO` 掌控状态。

### 公共查询接口
- **`getCurrentBalance() returns (uint256)`**
  - **说明**：读取当前的实时总额。公式：`历史基线余额 + (当前时间 - 上次更新时间) * 实时流速`。

- **`rate() returns (uint256)`**
  - **说明**：读取当前的每秒 UTO 释放流速（基于上次投票通过的用户数换算得出）。

### 内部特权接口
- **`updateUserCount(uint256 newUserCount)`**
  - **说明**：根据新用户数变轨计算，只能由 `WitnessDAO` 自动调用。
