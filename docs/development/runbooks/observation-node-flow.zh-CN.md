# 观察节点运行流程

适用目标：0.6.1-contract-public-pilot candidate

Observation Agent 验证 release、文字流、artifact 或 dispute，并签 task receipt；
它不需要投票钱包，绝不签 vote。

## 获取两份独立输入

1. pilot-invite.json：连接 URL 和公开 release 提示，可由 Pilot Server 分发。
2. pilot-trust-policy.json：必须经可信旁路取得，不能从同一个不可信 Relay 自报。

policy 固定 chain ID、Registry、Publisher、skill/version、actual ZIP Keccak、
manifest Keccak 和 allowed issuers。两者都不得包含 token、私钥、助记词或 keystore。

## 验证 package 与 profile

```powershell
uv run loveengine package verify <archive.zip> --expected-package-hash <policy-package-hash>
uv run loveengine node profile sign --input .\node-profile.json
```

node profile sign 只输出 EIP-712 typed_data 和 signer_required: true；它不会产生签名。
节点操作者必须让外部 signer 签名，再按 signed profile schema 组合签名结果。

## Dry run

```powershell
uv run loveengine node connect --invite .\pilot-invite.json --profile .\signed-profile.json --dry-run
```

dry-run 可以不带 trust policy，只检查连接计划，必须返回 trust_bound: false。它不
查询链、不验证 actual ZIP，也不建立 WebSocket，不能当作成功加入。

## Live connect

```powershell
uv run loveengine node connect --invite .\pilot-invite.json --trust-policy .\pilot-trust-policy.json --package <archive.zip> --profile .\signed-profile.json --rpc-url http://127.0.0.1:8545 --address <node-address> --cursor-db .\node.cursor.sqlite --expected-tasks 1 --output .\receipts.json
```

节点先比较 policy、invite、profile、RPC active release 和 actual ZIP，随后建立出站
WebSocket。任务还要匹配 allowed issuer、recipient、nonce 和 deadline。收到合法
任务后先 ACK，完成事件/artifact 或 dispute 验证后再签 receipt。

cursor-db 用于 SSE/WebSocket 重连。Relay 是 at-least-once；重复任务必须幂等，
同 ID 不同 payload、非成员、恶意 issuer、错绑或重复 receipt 均拒绝。

当前 node connect 是 expected-tasks/idle timeout 约束的有界会话，不是长期
scheduler 或自动直播发现服务。
