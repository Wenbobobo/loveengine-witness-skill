# Integration guide

本文给辅助开发团队使用。目标是让团队能直接接上 LoveEngine Witness Skill 的 M1/M2 开发，而不是重新解释整个 DAism 材料库。

## 1. 本地检查

```powershell
cd D:\zWenbo\AI\DAism
python .\tools\validate_loveengine_m0.py
python .\tools\validate_loveengine_m0.py --tamper-check
python .\tools\loveengine_m0_self_check.py
```

如果这三条命令不通过，先修复 M0 包一致性，再进入新功能开发。

## 2. 必读文件

开发前读：

- `README.md`
- `CONTRIBUTING.md`
- `docs/specs/love-engine-skill-spec.md`
- `docs/specs/love-engine-next-phase-spec.md`
- `docs/api/loveengine-contract-api.md`
- `docs/api/agent-skill-api.md`
- `skills/loveengine-witness/skill-manifest.json`

需要查原始设计时读：

- `LoveEngineSkill/UAS接口文档.md`
- `LoveEngineSkill/UAS 见证方案 2.0.md`
- `docs/kb/source-inventory.md`

## 3. 当前工程目标

下一阶段是 M1/M2 Local Witness Loop。

交付应覆盖：

- ManifestV2 / AgentNodeProfileV2 / EvidenceBundleV1 / TranscriptV1 schema。
- `tools/loveengine/` CLI。
- 本地见证者 fixture 生成。
- EIP-712 typed data 生成。
- Foundry 四合约原型。
- local relayer dry-run。
- local-loop transcript。

不要求覆盖：

- 真实直播平台。
- 真实 Agent 网络发现。
- 生产级身份系统。
- UHAH 完整业务。
- 交易型 Token。

## 4. 建议任务拆分

| 任务 | 输出 |
| --- | --- |
| Schema | JSON schema、fixture、schema tests |
| CLI | `loveengine manifest verify`、`node declare`、`evidence build` |
| Contracts | WitnessDAO、CorporateSink、PublicSink、StreamingEngine |
| EIP-712 | register/vote typed data、nonce/deadline/payload hash 测试 |
| Relayer | batchRegister/batchVote dry-run |
| Demo | local-loop 一键脚本和 transcript |

## 5. 接口边界

Agent 可以：

- 读源资料和 manifest。
- 校验 hash。
- 生成证据包和 typed data。
- 请求本地 signer 签名。
- 交给 relayer 提交。
- 生成 transcript。

Agent 不可以：

- 保存或读取私钥。
- 替见证者做不可追责的最终判断。
- 省略 `proposalId`、nonce、deadline、payload hash。
- 修改原始资料后不更新 hash。
- 把 UTO 当成金融资产设计。

## 6. PR 验收

PR 至少说明：

- 改了哪些层。
- 接口状态是 M0、M1 target 还是 M2 target。
- 是否改了 manifest 保护范围内的文件。
- 验证命令和输出摘要。
- 仍未完成的部分。

PR 模板在 `.github/pull_request_template.md`。
