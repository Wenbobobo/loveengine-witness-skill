# 发布者流程

适用目标：`0.7.0-invited-public-pilot` candidate
协议：`loveengine-witness-net/0.6`
最新 tag：`v0.6.0-contract-public-pilot`

PR #11 与 PR #12 已于 2026-08-10 合入 `main`，但 release/tag 尚未发布。Publisher 构建确定性 ZIP，生成
精确 Sepolia EIP-1559 transaction plan，经外部 signer 人工确认签名，并由 CLI 在
重新校验 plan、raw transaction 和 nonce 后广播。当前实现和本机测试已经覆盖这些
接口，但尚无真实 Clef 1.17.3 或 Sepolia deployment/release receipt；不得把以下
准备步骤称为已发布 release。

## Build 与本地完整性

```powershell
uv sync --frozen
uv run loveengine pilot contracts prepare
uv run loveengine package build --output .\dist
uv run loveengine package verify <archive.zip> --integrity-only
```

`integrity-only` 验证完整、非空 checksums，manifest/source hashes，release metadata、
必要文件、路径/大小和秘密文件规则，但必须返回 `trust_bound:false`。

## 可信验证与安装

```powershell
uv run loveengine package verify <archive.zip> --expected-package-hash <registry-keccak>
uv run loveengine package install <archive.zip> --target .\install-smoke --expected-package-hash <registry-keccak>
uv run loveengine package self-check --root .\install-smoke --expected-package-hash <registry-keccak>
```

`expected-package-hash` 是实际 ZIP bytes 的 Keccak-256，不是 manifest 中
`sha256:...` 的自引用 package hash。安装在临时目录完成全部验证后才切换 target。

## 外部 signer 前置门

首轮受邀试点只允许 Clef `manual_confirm`。兼容目标固定为经过来源与摘要核验的
Geth/Clef 1.17.3。Geth 1.17.4 已移除内置 Clef，不能把 Geth 1.17.5 当作 Clef
1.17.3 的升级或替代。Clef 只使用 IPC 或显式 loopback HTTP；raw key、master
seed、password 和 RPC credential 不进入 Agent context、CLI 参数、日志或 transcript。

`signer inspect` 分四个累积层级：

```powershell
# 1. static config
uv run loveengine signer inspect --config .\secrets\publisher-signer.json

# 2. ruleset + attestation evidence
uv run loveengine signer inspect --config .\secrets\publisher-signer.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json

# 3. evidence + exact Clef binary
uv run loveengine signer inspect --config .\secrets\publisher-signer.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json --clef-binary <clef-1.17.3-binary> --expected-binary-sha256 <sha256>

# 4. all prior evidence + read-only live probe
uv run loveengine signer inspect --config .\secrets\publisher-signer.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json --clef-binary <clef-1.17.3-binary> --expected-binary-sha256 <sha256> --probe
```

每个可选参数对必须完整；live probe 还要求 evidence 与 binary 两层已通过。probe 只
读取 Clef external API 版本，不签交易。expected binary digest 必须是
`sha256:<64 lowercase hex>`。即使四层都通过，也仍需真实人工确认签名、签后恢复
地址校验和链上 receipt，才能形成 end-to-end signer 证据。

## Sepolia deployment plan

先从经过 `pilot contracts prepare` 验证的 SkillRegistry artifact 生成 plan：

```powershell
uv run loveengine registry transaction deploy-plan --artifact <SkillRegistry-artifact.json> --sender <publisher> --nonce <nonce> --gas <gas-limit> --max-fee-per-gas <wei> --max-priority-fee-per-gas <wei> --created-at <unix> --expires-at <unix> --output .\plans\registry-deploy.json
```

发布者逐项核对：chain ID 11155111、sender、nonce、零 value、init-code/runtime hash、
gas、fee cap、created/expires 时间、request hash 和 plan hash。任一字段不符合预期就
废弃 plan，不允许 signer 自行“修正”。

## Sepolia release plan

release 输入必须绑定 `loveengine-witness`、`0.7.0-invited-public-pilot`、实际 ZIP
Keccak、canonical manifest Keccak、Publisher 和已经验证的 Registry：

```powershell
uv run loveengine registry transaction publish-plan --release <release.json> --sender <publisher> --nonce <nonce> --gas <gas-limit> --max-fee-per-gas <wei> --max-priority-fee-per-gas <wei> --created-at <unix> --expires-at <unix> --output .\plans\release-publish.json
```

plan 只表示待批准交易，不证明已经签名、广播或上链。

## 人工确认签名与提交

只有四层 signer 门通过，且发布者人工核对完整 plan 后才执行：

```powershell
uv run loveengine registry transaction sign --plan .\plans\release-publish.json --signer-config .\secrets\publisher-signer.json --ruleset-file .\clef\rules.js --rules-attestation-file .\clef\rules-attestation.json --clef-binary <clef-1.17.3-binary> --expected-binary-sha256 <sha256> --output .\plans\release-publish.signed.json
uv run loveengine registry transaction submit --plan .\plans\release-publish.json --signed .\plans\release-publish.signed.json --rpc-url-file .\secrets\sepolia-primary-rpc.txt
```

`sign` 只接受 Publisher role、allowlist 中的精确 request hash 和 `manual_confirm`；
签后重新解码 raw transaction，核对 sender、chain、nonce、to/data/value/gas/fees、
空 `accessList` 和
交易 hash。`submit` 再次验证 plan/signed envelope，读取主 RPC 当前 nonce，并只广播
同一 raw transaction。Sepolia RPC URL 必须从权限受限文件读取，不能直接放在命令行。

`expires_at` 只由 LoveEngine 的 `sign/submit` 强制执行，不会进入普通 EIP-1559 raw
transaction。过期后必须废弃 plan 和 signed envelope；将 raw transaction 交给其他
广播工具会绕过这个客户端时间门。若需要链上强制 deadline，必须另行设计智能账户或
合约入口，不能把当前字段解释为链上保护。

当前仓库没有真实执行上述两步的外部证据。普通开发验收应停在 plan 和负向校验，
不得使用 Anvil unlocked signer 冒充 Sepolia Publisher，也不得在无明确资金/窗口/
人工批准时发送交易。

## 双 RPC 只读确认

`registry verify` 可对两个不同、独立配置的 Sepolia RPC 分别做“当前 active release”
smoke；每个 RPC URL 均从受限文件读取：

```powershell
uv run loveengine registry verify --rpc-url-file .\secrets\sepolia-primary-rpc.txt --artifact <archive.zip> --chain-id 11155111 --registry <registry> --publisher <publisher> --skill-id loveengine-witness --version 0.7.0-invited-public-pilot
uv run loveengine registry verify --rpc-url-file .\secrets\sepolia-secondary-rpc.txt --artifact <archive.zip> --chain-id 11155111 --registry <registry> --publisher <publisher> --skill-id loveengine-witness --version 0.7.0-invited-public-pilot
```

这两条命令不固定相同 block，不能单独作为双 RPC 历史一致性证据。正式验收必须由
WitnessCoreTranscriptV2 记录一个 final safe block，并让 `pilot transcript verify`
通过两个 RPC 在该 block 核对 receipt/code/release。两个 RPC 一致只证明 chain
consistency；再匹配外部 NodeTrustPolicyV1，才可返回 `chain_verified:true` 和
`trust_bound:true`。它仍不证明 Publisher 现实身份、artifact 内容真实或服务达到
生产可用性。

旧 `loveengine registry publish --input <release> --dry-run` 仍是兼容计划入口，旧
`registry verify --release ...` 只返回本地 release-file consistency。Plugin、
marketplace、InviteV2 或镜像只负责发现/传输，不能替代 Registry 与独立 trust policy。
出现问题时 Publisher 可把 release 标为 deprecated/revoked；revoked 不可恢复。

完整退出门见 [0.7 SPEC](../../specs/love-engine-invited-public-pilot.md)。
