# LoveEngine CLI and operations reference

状态：`0.6.1-contract-public-pilot` candidate；最新 tag 为
`v0.6.0-contract-public-pilot`
协议：`loveengine-witness-net/0.6`
输出：成功写 stdout JSON；失败写 stderr JSON 和稳定错误码。

## 1. 环境

```powershell
uv sync --frozen
uv run loveengine version
uv run loveengine manifest verify
uv run loveengine pilot contracts prepare
```

合约和本机 pilot 固定使用 Foundry `1.7.1`。将包含 `forge`、`anvil` 和
`cast` 的目录设为 `FOUNDRY_BIN`，或安装到
`~/.codex/tools/foundry-v1.7.1/`。

`pilot contracts prepare` 严格验证 `forge` 和 `anvil` 都报告 Foundry `1.7.1`，并核验
版本化 `contracts/dependency-lock.json`。仅缺少时才从允许的 HTTPS 仓库受控检出完整固定
commit 的公开合约依赖；其版本化 submodule 图会先核验路径、URL 和 gitlink，再初始化
每个 direct submodule，更深层声明会失败关闭，最后核验最终规范树摘要；已有目录与锁定
规范树摘要不符时默认失败关闭。使用
`pilot contracts prepare --refresh-dependencies` 才会在 staging 中重新取得固定提交、
核验完整树后切换受管目录。随后执行 `forge build --threads 1`。成功 JSON 记录
toolchain、合约源码、依赖树、attestation 和五份部署 artifact 的摘要；不会记录 tool
output。它会在编译前将受管依赖中的 UTF-8 文本规范化为 LF，并写入忽略的
`contracts/lib`、`contracts/out`、`contracts/cache`。真实 package、
quickstart、demo、chain init 和 soak 都要求这些已经验证且仍匹配 attestation 的 artifact；
它们不会在主流程中隐式编译或下载。dry-run 不需要该前置步骤。

## 2. Loopback quickstart

```powershell
uv run loveengine pilot quickstart --root .\pilot
uv run loveengine pilot status --url http://127.0.0.1:8780
```

`quickstart` 只接受 `127.0.0.1`、`localhost` 或 `::1`。它构建真实包、准备并
启动持久 Anvil、部署合约、发布本地 Registry release、生成签名 bootstrap，
然后启动 Pilot Server 和 `/v1/ws` Relay，并生成 `pilot-invite.json` 与独立
`pilot-trust-policy.json`。默认打开浏览器；`--headless` 只禁止浏览器启动。

```powershell
uv run loveengine pilot quickstart --root .\pilot --dry-run --headless
```

Dry-run 只返回步骤和目标路径，不创建 root，不写 token、invite 或运行状态。
`0.6.1` 不把此命令描述为 Tailscale、公网、测试网或生产部署入口。

## 3. Package trust modes

```powershell
uv run loveengine package build --output .\dist
uv run loveengine package verify <archive.zip> --expected-package-hash <registry-keccak>
uv run loveengine package install <archive.zip> --target .\installed --expected-package-hash <registry-keccak>
uv run loveengine package self-check --root .\installed --expected-package-hash <registry-keccak>
```

`expected-package-hash` 是实际 ZIP bytes 的 Keccak-256，即
`SkillRegistry.Release.packageHash`。包内 `checksums.json` 使用 SHA-256 校验每个
非 checksum 文件，且必须非空并完整覆盖；`release.json.manifest_hash` 是
canonical manifest JSON 的 Keccak-256。三者语义不同。

没有可信 Registry hash 时必须显式声明：

```powershell
uv run loveengine package verify <archive.zip> --integrity-only
```

此模式只证明包内部一致，输出 `trust_bound: false`。安装使用临时目录，完整
验证及 self-check 通过后才替换目标。

## 4. Publisher and Registry

当前 Publisher CLI 不提交交易。它只生成可交给外部 signer 的调用计划：

```powershell
uv run loveengine registry publish --input .\release.json --dry-run
```

外部 signer 提交并确认后，使用只读 RPC 校验活动 release、ZIP 和 manifest：

```powershell
uv run loveengine registry verify --rpc-url http://127.0.0.1:8545 --artifact <archive.zip> --chain-id 31337 --registry <registry-address> --publisher <publisher-address> --skill-id loveengine-witness --version 0.6.1-contract-public-pilot
```

历史本地 release-file 一致性入口继续保留，但明确不绑定链上信任：

```powershell
uv run loveengine registry verify --release .\release.json --artifact <archive.zip> --chain-id 31337 --registry <registry-address> --publisher <publisher-address>
```

它返回 `verification_level: release_file_consistency` 和 `trust_bound: false`。

## 5. Observation node

正式连接必须携带 invite、可信旁路取得的 trust policy、可信 ZIP、RPC signer
地址和 signed profile。CLI 在连接前查询 Registry，并比较 policy、invite、
profile、actual ZIP 与 release；不会从 Relay 或收到的任务反推信任值。

```powershell
uv run loveengine node connect --invite .\pilot\pilot-invite.json --trust-policy .\pilot\pilot-trust-policy.json --package <archive.zip> --profile .\signed-profile.json --rpc-url http://127.0.0.1:8545 --address <node-address> --cursor-db .\node.cursor.sqlite --expected-tasks 1 --reconnect-attempts 3 --idle-timeout-seconds 60 --output .\receipts.json
```

争议复核任务还需要由节点操作者提供 verdict map：

```powershell
uv run loveengine node connect --invite .\pilot\pilot-invite.json --trust-policy .\pilot\pilot-trust-policy.json --package <archive.zip> --profile .\signed-profile.json --rpc-url http://127.0.0.1:8545 --address <node-address> --verdicts .\verdicts.json --expected-tasks 1
```

`--dry-run` 可不带 trust policy，只验证 invite/profile 形状并返回
`verification_level: connection_plan`、`trust_bound: false`。节点只建立出站
WebSocket，不需要公网入站端口。断线后复用 durable cursor 与 task journal。
`--reconnect-attempts` 范围为 0-10，`--idle-timeout-seconds` 范围为 1-900；
默认值分别为 3 和 60。重连会重验 Registry release。signed receipt 在发送前
落盘，Relay ACK 丢失时通过 `receipt_state`/`receipt_ack` 恢复；Relay 返回
`receipt_confirmed` 后握手才闭合，不重复执行任务。

## 6. Evidence and authorization

Pilot Server 写接口要求受限文件中的 Bearer token。带 `Origin` 的写请求还必须
与 `allowed_origin` 完全匹配；没有 Origin 的非浏览器请求仍必须提供 token。

关闭 session 不会隐式 finalize evidence：

```text
POST /v1/live/sessions/{session_id}/close
POST /v1/live/sessions/{session_id}/evidence/finalize
GET  /v1/live/sessions/{session_id}/evidence
```

Finalize POST 会重新读取每个 artifact，复算 SHA-256，并校验其 bytes 与事件
内容一致。GET 只读取已存在 bundle，不改变状态。

## 7. Transcript verification

```powershell
uv run loveengine demo lan-pilot --stage core --events 12 --observers 10 --output .\core-output
uv run loveengine pilot transcript verify .\core-output\witness-core.fixture.json
uv run loveengine demo lan-pilot --stage governance --events 12 --observers 10 --output .\governance-output
uv run loveengine pilot transcript verify .\governance-output\pilot.fixture.json
```

两个 demo 都会在临时 Anvil 仍运行时完成 RPC 和 policy 验证，并把三种
verification level 写入机器输出，随后关闭该链。链关闭后只能使用上面的离线命令；
手工传 `--rpc-url` 时必须保证它仍是 transcript 记录的同一条链。

V1 和含旧式最小 review payload 的 V2 都返回 `legacy_consistency`。只有带完整
review payload、跨字段 evidence binding 和 `evidence_verified:true` receipt 的
Core/当前 V2，离线才返回 `offline_integrity` 和 `trust_bound: false`；只有 RPC
返回 `chain_consistency`，仍不代表调用方认可该 Publisher；RPC 加外部 trust policy
才返回 `chain_verified` 与
`trust_bound: true`。RPC 查询固定在 transcript 的 final block，并核对区块
hash/timestamp、Registry、交易、code 和相应最终状态。

## 8. Explicit witness vote

```powershell
uv run loveengine witness vote approve --proposal-plan .\proposal-plan.json --rpc-url http://127.0.0.1:8545 --address <witness-address> --output .\vote-approval.json
```

命令查询 active proposal、payload hash、注册状态和 nonce，然后通过
`eth_signTypedData_v4` 请求外部 RPC signer。Agent 不自动投票，CLI 不接受私钥。

## 9. Chain and snapshots

```powershell
uv run loveengine pilot chain init --root .\pilot-chain
uv run loveengine pilot chain start --root .\pilot-chain
uv run loveengine pilot chain status --root .\pilot-chain --rpc-url http://127.0.0.1:8545
uv run loveengine pilot chain snapshot --root .\pilot-chain --rpc-url http://127.0.0.1:8545
uv run loveengine pilot chain restore --root .\pilot-chain --rpc-url http://127.0.0.1:8545 --snapshot <state>
```

```powershell
uv run loveengine pilot snapshot create --config .\pilot-config.json --chain-root .\pilot-chain --output .\snapshots
uv run loveengine pilot snapshot verify .\snapshots\<snapshot>
uv run loveengine pilot snapshot restore .\snapshots\<snapshot> --config .\pilot-config.json --chain-root .\pilot-chain
uv run loveengine pilot snapshot prune --output .\snapshots --older-than-days 30
```

Snapshot 限制 run ID 和恢复路径，拒绝 symlink、路径逃逸、负 retention、空或不
完整 checksums。恢复前先完整验证并准备 rollback；Windows SQLite 使用预验证副本
与完整回滚，不伪称存在跨文件系统原子事务。

## 10. Release gates

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\run_release_checks.ps1
```

脚本覆盖仓库/hash、非集成 pytest、Foundry、M2-M6 E2E、确定性双构建、
core-stage accelerated soak、secret scan 和 `git diff --check`。治理 soak 可用
`--stage governance` 单独运行；四小时墙钟 soak 需单独运行
并保存报告。发布门与跨平台 core runner 都把 `pilot contracts prepare` 作为其显式、
已记录的第一个合约阶段；不再依赖早先 `forge test` 偶然留下的 `contracts/out`。

后台运行时使用：

```powershell
uv run loveengine pilot soak --stage core --duration-seconds 1800 --events 30 --observers 10 --output .\pilot-soak --background
uv run loveengine pilot soak-status .\pilot-soak\pilot-soak-run.json
```

只有 `pilot soak-status` 返回 `status: passed`，且最终
`pilot-soak-report.json` 同时为 `passed: true` 时，才构成通过证据。失败报告仍是
有效诊断产物：`failure_reason: soak_report_failed` 只是生命周期分类，具体稳定错误码和
失败门应读取 `report.failure.code` 与 `report.checks`。`process_exited_without_report`
只说明记录的 PID 已不存在且未产生最终报告；部分 artifact 仅供诊断，不能作为成功结果，
也不能据此归因外部宿主为何终止了进程。

每次后台启动还会生成非敏感 `run_id`，并拒绝已有而无 state 的 report。状态读取器仅在
该 ID 与 report 匹配、记录的子进程已退出后，才会把通过报告绑定到固定 schema、stage、
事件数、观察者数与请求时长；它同时要求完整的标准成功检查集和所有已报告的 `checks`
都严格为 true。格式错误、stale/cross-run 错绑、缺少标准 checks 或 `passed:true` 与
checks 矛盾的报告都会返回失败和 `report_validation_error`，不能作为通过证据。

`peak_rss_bytes` 保留为兼容字段，且由 `peak_rss_bytes_scope` 明确标记为根进程的
OS 峰值。`memory.root_process_peak_rss_bytes` 也是该局部诊断；完整实验的资源门是
`memory.runtime_tree_sampled_peak_rss_bytes`，它在运行中采样本次 CLI 及其递归子进程的
sum-RSS。只有 `checks.runtime_tree_memory_under_512mb` 可表达本次自有 lab 的采样
sum-RSS 门低于 512 MiB；它还要求一次干净采样至少观察到根进程和三个预期子进程。短生命周期
进程仍可能落在采样间隔之间，所以这不是物理瞬时内存上界。采样不可用、采样错误、子进程
观察不足或超限都会使报告不能通过；它不测量共享主机其他任务，也不是操作系统级资源硬限制。

跨平台核心实验入口为：

```powershell
uv run python .\tools\run_core_experiments.py
```

PowerShell wrapper 调用同一 Python runner，不维护第二份流程。

## 11. Shared remote lab tools

remote lab 是 repository tool，不增加 LoveEngine 协议命令。它固定 host key，
只接受 SSH public key，并在部署前执行只读资源门：

```powershell
uv run python .\tools\run_remote_lab.py preflight --host <host> --user <user> --identity-file <ssh-key> --known-hosts <known-hosts>
uv run python .\tools\run_remote_lab.py run --host <host> --user <user> --identity-file <ssh-key> --known-hosts <known-hosts>
```

run 模式要求干净 Git worktree；远端只使用 loopback、唯一用户目录、nice +15、
最多两核和低并发。它先运行 core/recovery，再建立本机 SSH tunnel，使用公开
`loveengine node connect` 连接远端 Quickstart，并通过鉴权任务入口证明连接后任务
和绑定 receipt。runner 先同时连接 3 个公开 node 进程，再分别提交 3 个 tunnel
review task。每个任务指向刚创建的 finalized evidence；节点实际取回 bundle、
event 和 artifact 后才签名。报告必须包含 3 个 evidence-verified receipt、
3 个 ACK 和 3 个 receipt confirmation。成功或失败都会写
`remote-lab-report.json`；最终 postflight 单独记录进程检查和残留状态。工具没有
password、sudo、systemd、public bind 或远端文件自动清理选项。资源门失败使用
退出码 4。

## 12. Stable command tree

```text
version  manifest  node  fixture  evidence  transcript
eip712  relayer  registry  bootstrap  relay  network
live  dispute  review  proposal  package  pilot  witness  demo
```
