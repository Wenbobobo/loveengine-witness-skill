# 投票见证者流程（可选治理实验）

适用目标：0.6.1-contract-public-pilot candidate governance stage

Voting Witness 与 Observation Agent 是不同角色。只有本人明确选择参加治理实验时，
才通过外部 RPC signer 批准 vote；Agent、Relay 和主持人都不能替你签名。

## 审阅正确的 plan

witness vote approve 读取 OnchainProposalPlanV1。它只有：

- schema_version、chain_id、witness_dao；
- proposal_id、payload_hash；
- support、reason_hash、deadline。

EvidenceBundle、Gate 结果、StreamingEngine 和 PublicSink 地址不在该 schema 内，
必须从核心 transcript、部署记录和链上读取后另行交叉核对。不要把链下
ProposalPlan 与 OnchainProposalPlanV1 混为一份文件。

## 查询并显式批准

```powershell
uv run loveengine pilot chain status --root .\pilot-chain --rpc-url http://127.0.0.1:8545
uv run loveengine witness vote approve --proposal-plan .\onchain-proposal-plan.json --rpc-url http://127.0.0.1:8545 --address <witness-address> --output .\vote-approval.json
```

CLI 重新查询 chain ID、active proposal、payload hash、witness 注册和 nonce，然后
请求 eth_signTypedData_v4。它不接受私钥参数。确认钱包中显示的 proposalId、
payloadHash、support、reasonHash、nonce 和 deadline 与审阅结果一致。

vote-approval.json 可交给 permissionless relayer。relayer 只能提交有效签名，不能
修改绑定字段。UI 目前不显示 vote 或 PublicSink 状态；应使用链上事件和
PilotTranscriptV2 复核，而不是截图。

限制：ProposalGate 是链下 advisory check，WitnessDAO 不强制验证它；当前流程依靠
见证者在签名前人工确认 Gate/evidence 引用。
