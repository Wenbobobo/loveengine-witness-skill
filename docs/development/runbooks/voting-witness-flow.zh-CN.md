# 投票见证者批准流程

适用角色：投票见证者 / Voting Witness
适用版本：`0.6.0-contract-public-pilot`

投票见证者拥有链上 witness 地址。只有 ProposalGate 放行后，见证者才手动批准
一次投票。Agent 不自动投票，Relayer 不能替见证者签名。

## 1. 获取 proposal plan

主持人或提案协调者会提供 `proposal-plan.json`。先查看关键字段：

```powershell
uv run loveengine proposal gate --bundle .\evidence-bundle.json --output .\proposal-plan.json
```

或直接打开已经生成的 plan，核对：

- `proposal_id`
- `payload_hash`
- `evidence_bundle_hash`
- `deadline`
- `witness_address`
- `streaming_engine`
- `public_sink`

## 2. 核对链事实

```powershell
uv run loveengine pilot chain status `
  --root .\pilot-chain `
  --rpc-url http://127.0.0.1:8545
```

检查 chainId、合约地址和 code hash 与主持人公告一致。不要在 chain not ready
时签名。

## 3. 显式批准投票

```powershell
uv run loveengine witness vote approve `
  --proposal-plan .\proposal-plan.json `
  --rpc-url http://127.0.0.1:8545 `
  --address <witness-address> `
  --output .\vote-approval.json
```

私钥应保留在外部 RPC signer、钱包或你亲自控制的 signer 中。不要把私钥、
助记词或 keystore 交给 Agent。

## 4. 交付 vote approval

把 `vote-approval.json` 交给 relayer 或提案协调者。relayer 只能提交你的签名，
不能修改：

- `proposalId`
- `payloadHash`
- `reasonHash`
- `nonce`
- `deadline`
- `support`

错误 proposalId、payloadHash、nonce、deadline 或 signer 都会失败。

## 5. 在只读面板确认执行

提案执行后，只读面板应能显示 PublicSink 最终状态。

![只读面板中的证据与执行状态](../../assets/runbooks/viewer/viewer-03-gate-status-zh.png)

如果状态不一致，以链上事件、proposal plan 和 transcript 为准，不以截图为准。
