# API documents

这里放给辅助开发团队和 Agent adapter 使用的接口文档。它们是整理后的开发入口，不替代仓库根目录的原始资料。

## 文档列表

| 文件 | 用途 |
| --- | --- |
| `docs/api/loveengine-contract-api.md` | 四合约接口、EIP-712 约束、事件和集成顺序 |
| `docs/api/agent-skill-api.md` | Skill manifest、Agent 节点声明、传播任务、证据包、transcript |
| `docs/api/agent-network-api.md` | SkillRegistry、签名节点身份、bootstrap、网络任务、回执和 Relay Hub |
| `docs/api/live-evidence-api.md` | LiveGateway、事件哈希链、EvidenceBundleV2、争议复核、ProposalGate 和只读面板 |
| `docs/api/lan-pilot-api.md` | M5 PilotConfig、鉴权、观察协议、持久链、显式投票、snapshot 和统一 transcript |
| `docs/api/cli-reference.md` | 完整 CLI、运行、后台 soak、验证矩阵和故障排查 |

## 状态标记

接口文档使用这些状态：

| 状态 | 含义 |
| --- | --- |
| M0 implemented | 已在当前 M0 文件或脚本中存在 |
| M0 fixture | 只有 fixture，没有真实协议实现 |
| M1 implemented | schema / CLI 已实现并有自动测试 |
| M2 local implemented | 本地链、合约、relayer 或 transcript 已实现 |
| M3 local implemented | 本地 Anvil、Relay Hub 和三节点进程试点已实现 |
| M4 local implemented | 本地直播证据、三节点复核和只读面板已实现 |
| M5 release candidate | 可安装包、LAN 服务、真实观察、恢复和统一链上 E2E 已实现；正式四小时 soak 是发布门槛 |
| Open | 仍需决策 |

## 关键约束

- Agent 不接触私钥。
- relayer 可以提交交易，但不能替见证者签名。
- `VoteSignature` 必须绑定 `proposalId`、nonce、deadline 和 payload hash。
- `PublicSink` 是只读公共查询入口。
- 直播证据和凭证只上链 hash，不把原文大文件塞进合约。
