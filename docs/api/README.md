# API documents

这里放给辅助开发团队和 Agent adapter 使用的接口文档。它们是整理后的开发入口，不替代仓库根目录的原始资料。

## 文档列表

| 文件 | 用途 |
| --- | --- |
| `docs/api/loveengine-contract-api.md` | 四合约接口、EIP-712 约束、事件和集成顺序 |
| `docs/api/agent-skill-api.md` | Skill manifest、Agent 节点声明、传播任务、证据包、transcript |
| `docs/api/agent-network-api.md` | SkillRegistry、签名节点身份、bootstrap、网络任务、回执和 Relay Hub |

## 状态标记

接口文档使用这些状态：

| 状态 | 含义 |
| --- | --- |
| M0 implemented | 已在当前 M0 文件或脚本中存在 |
| M0 fixture | 只有 fixture，没有真实协议实现 |
| M1 implemented | schema / CLI 已实现并有自动测试 |
| M2 local implemented | 本地链、合约、relayer 或 transcript 已实现 |
| M3 target | 当前 Agent 网络试点实施目标 |
| M4+ target | 后续真实证据和争议闭环目标 |
| Open | 仍需决策 |

## 关键约束

- Agent 不接触私钥。
- relayer 可以提交交易，但不能替见证者签名。
- `VoteSignature` 必须绑定 `proposalId`、nonce、deadline 和 payload hash。
- `PublicSink` 是只读公共查询入口。
- 直播证据和凭证只上链 hash，不把原文大文件塞进合约。
