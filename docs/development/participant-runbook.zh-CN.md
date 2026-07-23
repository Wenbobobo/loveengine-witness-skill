# LoveEngine Witness 参与者手册

适用目标：0.6.1-contract-public-pilot candidate
最新 tag：v0.6.0-contract-public-pilot
当前已验证环境：本机 loopback + Anvil

本仓库只有一个 loveengine-witness Skill。默认流程是发布信任、Agent 任务/回执、
证据、争议复核和 ProposalGate；治理投票是可选实验。

## 选择角色

| 角色 | 阅读入口 | 是否写入 | 是否签名 |
| --- | --- | --- | --- |
| 主持人 | [Operator flow](runbooks/operator-flow.zh-CN.md) | session/event/close/finalize | 不签 receipt/vote |
| 观察节点 | [Observation node flow](runbooks/observation-node-flow.zh-CN.md) | ACK/receipt | 只签 task receipt |
| 投票见证者 | [Voting witness flow](runbooks/voting-witness-flow.zh-CN.md) | 可选 vote approval | 只在本人确认后签 vote |
| 只读观察者 | [Viewer flow](runbooks/public-viewer-flow.zh-CN.md) | 无 | 无 |
| 发布者 | [Publisher flow](runbooks/publisher-flow.zh-CN.md) | 外部 signer 提交 Registry tx | CLI 本身不签/不发 tx |

## 所有人先确认

1. invite 只负责连接，NodeTrustPolicyV1 才定义节点应信任的 release/issuer。
2. raw private key、mnemonic、keystore 和 token 不进入 Agent 对话、CLI 参数、
   fixture、日志、snapshot 或 transcript。
3. Observation Agent 和 Voting Witness 是不同角色；任务协议禁止 vote signing。
4. evidence 完整性不等于发言真实性，三个本机 actor 不等于三个独立组织。
5. ProposalGate 是链下 advisory check，不是 WitnessDAO 合约权限。
6. Operator/Viewer UI 不执行 finalize、Gate、vote 或 PublicSink 查询。

## 本机入口

```powershell
uv sync --frozen
uv run loveengine pilot quickstart --root .\pilot --headless
```

核心自动实验：

```powershell
uv run loveengine demo lan-pilot --stage core --events 12 --observers 10 --output .\core-output
```

只有要验证治理合约时才运行：

```powershell
uv run loveengine demo lan-pilot --stage governance --events 12 --observers 10 --output .\governance-output
```

quickstart 和 demo 都不是 SSH、Tailscale、公网、公共测试网或生产部署命令。详细
数据流与验证边界见[核心架构](../architecture/witness-core-and-data-flow.zh-CN.md)。
