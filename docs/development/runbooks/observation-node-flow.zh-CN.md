# 观察节点运行流程

适用角色：观察 Agent 节点 / Observation Node
适用版本：`0.6.0-contract-public-pilot`

观察节点通过 invite 加入 Relay，只验证文字流、证据和争议任务，并签
observation/review receipt。观察节点不需要投票钱包，也不会自动签投票。

## 1. 获取并检查 invite

主持人或发布者会提供 `pilot-invite.json`。先检查它不包含秘密：

```powershell
rg "token|private|mnemonic|keystore|secret" .\pilot-invite.json
```

预期：没有输出。

invite 应包含：

- `server_url`
- `relay_url`
- `dashboard_url`
- `chain_id`
- `registry`
- `publisher`
- `package_hash`

这些值应与主持人公告或 SkillRegistry release 一致。

## 2. 校验本地 Skill 包

```powershell
uv sync --frozen
uv run loveengine manifest verify
uv run loveengine package self-check --root .
```

如果 `package_hash_mismatch`、`publisher_mismatch` 或 `release_revoked` 出现，
停止加入，要求重新分发安装包或 release。

## 3. 准备节点 profile

节点 profile 只描述节点身份、版本和权限，不包含私钥。签名可以由外部 signer
或试点脚本生成。

```powershell
uv run loveengine node profile sign --input .\node-profile.json
```

输出的 signed profile 应绑定节点地址、profile hash、sequence 和有效期。

## 4. Dry-run 连接

```powershell
uv run loveengine node connect `
  --invite .\pilot-invite.json `
  --profile .\signed-profile.json `
  --dry-run
```

dry-run 只做校验，不执行长期 WebSocket 连接。失败时先看错误码：

- `package_hash_mismatch`：安装包不可信；
- `wrong_recipient`：任务不是发给该节点；
- `task_expired`：deadline 已过；
- `wrong_chain_id` 或 `wrong_registry`：invite 与链事实不一致。

## 5. 正式连接

```powershell
uv run loveengine node connect `
  --invite .\pilot-invite.json `
  --profile .\signed-profile.json
```

节点只建立出站 WebSocket，不需要公网 IP。断线后重新执行同一条命令，节点会从
本地 durable cursor 恢复未确认任务。

## 6. 对照 UI 状态

主持人或只读观察者可以在面板看到 Agent 连接数和 ACK 数。

![观察节点 ACK 反映在只读面板](../../assets/runbooks/viewer/viewer-01-dashboard-zh.png)

如果 Agent 数为 0，检查 Relay URL、网络连通性和 profile 有效期。
