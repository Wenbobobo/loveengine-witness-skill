# 只读观察者流程

适用角色：只读观察者 / Public Viewer  
适用版本：`0.6.0-contract-public-pilot`

只读观察者只需要浏览器。你不需要安装 Skill，不需要 token，不需要钱包。

## 1. 打开只读面板

主持人会给你一个 URL，例如：

```text
http://127.0.0.1:8780/demo/?lang=zh-CN
```

Tailscale 试点可能是：

```text
http://100.x.y.z:8780/demo/?lang=zh-CN
```

![只读面板入口](../../assets/runbooks/viewer/viewer-01-dashboard-zh.png)

页面应标明“只读 · 不签名”。如果页面要求 token，你打开的是主持人页面，不是
公开面板。

## 2. 查看 session

点击左侧 session 卡片，查看事件时间线、head hash 和 artifact integrity。

![查看 EvidenceBundle 与事件链](../../assets/runbooks/viewer/viewer-02-evidence-detail-zh.png)

你需要关注：

- 事件数量是否持续增加；
- 事件顺序是否连续；
- artifact integrity 是否为已验证；
- Agent 数和 Relay ACK 是否符合试点说明；
- 是否出现红色错误状态。

## 3. 理解错误状态

红色状态表示该页面能观察到不一致，而不是页面“坏了”。常见原因：

- artifact 缺失；
- session hash-chain 断裂；
- Chain 或 Relay 未 ready；
- Agent 掉线；
- package hash 或 Registry release 不匹配。

出现红色状态时，要求主持人提供 transcript 或 audit log 复核。

## 4. 复核最终状态

ProposalGate 放行并执行后，页面应能把证据、节点回执、proposal plan、投票和
PublicSink 状态串起来。

![Gate 与最终状态](../../assets/runbooks/viewer/viewer-03-gate-status-zh.png)

只读页面不是信任根。最终以链上合约事实、package hash、签名回执和 transcript
为准。
