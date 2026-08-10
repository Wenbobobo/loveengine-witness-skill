# LoveEngine CLI and operations reference

状态：`0.7.0-invited-public-pilot` candidate，叠加在尚未人工合并的 0.6.1
PR #11；最新 tag 为 `v0.6.0-contract-public-pilot`
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
output。它会在编译前将受管依赖中的 UTF-8 文本规范化为 LF，并按区分大小写的 POSIX
相对路径排序依赖记录，再写入忽略的
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
该兼容 quickstart 不把自身描述为 Tailscale、Sepolia 或生产部署入口。

### 2.1 V2 dual surfaces

`pilot serve` 读取 PilotConfigV1 或 V2。V2 启动两个独立 loopback listener：

```powershell
uv run loveengine pilot serve --config .\pilot-config-v2.json
uv run loveengine pilot status --url <admin-loopback-url> --surface admin
uv run loveengine pilot status --url <participant-loopback-url> --surface participant
uv run loveengine pilot task enqueue --input .\signed-task.json --admin-url <admin-loopback-url> --origin <allowed-origin> --token-file .\secrets\pilot-write-token.txt
```

`pilot status` 不接受调用方自报的 surface 名称：它会同时核对 `/healthz` 与
`/readyz` 返回的服务端入口身份。把 participant URL 当作 admin（或反向混用）会以
`pilot_surface_mismatch` 失败关闭。

Sepolia task issuer 先用独立 signer 对与 policy/bootstrap 一致的 NetworkTaskV2
人工确认签名，再通过 admin surface 入队：

```powershell
uv run loveengine network task sign --input .\task.unsigned.json --signer-config .\secrets\task-issuer.json --trust-policy .\pilot-trust-policy.json --bootstrap .\bootstrap.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json --clef-binary <clef-1.17.3-binary> --expected-binary-sha256 <sha256> --output .\signed-task.json
```

participant surface 只开放 allowlist 的 GET/SSE/artifact/WebSocket；所有 POST、token、
Operator UI、snapshot、task ingress 和 metrics 都只在 admin surface。若操作者已旁路
确认目标机登录正确 tailnet、无 Funnel/冲突且 ACL 正确，可显式运行：

```powershell
uv run loveengine pilot serve --config .\pilot-config-v2.json --tailscale-serve
```

该模式固定运行 900 秒，只把 participant loopback 映射到 HTTPS `*.ts.net`，并恢复
工具接管前的自有 Serve 状态；它不运行 `tailscale up`，不映射 admin/RPC/Clef，且
当前尚无真实 Tailscale Serve 运行证据。

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

历史计划入口继续保留：

```powershell
uv run loveengine registry publish --input .\release.json --dry-run
```

0.7 的 Sepolia 路径生成精确 EIP-1559 plan、经外部 signer 签名，并在再次复核后
提交。plan 命令固定 chain ID 11155111，`value` 必须为零：

```powershell
uv run loveengine registry transaction deploy-plan --artifact <SkillRegistry-artifact.json> --sender <publisher> --nonce <nonce> --gas <gas-limit> --max-fee-per-gas <wei> --max-priority-fee-per-gas <wei> --created-at <unix> --expires-at <unix> --output .\plans\deploy.json
uv run loveengine registry transaction publish-plan --release .\release.json --sender <publisher> --nonce <nonce> --gas <gas-limit> --max-fee-per-gas <wei> --max-priority-fee-per-gas <wei> --created-at <unix> --expires-at <unix> --output .\plans\publish.json
uv run loveengine registry transaction sign --plan .\plans\publish.json --signer-config .\secrets\publisher-signer.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json --clef-binary <clef-1.17.3-binary> --expected-binary-sha256 <sha256> --output .\plans\publish.signed.json
uv run loveengine registry transaction submit --plan .\plans\publish.json --signed .\plans\publish.signed.json --rpc-url-file .\secrets\sepolia-primary-rpc.txt
```

`sign` 要求 Publisher role、allowlist 中的精确 request hash 和 `manual_confirm`，并
重验恢复地址和解码后的 raw transaction；首轮 plan 只接受空 `accessList`。
`submit` 再校验 plan/envelope 和当前 nonce，
只广播同一 raw transaction。Sepolia RPC 必须从权限受限文件读取。当前没有真实
Clef/Sepolia receipt，所以这些接口已实现不等于 release 已发布。

`expires_at` 是 LoveEngine `sign/submit` 的客户端拒绝门，不是以太坊 type-2 raw
transaction 的链上字段。过期 raw transaction 若被导出，外部工具仍可能广播；因此
过期 plan 与 signed envelope 必须废弃，不能把该字段描述成链上 deadline。需要链上
强制过期语义时，应另用带 deadline 的智能账户或合约入口。

外部 signer 检查分四个累积层级：

```powershell
uv run loveengine signer inspect --config .\secrets\publisher-signer.json
uv run loveengine signer inspect --config .\secrets\publisher-signer.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json
uv run loveengine signer inspect --config .\secrets\publisher-signer.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json --clef-binary <clef-1.17.3-binary> --expected-binary-sha256 <sha256>
uv run loveengine signer inspect --config .\secrets\publisher-signer.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json --clef-binary <clef-1.17.3-binary> --expected-binary-sha256 <sha256> --probe
```

四层依次为 static config、rules/attestation evidence、binary/SHA+version、只读 live
API probe。可选参数必须成对完整，probe 要求前两类外部证据均已提供。兼容目标是
Geth/Clef 1.17.3；Geth 1.17.4 已移除内置 Clef，不能把 1.17.5 当作 Clef 升级。
`--expected-binary-sha256` 使用 `sha256:<64 lowercase hex>` 格式。首轮只允许
`manual_confirm`；probe 不会签名。

交易确认后，使用只读 RPC 校验活动 release、ZIP 和 manifest：

```powershell
uv run loveengine registry verify --rpc-url-file .\secrets\sepolia-primary-rpc.txt --artifact <archive.zip> --chain-id 11155111 --registry <registry-address> --publisher <publisher-address> --skill-id loveengine-witness --version 0.7.0-invited-public-pilot
```

历史本地 release-file 一致性入口继续保留，但明确不绑定链上信任：

```powershell
uv run loveengine registry verify --release .\release.json --artifact <archive.zip> --chain-id 31337 --registry <registry-address> --publisher <publisher-address>
```

它返回 `verification_level: release_file_consistency` 和 `trust_bound: false`。

## 5. Observation node

正式连接必须携带 invite、可信旁路取得的 trust policy、可信 ZIP、signed profile
和签名身份。CLI 在连接前查询 Registry，并比较 policy、invite、profile、actual
ZIP 与 release；不会从 Relay 或收到的任务反推信任值。本机兼容路径使用 Anvil
RPC signer：

V2 profile/bootstrap 不带 signer 参数时只输出供人工审阅的 EIP-712 typed data；带
完整 signer/runtime evidence 和 `--output` 时，才经受验证的 signer 路径生成并立即
复核签名结果：

```powershell
uv run loveengine node profile sign --input .\node-profile-v2.unsigned.json --signer-config .\secrets\node-signer.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json --clef-binary <clef-1.17.3-binary> --expected-binary-sha256 <sha256> --output .\signed-profile.json
uv run loveengine bootstrap build --input .\bootstrap-v2.unsigned.json --signer-config .\secrets\publisher-signer.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json --clef-binary <clef-1.17.3-binary> --expected-binary-sha256 <sha256> --output .\bootstrap.json
uv run loveengine bootstrap verify .\bootstrap.json --chain-id 11155111 --registry <registry-address>
```

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

Sepolia InviteV2 路径必须使用外部 signer config 和两个不同的受限 RPC URL 文件；
不能同时用同一 URL，也不能把 URL/credential 写入普通参数或 transcript：

```powershell
uv run loveengine node connect --invite .\pilot-invite-v2.json --trust-policy .\pilot-trust-policy.json --package <archive.zip> --profile .\signed-profile.json --rpc-url-file .\secrets\sepolia-primary-rpc.txt --secondary-rpc-url-file .\secrets\sepolia-secondary-rpc.txt --signer-config .\secrets\node-signer.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json --clef-binary <clef-1.17.3-binary> --expected-binary-sha256 <sha256> --cursor-db .\node.cursor.sqlite --expected-tasks 1 --reconnect-attempts 3 --idle-timeout-seconds 60 --output .\receipts.json
```

InviteV2 的 HTTPS/WSS `*.ts.net` 地址只负责连接发现；policy 固定 chain ID 11155111、
Registry/Publisher、skill/version、ZIP/manifest hash 和 allowed issuers。两个 RPC
一致但没有 policy 只构成 chain consistency。观察节点 signer role 必须为
`observation_node`，地址/chain 必须与 profile/policy 一致，首轮 Clef 仍是
`manual_confirm`。

现实参与者可另签一份不包含秘密的参与声明：

```powershell
uv run loveengine participant attest --input .\participant-attestation.unsigned.json --signer-config .\secrets\participant-signer.json --trust-policy .\pilot-trust-policy.json --bootstrap .\bootstrap.json --invite .\pilot-invite-v2.json --assignment-task .\signed-assignment-task.json --service-config .\participant-service-config.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json --clef-binary <clef-1.17.3-binary> --expected-binary-sha256 <sha256> --output .\participant-attestation.json
```

命令先与 policy、bootstrap、InviteV2、节点 assignment task、service config 和 Clef
evidence 交叉核对；输出绑定 run/role/node/profile、package/manifest/service-config、
assignment task/payload、InviteV2/trust-policy、ruleset/rules-attestation、opaque
operator/network group 和时间范围，不证明事件内容为真或参与者社会独立。
transcript 会验证这些声明的签名与交叉引用，但当前 Clef backend、ruleset 与 audit hash
仍是未签名的运行声明，因此验证结果固定返回
`signer_backend_evidence_verified:false`；真实签名地址有效不等于已证明 Clef 确实加载
了指定 rules。

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

0.7 本机 V2 使用固定 900 秒 profile：

```powershell
uv run loveengine pilot soak --stage core --duration-seconds 900 --events 30 --observers 10 --core-transcript-version 2 --output .\v2-acceptance
uv run loveengine pilot transcript verify .\v2-acceptance\witness-core.fixture.json
```

自动化 runner 可以传入非敏感的 `--run-id`，使其报告、Pilot runtime 和生成的
transcript 绑定到同一次运行；普通本机 demo 不指定时保留固定的演示 ID。

两个 demo 都会在临时 Anvil 仍运行时完成 RPC 和 policy 验证，并把三种
verification level 写入机器输出，随后关闭该链。链关闭后只能使用上面的离线命令；
手工传 `--rpc-url` 时必须保证它仍是 transcript 记录的同一条链。

V1 和含旧式最小 review payload 的历史 transcript 都返回 `legacy_consistency`。
WitnessCoreTranscriptV2 带完整
review payload、跨字段 evidence binding 和 `evidence_verified:true` receipt 的
Core 路径，离线返回 `offline_integrity`、`chain_verified:false` 和
`trust_bound:false`。

V2 链校验要求两个 RPC 要么同时提供、要么都不提供，并拒绝相同 URL：

```powershell
uv run loveengine pilot transcript verify .\witness-core-v2.json --rpc-url-file .\secrets\sepolia-primary-rpc.txt --secondary-rpc-url-file .\secrets\sepolia-secondary-rpc.txt
uv run loveengine pilot transcript verify .\witness-core-v2.json --rpc-url-file .\secrets\sepolia-primary-rpc.txt --secondary-rpc-url-file .\secrets\sepolia-secondary-rpc.txt --trust-policy .\pilot-trust-policy.json
```

双 RPC 无 policy 返回 `chain_consistency`、`chain_verified:false`、
`trust_bound:false`；双 RPC 加外部 policy 才返回 `chain_verified`、
`chain_verified:true`、`trust_bound:true`。查询固定在 transcript 的 final block，并
核对区块 hash/timestamp、Registry、交易、code 和 release。邀请试点 environment
必须是 `sepolia_invited_pilot`；本机 V2 是 `local_anvil`。当前没有真实 Sepolia V2
transcript。

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
`--stage governance` 单独运行；candidate 的正式工程门另运行 900 秒并保存报告。
该门不证明长期稳定性。发布门与跨平台 core runner 都把 `pilot contracts prepare` 作为其显式、
已记录的第一个合约阶段；不再依赖早先 `forge test` 偶然留下的 `contracts/out`。

推荐通过统一 runner 执行完整发布门和 15 分钟验收：

```powershell
uv run python .\tools\run_engineering_acceptance.py --output .\tmp\engineering-acceptance\<run-id>
```

后台运行时使用：在新的、干净的 worktree 中，必须先显式执行 `uv sync --frozen`
和 `uv run loveengine pilot contracts prepare`，并确认后者返回 `prepared: true`。准备阶段不计入
soak 时长；`pilot soak` 只复验已 attested 的产物，缺失时会失败关闭。

```powershell
uv run loveengine pilot soak --stage core --duration-seconds 900 --events 30 --observers 10 --core-transcript-version 2 --output .\pilot-soak --background
uv run loveengine pilot soak-status .\pilot-soak\pilot-soak-run.json
```

只有 `pilot soak-status` 返回 `status: passed`，且最终
`pilot-soak-report.json` 同时为 `passed: true` 时，才构成通过证据。失败报告仍是
有效诊断产物：`failure_reason: soak_report_failed` 只是生命周期分类，具体稳定错误码和
失败门应读取 `report.failure.code` 与 `report.checks`。`process_exited_without_report`
只说明记录的 PID 已不存在且未产生最终报告；部分 artifact 仅供诊断，不能作为成功结果，
也不能据此归因外部宿主为何终止了进程。

每次 soak（前台或后台）都会生成非敏感 `run_id`；后台启动会预先把它写入 state，
再同时传入 Pilot runtime、transcript 和 report，并拒绝已有而无 state 的 report。
状态读取器仅在该 ID 在 state、report、离线重验的 transcript 和 verifier 回传值中一致、
记录的子进程已退出、report 的实测 `elapsed_seconds` 达到请求时长（仅允许 1 秒计时
舍入余量），且本机状态检查已跨过 `planned_end_epoch` 后，才会把通过报告绑定到固定 schema、
stage、事件数与观察者数；它同时要求完整的标准成功检查集和所有已报告的
`checks` 都严格为 true、`secret_leaks` 为一个空列表，以及 transcript 路径在本次输出
目录内。状态读取器会重新读取该 transcript，不传 RPC 或 trust policy 地复验完整性；core
还必须确有 3 个 observation receipt、3 个 review receipt 和 ready Gate。格式错误、
stale/cross-run 错绑、缺少标准 checks、secret finding、不可复验 transcript 或
`passed:true` 与 checks 矛盾的报告都会返回失败和 `report_validation_error`，不能作为通过证据。
记录的子进程已退出后，状态读取器才会以同目录的原子替换把首次经复验的终态写回 state：
`status`、`finished_at` 以及适用的窄化失败分类。子进程仍存活时，即使已经出现部分或
失败报告，state 仍保持 `running`；在启动器尚未登记 PID 的短窗口，state 保持 `starting`，
但该 launch lease 最长为 30 秒。超过 lease 而仍未登记 PID 会原子写为
`failed`，并带 `failure_reason: launch_registration_timeout`，使监控得到明确终态；
保留该 state 以保护原始证据，新的实验必须选择新的 output 目录。
同一 output 目录的初始 state 使用排他保留；另一个启动器在 PID 登记窗口或运行期间尝试
复用该目录会被拒绝，不能产生两份共享 report 的 worker。
这些持久字段只是可恢复的生命周期摘要，每次查询仍会
重新验证 report 与 transcript，不能充当信任锚。

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

## 11. Historical 0.6.1 shared remote lab tools

remote lab 是 repository tool，不增加 LoveEngine 协议命令。它固定 host key，
只接受 SSH public key，并在部署前执行只读资源门：

```powershell
uv run python .\tools\run_remote_lab.py preflight --host <host> --user <user> --identity-file <ssh-key> --known-hosts <known-hosts>
uv run python .\tools\run_remote_lab.py run --host <host> --user <user> --identity-file <ssh-key> --known-hosts <known-hosts>
```

run 在上传前从本机锁定树构建确定性依赖 ZIP；远端以本机 archive hash 验证全部
成员，在 staging 复算 manifest、逐文件 hash 和树摘要后原子安装。因此共享主机不执行
Git 依赖下载，报告以 `contract_dependency_bundle.verified:true` 绑定两侧摘要。

run 模式要求干净 Git worktree；远端只使用 loopback、唯一用户目录、nice +15、
低并发，并始终预留一颗 CPU 给既有任务（2 vCPU 时 lab 仅绑定 1 核，否则最多
2 核）。每个受控 core/Quickstart 组都有身份绑定、带期限的 watchdog；runner
在继续 core 等待或任务入队前核验其存活，清理后核验其退出。它先运行
core/recovery，再建立本机 SSH tunnel，使用公开
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
eip712  relayer  registry  signer  bootstrap  relay  network
participant  live  dispute  review  proposal  package  pilot  witness  demo
```

0.7 的 signer、dual-surface、transaction-plan 和 V2 命令已实现并有本机测试；
以上 0.6.1 remote lab 报告不能接受 0.7 candidate。真实 Clef 1.17.3、Sepolia、
Tailscale Serve 和受邀远端运行仍是发布前缺失证据。
