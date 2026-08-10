# LoveEngine Witness 参与者手册

适用目标：`0.7.0-invited-public-pilot` candidate
协议：`loveengine-witness-net/0.6`
最新 tag：`v0.6.0-contract-public-pilot`

0.7 分支叠加在尚未人工合并的 0.6.1 PR #11 上。当前已实现本机 V2、双入口、
外部 signer adapter、Sepolia transaction plan、双 RPC 校验契约和 Tailscale Serve
preflight；尚未完成真实 Clef/Sepolia/Tailscale/受邀远端验收。不要把本文中的准备
命令描述为已经上线的公开试点。

本仓库只有一个 `loveengine-witness` Skill。默认流程是发布信任、Agent 任务/回执、
证据、争议复核和 ProposalGate；治理投票是可选实验。

## 选择角色

| 角色 | 阅读入口 | 是否写入 | 是否签名 |
| --- | --- | --- | --- |
| 主持人 | [Operator flow](runbooks/operator-flow.zh-CN.md) | 仅 admin surface 的 session/event/close/finalize/task ingress | 不签 receipt/vote |
| 观察节点 | [Observation node flow](runbooks/observation-node-flow.zh-CN.md) | WebSocket ACK/receipt | 只经外部 signer 签 task receipt |
| 投票见证者 | [Voting witness flow](runbooks/voting-witness-flow.zh-CN.md) | 可选 governance vote approval | 只在本人确认后签 vote |
| 只读参与者 | [Viewer flow](runbooks/public-viewer-flow.zh-CN.md) | 无；仅 participant allowlist | 无 |
| 发布者 | [Publisher flow](runbooks/publisher-flow.zh-CN.md) | 外部 signer 签名，CLI 重验后提交 Registry tx | 首轮逐笔 manual confirm |

## 所有人先确认

1. `PilotInviteV2` 只负责连接发现，`NodeTrustPolicyV1` 才定义节点应信任的
   chain/Registry/Publisher/release/issuer；二者通过不同渠道取得并分别核验。
2. raw private key、mnemonic、keystore、Clef seed/password、RPC credential、Pilot
   token 和 Tailscale auth key 不进入 Agent 对话、CLI 参数、普通配置、日志、snapshot
   或 transcript。Sepolia RPC URL 从权限受限文件读取。
3. Observation Agent 和 Voting Witness 是不同角色；任务协议禁止 vote signing。
4. evidence 完整性不等于发言真实性，本机 actor 不等于现实独立组织，参与者
   attestation 也不是对事实的背书。
5. ProposalGate 是链下 advisory check，不是 WitnessDAO 合约权限。
6. participant surface 没有 POST、token、Operator UI、snapshot、task ingress、RPC
   或 signer endpoint；这些只存在于分离的 admin/本机边界。

## 本机复现入口

```powershell
uv sync --frozen
uv run loveengine pilot contracts prepare
uv run loveengine pilot quickstart --root .\pilot --headless
```

核心兼容实验：

```powershell
uv run loveengine demo lan-pilot --stage core --events 12 --observers 10 --output .\core-output
```

0.7 本机 V2 固定门：

```powershell
uv run loveengine pilot soak --stage core --duration-seconds 900 --events 30 --observers 10 --core-transcript-version 2 --output .\v2-acceptance
uv run loveengine pilot transcript verify .\v2-acceptance\witness-core.fixture.json
```

该 V2 结果必须标记 `environment: local_anvil` 和模拟 actor，只证明本地协议绑定。
只有验证治理合约时才运行 `--stage governance`；它不是受邀试点的前置条件。

## 受邀观察节点准备

操作者应通过可信旁路分别交付以下非秘密或受限文件：

- `pilot-invite-v2.json`：HTTPS/WSS `*.ts.net` participant 地址和公开 release hint；
- `pilot-trust-policy.json`：chain ID 11155111、Registry、Publisher、skill/version、
  ZIP/manifest hash 和 allowed issuers；
- 确定性 ZIP 与签名 node profile；
- 节点自己的 `ExternalSignerConfigV1`、ruleset/attestation 和经过核验的 Clef 1.17.3；
- 两个由不同提供方或独立基础设施提供、内容不同的受限 Sepolia RPC URL 文件。

先检查 signer。四层检查含义见 [CLI 参考](../api/cli-reference.md)：

```powershell
uv run loveengine signer inspect --config .\secrets\node-signer.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json --clef-binary <clef-1.17.3-binary> --expected-binary-sha256 <sha256> --probe
```

首轮只接受 `manual_confirm`。Geth 1.17.4 已移除内置 Clef；Geth 1.17.5 不能作为
Clef 1.17.3 的替代或升级证据。`--probe` 只读版本，不证明签名成功。

节点只建立出站连接：

```powershell
uv run loveengine node connect --invite .\pilot-invite-v2.json --trust-policy .\pilot-trust-policy.json --package <archive.zip> --profile .\node-profile.json --rpc-url-file .\secrets\sepolia-primary-rpc.txt --secondary-rpc-url-file .\secrets\sepolia-secondary-rpc.txt --signer-config .\secrets\node-signer.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json --clef-binary <clef-1.17.3-binary> --expected-binary-sha256 <sha256> --cursor-db .\node.cursor.sqlite --expected-tasks 1
```

节点会先验证两个 RPC 的记录区块/release 一致、policy、ZIP、profile 和 signer，再
连接 InviteV2 的 WSS Relay。两个 RPC 一致但没有 policy 只构成 chain consistency；
invite 自洽也不能替代 policy。节点执行任务前需人工确认签名，先将 receipt 写入
本地 journal，再发送并完成 Relay receipt confirmation。

## 现实参与者声明

参与者声明只表达“谁在什么时间以什么角色参与了哪次 run”，不表达事件内容为真：

```powershell
uv run loveengine participant attest --input .\participant-attestation.unsigned.json --signer-config .\secrets\participant-signer.json --trust-policy .\pilot-trust-policy.json --bootstrap .\bootstrap.json --invite .\pilot-invite-v2.json --assignment-task .\signed-assignment-task.json --service-config .\participant-service-config.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json --clef-binary <clef-1.17.3-binary> --expected-binary-sha256 <sha256> --output .\participant-attestation.json
```

命令会先把 unsigned attestation 与 trust policy、bootstrap membership、InviteV2、
分配给该节点的签名任务、participant service config 和 Clef evidence 交叉核对。
签名前仍须人工核对 run ID、role、node/profile、package/manifest/service-config hash、
assignment task/payload、InviteV2/trust-policy hash、ruleset/rules-attestation hash、
operator/network group hash 和时间范围。不要签未知 run、未知 release 或替他人
生成的身份声明。

## 只读参与者入口

只访问 InviteV2 中的 `participant_url`/`dashboard_url`。正常 participant surface
不应出现 Operator 页面、write token、POST、metrics、snapshot、RPC 或 signer 路由；
出现任一项应立即停止并通知操作者。Tailscale ACL 只限制可达性，不证明现实身份，
也不把 dashboard 变成信任根。

真实受邀试点必须生成 `environment: sepolia_invited_pilot` 的终态 V2 transcript，
满足双 RPC + policy、三名受邀节点、900 秒门、清理/恢复和零秘密发现。当前仓库还
没有这份证据。详细流程见[0.7 SPEC](../specs/love-engine-invited-public-pilot.md)和
[核心架构](../architecture/witness-core-and-data-flow.zh-CN.md)。
