# LoveEngine M1/M2 Local Witness Loop SPEC

状态：`implemented`
目标版本：`0.2.0-local-loop`  
依赖基线：`0.1.1-m0`

## 1. 目标和非目标

本 SPEC 定义 M1 协议基础和 M2 本地见证闭环。完成后，开发者或 Agent 可以从 manifest 校验开始，在本地链完成注册、排期、证据、提案、投票、执行、查询和 transcript 验证。

不接入真实直播、生产密钥、真实企业身份、UHAH、Web UI 或公网 Agent discovery。

## 2. 技术结构

```text
src/loveengine_witness/
  canonical.py
  cli.py
  demo.py
  errors.py
  evidence.py
  fixtures.py
  hashes.py
  jsonio.py
  manifest.py
  node_profile.py
  relayer.py
  schema.py
  secrets.py
  toolchain.py
  transcript.py
  typed_data.py
schemas/
contracts/
tests/
examples/transcripts/
```

纯协议模块不能依赖 Foundry、Cast、Anvil、HTTP 或具体 Agent runtime。`demo.py` 和 `toolchain.py` 是明确的本地集成适配层。

## 3. 序列化与 hash

- Schema 使用 JSON Schema Draft 2020-12。
- canonical JSON v1：UTF-8、对象 key 排序、无多余空白、`ensure_ascii=false`。
- 文件和 Skill 包 hash：SHA-256，格式 `sha256:<hex>`。
- 链上 `payloadHash`、`evidenceBundleHash`、`reasonHash`：canonical JSON 或原始内容的 Keccak-256，格式 `0x<64 hex>`。
- EVM 数值在 JSON 中使用十进制字符串。
- 时间使用 UTC RFC 3339。
- 地址输入必须为 20-byte EVM 地址；输出使用 checksum 地址。

## 4. 数据模型

### SkillManifestV2

必填：

- `schema_version`
- `skill_id`
- `version`
- `status`
- `protocol`
- `source_refs`
- `source_hashes`
- `commands`
- `contracts`
- `eip712`
- `evidence`
- `security`
- `package_hash`

`security.private_keys_in_agent_context` 必须为 `false`。

### AgentNodeProfileV2

包含节点 ID、runtime、Skill 版本、地址、signer 类型、能力、endpoint、availability 和 policy。

必须满足：

- `private_key_available_to_agent=false`
- `policy.can_modify_source_materials=false`
- signer 类型仅允许 `clef`、`cast`、`browser_wallet`、`aa`、`anvil-test`

### EvidenceBundleV1

包含 bundle/session ID、proposal type、subject、source refs、attachments、summary、created_at、artifact SHA-256 和链上 payload Keccak-256。

原始证据只存路径或 URI 和 hash；Agent 摘要必须标记 `kind=summary`。

### LocalLoopTranscriptV1

包含 run ID、manifest hash、chain ID、合约地址、事件、证据包、提案、投票摘要、最终状态和 transcript 自身 hash。

不得包含私钥、mnemonic、keystore 内容、认证 token 或完整 RPC 凭据。

## 5. CLI

入口由 `pyproject.toml` 的 `[project.scripts]` 提供：

```text
loveengine = loveengine_witness.cli:main
```

命令：

```text
loveengine manifest verify [--manifest PATH]
loveengine node declare --config PATH [--output PATH]
loveengine fixture generate --witnesses N --output DIR
loveengine evidence build --session PATH --output PATH
loveengine transcript verify TRANSCRIPT
loveengine eip712 register-message --input PATH
loveengine eip712 vote-message --proposal-id ID --input PATH
loveengine relayer batch-register --input PATH --dry-run
loveengine relayer batch-vote --input PATH --dry-run
loveengine demo local-loop --config PATH --output DIR
```

成功时 stdout 只输出 JSON。错误时 stderr 输出：

```json
{
  "error": {
    "code": "stable_machine_code",
    "message": "human readable message"
  }
}
```

退出码：

- `0` 成功
- `2` 参数或 schema 错误
- `3` 文件或环境错误
- `4` signer、RPC 或链上错误

## 6. 合约

### StreamingEngine

- 保存 `checkpointTime`、`baseBalance`、`rate`。
- `getCurrentBalance()` 返回 checkpoint 后的累计值。
- `updateUserCount()` 只能由 WitnessDAO 调用，并在修改 rate 前结算旧余额。
- rate-per-user 是部署参数，不把 365 或 52 写死为业务常量。

### PublicSink

- 构造时绑定不可变 StreamingEngine 地址。
- 只暴露 `getTotalUTO()`。
- 不提供 owner、升级或状态修改入口。

### CorporateSink

- `scheduleBroadcast(timestamp, liveMetadataHash)`
- `uploadCertificate(index, hash)`
- `nextBroadcastTime()`
- `getCorporateCSR(index)`
- `getCompensation()`
- 只有 corporate admin 可排期和上传。
- 只有 WitnessDAO 可记录批准的补偿。
- 最小排期间隔和提案窗口是部署参数。

### WitnessDAO

- `batchRegister(RegisterSignature[])`
- `batchVote(VoteSignature[])`
- `proposeUserCount(newUserCount, evidenceBundleHash)`
- `proposeCompensation(amount, requestHash, evidenceBundleHash)`
- `activeProposalId()`
- `getDomainSeparator()`

RegisterSignature 绑定 witness、nonce、deadline、chainId 和 verifyingContract。

VoteSignature 绑定 witness、proposalId、support、reasonHash、payloadHash、nonce、deadline、chainId 和 verifyingContract。

达到 `minValidVotes` 且赞成率达到 `approvalThresholdBps` 时，可以在 `batchVote` 中 finalize 并 execute。任何地址都可提交 batch。

## 7. 本地闭环

1. 校验 M1 manifest。
2. 启动 Anvil 并部署四合约。
3. 生成测试专用见证者身份，私钥只存在于隔离测试进程。
4. 构建并签署注册 typed data。
5. relayer 调用 `batchRegister`。
6. corporate admin 调用 `scheduleBroadcast`。
7. CLI 生成 EvidenceBundle。
8. corporate admin 提交用户数提案。
9. 见证者签署 VoteSignature。
10. relayer 调用 `batchVote`。
11. WitnessDAO 执行 StreamingEngine 更新。
12. 查询 PublicSink。
13. 生成并验证 transcript。

快速 demo 使用 5 个见证者。Foundry 单独提供 69 签名批量规模测试。

## 8. 测试矩阵

Python：

- schema 正反例。
- canonical JSON 稳定性。
- SHA-256 与 Keccak-256 固定向量。
- M0 manifest 兼容。
- 私钥字段扫描。
- CLI stdout/stderr 和退出码。
- transcript hash 与引用校验。

Foundry：

- domain separator。
- register nonce、deadline、错误 signer。
- vote proposalId、payloadHash、nonce、deadline。
- 非见证者和重复投票。
- 低于最小票数或 90% 不执行。
- 69 签名批量测试。
- 排期间隔、窗口前、窗口内和窗口后。
- StreamingEngine checkpoint。
- PublicSink ABI 不含 mutation。

端到端：

- demo 能完成全部 13 步。
- transcript 能反查 manifest、EvidenceBundle、事件和最终总账。
- 所有输出通过私钥、mnemonic 和 token 扫描。

## 9. 完成定义

M1 完成：

```powershell
uv run pytest
uv run loveengine manifest verify
```

M2 完成：

```powershell
forge test
uv run loveengine demo local-loop
uv run loveengine transcript verify examples/transcripts/local-loop.fixture.json
```

以上命令必须进入 CI。不能以“接口已写但 demo 未闭环”声明 M2 完成。
