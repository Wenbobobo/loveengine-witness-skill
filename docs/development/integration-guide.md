# Developer experiment guide

适用目标：0.6.1-contract-public-pilot candidate
最新发布 tag：v0.6.0-contract-public-pilot

本指南按当前能力而不是 M1-M6 历史组织实验。默认验证路径在 ProposalGate 结束；
治理合约是最后一个可选实验。

## 环境与阅读顺序

需要 Python 3.11+、uv，以及用于合约实验的 Foundry 1.7.1。

```powershell
uv sync --frozen
uv run loveengine version
uv run python .\tools\check.py
uv run loveengine pilot contracts prepare
```

`pilot contracts prepare` 是所有会创建真实 package、Anvil 或 Pilot runtime 的命令
的显式前置步骤。它严格检查 Forge/Anvil `1.7.1` 和版本化的
`contracts/dependency-lock.json`，使用单线程构建并返回可记录的 artifact、源码、依赖
和 attestation 摘要。仅缺少的 forge-std/OpenZeppelin 才会按固定 commit 安装；已有
目录的规范树摘要不符会失败关闭，需显式使用
`pilot contracts prepare --refresh-dependencies`，在 staging 核验后才替换受管目录。
它在编译前将受管依赖中的 UTF-8 文本规范化为 LF，并会改变被忽略的
`contracts/lib`、`contracts/out`、`contracts/cache`；因此不要把它
混进一次已有计时的 soak。原始 `package build`、quickstart、demo 和 soak 均不会
隐式执行该步骤，并会重新核验 attestation。

依次阅读 [QA](../../QA.md)、[核心架构](../architecture/witness-core-and-data-flow.zh-CN.md)、
[CLI 参考](../api/cli-reference.md)和
[活动远程实验 SPEC](../specs/love-engine-pre-enterprise-remote-lab.md)。历史阶段报告只在
追查兼容性时阅读。

## 实验 0：package 与 Registry 信任

```powershell
uv run loveengine package build --output .\dist
uv run loveengine package verify <archive.zip> --integrity-only
uv run loveengine registry verify --rpc-url http://127.0.0.1:8545 --artifact <archive.zip> --chain-id 31337 --registry <registry-address> --publisher <publisher-address> --skill-id loveengine-witness --version 0.6.1-contract-public-pilot
```

观察：

- integrity-only 只验证 ZIP 内部 checksums、manifest、release metadata 和秘密文件
  规则，必须返回 trust_bound: false。
- Registry 验证比较 actual ZIP Keccak、canonical manifest Keccak 和 active
  release。

证明：拿到的 bytes 与某个链上 release 一致。
不证明：这个 Publisher 是否是你认可的发布者；认可关系来自独立 trust policy。

## 实验 1：Relay、任务与回执

先运行 quickstart，再在独立终端使用它生成的 invite、trust policy、ZIP 和 signed
profile 连接：

```powershell
uv run loveengine pilot quickstart --root .\pilot --headless
```

```powershell
uv run loveengine node connect --invite .\pilot\pilot-invite.json --trust-policy .\pilot\pilot-trust-policy.json --package .\pilot\release\loveengine-witness-0.6.1-contract-public-pilot.zip --profile .\pilot\profiles\node-1.json --rpc-url http://127.0.0.1:8545 --address <node-address> --cursor-db .\node-1.cursor.sqlite --expected-tasks 1
```

观察：节点先核对 policy、RPC release、ZIP 和 profile，再建立出站 WebSocket；任务
通过鉴权 `POST /v1/relay/tasks` 提交已经签名的 NetworkTaskV2；ACK 与完成
receipt 分开；连接建立后 Relay 仍可推送任务。Pilot 不替 Publisher 签名。
重复提交同一份 signed task 是幂等的；同 taskId 或 issuer+nonce 对应不同内容会
冲突。节点先把 signed receipt 写入 cursor DB，再发送；默认最多重连 3 次，每次
idle timeout 60 秒，ACK 丢失时恢复同一回执而不重做任务。

证明：任务和 receipt 的签名、成员、recipient、nonce、deadline 以及连接绑定。
不证明：节点由现实中的独立组织控制，或 Relay 是高可用服务。

## 实验 2：event、artifact 与 finalize

使用 Operator 页面或鉴权 HTTP 写入创建 session、连续发布文字并关闭。关闭不会
自动 finalize，必须显式调用：

    POST /v1/live/sessions/{session_id}/evidence/finalize

随后只读：

    GET /v1/live/sessions/{session_id}/evidence

观察 pilot.sqlite 中的事件元数据和 artifacts/sha256 下的原文，篡改任意 artifact
后再次验证必须失败。

证明：保存的 bytes、SHA-256、canonical event Keccak 和事件顺序一致。
不证明：文字内容是真实事实，也不证明单机 artifact 永远可用。

## 实验 3：争议与 ProposalGate

```powershell
uv run loveengine demo lan-pilot --stage core --events 12 --observers 10 --output .\core-output
```

实验派发三个不同节点的 review_dispute 任务。每个 review payload 绑定 finalized
bundle、events 和 artifact URL，节点先重新取回并复算事件链、artifact bytes 与
bundle 引用，再签 verdict receipt。随后聚合多数结果，并在 finalized bundle 和
critical dispute 均满足条件时运行 Gate。输出 WitnessCoreTranscriptV1。

离线验证检查 hash、签名、成员、quorum 和引用；RPC + trust policy 才能把 release
anchor 绑定为 trust_bound: true。

证明：核心流程从 release 到 Gate 的记录可复核。
不证明：Gate 是链上权限、review verdict 为现实真相，或三个本机 actor 社会独立。

## 实验 4：可选治理合约

```powershell
uv run loveengine demo lan-pilot --stage governance --events 12 --observers 10 --output .\governance-output
```

该实验在核心结果后注册 witness、创建 proposal、请求五个显式 EIP-712 approval、
提交交易并读取 PublicSink，输出 PilotTranscriptV2。

证明：一次干净 Anvil 部署中的合约签名、quorum、交易、code 和最终状态。
不证明：ProposalGate 被合约强制执行、多场公司排期安全，或公共测试网已经部署。

## 一键本机报告

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\run_core_experiments.ps1
```

PowerShell 入口只是跨平台 Python runner 的薄包装。Linux/Windows 都可直接运行：

```powershell
uv run python .\tools\run_core_experiments.py
```

runner 在忽略的 tmp/core-experiments 目录运行核心 E2E、三种验证等级和篡改检查。
报告必须写明 environment: local_anvil、actors_simulated: true，以及每项实验
“证明/不证明”的边界。它把 `contracts prepare` 作为首个已记录阶段，因此来自干净
源码 checkout 的构建 provenance 也保留在报告中。

默认 soak 也停在 core；治理 soak 必须显式指定：

```powershell
uv run loveengine pilot soak --stage core --duration-seconds 1 --events 12 --observers 10 --output .\core-soak
uv run loveengine pilot soak --stage governance --duration-seconds 1 --events 12 --observers 10 --output .\governance-soak
```

## 实验 5：共享 Linux remote lab

先配置独立 SSH key 和已经旁路核对的 known_hosts。runner 不接受密码：

```powershell
uv run python .\tools\run_remote_lab.py preflight --host <host> --user <user> --identity-file <ssh-key> --known-hosts .\tmp\remote-known-hosts
uv run python .\tools\run_remote_lab.py run --host <host> --user <user> --identity-file <ssh-key> --known-hosts .\tmp\remote-known-hosts
```

preflight 是只读操作；部署只接受干净 commit，在远端 home 的唯一目录内以 nice
+15、最多两核和低构建并发运行 core/recovery。Pilot 与 Anvil 不绑定 Tailscale
地址，报告/transcript 下载后由本机再次离线验证。随后 runner 建立 SSH tunnel，
启动 3 个公开 node CLI 进程连接远端 Quickstart，等三者全部连接后才分别提交
签名任务并验收 3 个绑定 receipt。每个 review task 指向辅助程序刚创建并
finalize 的真实 evidence，节点会通过 tunnel 读取并复算；报告要求三份
`evidence_verified:true`，Relay 还必须精确记录 `acked:3` 和
`receipt_confirmed:3`。它只停止自己创建的进程组；成功或失败都写 report，
postflight 另验无相关残留。详细门槛见
[共享主机 runbook](runbooks/remote-lab-flow.zh-CN.md)。

证明：相同 commit 能否在受约束 Linux 主机上复跑核心/恢复测试，以及公开节点
路径能否经安全 tunnel 完成连接后任务和 receipt。
不证明：生产服务、公共网络、真实组织独立性、生产 signer 或企业接入。

2026-07-27 的 candidate baseline `2fd3a29` 已通过旧版短实验；它只提供单节点
tunnel 的历史证据。2026-07-29 的合并 commit
`76b7163fc2a72c503db6a1b34fd2670b5b9ab580` 已通过增强后的短实验：3 个
observation receipt、3 个 review receipt、Gate ready、恢复测试通过，下载
transcript 为 `offline_integrity` / `trust_bound:false`；3 个公开 node 分别得到
evidence-verified 绑定 receipt，Relay 精确记录 `acked:3` 和
`receipt_confirmed:3`，postflight 未发现相关残留进程。增强后的 runner 仍必须以
每次报告中的精确 source_commit、三节点/三回执、`evidence_verified`、
`relay_receipt_confirmed` 和 postflight 字段逐次验收。本机前台 30 分钟 core soak
已通过；远端 30 分钟和全部 4 小时 soak 仍延期。

## 变更验收

```powershell
uv run python .\tools\check.py
uv run pytest -m "not integration"
uv run pytest .\tests\integration
powershell -ExecutionPolicy Bypass -File .\tools\run_release_checks.ps1
```

release gate 还包括 Foundry、确定性双构建、core accelerated soak、secret scan 和
git diff check。文档、schema 或 source inventory 变化后必须统一刷新 current/M0
manifest 和必要 fixture；不要手工编辑 hash。

## 当前不做

- 不把 fixture/Operator UI 扩展成公司直播平台。
- 不实现自动发现或常驻调度 daemon。
- 不把 quickstart 或 SSH-tunnel remote lab 当作 Tailscale 直接服务、公网或
  测试网部署。
- 不删除 M0-M6 历史 codec/schema/transcript reader 来降低复杂度。
