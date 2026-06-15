# LoveEngine Witness Skill 下一阶段规格

整理者说明：本文评审 `0.1.0-m0` 第一版，并给出下一阶段开发规格。它不替代 `LoveEngineSkill/` 下的原始记录，也不改写 `docs/specs/love-engine-skill-spec.md`。后续开发应先读第一版规格，再读本文。

## 1. 当前判断

第一版已经完成了正确的起点：LoveEngine 不是先做成一个中心化福利网站，而是先做成可被 Agent 网络安装、校验、传播和运行的 Witness Skill。这个判断符合最新方向，也修正了早期容易滑向“大平台产品”的问题。

但它还不是可运行的 LoveEngine。当前版本更准确地说是 M0：一个可信入口和传播原型。它能让新 Agent 知道读什么、校验什么、不能做什么，并能用固定 fixture 表达“我具备见证节点的最低能力”。它还不能完成链上注册、直播见证、证据打包、EIP-712 签名、批量投票、流账本更新或企业补偿。

下一阶段的目标不是直接做完整系统，而是把 M0 推进到 M1/M2：让本地开发者或 Agent 能跑出一条最小闭环，从 manifest 校验开始，到本地链上的排期、提案、投票、执行和总账查询结束。只要这条链跑通，后面的直播、多 Agent 协作、Hermes/Harness 接入才有可靠地基。

## 2. 第一版完成情况

已完成的部分：

- `docs/specs/love-engine-skill-spec.md` 是当前主规格，已把方向收敛到 Agent 网络传播和 UAS 见证协议。
- `skills/loveengine-witness/skill-manifest.json` 已定义 Skill ID、版本、源资料 hash、能力、权限、治理参数、签名边界和 relayer 策略。
- `skills/loveengine-witness/agent-onboarding.md` 已提供新 Agent 的最小导读，明确私钥不进入 Agent context。
- `skills/loveengine-witness/fixtures/agent-node-profile.fixture.json` 已提供节点能力声明样例。
- `skills/loveengine-witness/fixtures/propagation-task.fixture.json` 已提供 `propagate_skill` 任务样例，并绑定 manifest hash。
- `tools/validate_loveengine_m0.py` 已覆盖 source hash、spec hash、能力、权限、治理参数、投票签名绑定、PublicSink 只读策略和 manifest 篡改检查。
- `tools/loveengine_m0_self_check.py` 已能输出本地节点能力摘要。

需要注意的细节：

- manifest 使用 `skill_id` 作为主键，而不是通用 `id`。这可以接受，但下一阶段要固定字段名，不能让不同 Agent 自行猜测。
- `docs/kb/source-inventory.md` 已把 M0 文件纳入资料清单，但下一阶段规格也需要纳入，避免后来者只读第一版。
- 当前 M0 绑定了协作者的合约接口文档，同时对 `batchVote` 做了必要安全修正：签名必须绑定 `proposal_id`、nonce、deadline 和 payload hash，不能只对“最新活跃提案”签名。

## 3. 目前能实现的效果

当前版本可以实现四件事：

1. 让新 Agent 快速进入语境。它不需要重读全部源文件，就能知道 LoveEngine Witness Skill 的入口、边界和当前焦点。
2. 让一个节点声明自己具备传播和见证前置能力。声明还只是 fixture，不是注册协议。
3. 让其他 Agent 校验 Skill 包是否被篡改。这里已经有 source hash、spec hash、package hash 和篡改拒绝逻辑。
4. 让开发方向避免跑偏。它明确禁止把第一版做成钱包、交易所、中心化福利平台或 UHAH 全量产品。

当前版本不能实现这些效果：

- 不能在链上注册见证者。
- 不能生成或校验真实 EIP-712 签名。
- 不能提交 `batchRegister` 或 `batchVote`。
- 不能创建直播排期、证据包或提案。
- 不能运行四合约闭环。
- 不能验证 90% 投票阈值、2 小时窗口、2 周间隔、流账本累计。
- 不能完成 Agent 间发现、同步、信誉、黑名单或争议处理。

所以第一版是合格的 M0，但不能被描述成可用的业务系统。

## 4. 下一阶段目标

下一阶段命名为 `M1/M2 Local Witness Loop`。

目标：在本地环境中跑通一条最小、可验证、可复现的 LoveEngine 见证闭环。

闭环顺序：

```text
manifest 校验
-> 节点声明
-> 本地链部署四合约
-> 见证者注册签名
-> relayer batchRegister
-> 企业排期 scheduleBroadcast
-> 证据包生成 EvidenceBundle
-> 企业提交用户数或补偿提案
-> 见证者投票签名
-> relayer batchVote
-> WitnessDAO finalize/execute
-> StreamingEngine 更新流速
-> PublicSink getTotalUTO 查询
-> 生成可审计 transcript
```

这个闭环不要求真实直播，也不要求真实 Agent 网络。它只要求所有接口、数据结构、签名、状态迁移和审计记录都按未来真实系统的形状运行。

## 5. 非目标

下一阶段不做这些事：

- 不做完整网页产品。
- 不做 UHAH。
- 不做多企业、多币种、多地区结算。
- 不做生产级身份系统。
- 不做真实随机数或复杂抽样治理。
- 不做 AI 自动裁决争议。
- 不让 Agent 持有或暴露私钥。
- 不让 UTO 变成可交易资产。

这些都重要，但现在做会稀释主线。

## 6. 交付物

### 6.1 规格和索引

- 新增本文为下一阶段主规格。
- 更新 `docs/kb/source-inventory.md`，把本文标为 M1/M2 规格入口。
- 更新 `docs/kb/sources.json`，加入机器可读条目。

### 6.2 Skill 包升级

将 `skills/loveengine-witness/skill-manifest.json` 升级到 `0.2.0-local-loop` 草案。保留 `0.1.0-m0` 的源 hash 和安全边界，新增：

- `schema_version`
- `skill_id`
- `version`
- `protocol`
- `source_refs`
- `source_hashes`
- `artifacts`
- `commands`
- `contracts`
- `eip712`
- `evidence`
- `test_matrix`

字段名必须稳定。后续 Agent 只认 `skill_id`，不再兼容另一个 `id` 字段。

### 6.3 合约原型

建议新增 `LoveEngineSkill/prototype/contracts/` 或独立工程 `loveengine-witness-prototype/`。如果继续放在当前资料库，必须把它标记为原型，不要和原始材料混在一起。

合约包括：

- `WitnessDAO.sol`
- `StreamingEngine.sol`
- `PublicSink.sol`
- `CorporateSink.sol`
- `interfaces/IWitnessDAO.sol`
- `interfaces/IStreamingEngine.sol`
- `interfaces/IPublicSink.sol`
- `interfaces/ICorporateSink.sol`

建议用 Foundry。原因很简单：这里的风险在签名、时间窗口和状态迁移，Foundry 对这些测试更直接。

### 6.4 CLI 和脚本

新增 `tools/loveengine/`，提供命令：

```text
loveengine manifest verify
loveengine node declare
loveengine fixture generate --witnesses 69
loveengine evidence build --session <id>
loveengine eip712 register-message
loveengine eip712 vote-message --proposal <id>
loveengine relayer batch-register --dry-run
loveengine relayer batch-vote --dry-run
loveengine transcript build
```

早期可以用 Python 实现。以后要接 Hermes/Harness，再把这些命令包成 Tool Adapter。

### 6.5 本地演示

提供一个一键脚本：

```text
loveengine demo local-loop
```

它应输出：

- deployed contract addresses
- registered witness count
- broadcast schedule
- evidence bundle hash
- proposal id
- vote count
- approval ratio
- executed action
- current total UTO
- transcript path

## 7. 数据模型

### SkillManifestV2

```json
{
  "schema_version": "loveengine.skill-manifest/0.2",
  "skill_id": "loveengine-witness",
  "version": "0.2.0-local-loop",
  "status": "draft",
  "protocol": "loveengine-witness-net/0.2",
  "source_refs": [],
  "source_hashes": {},
  "commands": {},
  "contracts": {},
  "eip712": {},
  "evidence": {},
  "security": {}
}
```

必填规则：

- `skill_id` 必须是 `loveengine-witness`。
- `status` 在原型期只能是 `draft`。
- `source_refs` 必须至少包含第一版规格、本文、合约接口文档、见证方案 2.0 和 source inventory。
- `source_hashes` 必须覆盖所有 `source_refs`。
- `security.private_keys_in_agent_context` 必须为 `false`。

### AgentNodeProfileV2

```json
{
  "node_id": "loveengine-local-001",
  "agent_runtime": "codex",
  "skill_id": "loveengine-witness",
  "skill_version": "0.2.0-local-loop",
  "address": "0x...",
  "signer_type": "cast",
  "capabilities": [],
  "network_endpoints": [],
  "policy": {
    "can_request_signature": true,
    "can_submit_transaction": true,
    "can_modify_source_materials": false
  }
}
```

### EvidenceBundleV1

```json
{
  "bundle_id": "uas-session-<date>-<seq>",
  "session_id": "uas-live-001",
  "proposal_type": "USER_COUNT",
  "subject": {
    "corporate": "0x...",
    "new_user_count": "1000000"
  },
  "source_refs": [],
  "attachments": [],
  "transcript_hash": "sha256:...",
  "payload_hash": "sha256:...",
  "created_at": "2026-06-15T00:00:00+08:00"
}
```

M1 可以用 fixture 文本生成 evidence。M2 必须把 `payload_hash` 写入提案签名和链上事件。

### LocalLoopTranscriptV1

```json
{
  "run_id": "local-loop-001",
  "manifest_hash": "sha256:...",
  "chain_id": 31337,
  "contracts": {},
  "events": [],
  "evidence_bundle": {},
  "proposal": {},
  "votes": [],
  "final_state": {}
}
```

transcript 是下一阶段的关键产物。它让人和 Agent 都能复盘这次闭环，而不是只看一句“测试通过”。

## 8. 合约规格

### WitnessDAO

必须支持：

- `batchRegister(RegisterSignature[] calldata sigs)`
- `batchVote(VoteSignature[] calldata sigs)`
- `getDomainSeparator() returns (bytes32)`
- `proposeUserCount(uint256 newUserCount, bytes32 evidenceBundleHash)`
- `proposeCompensation(uint256 amount, bytes32 requestHash, bytes32 evidenceBundleHash)`
- `activeProposalId()`

`RegisterSignature` 必须绑定：

- `witness`
- `nonce`
- `deadline`

`VoteSignature` 必须绑定：

- `chainId`
- `verifyingContract`
- `proposalId`
- `support`
- `reasonHash`
- `payloadHash`
- `nonce`
- `deadline`

不能只对最新活跃提案签名。这个边界必须写进合约测试。

### StreamingEngine

必须支持：

- `getCurrentBalance()`
- `rate()`
- `updateUserCount(uint256 newUserCount)`

更新用户数时先结算历史累计值，再改流速。测试必须覆盖时间推进后的余额单调增长。

### PublicSink

必须支持：

- `getTotalUTO()`

PublicSink 不应有 owner 修改口。它是只读查询代理，不是治理入口。

### CorporateSink

必须支持：

- `scheduleBroadcast(uint256 timestamp, bytes32 liveMetadataHash)`
- `uploadCertificate(uint256 index, bytes32 hash)`
- `nextBroadcastTime()`
- `getCorporateCSR(uint256 index)`
- `getCompensation()`

必须约束：

- 直播排期最小间隔为部署参数，默认 2 周。
- 提案只能在排期时间之后的 2 小时窗口内发起。
- certificate hash 只存 hash，不把大文件上链。

## 9. Agent 行为规格

Agent 可以做：

- 读取 manifest、规格、接口文档和 fixture。
- 校验 source hash、manifest hash 和 package hash。
- 生成节点声明。
- 生成 evidence bundle。
- 生成待签名 EIP-712 typed data。
- 调用本地 signer 请求签名。
- 把签名交给 relayer。
- 汇总链上事件，生成 transcript。
- 向其他 Agent 传播 Skill 包和校验方法。

Agent 不可以做：

- 把私钥放进 prompt、日志、fixture 或 transcript。
- 替见证者做最终判断。
- 修改原始资料后仍宣称 package hash 不变。
- 在投票签名里省略 `proposalId`。
- 把反对票简单丢弃。
- 把 UTO 描述成可交易资产。

## 10. 测试矩阵

### Manifest

- 缺少任一 source ref，应失败。
- 任一 source hash 不匹配，应失败。
- 修改 manifest 后不更新 package hash，应失败。
- 出现 `store_private_key`、`sign_without_policy`、`write_source_materials` 权限，应失败。

### EIP-712

- chainId 错误，应失败。
- verifyingContract 错误，应失败。
- proposalId 错误，应失败。
- nonce 重放，应失败。
- deadline 过期，应失败。
- payloadHash 被替换，应失败。

### WitnessDAO

- 非见证者投票，应失败。
- 同一见证者重复投票，应失败。
- 票数未达 `min_valid_votes`，不得执行。
- 赞成率低于 90%，不得执行。
- 达到票数和阈值，应 finalize 并 execute。
- 反对票带 `reasonHash`，事件必须保留。

### CorporateSink

- 排期早于最小间隔，应失败。
- 窗口前提案，应失败。
- 窗口后提案，应失败。
- 窗口内提案，应成功。
- certificate index 重复行为必须明确，不能隐式覆盖。

### StreamingEngine / PublicSink

- 初始余额可查询。
- 时间推进后余额增长。
- 用户数更新前会 checkpoint。
- PublicSink 只能读 `getTotalUTO`。

### Local loop

- 一次 demo 能完成注册、排期、提案、投票、执行和查询。
- demo 输出 transcript。
- transcript 内的 hash 能反查到本地 fixture。

## 11. 架构建议

建议采用端口和适配器结构，避免把 Agent、合约、CLI 和本地 signer 混在一起。

```text
loveengine/
  domain/
    manifest.py
    node_profile.py
    evidence_bundle.py
    proposal.py
    vote.py
  use_cases/
    verify_manifest.py
    declare_node.py
    build_evidence.py
    build_typed_data.py
    submit_batch_vote.py
    build_transcript.py
  adapters/
    filesystem_sources.py
    foundry_contracts.py
    cast_signer.py
    local_relayer.py
  cli/
    main.py
```

domain 层不能依赖 Foundry、cast、HTTP 或具体 Agent runtime。这样未来接 Codex、Hermes、Harness、浏览器钱包或 AA signer，只需要换 adapter。

## 12. 验收标准

M1 验收：

- `loveengine manifest verify` 能验证 M0/M1 manifest。
- `loveengine node declare` 能生成节点声明，并明确私钥边界。
- `loveengine fixture generate --witnesses 69` 能生成一组本地见证者 fixture。
- `loveengine evidence build` 能输出 EvidenceBundle 和 payload hash。
- 所有 JSON fixture 可被 schema 校验。

M2 验收：

- Foundry 合约测试通过。
- 本地链能部署四合约。
- `batchRegister` 能注册 fixture 见证者。
- `scheduleBroadcast` 和提案窗口约束可测。
- `batchVote` 能在阈值达成后执行提案。
- `PublicSink.getTotalUTO()` 能返回更新后的累计值。
- demo 生成 transcript，包含合约地址、事件、证据 hash、投票摘要和最终状态。

## 13. 开发顺序

1. 固定 schema：ManifestV2、AgentNodeProfileV2、EvidenceBundleV1、TranscriptV1。
2. 写 schema 测试和 manifest 兼容测试。
3. 搭 Foundry 合约骨架。
4. 先实现 `StreamingEngine` 和 `PublicSink`。
5. 实现 `CorporateSink` 排期和凭证。
6. 实现 `WitnessDAO` 注册、提案、投票和执行。
7. 做本地 relayer dry-run。
8. 接 EIP-712 typed data 生成。
9. 接本地 signer。
10. 做 local-loop demo。
11. 生成 transcript。
12. 把 demo 输出纳入回归测试。

## 14. 待决问题

- `min_valid_votes` 初始值用 50 还是 69。当前建议：合约参数化，demo 默认 69，文档说明可降到 50。
- UTO 流速到底用 `365 UTO / 人 / 年` 还是 `52 UTO / 人 / 天`。当前建议：不要在合约里写死叙事数字，使用部署参数和单位说明。
- 见证者抽样由谁执行。M2 暂时不做真实抽样，只做注册集合和固定 fixture。
- 黑名单由谁维护。M2 只保留接口和事件，不做治理。
- 反对票证据如何被三个 Agent 交叉检查。M2 只保留 `reasonHash` 和 EvidenceBundle 引用。
- Hermes/Harness 如何接入。M2 先输出稳定 CLI 和 JSON transcript，后续再做 adapter。

## 15. 下一步

最合理的下一步是写 M1 schema 和 CLI，而不是直接写完整合约。原因是 Agent 网络传播依赖稳定数据形状；没有 schema，合约、relayer、skill manifest 和 transcript 会各说各话。

推荐马上做：

1. 新建 `tools/loveengine/`。
2. 实现 ManifestV2、NodeProfileV2、EvidenceBundleV1、TranscriptV1 的 JSON schema。
3. 把 `validate_loveengine_m0.py` 拆成可复用 validator。
4. 新增 M1 fixtures。
5. 再进入 Foundry 合约原型。

到 M2 结束时，我们应当拥有一个可以被其他 Agent 复跑的最小 LoveEngine：它还不连接真实世界，但已经有了协议骨架、签名边界、链上状态和审计记录。这是后续扩散到 Agent 网络的最低可用形态。
