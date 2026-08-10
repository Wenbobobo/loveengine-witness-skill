# LoveEngine Pre-enterprise Remote Lab SPEC

状态：exact-candidate local and remote acceptance verified
目标版本：0.6.1-contract-public-pilot
协议：loveengine-witness-net/0.6
更新日期：2026-08-09

## 目标

在不接公司直播、不开放公网服务、不引入生产钱包的前提下，把已经通过本机和 CI
验证的 Witness 核心放到一台共享 Linux 主机上重复运行，证明其跨平台运行、
资源约束、重启与 snapshot 恢复能力。

远程实验仍使用本机 Anvil signer 和模拟 actor。它只能增加 Linux/SSH 环境证据，
不能把模拟角色变成现实独立见证者，也不能证明公众陈述真实。

## 固定边界

- 远端 Pilot、Anvil 和 RPC 只监听 loopback。需要跨主机访问时使用 SSH tunnel，
  不直接绑定 Tailscale IP 或 `0.0.0.0`。
- SSH 必须固定 host key 并使用独立 public key。远程工具只读取指定的
  `UserKnownHostsFile`，显式禁用 `GlobalKnownHostsFile`，且不加载远端 login shell；
  工具没有 password 参数，不把密码写入命令行、环境变量、文件、日志或 transcript。
- 不使用 sudo、systemd、Docker daemon、全局 Python 安装或系统包变更。
- 每次部署使用 `$HOME/.local/share/loveengine-witness-lab/` 下的唯一目录，
  不覆盖既有目录，不自动删除远端证据。
- 共享主机实验提高 nice 值、限制 uv/构建并发，并始终为其他任务保留至少一个可用 CPU。
  因此 2 vCPU 主机上的 lab 最多绑定 1 核；CPU 更多时最多绑定 2 核且仍保留 1 核。
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
commit。它同时在本机核对版本化 dependency lock 和完整依赖树，生成带规范 manifest、
逐文件 SHA-256 和固定时间戳的确定性 ZIP。远端必须绑定本机 ZIP hash，拒绝链接、重复
成员和路径逃逸，完整验证 staging 树后原子安装 `contracts/lib`；远端不从 GitHub
获取合约依赖。SSH 强制 `BatchMode=yes`、`PasswordAuthentication=no`、
`StrictHostKeyChecking=yes` 和指定 known_hosts/identity。

上传只发生在 preflight 通过后。远端目录已存在即拒绝，避免覆盖并行任务或旧
实验。源码准备、Foundry 依赖和实验输出全部留在该唯一目录。

### 3. 低资源 core 与恢复实验

远端调用同一个 `tools/run_core_experiments.py`：

- 在启动 core supervisor、guardian 和 Anvil 之前，`start_shared_core.py` 必须先取得按用户
  范围的 advisory lock，并在本次唯一输出目录执行一次资源门。通过结果只以短时、一次性
  POSIX FD lease 传给直接 supervisor；lease 绑定 schema、`safe_to_run:true`、未弱化阈值、
  输出目录和 launcher PID/start tick，并在 15 秒内 EOF 消费。supervisor 在同一受管进程组
  内执行 core，不保留可重放 handoff 文件、nonce 或 child-side bypass。不能用裸 PID、
  通配命令行或 `skip` 开关绕过该门；拒绝时不得创建 core 进程组，并必须留下结构化失败报告；

- `uv sync --frozen`；
- 显式运行 `loveengine pilot contracts prepare`：严格核对 forge/anvil 1.7.1、
  dependency lock 与刚安装的本地依赖树；缺失或不匹配均失败关闭，不在共享主机执行
  dependency Git fetch 或 `--refresh-dependencies`，并执行 `forge build --threads 1`；报告
  保留实际 artifact、源码、依赖树和 attestation 摘要。归档不携带本机 `contracts/out`；
- 12 个事件、3 个模拟观察者的 core stage；
- offline/RPC/policy 三种验证等级；
- trust policy 与 transcript 篡改测试；
- 持久 Anvil 重启、quickstart Relay 连接和 snapshot 恢复测试。

最终下载机器报告和 WitnessCoreTranscriptV1，在本机再次执行离线验证。报告必须
标记 `remote_services_exposed:false`、`remote_bind:loopback_only`、
`actors_simulated:true`。

远端 core 不以单个 `status: passed` 作为通过条件。runner 必须同时核对 core report 的
`shared_host` 资源档、12 个事件、3 个 observation receipt、3 个 review receipt、
Gate、恢复测试、三种 verification level 和 contract preparation；下载的 transcript
必须以相同 `run_id` 返回 `valid:true`、`offline_integrity`、`chain_verified:false`、
`trust_bound:false`、`local_anvil`、模拟 actor 和相同 quorum/Gate 字段。
core/recovery 还必须由独立 guardian 限制生命周期：它以本次 runner 的超时上限运行，
只在 PID、start tick、PGID、SID、`start_shared_core.py --supervisor` 命令，以及 guardian
自身位于本次 deployment 的 canonical 脚本和 NUL 分隔 argv 中精确的 mode、target PID、
target start tick、timeout 和共享 lock FD 都可验证时作用于该 session。该 FD 必须大于等于 3、
与按用户 canonical lock 是同一 regular-file inode，并在 guardian 进程内持有非阻塞
exclusive `flock`；runner 还必须经 `/proc/<pid>/fd/<fd>` 将 live guardian 绑定到同一路径。
runner 在等待 core 结果前验证
guardian 仍活着，完成或失败清理后验证它已退出，并要求其原子终态文件与当前 guardian/
target 身份匹配。通过的 core 只接受 `target_exited_before_deadline` / `terminated:false`；
身份错绑、不可检查、超时回收或缺少终态文件均失败关闭；
本地 SSH 编排器消失不能使 core 进程组无限保留。

该 lock 只协调遵守协议的 LoveEngine 进程；同一 Unix UID 不是安全隔离边界，容量检查也是
启动瞬间的快照。要获得对恶意同 UID 或外部并行任务的隔离，需要独立账号、cgroup/容器或 VM，
本规格不作此承诺。

### 4. SSH tunnel 分离实验

`run_remote_lab.py run` 在 core/recovery 通过后重新执行资源门，把远端 loopback
Pilot/Anvil 映射到本机随机 loopback 端口，再启动 3 个公开
`loveengine node connect` 进程。三者全部连接后，远端 loopback 辅助程序才使用
Anvil 测试 signer 分别创建签名 NetworkTaskV2。辅助程序为每个任务创建、写入、
关闭并 finalize 一份真实 evidence，再让 review payload 通过 tunnel 可访问的 URL
绑定 bundle、events、artifacts、revision、event count 和 head hash，最后经鉴权
`POST /v1/relay/tasks` 入队。每个节点必须实际取回并复算自己的证据，返回与当前
连接、pending task 和 dispute 分别绑定的 receipt。该阶段不得直接暴露远端 RPC，
也不得把 Operator token 或 Anvil 测试账户用于非实验网络。

Quickstart supervisor 必须在启动 Pilot child 前创建独立 watchdog，并且仅在 watchdog 和
child 都已启动后原子写出其 ready record；launcher 未获得该 record 前不得返回成功。因此
Quickstart launcher 也必须取得与 core 相同的 advisory lock，在唯一输出目录内重新执行资源门，
并以一次性 POSIX FD lease 绑定 supervisor 的输出目录、完整阈值和 launcher 身份；执行时
资源拒绝返回 `blocked_by_resource_guard` 和退出码 4，不进入启动恢复。该 lock 只协调遵守协议
的 LoveEngine 实验，不是同一 UID 的恶意隔离边界。
本地 SSH 编排器在启动窗口消失时，watchdog 仍能限制该 detached process group。Quickstart
启动后，runner 会从其拥有的 process group 的 `/proc` socket inode
运行时读取 TCP listener，仅接受预期 Pilot 与 Anvil 端口、`127.0.0.1`/`::1` 地址和
零个额外/非 loopback listener。启动器同时创建 60--900 秒范围内的独立 watchdog；
runner 对它的存活检查必须同时核验 watchdog 的 PID/start tick、独立 session、位于本次
远端 deployment `tools/start_shared_quickstart.py` 的 canonical 脚本、mode，以及 NUL 分隔
argv 中精确且唯一的 target PID、target start tick、timeout 和共享 lock FD。该 FD 必须是
大于等于 3 的 inherited descriptor，指向 canonical per-user lock 的同一 inode，并由 guardian
持有 `flock`；runner 通过 `/proc/<pid>/fd/<fd>` 复核路径。它在正常路径只会在 PID、
start tick、PGID、SID 和受管 Quickstart supervisor 命令均匹配时终止该组；若 supervisor
已消失，只会在存活成员仍证明
`PGID == SID == 原始 PID` 时清理原始会话，PID 重用或身份不符均拒绝操作。
`/proc` 或 `ps` 枚举不可用属于未知状态，不得发送 TERM/KILL，也不得把仍存在但 cmdline
不可读的 watchdog 当作已退出；两种情况都必须失败并保留可诊断报告。
启动异常时的 cleanup 也采用同一 fail-closed 规则：没有已记录 start tick、`PGID == SID == PID`、
canonical 启动脚本和预期 mode 的完整匹配，不得按裸 PID 发送 group signal；此时保留
失败报告，并只依赖已经通过身份验证的 guardian 的期限回收。
supervisor 只在 Pilot 子进程存活期间担任组 leader；子进程退出后 supervisor 也退出，
guardian 随即按上述受限的 leader-loss 路径回收仍存活的同一 session 成员。即使本地 SSH
编排器消失，短实验也不会无限保留远端 Quickstart。
runner 会主动停止持久 Quickstart 进程组；正常清理后 watchdog 必须自行退出并被报告验证，
但该终态只能标注为 `requested_teardown`，不构成 Quickstart 自然完成证明。
此外 runner 必须在建立 tunnel/通过 `readyz` 前，以及三个公开节点已连接、任何任务
提交前，重新按 watchdog 的 PID、start tick、脚本、mode、target PID、target start tick 和
timeout 验证其存活；任何一次失败都不得继续
进入或完成 task submission。

### 5. Candidate 工程验收门

后续 candidate 的正式工程门固定为 900 秒、30 个事件和 10 个只读观察者，并覆盖
一次 Pilot 重启、一次 Anvil 重启和节点断线恢复。它必须单独保存 CPU、内存、磁盘、
ACK 和恢复报告。该门用于发现回归和验证恢复，不构成四小时或生产长期可用性证明。
共享主机存在重要任务时仍须重新 preflight，短 core E2E 通过不自动触发 900 秒门。
内存报告必须区分根进程 HWM 与本次拥有的 CLI
加递归子进程树采样 sum-RSS 峰值，并保存样本数、采样周期、最大进程数、可用性和稳定失败码；
只有后者可用作整套 lab 的 512 MiB 采样门，且至少要观察到根进程和三个预期子进程。该采样
不等于物理瞬时内存上界，不测量、不预留也不得挤占共享主机上其他任务的资源。

## 验收

- preflight 输出 `loveengine.remote-host-preflight/1` 且 `safe_to_run:true`；
- dependency bundle 的本机构建和远端安装必须由同一 archive/manifest hash、依赖树、
  文件数和字节数绑定，并返回 `contract_dependency_bundle.verified:true`；
- 远端 core 报告状态 passed，3 个 observation receipt、3 个 review receipt、
  Gate ready；
- restart/snapshot 测试通过，下载 transcript 在本机返回
  `offline_integrity`、`trust_bound:false`；
- tunnel smoke 必须在任务入队前同时连上 3 个公开 node CLI 进程；三份 receipt
  必须各自包含 `evidence_verified:true` 并绑定正确 node/task/dispute，Relay
  必须精确记录 3 个 stored ACK 和 3 个 `receipt_confirmed`，并确认本次
  Quickstart 进程组已停止；
- `tunnel_smoke.listener_inspection` 必须记录预期端口、listener 数、owned group
  进程数、non-loopback/extra listener 数，并以 `loopback_only:true`、
  `expected_ports_listening:true`、两个计数均为零通过；
- 每个 `task_submissions` 必须显式为 `queued:true`，并与对应公开节点的预期
  recipient 和 `review_dispute` task ID 一致；`watchdog_cleanup.verified` 必须为 true；
- `tunnel_smoke.local_process_cleanup` 的每一项必须为 `verified:true`；即使本机 node 或
  SSH tunnel 的 terminate/wait 报错，runner 仍必须继续执行远端 group 和 watchdog 清理；
- `core_cleanup_verification` 必须同时记录并通过 core guardian 的 live/exit 校验；
- `tunnel_smoke.requested_teardown.group_cleanup_verified:true` 与
  `tunnel_smoke.requested_teardown.watchdog_safe_terminal:true`；该字段表示 runner 主动
  请求的受控回收，不表示 Quickstart 自然结束；
- `tunnel_smoke.watchdog_liveness` 必须在 `before_tunnel_readiness` 与
  `before_task_submission` 两个时点均为 `verified:true`；
- 成功或失败均写 `remote-lab-report.json`；最终 postflight 必须成功检查进程，
  且 `postflight_cleanup_verified:true`。postflight 的一分钟负载可保留刚结束
  实验的影响，因此清理判定只依赖进程快照成功且无相关进程；
- 没有监听非 loopback 端口，没有 sudo/systemd 和系统级安装；
- 没有密码、token、私钥或 Anvil key 进入仓库、报告、日志和 transcript；
- 本机全量发布门和 GitHub Actions 继续通过。

## 实测记录

2026-08-04，精确 candidate
`9e5058ec51942a8c1e4004457d58c05d2ea5b824` 完成当前增强路径：

- 本机四小时 core run 使用 run ID `ee4baec07ebb4753adeed7a3fbc98bc3`，处理
  240 个事件和 10 个只读观察者；18 项报告检查全部为 true，failure 为 null，
  secret finding 为零，stderr 为空；独立离线 transcript 复验为
  `offline_integrity`、`trust_bound:false`；
- 共享 ARM64 Linux 报告状态为 passed，source commit 精确匹配；dependency bundle
  837 个文件完整绑定，本机/远端 archive、manifest、树摘要、文件数和字节数一致；
- core 得到 3 个 observation receipt、3 个 review receipt、Gate ready，并通过
  restart/snapshot 恢复；tunnel 在任务入队前连接 3 个公开 node CLI，得到 3 个
  evidence-verified 绑定回执、3 个 ACK 和 3 个 receipt confirmation；
- Pilot/Anvil 始终 loopback-only，key-only SSH、pinned known_hosts、watchdog 和
  postflight cleanup 均通过。报告 SHA-256 为
  `c646d1bd20cf3aa64dd3e20d7b4ef0f99cdf703af79091020108ce71cb276010`，下载
  transcript SHA-256 为
  `889464abf89e3233b04490cc1a38337f9caf12198de8584a0f123eaf890c5b42`。

上述证据绑定 `9e5058e`，不自动接受后续文档或代码提交。每个新 candidate 仍须运行
完整发布门、900 秒工程验收和精确 source commit 的短远端 lab。四小时结果保留为
该历史 candidate 的额外运行证据，不再作为后续候选的强制门。

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
当前增强路径还要求 3 个公开节点同时在线后再投递 3 个独立任务。它只接受精确
匹配当前 source_commit 的新机器报告，不能用旧报告替代。

主机最初因缺少固定 Foundry 1.7.1 被门禁阻断。操作者另行校验官方 immutable
release 的 ARM64 归档、SHA-256 和 GitHub attestation，以原子方式放入用户专用
目录后重新运行门禁；runner 没有自动安装系统软件或降低阈值。

## 明确延期

- 公司直播认证、调度、抓取和企业数据 adapter；
- 生产 Publisher/witness 身份、外部 signer 托管与恢复；
- Tailscale 直接服务暴露、TLS、公网域名、公共测试网；
- 多副本 artifact、生产监控、异地备份和 HA；
- CorporateSink 多场排期与链上强制 ProposalGate。

完成本规格表示“共享远程 Linux 核心实验可重复”，不表示生产部署完成；900 秒
工程门也不等于长期稳定性或可用性证明。
