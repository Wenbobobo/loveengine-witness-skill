# Integration guide

本文给参与 LoveEngine Witness Skill M6 合约融合、公网预备和后续升级的开发者和 Agent 使用。

## 1. 初始化

```powershell
cd <loveengine-witness-skill>
uv sync
uv run python .\tools\check.py
```

不要依赖父目录 DAism 中的重复脚本或文档。

## 2. 阅读顺序

1. `README.md`
2. `docs/specs/love-engine-master-plan.md`
3. `docs/development/m4-skill-supervision-and-next-stage-gaps.md`
4. `docs/specs/love-engine-contract-public-pilot-spec.md`
5. `docs/archive/specs/implemented/love-engine-agent-network-pilot-spec.md`
6. `docs/api/README.md`
7. `skills/loveengine-witness/skill-manifest.json`

查原始约束时再读：

- `docs/reference/source-materials/current/UAS接口文档.md`
- `docs/reference/source-materials/current/UAS 见证方案 2.0.md`

## 3. 开发顺序

M1：

1. 先写 schema 或行为测试并确认失败。
2. 实现最小 domain/use-case。
3. 实现 CLI adapter。
4. 运行 pytest 和 M0 compatibility。

M2：

1. 安装并固定 Foundry。
2. 先写失败的 Foundry 测试。
3. 按 StreamingEngine、PublicSink、CorporateSink、WitnessDAO 顺序实现。
4. 接 EIP-712、signer、relayer 和 demo。
5. 生成 transcript 并进行敏感信息扫描。

M3：

1. 先为 SkillRegistry 状态迁移、签名篡改和 Relay 重投递编写失败测试。
2. 实现 Registry 与 `0.3.x` manifest。
3. 实现节点 profile、bootstrap、task 和 receipt 的 EIP-712 校验。
4. 实现只使用出站连接的 RelayTransport 和 SQLite 队列。
5. 接入 Registry 与 `BroadcastScheduled` 事件，最后运行三节点 Anvil E2E。

M3 验收：

```powershell
uv run pytest .\tests\integration\test_network_demo.py
uv run loveengine network demo --nodes 3 --output .\examples\transcripts
uv run loveengine network transcript verify .\examples\transcripts\network-pilot.fixture.json
```

M4：

1. 先写 LiveEvent、ArtifactStore、EvidenceBundleV2 和 dispute 的失败测试。
2. domain/use-case 只依赖端口，不依赖 aiohttp 或 SQLite。
3. LiveGateway、SQLite、文件系统和 dashboard 作为 adapters 接入。
4. 最后运行三节点 live-evidence E2E 和 transcript 验证。

M4 验收：

```powershell
uv run pytest .\tests\integration\test_live_evidence_demo.py
uv run loveengine demo live-evidence --nodes 3 --input .\examples\live\live-session.fixture.ndjson --output .\examples\transcripts
uv run loveengine live transcript verify .\examples\transcripts\live-review.fixture.json
```

M5：

1. 先验证 `SKILL.md` frontmatter 与确定性 ZIP。
2. Pilot Server 写接口必须先有缺失/错误 token 的失败测试。
3. `observe_live_text` 必须在独立 Agent 进程中验证 SSE、event hash chain 和 artifact。
4. 持久链先测试 state dump/load 和 code hash，再接 proposal。
5. 投票只能由五个显式 `witness vote approve` 命令产生。
6. 最后执行带服务重启、Anvil 重启和三个 Agent 断线的统一 E2E。

M5 快速验收：

```powershell
uv run pytest .\tests\integration\test_pilot_chain.py
uv run pytest .\tests\integration\test_pilot_demo.py
uv run pytest .\tests\integration\test_pilot_soak.py
uv run loveengine demo lan-pilot --events 12 --observers 10 --output .\pilot-output
uv run loveengine pilot transcript verify .\pilot-output\pilot.fixture.json
```

正式发布前必须单独执行四小时 soak：

```powershell
uv run loveengine pilot soak --duration-seconds 14400 --events 240 --observers 10 --output .\pilot-soak
```

M6：

1. 合约团队交付稿只保存在 `docs/reference/contracts/contract-team-v2/`，不要原地改写。
2. 先写合约失败测试，再融合 contract2 的业务结构和 NatSpec。
3. 保留 `VoteSignature` 的 proposalId、payloadHash、reasonHash、nonce 和 deadline。
4. README/运行手册必须按角色说明参与流程。
5. Plugin 只是分发入口；SkillRegistry package hash 仍是信任根。

M6 快速验收：

```powershell
uv run pytest .\tests\test_cli.py .\tests\test_cli_commands.py .\tests\test_pilot_server.py
cd contracts
forge test
cd ..
uv run python .\tools\check.py
```

## 4. 接口规则

- 新增或修改公开字段时同步更新 `schemas/` 和 `docs/api/`。
- EVM 大整数在 JSON 中使用十进制字符串。
- raw private key、mnemonic、keystore 和 token 不能进入 Agent context、fixture、日志或 transcript。
- relayer 只能提交签名，不得替 witness 签名。
- 任何治理默认值必须标注为 fixture、部署参数或治理参数。

## 5. PR 验收

PR 必须列出：

- 修改层：docs、skill、schemas、Python、contracts、relayer 或 adapter。
- 公开接口变化。
- manifest/hash 影响。
- 验证命令和结果。
- 未完成内容。

使用 `.github/pull_request_template.md` 自查。
