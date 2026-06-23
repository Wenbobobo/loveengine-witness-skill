# LoveEngine CLI and operations reference

状态：M5 release candidate  
适用版本：`0.5.0-lan-pilot`  
输出约定：成功写 stdout JSON；错误写 stderr JSON，并使用稳定错误码。

## 1. 环境与来源诊断

```powershell
uv sync --frozen
uv run loveengine version
uv run loveengine manifest verify
```

`loveengine version` 返回 Python 包版本、Skill 版本、协议版本和实际加载的
`package_root`。如果顶层帮助中没有 `package`、`pilot` 或 `witness`，先检查
该路径是否指向预期工作树，再重新执行 `uv sync --frozen`。不要用旧 editable
install 的报错判断当前分支是否实现了命令。

Foundry/Anvil 验收固定使用 Foundry `1.7.1`。首次准备依赖：

```powershell
cd contracts
forge install foundry-rs/forge-std@v1.9.7 --no-git
forge install OpenZeppelin/openzeppelin-contracts@v5.3.0 --no-git
cd ..
```

## 2. 一条完整演示路径

```powershell
uv run loveengine manifest verify
uv run loveengine demo lan-pilot --events 12 --observers 10 --output .\pilot-output
uv run loveengine pilot transcript verify .\pilot-output\pilot.fixture.json
```

该路径构建并安装确定性 ZIP，部署持久化 Anvil 合约，启动 Pilot Server 和
三个观察 Agent，摄取文字事件，生成 EvidenceBundle，完成争议复核和显式
投票，执行 WitnessDAO 提案并查询 PublicSink。

## 3. 发布包

```powershell
uv run loveengine package build --output .\dist
uv run loveengine package verify .\dist\loveengine-witness-0.5.0-lan-pilot.zip
uv run loveengine package install .\dist\loveengine-witness-0.5.0-lan-pilot.zip --target .\installed
uv run loveengine package self-check --root .\installed
```

ZIP 路径排序、时间戳和权限固定；包内包含 `LICENSE`、checksums、SPDX SBOM、
Schema、Python 运行时、Skill 入口及固定合约 ABI/bytecode。Registry 使用实际
ZIP bytes 的 Keccak-256 作为 `packageHash`。

## 4. Pilot Server

配置遵循 `PilotConfigV1`。写 token 只能由受限文件通过 `token_file` 载入，
不能作为 CLI 参数传递。

```powershell
uv run loveengine pilot serve --config .\pilot-config.json
uv run loveengine pilot status --url http://127.0.0.1:8780
```

运行后：

- 主持人控制台：`http://127.0.0.1:8780/operator/`
- 只读证据面板：`http://127.0.0.1:8780/demo/`
- 健康检查：`/healthz`
- 就绪检查：`/readyz`
- 指标：`/v1/metrics`

局域网只读接口可开放；写接口要求 Bearer token 和匹配的 Origin。M5 不提供
公网 TLS、生产身份认证或高可用。

## 5. 持久化 Anvil

```powershell
uv run loveengine pilot chain init --root .\pilot-chain
uv run loveengine pilot chain start --root .\pilot-chain
uv run loveengine pilot chain status --root .\pilot-chain --rpc-url http://127.0.0.1:8545
uv run loveengine pilot chain snapshot --root .\pilot-chain --rpc-url http://127.0.0.1:8545
uv run loveengine pilot chain restore --root .\pilot-chain --rpc-url http://127.0.0.1:8545 --snapshot <state>
```

`status` 会复核 chainId、合约地址和 code hash。损坏或不匹配的 state dump
必须被拒绝。

## 6. 系统 snapshot

```powershell
uv run loveengine pilot snapshot create --config .\pilot-config.json --chain-root .\pilot-chain --output .\snapshots
uv run loveengine pilot snapshot verify .\snapshots\<snapshot>
uv run loveengine pilot snapshot restore .\snapshots\<snapshot> --config .\pilot-config.json --chain-root .\pilot-chain
uv run loveengine pilot snapshot prune --output .\snapshots --older-than-days 30
```

系统 snapshot 包含 SQLite online backup、artifact、audit JSONL、Anvil
deployment/state 和 checksums。恢复必须先验证完整性。

## 7. 显式投票

```powershell
uv run loveengine witness vote approve `
  --proposal-plan .\proposal-plan.json `
  --rpc-url http://127.0.0.1:8545 `
  --address <witness-address> `
  --output .\vote-approval.json
```

命令先查询 active proposal、payload hash、见证者注册状态和 nonce，再通过
`eth_signTypedData_v4` 请求外部 RPC signer。Agent 不自动签票，CLI 也不接受
私钥参数。

## 8. 旧里程碑演示与 transcript

```powershell
uv run loveengine demo local-loop --output .\examples\transcripts
uv run loveengine transcript verify .\examples\transcripts\local-loop.fixture.json

uv run loveengine network demo --nodes 3 --output .\examples\transcripts
uv run loveengine network transcript verify .\examples\transcripts\network-pilot.fixture.json

uv run loveengine demo live-evidence --nodes 3 --input .\examples\live\live-session.fixture.ndjson --output .\examples\transcripts
uv run loveengine live transcript verify .\examples\transcripts\live-review.fixture.json
```

## 9. 长时间 soak

前台正式四小时命令：

```powershell
uv run loveengine pilot soak --duration-seconds 14400 --events 240 --observers 10 --output .\pilot-soak
```

在 Codex、CI 调试终端或可能超时的远程会话中，应使用后台模式：

```powershell
uv run loveengine pilot soak --duration-seconds 14400 --events 240 --observers 10 --output .\pilot-soak --background
uv run loveengine pilot soak-status .\pilot-soak\pilot-soak-run.json
```

后台状态区分 `starting`、`running`、`passed`、`failed`，并给出 PID、进度、
stdout、stderr 和报告路径。`passed` 只由完整 `pilot-soak-report.json` 决定，
不能仅根据 PID 消失推断成功。

任务接收 ACK 延迟与最终任务完成时间是两个不同指标：

- `latency_ms`：Agent 验证并接受任务所需时间，适用 p95 `< 2s`、max `< 5s`。
- `completion_latency_ms`：直播观察直到 session 关闭并签署回执的总时间，
  自然接近直播时长，不适用 ACK 阈值。

`observe_live_text` 的签名 payload 同时携带 `max_duration_seconds`；协议上限
为 14,700 秒。这样长任务不会沿用旧的 30 秒短任务超时，也不会变成无界等待。

## 10. 完整验证

长测试矩阵由脚本统一执行，避免手工漏项：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\run_release_checks.ps1
```

脚本依次运行仓库/hash 检查、Python 单元测试、Foundry、M2/M3/M4 E2E 和
M5 chain/demo/accelerated-soak。正式四小时墙钟运行不属于 CI 快速门，必须
单独执行并保存报告。

## 11. 稳定命令树

```text
version
manifest  node  fixture  evidence  transcript
eip712    relayer  registry  bootstrap
relay     network  live  dispute  review
proposal  package  pilot  witness  demo
```
