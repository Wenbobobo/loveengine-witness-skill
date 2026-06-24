# LoveEngine 参与者运行手册

状态：`current`
适用版本：`0.6.0-contract-public-pilot`

本文给非开发者和试点协作者使用。协议细节见 `docs/api/README.md`；这里
只说明“我是谁、打开哪里、运行哪条命令”。

详细截图步骤按角色拆分：

- [主持人操作流程](runbooks/operator-flow.zh-CN.md)
- [观察节点运行流程](runbooks/observation-node-flow.zh-CN.md)
- [投票见证者批准流程](runbooks/voting-witness-flow.zh-CN.md)
- [只读观察者流程](runbooks/public-viewer-flow.zh-CN.md)
- [发布者流程](runbooks/publisher-flow.zh-CN.md)

## 1. 主持人 / Operator

你负责启动服务、创建直播 session、发布文字、关闭 session，并查看
ProposalGate 是否放行。主持人不持有投票私钥。

最短启动：

```powershell
uv sync --frozen
uv run loveengine pilot quickstart --root .\pilot --open-ui
```

如果在 Debian/Tailscale 服务器上无图形界面：

```powershell
uv run loveengine pilot quickstart --root .\pilot --host 100.x.y.z --base-url http://100.x.y.z:8780 --headless
```

命令会输出 JSON：

- `operator_url`：主持人操作台；
- `dashboard_url`：只读面板；
- `invite_path`：发给观察节点的 invite 文件；
- `token_file`：写入 token 的本地文件路径。

不要把 token 复制到聊天、README、fixture、日志或 transcript。需要手动打开
页面时使用：

- 中文主持人页：`/operator/?lang=zh-CN`
- 英文主持人页：`/operator/?lang=en`

截图版步骤见[主持人操作流程](runbooks/operator-flow.zh-CN.md)。

## 2. 观察 Agent 节点 / Observation Node

你负责验证文字流、证据和争议任务，并签 observation/review 回执。你不需要
投票钱包，不会自动签投票。

最短流程：

```powershell
uv sync --frozen
uv run loveengine manifest verify
uv run loveengine node connect --invite .\pilot-invite.json --profile .\signed-profile.json
```

进入试点前检查：

- invite 中的 `server_url`、`relay_url`、`dashboard_url` 是否来自主持人；
- `chain_id`、`registry`、`publisher`、`package_hash` 是否与主持人公告一致；
- invite 不应包含 token、私钥、助记词或 keystore。

如果节点断线，重新执行同一条 connect 命令即可从本地 cursor 恢复。

截图与 dry-run 步骤见[观察节点运行流程](runbooks/observation-node-flow.zh-CN.md)。

## 3. 投票见证者 / Voting Witness

你负责在 Gate 放行后，手动批准一次链上投票。你的私钥应留在外部 signer、
RPC 钱包或你亲自控制的钱包中，不进入 Agent。

最短流程：

```powershell
uv run loveengine witness vote approve `
  --proposal-plan .\proposal-plan.json `
  --rpc-url http://127.0.0.1:8545 `
  --address <witness-address> `
  --output .\vote-approval.json
```

执行前核对：

- `proposal_id` 是否是当前 active proposal；
- `payload_hash` 是否来自已 finalize 的 EvidenceBundle；
- `deadline` 未过期；
- signer 地址与你的 witness 地址一致。

没有显式批准时，relayer 不能执行你的投票。

完整核对清单见[投票见证者批准流程](runbooks/voting-witness-flow.zh-CN.md)。

## 4. 只读观察者 / Public Viewer

你只需要浏览器，不需要安装、不需要 token、不需要钱包。

打开主持人提供的：

- 中文只读面板：`/demo/?lang=zh-CN`
- 英文只读面板：`/demo/?lang=en`

你可以看到 session 状态、事件时间线、EvidenceBundle hash、节点回执、
ProposalGate、PublicSink 和关键指标。页面没有写入操作。

截图版说明见[只读观察者流程](runbooks/public-viewer-flow.zh-CN.md)。

## 5. 发布者 / Publisher

你负责构建确定性 ZIP、发布 SkillRegistry release、维护版本状态。

```powershell
uv run loveengine package build --output .\dist
uv run loveengine package verify .\dist\loveengine-witness-0.6.0-contract-public-pilot.zip
uv run loveengine registry publish --input .\release-plan.json
uv run loveengine registry verify --input .\release-plan.json
```

Plugin 或 marketplace 只是分发入口，不是信任根。节点必须以链上的
SkillRegistry `packageHash` 校验安装包。

发布和撤销步骤见[发布者流程](runbooks/publisher-flow.zh-CN.md)。

## 6. 常见问题

| 现象 | 处理 |
| --- | --- |
| `pilot` 不是有效命令 | 你运行的是旧环境；执行 `uv sync --frozen`，再看 `uv run loveengine version` 的 `package_root`。 |
| 端口占用 | 改 `--port` 和 `--base-url`，或者停止旧服务。 |
| token 错误 | 重新读取 `token_file`；不要把 token 放进命令行参数。 |
| chain not ready | 先运行 `loveengine pilot chain status`，确认 chainId、地址和 code hash。 |
| package hash 不匹配 | 重新下载/安装包；不要跳过 `package verify`。 |
| 节点掉线 | 使用同一 invite 重连，节点会从 durable cursor 恢复。 |
| 只读页面看不到更新 | 检查 `/readyz`、SSE 连接和浏览器是否访问了正确 base URL。 |

## 7. Tailscale 与公网切换

M6 的 invite 使用统一 `public_base_url` 派生所有外部 URL。Tailscale 可用
`http://100.x.y.z:8780`，局域网可用 `http://192.168.x.y:8780`，公网域名可
在反向代理后使用 `https://loveengine.example`。切换域名时不要手工拼接单个
端点；重新生成 invite。
