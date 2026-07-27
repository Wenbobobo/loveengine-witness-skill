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
4. 以 nice +15、最多两核和低构建并发执行 core/recovery 测试；
5. 下载 report 和 transcript，并在本机重新离线验证；
6. 再次执行资源门，启动一个受限的 loopback Quickstart；
7. 建立本机 SSH tunnel，使用公开 `loveengine node connect`；
8. 创建并 finalize 一份 tunnel 可访问的 evidence；
9. 节点连接后提交绑定 bundle/events/artifacts 的签名 V2 review task，节点实际
   复算证据并返回一个绑定 receipt；
10. 终止且只终止本次创建的 Quickstart 进程组，再执行只读 postflight。

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
- 3 个 observation receipt 和 3 个 review receipt；
- recovery tests true；
- 下载 transcript 为 offline_integrity 且 trust_bound false；
- `tunnel_smoke.status: passed`、`receipt_count: 1`、
  `evidence_verified: true`、`relay_acked: 1`、
  `relay_receipt_confirmed: 1`；
- `owned_process_cleanup: true`，远端实验文件仍保留。
- `postflight_cleanup_verified: true`。若失败，报告还必须包含 `phase` 和稳定的
  error type；不要只看终端最后一行。

这证明相同 commit 能在受约束 Linux 主机上复跑，不证明现实事实、组织独立性、
公网可用性、生产密钥安全或企业系统已经接入。

30 分钟/4 小时 soak 只在单独确认的空闲窗口执行，短实验不会自动启动它们。

2026-07-27 的 candidate baseline `2fd3a29` 已满足旧版短实验字段，机器报告
SHA-256 为
`0d701ca1b56b5cb4d37ba75cdb92b311eaff405906a3f025cd5e7f671d25d075`。
当前 runner 又增加了 evidence 实际复算、receipt ACK-loss 恢复、精确 dependency
commit 和失败/postflight 报告；增强路径始终以最新报告中的精确
`source_commit` 和完整验收字段为准。长 soak 仍未执行。
