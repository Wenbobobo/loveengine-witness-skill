# 发布者流程

适用目标：0.6.1-contract-public-pilot candidate

Publisher 构建确定性 ZIP、生成未提交的 Registry publish plan，并在外部 signer
提交后执行只读验证。仓库 CLI 当前不会直接发送 publish transaction。

## Build 与本地完整性

```powershell
uv run loveengine package build --output .\dist
uv run loveengine package verify <archive.zip> --integrity-only
```

integrity-only 验证完整、非空 checksums，manifest/source hashes，release metadata、
必要文件、路径/大小和秘密文件规则，但必须返回 trust_bound: false。

## 可信验证与安装

```powershell
uv run loveengine package verify <archive.zip> --expected-package-hash <registry-keccak>
uv run loveengine package install <archive.zip> --target .\install-smoke --expected-package-hash <registry-keccak>
uv run loveengine package self-check --root .\install-smoke --expected-package-hash <registry-keccak>
```

expected-package-hash 是 actual ZIP bytes 的 Keccak-256，不是 manifest 中
sha256:... 的自引用 package hash。安装在临时目录完成全部验证后才切换 target。

## Registry plan 与只读确认

```powershell
uv run loveengine registry publish --input .\release-plan.json --dry-run
uv run loveengine registry verify --rpc-url <rpc-url> --artifact <archive.zip> --chain-id <chain-id> --registry <registry> --publisher <publisher> --skill-id loveengine-witness --version 0.6.1-contract-public-pilot
```

publish 只输出 calldata/交易计划；外部 signer 提交并确认后，verify 查询指定
SkillRegistry 的 active release，比较 ZIP Keccak 和 manifest Keccak。旧
--release 本地模式只返回 release_file_consistency 与 trust_bound: false。

Plugin、marketplace、invite 或镜像只负责发现/传输，不能替代 Registry 和独立
NodeTrustPolicyV1。出现问题时 Publisher 可把 release 标为 deprecated/revoked；
revoked 不可恢复。
