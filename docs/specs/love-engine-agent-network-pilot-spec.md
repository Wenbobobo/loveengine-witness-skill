# LoveEngine M2 收口与 M3 Agent 网络试点 SPEC

状态：`planned`  
目标版本：`0.3.0-network-pilot`  
依赖版本：`0.2.0-local-loop`

## 1. 核心目标

M3 将 M2 的单机本地闭环扩展为三个独立 Agent 节点的可验证网络试点。

系统采用：

```text
SkillRegistry / 四核心合约
              ↓
       Chain Event Watcher
              ↓
          Relay Hub
        ↙     ↓     ↘
    Agent A Agent B Agent C
```

- 链上保存 Skill 版本和 UAS 业务事实。
- Relay Hub 负责中心转发、离线队列和 artifact 分发。
- NetworkTask 和 TaskReceipt 使用 EIP-712 端到端签名。
- Agent 全部主动出站连接，不依赖公网 IP、端口映射或 NAT 穿透。
- 本阶段不实现 P2P、直连、自动投票或真实直播。

## 2. Gate 0：M2 正式收口

### 2.1 可复现验证

- 使用全新环境运行全部 Python 测试。
- 使用 Foundry 1.7.1 运行合约测试。
- 运行真实五节点 Anvil E2E。
- 验证 transcript hash 和秘密扫描。

### 2.2 文档与接口校准

- 编写 `docs/development/contract-team-handoff.md`。
- 修正 M0/M2 manifest 路径、SHA-256/Keccak 和 CLI 参数示例。
- 使本 SPEC 的目录说明与实际 flat Python package 一致。
- 补充错误 signer 和错误 proposalId 合约测试。

### 2.3 远端交付

- 推送 M2 收口分支并创建 PR。
- Python、Foundry 和 Anvil CI 全绿。
- 合并到 `main`。
- 创建 `v0.2.0-local-loop` tag。

验收：从远端干净 clone 可完成完整测试和 E2E。

## 3. M3.1：链上版本信任

新增辅助合约 `SkillRegistry`。它不计入四个 UAS 核心合约。

To-do：

- Publisher 命名空间。
- 发布、current version、deprecated、revoked 和 replacement version。
- 完整 event 和 view API。
- Python Registry client。
- manifest 记录 chain、Registry、Publisher 和 release hash。
- artifact 下载后对照链上 package/manifest hash。

验收：

- 篡改包被拒绝。
- 错误 Publisher 被拒绝。
- deprecated 版本不给新任务。
- revoked 版本始终拒绝。

## 4. M3.2：节点身份与 Bootstrap

To-do：

- `SignedAgentNodeProfileV1` schema 和 typed-data。
- Publisher 签名的 `BootstrapBundleV1`。
- 节点 profile 自签名。
- sequence、validUntil 和目录 hash。
- Bootstrap 轮换和旧 sequence 拒绝。

验收：

- 三个节点验证同一 bootstrap。
- 修改 profile、endpoint、目录或签名后校验失败。
- 过期和旧 sequence 被拒绝。

## 5. M3.3：Relay Hub

技术边界：

- HTTP 提供 health、bootstrap、release 和 artifact。
- WebSocket 提供 challenge、authenticate、task、receipt、ack、heartbeat 和 error。
- SQLite 保存离线消息和 cursor。
- at-least-once delivery；节点端幂等。

To-do：

- `RelayTransport` 抽象和实现。
- challenge-response 节点认证。
- artifact 按 package hash 提供。
- 离线队列、ack、重试和 cursor 恢复。
- 结构化日志和 connected/queued/delivered/acked/rejected/latency 指标。

验收：

- 三节点只使用出站连接即可上线。
- 节点掉线重连后收到未确认任务。
- 重复投递只执行一次。
- Relay 修改任务后节点拒绝。

## 6. M3.4：链上事件任务桥接

To-do：

- 监听 `SkillRegistry` release event。
- 监听 `CorporateSink.BroadcastScheduled`。
- 生成 `propagate_skill` 和 `observe_broadcast`。
- 签名任务绑定 issuer、recipient、manifest、payload、nonce 和 deadline。
- 节点生成签名 TaskReceipt。
- 以 chainId、txHash、logIndex 保证事件幂等。

验收：

- 新 release 触发 Skill 传播任务。
- 直播排期触发观察任务。
- wrong chain、Registry、recipient、nonce、deadline 和 payload 均失败。
- 没有任何任务请求投票签名。

## 7. M3.5：三节点端到端

To-do：

1. Anvil 部署四核心合约和 SkillRegistry。
2. 测试 Publisher 发布 `0.3.0-network-pilot`。
3. 启动 Relay Hub。
4. 启动三个独立 Agent 进程。
5. 三节点获取 artifact 并校验链上 hash。
6. 完成 Skill 传播回执。
7. 触发 `BroadcastScheduled`。
8. 三节点完成 observe 回执。
9. 模拟离线、恢复、重复投递和 deprecated release。
10. 生成 `NetworkTranscriptV1`。

验收产物：

- 三个已认证节点。
- 两类任务的签名和回执。
- 链上 release、业务 event 和链下消息可相互反查。
- transcript hash 可复算且无秘密。
- CLI 输出网络指标。

## 8. 数据与安全约束

- JSON 使用 canonical JSON v1。
- 文件 hash 使用 SHA-256。
- EIP-712 payload 使用 Keccak-256 bytes32。
- EVM 数值使用十进制字符串。
- 所有签名绑定 chainId 和 SkillRegistry 地址。
- 私钥只存在于隔离测试 signer，不进入 Agent context、Relay、日志或 transcript。
- Relay Hub 不是信任根，不能代签任务或回执。
- M3 不传输敏感 Evidence；端到端加密延后。

## 9. 测试矩阵

必须覆盖：

- Registry 非法发布、重复版本和错误状态迁移。
- Publisher、bootstrap、profile、task 和 receipt 签名篡改。
- wrong chainId、Registry、recipient、nonce、deadline 和 payload。
- 离线补发、重复投递和 cursor 恢复。
- Relay 伪造或修改消息。
- deprecated/revoked 版本拒绝。
- 三节点传播和链上事件任务。
- 日志、fixture 和 transcript 秘密扫描。

## 10. 完成定义

```powershell
uv run python .\tools\check.py
uv run pytest -m "not integration"
cd contracts
forge test
cd ..
uv run pytest tests/test_demo.py
uv run pytest tests/integration/test_network_demo.py
uv run loveengine network demo --nodes 3 --output examples/transcripts
uv run loveengine network transcript verify <path>
```

不能以单元测试通过代替三节点 E2E。

