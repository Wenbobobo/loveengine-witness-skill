# LoveEngine Pre-enterprise Remote Lab SPEC

状态：short remote acceptance verified; long soak deferred
目标版本：0.6.1-contract-public-pilot
协议：loveengine-witness-net/0.6
更新日期：2026-07-27

## 目标

在不接公司直播、不开放公网服务、不引入生产钱包的前提下，把已经通过本机和 CI
验证的 Witness 核心放到一台共享 Linux 主机上重复运行，证明其跨平台运行、
资源约束、重启与 snapshot 恢复能力。

远程实验仍使用本机 Anvil signer 和模拟 actor。它只能增加 Linux/SSH 环境证据，
不能把模拟角色变成现实独立见证者，也不能证明公众陈述真实。

## 固定边界

- 远端 Pilot、Anvil 和 RPC 只监听 loopback。需要跨主机访问时使用 SSH tunnel，
  不直接绑定 Tailscale IP 或 `0.0.0.0`。
- SSH 必须固定 host key 并使用独立 public key。远程工具没有 password 参数，
  不把密码写入命令行、环境变量、文件、日志或 transcript。
- 不使用 sudo、systemd、Docker daemon、全局 Python 安装或系统包变更。
- 每次部署使用 `$HOME/.local/share/loveengine-witness-lab/` 下的唯一目录，
  不覆盖既有目录，不自动删除远端证据。
- 共享主机实验提高 nice 值、最多使用两个 CPU，并限制 uv/构建并发。
- 只读 preflight 不写远端状态；资源门不通过时不得用 override 强行运行。

## 实验阶段

### 0. 本机回归

跨平台 runner 必须先在本机完成 core E2E、离线 transcript 校验、policy/tamper
测试。PowerShell 入口只包装同一个 Python runner，不维护第二份实验逻辑。

### 1. 远程只读 preflight

`tools/remote_host_preflight.py` 检查：

- Linux、CPU 数量和一分钟 load/CPU；
- 可用内存与部署目录可用磁盘；
- Python 3、uv、Git、bash、tar、ps、精确固定 Foundry 1.7.1 的 forge/anvil；
- 已存在的 LoveEngine、Anvil 或 Forge 进程；
- 只包含进程名、PID 和资源比例的 top process 摘要，不记录命令参数。

默认门槛是至少 2 CPU、3 GiB 可用内存、5 GiB 可用磁盘，且 load/CPU 不超过
0.5。共享主机操作者可以把门槛调得更保守，但不能在自动流程中降低后继续运行。

### 2. 精确源码部署

`tools/run_remote_lab.py` 只接受干净 Git worktree，使用 `git archive` 传输当前
commit。SSH 强制 `BatchMode=yes`、`PasswordAuthentication=no`、
`StrictHostKeyChecking=yes` 和指定 known_hosts/identity。

上传只发生在 preflight 通过后。远端目录已存在即拒绝，避免覆盖并行任务或旧
实验。源码准备、Foundry 依赖和实验输出全部留在该唯一目录。

### 3. 低资源 core 与恢复实验

远端调用同一个 `tools/run_core_experiments.py`：

- `uv sync --frozen`；
- 缺少本地合约依赖时，以单线程安装精确 commit 固定的 forge-std/OpenZeppelin，
  并执行 `forge build`；
- 12 个事件、3 个模拟观察者的 core stage；
- offline/RPC/policy 三种验证等级；
- trust policy 与 transcript 篡改测试；
- 持久 Anvil 重启、quickstart Relay 连接和 snapshot 恢复测试。

最终下载机器报告和 WitnessCoreTranscriptV1，在本机再次执行离线验证。报告必须
标记 `remote_services_exposed:false`、`remote_bind:loopback_only`、
`actors_simulated:true`。

### 4. SSH tunnel 分离实验

`run_remote_lab.py run` 在 core/recovery 通过后重新执行资源门，把远端 loopback
Pilot/Anvil 映射到本机随机 loopback 端口，再使用公开 `loveengine node connect`。
节点先完成连接，随后由远端 loopback 辅助程序使用 Anvil 测试 signer 创建签名
NetworkTaskV2。辅助程序先创建、写入、关闭并 finalize 一份真实 evidence，再让
review payload 通过 tunnel 可访问的 URL 绑定 bundle、events、artifacts、revision、
event count 和 head hash，最后经鉴权 `POST /v1/relay/tasks` 入队。节点必须实际
取回并复算证据后返回一个绑定当前连接和 pending task 的 receipt。该阶段不得直接
暴露远端 RPC，也不得把 Operator token 或 Anvil 测试账户用于非实验网络。

### 5. 长时间门

30 分钟 smoke 和 4 小时 soak 只在主机空闲窗口执行。当前共享主机存在重要任务，
所以短 core E2E 通过不自动触发 soak。长时间门必须重新 preflight，并单独保存
CPU、内存、磁盘、ACK 和恢复报告。

## 验收

- preflight 输出 `loveengine.remote-host-preflight/1` 且 `safe_to_run:true`；
- 远端 core 报告状态 passed，3 个 observation receipt、3 个 review receipt、
  Gate ready；
- restart/snapshot 测试通过，下载 transcript 在本机返回
  `offline_integrity`、`trust_bound:false`；
- tunnel smoke 使用公开 node CLI 收到连接后任务；receipt 必须包含
  `evidence_verified:true`，Relay 必须记录一个 stored ACK 和一个
  `receipt_confirmed`，并确认本次 Quickstart 进程组已停止；
- 成功或失败均写 `remote-lab-report.json`；最终 postflight 必须成功检查进程，
  且 `postflight_cleanup_verified:true`。postflight 的一分钟负载可保留刚结束
  实验的影响，因此清理判定只依赖进程快照成功且无相关进程；
- 没有监听非 loopback 端口，没有 sudo/systemd 和系统级安装；
- 没有密码、token、私钥或 Anvil key 进入仓库、报告、日志和 transcript；
- 本机全量发布门和 GitHub Actions 继续通过。

## 实测记录

2026-07-27，candidate baseline `2fd3a29` 在共享 ARM64 Linux 主机上通过短验收。
本机保留报告的 SHA-256 为
`0d701ca1b56b5cb4d37ba75cdb92b311eaff405906a3f025cd5e7f671d25d075`：

- preflight、core 前资源门和 tunnel 前资源门均为 `safe_to_run:true`；owned
  process stop/absence 校验通过，另行只读检查没有相关残留进程；
- core 为 passed，3 个 observation receipt、3 个 review receipt、Gate ready，
  restart/snapshot 恢复测试通过；
- 下载 transcript 在本机返回 `offline_integrity`、`trust_bound:false`；
- 公开 node CLI 在连接后收到签名任务，返回 1 个绑定 receipt，Relay 记录 1 个
  ACK；
- Quickstart 清理的 stop/absence 校验都返回 0，远端服务始终只绑定 loopback。

该记录是改进前 baseline：当时 contract dependency 命令仍使用 release tag，
tunnel review task 只证明任务/回执绑定。当前 runner 已把依赖固定到不可变 commit，
并要求 review 节点实际复算 finalized evidence、完成双向 receipt confirmation；
增强路径只接受精确匹配当前 source_commit 的新机器报告，不能用旧报告替代。

主机最初因缺少固定 Foundry 1.7.1 被门禁阻断。操作者另行校验官方 immutable
release 的 ARM64 归档、SHA-256 和 GitHub attestation，以原子方式放入用户专用
目录后重新运行门禁；runner 没有自动安装系统软件或降低阈值。

## 明确延期

- 公司直播认证、调度、抓取和企业数据 adapter；
- 生产 Publisher/witness 身份、外部 signer 托管与恢复；
- Tailscale 直接服务暴露、TLS、公网域名、公共测试网；
- 多副本 artifact、生产监控、异地备份和 HA；
- CorporateSink 多场排期与链上强制 ProposalGate。

完成本规格表示“共享远程 Linux 核心实验可重复”，不表示生产部署完成。
