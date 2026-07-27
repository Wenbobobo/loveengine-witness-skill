# API documents

当前接口按“核心”和“可选治理实验”阅读，不按 M1-M6 历史阶段推断能力。

| 文档 | 当前用途 |
| --- | --- |
| [Agent Skill API](agent-skill-api.md) | manifest、package、evidence 和历史兼容格式 |
| [Agent Network API](agent-network-api.md) | Registry、profile/bootstrap、task/receipt、Relay |
| [Live evidence API](live-evidence-api.md) | event、artifact、finalize、dispute 和 Gate |
| [Local Pilot API](lan-pilot-api.md) | invite/policy、服务、stage、snapshot 和 transcript |
| [Contract API](loveengine-contract-api.md) | 核心 SkillRegistry 与四个治理实验合约的真实 ABI |
| [Extension interfaces](extension-interfaces.md) | transport、signer、source、storage adapter 边界 |
| [CLI reference](cli-reference.md) | 当前命令、信任模式和运行约束 |

状态口径：

- latest tag：v0.6.0-contract-public-pilot；
- current candidate：0.6.1-contract-public-pilot；
- local implemented：有代码和自动测试，但不代表远程/生产部署；
- governance experiment：不是 Witness Skill 默认核心；
- historical：只为旧 schema/fixture/transcript 兼容保留。

不可破坏约束：Agent 不接触私钥；invite 不是信任根；Observation Agent 不签 vote；
raw evidence 不上链；PublicSink 只读；离线完整性不能返回 trust_bound: true。
