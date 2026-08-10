# Sepolia 受邀公开试点操作手册

适用版本：`0.7.0-invited-public-pilot` candidate

协议：`loveengine-witness-net/0.6`
固定验收：900 秒、30 个事件、10 个只读观察者

## 1. 这次实验公开什么

下一次实验是混合边界，不是把整套服务直接开放到互联网：

- SkillRegistry 的 deployment、release 和历史区块位于 Sepolia，链上事实公开可查；
- participant HTTP/WebSocket 入口只通过 Tailscale Serve 暴露给受邀 tailnet 成员；
- admin/write、Sepolia RPC、Clef 和 write token 始终留在 loopback 或受限文件中；
- 禁止 Tailscale Funnel；不部署 WitnessDAO、CorporateSink、StreamingEngine 或
  PublicSink 到 Sepolia；核心流程在 ProposalGate 结束。

因此，“public”只表示测试网锚点可公开复核，不表示匿名公网用户可以访问 Pilot，
更不表示 Agent 已证明内容真实或参与者社会独立。

## 2. 需要准备的资源

| 类别 | 最小输入 | 约束 |
| --- | --- | --- |
| 源码 | 一个干净、已通过本机门的 40 位 commit | 实验中不得切换 HEAD |
| Sepolia | 少量测试 ETH、SkillRegistry、Publisher 地址 | 只使用测试资产 |
| RPC | 两个受限 RPC URL 文件 | 内容不同，最好来自不同提供方 |
| Signer | Publisher、task issuer、至少 3 个 node 的 Clef | 固定 Clef 1.17.3、`manual_confirm` |
| 参与者 | 至少 3 个 node、2 个现实操作者、2 个网络组 | 地址、profile、cursor 各自独立 |
| 服务 | 一台已登录 tailnet 的 Pilot 主机 | 只用 Serve；禁止 Funnel |
| 证据 | 独立输出目录和真实 session 输入 | 不使用预设 verdict 代替现实输入 |

首轮试点不要求企业直播 adapter、生产钱包、公网域名、TLS 证书运维、HA 或治理合约。

## 3. 建立计划目录

从仓库根目录执行：

```powershell
$pilotRoot = ".\tmp\pilot-plans\sepolia-001"
New-Item -ItemType Directory -Force $pilotRoot | Out-Null
Copy-Item .\config\examples\invited-public-pilot.sepolia.example.yaml `
  "$pilotRoot\pilot.yaml"
New-Item -ItemType Directory -Force `
  "$pilotRoot\inputs", "$pilotRoot\secrets", "$pilotRoot\state" | Out-Null
```

编辑 `pilot.yaml`。模板中的 `execution_requested` 默认是 `false`；它只表达操作者
是否申请执行，不是启动授权，也不替代各输入自己的 verifier。YAML 只记录非秘密字段
和秘密文件路径。以下内容不得写入 YAML、命令行、日志、fixture
或 transcript：

- 原始私钥、助记词、密码；
- RPC 完整 URL 或 API key；
- Pilot write token；
- Tailscale auth key；
- Clef keystore 密码。

秘密文件应限制为当前用户可读。Windows 需要核对 ACL；Linux 建议使用 `chmod 600`。
模板中的相对路径以 `pilot.yaml` 所在目录为基准。

## 4. 校验 YAML

先做结构和边界检查：

```powershell
uv run python .\tools\validate_invited_pilot_plan.py `
  .\tmp\pilot-plans\sepolia-001\pilot.yaml
```

准备完所有引用文件后，再做存在性检查：

```powershell
uv run python .\tools\validate_invited_pilot_plan.py `
  .\tmp\pilot-plans\sepolia-001\pilot.yaml --check-input-files
```

结构检查不读取秘密。`--check-input-files` 会在本机受限读取两个 RPC URL 和 write
token，以校验权限、格式和 RPC hostname 独立性，但绝不回显其内容或写入报告。
输出只有 config hash、commit、参与者/分组数量和文件计数。
机器输出始终包含 `execution_authorized:false`；`valid:true` 只表示该 validation
scope 通过，任何自动化都不得用它单独启动试点。
校验器会拒绝单 RPC、重复 node、少于两个操作者/网络组、同端口的管理/参与入口、
Funnel、错误的 900/30/10 profile、明显的内联秘密和仍含占位符的执行申请。
它只报告引用输入在执行校验的机器上存在，不把任意文件存在解释为“可执行”。

## 5. 执行顺序

### A. 冻结本机候选

在干净 worktree 运行仓库检查、测试和 900 秒本机 V2 门。把 exact commit 和实际
package/manifest hash 写入 YAML；旧 candidate 的报告不能接受新 commit。

### B. 检查外部 signer

对 Publisher、task issuer 和每个 node 逐一运行四层 `signer inspect`：static config、
rules/attestation、Clef binary SHA/version、只读 live probe。任一层失败即停止。
完整命令见 [CLI reference](../../api/cli-reference.md#4-publisher-and-registry)。

首轮必须由操作者在 Clef 上人工确认。LoveEngine 只传结构化请求，不接触私钥。

### C. 部署并发布 Registry release

1. 生成 SkillRegistry deployment EIP-1559 plan；
2. 人工核对 chain ID、sender、nonce、gas/fee cap、creation code hash 和 expiry；
3. 经验证的 Publisher Clef 签名，CLI 复核 raw transaction 后广播；
4. 生成并提交 `publishRelease` plan；
5. 在两个 RPC 上独立读取相同历史区块并验证 Registry、Publisher、ZIP 和 manifest。

`expires_at` 是 LoveEngine 客户端门，不是链上 deadline。过期 plan 和 signed envelope
必须废弃。详细流程见 [Publisher runbook](publisher-flow.zh-CN.md)。

### D. 启动双入口 Pilot

生成 PilotConfigV2、BootstrapV2、InviteV2、NodeTrustPolicyV1 和 service config。
先启动两个 loopback listener，并分别执行：

```powershell
uv run loveengine pilot status --url http://127.0.0.1:8780 --surface admin
uv run loveengine pilot status --url http://127.0.0.1:8781 --surface participant
```

只有 `/readyz` 同时验证 release/config/package/manifest/version 与 bootstrap 成员绑定后，
才允许 Tailscale Serve 接管 participant listener。Serve 必须精确映射一个 `*.ts.net`
主机的 `/` 到 participant loopback；不映射 admin、RPC 或 Clef。退出时恢复工具接管前
的自有 Serve 配置。

### E. 接入三名现实参与者

每名参与者从可信旁路分别取得 ZIP、trust policy、profile 和 assignment；InviteV2
只用于连接发现，不是信任根。节点必须使用自己的 Clef 地址、两个 RPC 文件和独立
cursor database。连接命令见 [Participant runbook](../participant-runbook.zh-CN.md)。

每个 node 的 `profile_file`、signer `config_file` 和 `cursor_database` 必须使用独立
路径。首轮 `resume_existing_cursor:false`，节点操作者必须在自己的主机上确认 cursor
路径不存在，再由 node connect 创建；只有具备同一 node/run 的恢复证据时才可显式设为
`true`。中央 YAML 校验器不声称能检查远端主机文件系统。

参与声明只记录 opaque operator/network group。它能发现“明显同组”，不能证明现实中
真正独立；试点组织者必须另存参与者授权与责任记录。

### F. 运行 900 秒核心闭环

在节点已认证连接后再入队任务。验收至少要求：

- 30 个真实输入事件和可重算 artifact；
- 3 个 observation receipt、3 个 review receipt；
- receipt 与连接、task、evidence、dispute 一一绑定并确认 ACK；
- 一次 ACK-loss/reconnect 恢复证据，计时直到认证节点实际重连；
- ProposalGate `ready`；
- 两个 RPC 在 transcript 的固定历史区块一致；
- 加独立 policy 后 verifier 返回 `chain_verified:true`、`trust_bound:true`；
- 所有拥有的进程退出、stderr 为空、secret finding 为 0、Serve 配置恢复。

## 6. 停止条件

出现以下任一情况立即停止，不降级为“部分通过”：

- RPC 对固定区块、Registry code/release 或 receipt 结果不一致；
- Clef binary/rules/audit evidence 不匹配，或操作者拒绝确认；
- Tailscale 状态、hostname、Serve root handler 或 Funnel 状态不符合计划；
- participant/admin 路由混合，write token 或 RPC URL 出现在输出中；
- 参与者、receipt、artifact、issuer、nonce、deadline 或 trust policy 绑定失败；
- exact commit、package hash、manifest hash 在运行期间变化；
- 清理、恢复、离线 transcript 验证或任一机器报告失败。

## 7. 通过后能声称什么

可以声称：同一 exact candidate 在 Sepolia 锚定 release、tailnet-only participant
入口和至少三名受邀节点上完成一次可复核 Witness 核心闭环。

仍不能声称：事件内容真实、节点在社会关系上独立、服务达到生产可用、私钥体系完成
生产审计、企业直播已接入，或治理合约已经适合真实资产。
