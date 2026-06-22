# Integration guide

本文给参与 LoveEngine Witness Skill M4 直播证据试点及后续升级的开发者和 Agent 使用。

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
4. `docs/specs/love-engine-live-evidence-pilot-spec.md`
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
