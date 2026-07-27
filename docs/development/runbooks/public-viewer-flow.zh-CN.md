# 只读观察者流程

适用目标：0.6.1-contract-public-pilot candidate（本机 viewer）

Viewer 不需要 token、钱包或 Skill。当前已验证地址是
http://127.0.0.1:8780/demo/?lang=zh-CN；远程 URL 必须另行完成部署验收。

![只读面板入口](../../assets/runbooks/viewer/viewer-01-dashboard-zh.png)

选择 session 后检查 sequence、head hash、event count 和 artifact integrity。

![事件与 evidence 详情](../../assets/runbooks/viewer/viewer-02-evidence-detail-zh.png)

红色状态表示页面观察到 artifact 缺失、hash-chain 断裂、服务/链未 ready 或节点
掉线。要求主持人提供 transcript/audit log 复核，不要把错误隐藏为绿色。

![Gate 说明区域](../../assets/runbooks/viewer/viewer-03-gate-status-zh.png)

当前 Viewer 只展示 evidence/session 状态和静态 Gate 说明，不执行 finalize 或
ProposalGate，也不显示真实 vote、交易或 PublicSink 状态。最终结论以 package
hash、签名 receipts、transcript，以及治理实验中的 RPC/链上事件为准，截图不是
信任根。
