# LoveEngine Witness Skill

[English README](README.md)

LoveEngine Witness Skill 是面向 Agent 网络的公共利益见证协议。它将资料溯源、EIP-712 身份与任务、本地治理合约、Relay 网络、证据产物和可重放 transcript 组合成可验证 Skill，同时保证私钥不进入 Agent 上下文。

当前稳定演示版本：`0.4.0-live-evidence-pilot`。
上一版纯网络演示：`0.3.1-demo-ready`。
当前发布候选：`0.5.0-lan-pilot`，协议为 `loveengine-witness-net/0.5`。

## Skill 模型

LoveEngine 采用“薄指令层、厚协议运行时”。面向 Codex 的 `SKILL.md`
只定义触发条件、验证流程、命令导航和安全边界；实际协议由版本化
manifest、Schema、Python 用例、适配器、合约和 transcript 承担。
这样可以保持 Agent 上下文精简，同时保证行为确定、可测试、可复核。

## 系统架构

```mermaid
flowchart LR
    Sources[原始资料] --> Manifest[带 hash 的 Skill 包]
    Manifest --> Registry[SkillRegistry]
    Registry --> Relay[Relay Hub]
    Chain[UAS 合约与链上事件] --> Relay
    Relay --> A[Agent A]
    Relay --> B[Agent B]
    Relay --> C[Agent C]
    A --> Receipts[签名回执]
    B --> Receipts
    C --> Receipts
    Live[LiveSource 端口] --> Gateway[Live Gateway]
    Gateway --> Artifacts[ArtifactStore 端口]
    Gateway --> Evidence[EvidenceBundle]
    Evidence --> Review[争议复核协调器]
    Review --> Gate[ProposalGate]
    Registry --> Dashboard[只读面板]
    Receipts --> Dashboard
    Evidence --> Dashboard
    Gate --> Dashboard
```

可替换模块见[扩展接口文档](docs/api/extension-interfaces.md)。领域层不依赖具体 signer、直播平台、对象存储、数据库或传输方式。

## 全流程

```mermaid
flowchart TD
    Verify[校验 manifest 与源资料 hash] --> Deploy[部署四个 UAS 合约和 SkillRegistry]
    Deploy --> Register[通过 EIP-712 注册见证者]
    Register --> Schedule[登记直播排期]
    Schedule --> Observe[Relay 下发观察任务]
    Observe --> Build[生成不可变证据包]
    Build --> Review[三个节点复核争议]
    Review --> Gate{ProposalGate}
    Gate -->|争议 dismissed| Propose[生成提案执行计划]
    Gate -->|upheld 或 unresolved| Block[阻止提案]
    Propose --> Vote[见证者签名投票]
    Vote --> Execute[执行通过的提案]
    Execute --> Query[查询 PublicSink]
    Query --> Transcript[复核 transcript]
```

## M3 三节点时序

```mermaid
sequenceDiagram
    participant Chain as 链
    participant Relay
    participant A as Agent A
    participant B as Agent B
    participant C as Agent C
    Chain->>Relay: ReleasePublished / BroadcastScheduled
    Relay->>A: 已签名 NetworkTask
    Relay->>B: 已签名 NetworkTask
    Relay->>C: 已签名 NetworkTask
    A-->>Relay: 已签名 TaskReceipt
    B-->>Relay: 已签名 TaskReceipt
    C-->>Relay: 已签名 TaskReceipt
    Relay-->>Relay: ACK、重试、去重
    Relay-->>Chain: Relay 无签名权限
```

## 目录结构

```text
contracts/                 Foundry UAS 合约与 SkillRegistry
docs/api/                  公开协议与扩展接口
docs/development/          接入指南、演示手册和验收报告
docs/reference/            当前有效的源约束
docs/archive/              历史源稿和已实施规格
docs/specs/                当前总规划和活动 SPEC
examples/                  无秘密的 fixture 与 transcript
schemas/                   JSON Schema Draft 2020-12
skills/loveengine-witness/ 可验证 Skill manifest 与 onboarding
src/loveengine_witness/    Python 领域、用例、端口、适配器和 CLI
tests/                     单元、合约和端到端测试
tools/                     仓库及兼容性检查
```

## 环境准备

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Foundry `1.7.1`

```powershell
uv sync --frozen
cd contracts
forge install foundry-rs/forge-std@v1.9.7 --no-git
forge install OpenZeppelin/openzeppelin-contracts@v5.3.0 --no-git
cd ..
```

## 完整验证

```powershell
uv run python .\tools\check.py
uv run pytest -m "not integration" -q

cd contracts
forge test
cd ..

uv run pytest .\tests\test_demo.py
uv run pytest .\tests\integration\test_network_demo.py
uv run pytest .\tests\integration\test_live_evidence_demo.py
uv run pytest .\tests\integration\test_pilot_chain.py
uv run pytest .\tests\integration\test_pilot_demo.py
uv run pytest .\tests\integration\test_pilot_soak.py
```

## 运行演示

```powershell
uv run loveengine manifest verify

uv run loveengine demo local-loop --output .\examples\transcripts
uv run loveengine transcript verify .\examples\transcripts\local-loop.fixture.json

uv run loveengine network demo --nodes 3 --output .\examples\transcripts
uv run loveengine network transcript verify .\examples\transcripts\network-pilot.fixture.json

uv run loveengine demo live-evidence --nodes 3 --input .\examples\live\live-session.fixture.ndjson --output .\examples\transcripts
uv run loveengine live transcript verify .\examples\transcripts\live-review.fixture.json

uv run loveengine package build --output .\dist
uv run loveengine package verify .\dist\loveengine-witness-0.5.0-lan-pilot.zip
uv run loveengine package install .\dist\loveengine-witness-0.5.0-lan-pilot.zip --target .\installed
uv run loveengine package self-check --root .\installed

uv run loveengine demo lan-pilot --events 12 --observers 10 --output .\pilot-output
uv run loveengine pilot transcript verify .\pilot-output\pilot.fixture.json
```

局域网控制面、持久链和恢复命令见 [M5 API](docs/api/lan-pilot-api.md)。M3 对外演示顺序仍保留在[演示手册](docs/development/m3-demo-runbook.md)。

## 局域网试点操作

先在源码管理之外创建 token 文件，并通过 `PilotConfigV1.token_file`
引用。CLI 不接受 token 明文参数。

```powershell
uv run loveengine pilot chain init --root .\pilot-chain
uv run loveengine pilot chain start --root .\pilot-chain
uv run loveengine pilot chain status --root .\pilot-chain --rpc-url http://127.0.0.1:8545

uv run loveengine pilot serve --config .\pilot-config.json
uv run loveengine pilot status --url http://127.0.0.1:8780

uv run loveengine pilot snapshot create --config .\pilot-config.json --chain-root .\pilot-chain --output .\snapshots
uv run loveengine pilot snapshot verify .\snapshots\<snapshot>
uv run loveengine pilot snapshot restore .\snapshots\<snapshot> --config .\pilot-config.json --chain-root .\pilot-chain
uv run loveengine pilot snapshot prune --output .\snapshots --older-than-days 30
```

正式四小时 soak：

```powershell
uv run loveengine pilot soak --duration-seconds 14400 --events 240 --observers 10 --output .\pilot-soak
```

该命令会注入一次 Pilot Server 重启、一次 Anvil 重启，以及三个观察
Agent 各一次断线。事件连续性、ACK 延迟、恢复时间、磁盘、内存或
secret scan 任一阈值不达标都会失败。

## CLI 分组

```text
manifest  node  fixture  evidence  transcript
eip712    relayer  registry  bootstrap
relay     network  live  dispute  review
proposal  package  pilot  witness  demo
```

成功结果写入 stdout JSON；错误写入 stderr，并使用稳定错误码。

## 安全边界

- 私钥、助记词、keystore 和 token 不进入 Agent context、fixture、日志或 transcript。
- Relayer 只能提交签名，不能代替见证者或节点签名。
- 签名任务必须绑定 chainId、合约、issuer、recipient、payloadHash、nonce 和 deadline。
- 原始证据不上链；提案和合约只使用内容 hash。
- Agent 不得自动签投票。每个见证者必须单独执行
  `loveengine witness vote approve`，并使用外部 RPC signer。
- `PublicSink` 始终只读。

## 阅读顺序

1. [工程总规划](docs/specs/love-engine-master-plan.md)
2. [M5 活动 SPEC](docs/specs/love-engine-lan-pilot-spec.md)
3. [API 入口](docs/api/README.md)
4. [开发接入指南](docs/development/integration-guide.md)
5. [资料索引](docs/kb/source-inventory.md)

## License

仓库尚未选择统一开源许可证，不得从历史 SCC0 或 DAism 资料推断许可证。
