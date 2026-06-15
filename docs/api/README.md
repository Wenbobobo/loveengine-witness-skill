# API documents

这里放给辅助开发团队和 Agent adapter 使用的接口文档。它们是整理后的开发入口，不替代 `LoveEngineSkill/` 下的原始资料。

## 文档列表

| 文件 | 用途 |
| --- | --- |
| `docs/api/loveengine-contract-api.md` | 四合约接口、EIP-712 约束、事件和集成顺序 |
| `docs/api/agent-skill-api.md` | Skill manifest、Agent 节点声明、传播任务、证据包、transcript |

## 状态标记

接口文档使用这些状态：

| 状态 | 含义 |
| --- | --- |
| M0 implemented | 已在当前 M0 文件或脚本中存在 |
| M0 fixture | 只有 fixture，没有真实协议实现 |
| M1 target | 下一步 schema / CLI 目标 |
| M2 target | 本地链、合约或 relayer 目标 |
| Open | 仍需决策 |

## 关键约束

- Agent 不接触私钥。
- relayer 可以提交交易，但不能替见证者签名。
- `VoteSignature` 必须绑定 `proposalId`、nonce、deadline 和 payload hash。
- `PublicSink` 是只读公共查询入口。
- 直播证据和凭证只上链 hash，不把原文大文件塞进合约。
