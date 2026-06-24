# LoveEngine M5 LAN Pilot SPEC

状态：`release candidate; formal four-hour soak pending`
目标版本：`0.5.0-lan-pilot`
协议版本：`loveengine-witness-net/0.5`
依赖版本：`0.4.0-live-evidence-pilot`

## 1. 核心目标

M5 将 M4 从本地协议 Demo 提升为可安装、可分发、可在局域网真实运行、可观察和可恢复的小规模试点：

```text
deterministic Skill ZIP
→ clean install and verification
→ LAN Pilot Server
→ operator text stream
→ three observe_live_text Agents
→ EvidenceBundleV2
→ dispute review and ProposalGate
→ explicit witness vote approval
→ WitnessDAO execution
→ PublicSink query
→ PilotTranscriptV1
```

固定规模为一台局域网服务器、三个独立 Agent、最多十个只读观察者、单场最长四小时。链上事实层使用固定 chainId 的持久化 Anvil。

## 2. Gate 0：监督基线与文档

- [x] 独立提交 M4 监督文档、索引和 manifest/hash 更新。
- [x] 将 M4 SPEC 归档到 `docs/archive/specs/implemented/`。
- [x] 创建本 SPEC 并将企业补偿顺延到 M6。
- [x] 校准 README、Agent Skill API、文档入口和 manifest。

验收：M0–M4 测试、合约和 transcript 保持兼容。

## 3. M5.1：标准 Skill 与确定性发布

- [x] 新增 Codex 优先、协议可移植的 `SKILL.md` 和 `agents/openai.yaml`。
- [x] 实现确定性 ZIP、`checksums.json` 和 `sbom.spdx.json`。
- [x] ZIP 只包含安装运行必需的 Skill、Python、Schema、manifest、合约和发布元数据。
- [x] 拒绝路径穿越、符号链接、秘密文件、缺失文件和 checksum 篡改。
- [x] Registry publish 使用实际 ZIP Keccak-256；该值由发布交易绑定，避免 archive 自含 hash 的循环依赖。
- [x] 实现 package build、verify、install 和 self-check CLI。

验收：同一提交两次构建 byte-for-byte 相同；全新目录可验证、安装、自检及执行 `uv sync --frozen`。

## 4. M5.2：局域网 Pilot Server

- [x] 新增 `PilotConfigV1` 和单一 aiohttp 控制服务。
- [x] 组合 LiveGateway、Relay、dashboard、operator UI、health、readiness 和 metrics。
- [x] 主持人页面支持创建、发布文字和关闭 session。
- [x] 所有写接口要求从受限文件加载的 Bearer token；只读接口局域网开放。
- [x] token 不进入参数、日志、fixture、页面存储或 transcript。
- [x] 增加 artifact 下载、同源写入检查和结构化错误。

验收：错误或缺失 token 无法写入；只读 dashboard 与 SSE 可被十个观察者同时访问。

## 5. M5.3：真实文字观察

- [x] 固定 `ObserveLiveTextPayloadV1`、`LiveObservationReceiptV1` 和 `ObservationSetV1`。
- [x] session 创建后向三个节点派发 `observe_live_text`。
- [x] Agent 通过 SSE 验证 sequence、previous hash、content hash 和 artifact hash。
- [x] Agent 持久化 cursor 并在断线后恢复。
- [x] session 关闭后生成签名观察回执。
- [x] 三个不同节点的有效回执聚合为 ObservationSet。
- [x] ObservationSet 与 EvidenceBundleV2 相互绑定。

验收：错误链、重复事件、cursor 回退、过期任务、错误 recipient 和 Relay 篡改均被拒绝；三个节点得到同一 head hash。

## 6. M5.4：持久化链与显式投票

- [x] 实现 chain init/start/status/snapshot/restore。
- [x] 保存 chainId、合约地址、部署交易、code hash 和起始区块。
- [x] 重启后恢复 Anvil state 并复核部署清单。
- [x] ProposalGate 输出链上 proposal plan。
- [x] 见证者通过显式 CLI 与 RPC signer 批准投票。
- [x] Agent 不自动签投票；relayer 只聚合和提交。

验收：没有显式批准不能执行；错误 proposalId、payloadHash、nonce、deadline 或 signer 均失败。

## 7. M5.5：统一系统 E2E

- [x] 从 ZIP 安装 Skill。
- [x] 启动 Anvil、Pilot Server 和三个独立 Agent。
- [x] 通过 HTTP/operator 路径摄取真实文字。
- [x] 完成观察、证据、争议复核和 ProposalGate。
- [x] 完成提案、五个显式见证者批准、relayer 提交和 PublicSink 查询。
- [x] 生成 `PilotTranscriptV1` 并支持离线复核。

验收：archive、事件、观察、证据、复核、gate、proposal、投票、交易和 PublicSink 状态可以双向追溯；任一中间 hash 修改都会失败。

## 8. M5.6：可观测性、备份和恢复

- [x] 所有请求和任务携带 run/correlation ID。
- [x] 输出脱敏 JSONL 运行日志和 hash-chained append-only audit JSONL。
- [x] `/healthz` 只表示进程存活；`/readyz` 检查存储、Relay 和 Anvil。
- [x] `/v1/metrics` 输出连接、速率、lag、队列、ACK 延迟、拒绝、磁盘、链块和恢复次数。
- [x] session 关闭及链上执行后自动生成校验 snapshot。
- [x] snapshot 包含 SQLite backup、artifact、audit、Anvil state 和 checksums。
- [x] 默认保留 30 天，由显式 prune 命令清理。

验收：服务和 Anvil 重启后 cursor、队列、session、地址和审计链一致恢复；损坏 snapshot 被拒绝。

## 9. M5.7：试点与发布

- [x] CI 运行 package/install 及加速故障与负载 E2E。
- [x] 正式 soak runner 支持 3 节点、10 观察者、4 小时、不少于 240 条事件。
- [x] soak runner 模拟服务重启、Anvil 重启和三个 Agent 断线。
- [ ] committed event 零丢失、零 sequence gap。
- [ ] ACK p95 小于 2 秒、max 小于 5 秒。
- [ ] 故障后 30 秒内恢复并清空队列。
- [ ] 服务内存低于 512 MB，单场新增磁盘低于 250 MB。
- [ ] secret scan 为 0。
- [ ] GitHub Release 附带 ZIP、checksums、SBOM 和验收报告；需正式四小时报告。
- [ ] 发布 `v0.5.0-lan-pilot`。

## 10. 边界

- 只修改独立 LoveEngineSkill 仓库。
- 不接入具体直播平台 SDK。
- 不部署公共测试网或公网服务。
- 不实现 TLS、HA、P2P、端到端加密或生产密钥托管。
- 不实现浏览器钱包、Clef、AA signer、自动投票、视频处理或多模态分析。
- 默认不修改四个核心业务合约。
- 企业补偿试点顺延至 M6。
