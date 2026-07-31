# 共享远程主机实验流程

适用范围：企业接入前的 Linux/Tailscale 实验主机。

本流程不会部署公司直播 adapter，不开放公网服务，不使用生产钱包。远端机器上有
其他重要任务时，资源门具有否决权。

## 1. SSH 身份与 host key

为 LoveEngine remote lab 创建独立 SSH key，并由主机操作者把公钥加入实验用户的
`authorized_keys`。不要把密码传给脚本，也不要把私钥放进仓库。
公钥登录验证成功后，应轮换曾经通过聊天或其他非密钥通道提供的密码。

known_hosts 必须来自已核对的主机指纹。首次扫描只能建立 TOFU 候选，仍应通过
主机控制台或管理员旁路核对指纹后再运行：

```powershell
ssh-keyscan -p 22 <host> | Set-Content .\tmp\remote-known-hosts
ssh-keygen -lf .\tmp\remote-known-hosts -E sha256
```

## 2. 只读 preflight

```powershell
uv run python .\tools\run_remote_lab.py preflight `
  --host <host> --user <user> --port 22 `
  --identity-file <ssh-key> `
  --known-hosts .\tmp\remote-known-hosts
```

该命令只把 preflight Python 源码送入远端 `python3 -`，不创建工作目录。必须确认：

- `safe_to_run` 为 true；
- Python、uv、Git、bash、tar、ps 可用，forge/anvil 精确为 Foundry 1.7.1；
- 没有既有 LoveEngine/Anvil/Forge 进程；
- load、内存和磁盘均在门槛内。
- host 仍至少有一颗 CPU 预留给已有工作：2 vCPU 时实验只可绑定 1 核；更多 CPU
  时最多绑定 2 核且保留 1 核。

资源门返回退出码 4 时停止。共享主机不得通过降低门槛或手动修改报告绕过。

如果唯一阻塞是用户目录缺少固定 Foundry，runner 仍必须停止。主机操作者可以在
独立步骤中下载官方 immutable v1.7.1 对应架构归档，同时验证发布摘要和 GitHub
attestation，先在阶段目录复验 `forge/anvil --version`，再原子切换到
`~/.codex/tools/foundry-v1.7.1/`。共享主机上不要现场编译；完成后重新执行完整
preflight。该工具链准备不属于 runner 的自动行为。

## 3. 部署和短核心实验

本地 worktree 必须干净，且当前 commit 已经过本机回归：

```powershell
uv run python .\tools\run_remote_lab.py run `
  --host <host> --user <user> --port 22 `
  --identity-file <ssh-key> `
  --known-hosts .\tmp\remote-known-hosts
```

编排器会：

1. 再次执行只读 preflight；
2. 将当前 commit 打成 tar；
3. 在远端 home 下创建唯一、权限收紧的实验目录；
4. 在 core supervisor、guardian 或 Anvil child 创建前，先取得按用户范围的 advisory
   lock，并在该唯一输出目录内再次执行资源门。只有 `safe_to_run:true` 的结果才能以短时、
   EOF 分隔的 POSIX FD lease 交给直接 supervisor；它必须在 15 秒内一次性消费，绑定输出目录、
   完整阈值和 launcher PID/start tick。supervisor 随后在同一进程组运行 core，因此不再有
   可重放 handoff 文件、nonce 或 child-side 资源门。该机制不是通用 PID 白名单或跳过资源门
   的选项；拒绝时不创建 core 进程组，并留下结构化 `core-experiment-report.json`；
5. 在唯一远端目录中先显式执行 `pilot contracts prepare`，从干净源码和 dependency lock
   重建忽略的合约产物；若已有受管依赖与锁失配则停止，不在共享主机上自动刷新。随后以
   nice +15、低构建并发和保留一核的 CPU affinity 执行 core/recovery 测试，并为该独立
   session 启动最长等于本次 `--timeout-seconds` 的 core guardian；
6. 下载 report 和 transcript，并在本机重新离线验证；
7. 再次取得同一 advisory lock，在唯一 Quickstart 输出目录内重新执行资源门，并以一次性
   POSIX FD lease 启动受限的 loopback Quickstart；资源门拒绝返回
   `blocked_by_resource_guard` / 退出码 4，不进入启动恢复；
8. 建立本机 SSH tunnel，启动 3 个公开 `loveengine node connect` 进程；
9. 等 3 个节点全部连接后，为每个节点分别创建并 finalize 一份 tunnel 可访问的
   evidence；
10. 分别提交绑定 bundle/events/artifacts 的签名 V2 review task；每个节点实际
   复算自己的证据并返回与 node/task/dispute 绑定的 receipt；
11. 终止且只终止本次创建的 Quickstart 进程组，再执行只读 postflight。

SSH 调用只使用指定的 known_hosts，禁用系统全局 known_hosts，也不加载远端 login
shell。Quickstart 在远端启动一个只在 Pilot 子进程存活期间担任 leader 的 supervisor 和独立、
最长 900 秒的 watchdog。supervisor 必须先创建 watchdog、再启动 Pilot child，并且只在
两者均已存在后以原子 ready record 向 launcher 返回身份；因此 SSH launcher 在此窗口消失
也不会留下无期限的 Pilot 进程组。runner 对 watchdog 的存活检查还会核验独立 session、位于本次
deployment `tools/start_shared_quickstart.py` 的 canonical 脚本，以及其 NUL 分隔 argv 中
精确且唯一的 mode、target PID、target start tick 和 timeout。正常路径只在
PID/start tick/PGID/SID/supervisor 命令都匹配时终止自己的 Quickstart 组。若 supervisor
已消失，它只会在剩余非 zombie 进程仍证明
`PGID == SID == 原始 PID` 时清理该原始会话；身份不符或 PID 重用都会拒绝操作。
Pilot 子进程退出后 supervisor 也退出；若有残留成员，watchdog 以受限的 leader-loss
路径回收同一 session。即使本地编排器异常退出，watchdog 也会限制该短实验的残留时间；
正常停止后它必须自行退出。runner 会主动终止持久 Quickstart 进程组；因此 watchdog 的
退出只按安全终态验证，并在 `requested_teardown` 中记录
`requested_by: remote_lab_runner`，不能把它解释为 Pilot 自然完成。`/proc` 或 `ps` 无法
确认 session 时不得发送信号；PID 仍存在但 cmdline 不可读时也不得把 watchdog 当作已退出，
二者都以失败报告处理。
启动阶段的失败清理同样不例外：只有已经记录的 PID start tick、`PGID == SID == PID`、
canonical 启动脚本和预期 mode 全部仍匹配时才可向该组发 TERM/KILL；无法读取身份、
leader 已消失或怀疑 PID 重用时不发信号，交由已认证 guardian 的期限或失败报告处理。

core guardian 使用同样的 PID/start tick/PGID/SID/命令身份边界，并且其 canonical 脚本与
NUL 分隔 argv 也必须精确绑定 mode、target PID、target start tick 和 timeout；它独立于 Quickstart
watchdog。它覆盖 core/recovery 阶段中本地 SSH 编排器消失的情况；runner 在开始等待
core 结果前确认 guardian 存活，停止 core 后确认 guardian 已退出，并下载/核验其原子写入的
身份绑定终态文件。只有 `target_exited_before_deadline` 且 `terminated:false` 才能接受一次
通过的 core；身份不匹配、`/proc` 不可检查、超时回收或缺失终态文件都会使远端实验失败。
若 SSH 在 ready record 返回前中断，runner 仅在成功恢复该身份绑定 record 后清理对应进程组；
否则不会猜测 PID，而由 guardian 的期限处理。

advisory lock 仅协调遵守该运行器的同一用户 LoveEngine 实验，不能把一次容量快照变成持续
资源预留，也不能防御同一 Unix UID 的恶意进程。此边界需要独立账号、cgroup/容器或 VM，
不属于本实验范围。

在建立 tunnel 前，runner 从 Quickstart 所属 process group 的 `/proc` socket inode
读取实时 listener：只接受两个预期端口、`127.0.0.1` 或 `::1`，并在报告中保存 listener
数、group 进程数和 extra/non-loopback listener 数。该检查不是端口探测的替代品，二者
都必须通过。
此外，runner 会在建立 tunnel/读取 `readyz` 前，以及三个节点都连接、任何 task 入队前，
再次按 watchdog 的 PID、start tick、脚本、mode、target PID、target start tick 和 timeout
核验它仍然存活；任一检查失败即停止，不提交任务。

它不会清理远端目录。确认报告已回收且没有其他进程使用该目录后，再由主机操作者
决定是否删除。

## 4. 手工 tunnel 诊断

`run` 已自动完成 tunnel smoke。只有排查失败时才手工映射已知端口：

```powershell
ssh -N -L 8780:127.0.0.1:8780 -L 8545:127.0.0.1:8545 `
  -i <ssh-key> -o StrictHostKeyChecking=yes `
  -o UserKnownHostsFile=.\tmp\remote-known-hosts <user>@<host>
```

只在 tunnel 存活时使用映射端口。不要在防火墙中开放 8545，也不要把远端
Pilot 配置改成 `0.0.0.0`。

## 5. 结果判断

通过报告至少包含：

- `remote_bind: loopback_only`；
- `remote_services_exposed: false`；
- core status passed、Gate ready；
- `core_acceptance.accepted: true`：远端 report 与本机下载 transcript 的 run ID、
  12 个事件、3+3 receipt、Gate、恢复测试和离线边界均严格一致；
- `core_cleanup_verification.watchdog_liveness.verified: true` 与
  `core_cleanup_verification.watchdog_cleanup.verified: true`；
- 3 个 observation receipt 和 3 个 review receipt；
- recovery tests true；
- 下载 transcript 为 offline_integrity 且 trust_bound false；
- `tunnel_smoke.status: passed`、`node_count: 3`、`receipt_count: 3`；
- 三份 receipt 均为 `evidence_verified: true`，且分别绑定自己的
  node/task/dispute；
- `relay_acked: 3`、`relay_receipt_confirmed: 3`；
- `task_submissions` 的三项均为 `queued:true`，其 task ID/recipient 分别等于对应
  node 的预期值；
- `listener_inspection.loopback_only: true`、
  `listener_inspection.expected_ports_listening: true`，non-loopback 与 extra listener
  计数均为 0；
- `owned_process_cleanup: true`，远端实验文件仍保留。
- `local_process_cleanup` 的每项均为 `verified: true`；本机 node 或 SSH tunnel 的
  terminate/wait 失败也不得跳过远端 group 或 watchdog 的清理；
- `watchdog_cleanup.verified: true`；
- `tunnel_smoke.requested_teardown.group_cleanup_verified: true`，且
  `tunnel_smoke.requested_teardown.watchdog_safe_terminal: true`；该字段表示受控回收，
  不表示自然完成；
- `watchdog_liveness.before_tunnel_readiness.verified: true` 与
  `watchdog_liveness.before_task_submission.verified: true`；
- `postflight_cleanup_verified: true`。若失败，报告还必须包含 `phase` 和稳定的
  error type；不要只看终端最后一行。

这证明相同 commit 能在受约束 Linux 主机上复跑，不证明现实事实、组织独立性、
公网可用性、生产密钥安全或企业系统已经接入。

30 分钟/4 小时 soak 只在单独确认的空闲窗口执行，短实验不会自动启动它们。
未来长运行验收必须同时保留 `passed:true` 的最终报告、进程树内存采样的峰值/样本数/
采样周期/可用性，以及失败时的稳定 `failure.code`；不能只看终端退出码、根进程 RSS
或局部 artifact。该峰值是本次拥有的 lab 进程树的采样 sum-RSS，且至少须观察到根进程和
三个预期子进程；它不代表物理瞬时内存上界或共享主机整体资源使用。

2026-07-27 的 candidate baseline `2fd3a29` 已满足旧版短实验字段，机器报告
SHA-256 为
`0d701ca1b56b5cb4d37ba75cdb92b311eaff405906a3f025cd5e7f671d25d075`。
当前 runner 又增加了 evidence 实际复算、receipt ACK-loss 恢复、精确 dependency
commit、三公开节点短时跨主机门和失败/postflight 报告；增强路径始终以最新报告中的精确
`source_commit` 和完整验收字段为准。长 soak 仍未执行。
