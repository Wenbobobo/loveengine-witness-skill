# M5 release closeout execution plan

状态：historical closeout checklist；已由 0.6.1 核心优化规格取代
日期：2026-06-23
适用版本：`0.5.0-lan-pilot`
权威规格归档：`docs/archive/specs/implemented/love-engine-lan-pilot-spec.md`

## 1. 结论与问题边界

本计划记录 M5 发布候选的工程收口：修复主工作
目录看不到 `pilot` 命令的问题，使长时间 soak 能独立于终端会话运行，改进
局域网操作界面的可观察性，整理 README 和许可证，并将经过验证的变更通过
PR 合并回 `main`。

用户观察到的：

```text
argument command: invalid choice: 'pilot'
```

根因是本地主工作目录仍停在不含 M5 的旧版 `main`，而 M5 工作树中的 CLI
已经注册 `package`、`pilot` 和 `witness`。因此必须同时解决代码交付和运行
来源可诊断性，不能只修改 argparse。

## 2. 不可变约束

- 仅修改独立 `LoveEngineSkill` 仓库。
- 四个核心业务合约保持不变。
- Agent 不能自动签署投票。
- token、私钥、mnemonic 和 keystore 不进入参数、日志、截图、fixture 或
  transcript。
- 四小时试点是发布 tag 的硬门槛；一小时预运行只能证明长时间运行路径，
  不能冒充四小时验收。
- Git 集成顺序固定为：功能分支推送、PR、远端检查、合并、主线
  fast-forward、删除已合并分支与 worktree。

## 3. Milestone 0：基线和计划冻结

- [x] 0.1 确认 `main`、`origin/main`、M5 分支和 worktree 的提交关系。
- [x] 0.2 在旧 `main` 复现 `pilot` 无效，在 M5 分支确认命令存在。
- [x] 0.3 盘点当前 operator、dashboard、README、API 和发布门槛。
- [ ] 0.4 将本计划加入文档索引、资料清单和 manifest 保护范围。
- [ ] 0.5 运行仓库检查，确认计划文档没有破坏 hash 或活动链接。

验收产物：

- 本计划可由文档入口发现。
- `tools/check.py` 通过。
- Git diff 只包含预期的计划和索引改动。

## 4. Milestone 1：CLI 来源诊断与后台 soak

### 1.1 回归测试先行

- [ ] 增加 CLI 测试，断言安装后的顶层帮助包含 `package`、`pilot` 和
  `witness`。
- [ ] 增加 `pilot soak` 参数解析测试。
- [ ] 增加后台启动和状态查询的正例、失败进程和陈旧 PID 测试。
- [ ] 验证后台命令不会把环境变量值或 token 写入状态文件。

### 1.2 运行来源诊断

- [ ] 在版本或诊断输出中提供包版本、代码根目录和当前协议版本。
- [ ] 增加自检，明确区分“当前代码未包含 M5”和“CLI 参数错误”。
- [ ] 文档要求使用 `uv sync --frozen` 后再运行 CLI。
- [ ] 在 M5 API 中记录旧工作树/旧 editable install 的排查方法。

### 1.3 后台长跑入口

- [ ] 保持现有前台命令兼容：

```powershell
uv run loveengine pilot soak --duration-seconds 14400 --events 240 --observers 10 --output <dir>
```

- [ ] 增加后台启动选项，使用独立进程运行且立即返回结构化 JSON。
- [ ] 状态目录至少记录 PID、开始时间、计划结束时间、命令摘要、stdout、
  stderr、状态和结果路径。
- [ ] 增加状态查询命令，区分 `starting`、`running`、`passed`、`failed` 和
  `stale`。
- [ ] Windows 后台进程使用隐藏窗口，避免依赖 Codex 单次命令超时。
- [ ] 结果只以已完成报告为准，不能仅凭进程退出码宣称成功。

验收产物：

- 旧主线错误有自动化回归覆盖。
- 60 秒后台 smoke 能启动、查询并完成。
- 失败子进程能保留 stderr 和稳定错误码。
- CLI 输出和状态文件通过秘密扫描。

## 5. Milestone 2：真实可观察 UI

### 2.1 Operator console

- [ ] 将主持人页面整理为“运行状态、直播控制、事件流、节点/链状态、
  ProposalGate”五个清晰区域。
- [ ] token 使用密码输入框，仅保存在当前页面内存，不使用
  `localStorage`、`sessionStorage` 或 URL。
- [ ] 创建 session、发布事件和关闭 session 均显示明确成功或失败反馈。
- [ ] 展示 sequence、head hash、stream lag、Agent 连接数和队列深度。
- [ ] 在窄屏下保持可操作，并保留键盘焦点和可读对比度。

### 2.2 Read-only dashboard

- [ ] 增加总览指标、session 列表、时间线和完整性状态。
- [ ] 明确区分正常、警告、拒绝和不可验证状态。
- [ ] 页面只有 GET/SSE 行为，不提供写操作。
- [ ] API 不可用时显示降级状态，而不是空白或静默失败。

### 2.3 可复现截图

- [ ] 新增无秘密的 UI fixture 启动方式，生成稳定的 session、事件和指标。
- [ ] 用真实浏览器分别检查 operator 和 dashboard。
- [ ] 保存两张实际页面截图到 `docs/assets/`。
- [ ] 将截图加入中英文 README，并标明它们来自本地 fixture。

验收产物：

- UI 单元/集成测试通过。
- 浏览器能实际打开两个页面，无控制台阻断错误。
- 两张截图由当前代码生成，不是设计稿。
- HTML、截图目录和运行日志秘密扫描为 0。

## 6. Milestone 3：README、命令参考和 SCC0

### 3.1 README 收敛

- [ ] 中英文 README 的快速运行流程均缩减到 3–4 条核心命令。
- [ ] 每条核心命令说明其输入、可观察结果和下一步。
- [ ] 删除难以理解的“CI 分组”措辞，改为简短的质量门说明或链接。
- [ ] 阅读顺序只列架构/规格、API/CLI、运维和资料溯源四类入口。
- [ ] 两份 README 的版本、命令、截图和链接保持一致。

### 3.2 完整命令文档

- [ ] 新增 `docs/api/cli-reference.md`。
- [ ] 覆盖环境准备、package、Pilot Server、chain、Agent、直播、争议、
  vote、transcript、snapshot、soak 和故障排查。
- [ ] 区分演示命令、开发验证和正式四小时 release gate。
- [ ] 所有长时间命令提供后台运行和状态查询示例。

### 3.3 SCC0

- [ ] 从工作区保存的 SCC0 原始资料确认许可证文本和版本。
- [ ] 在仓库根添加 `LICENSE`，只对 LoveEngineSkill 仓库生效。
- [ ] README、包元数据、Skill 发布包、SBOM 和资料索引统一声明 SCC0。
- [ ] 验证归档内包含许可证且校验和覆盖该文件。

验收产物：

- README 的复制粘贴快速流程可运行。
- 文档链接、双语命令一致性和 Mermaid 检查通过。
- Python 元数据与根 `LICENSE` 一致。
- 确定性 ZIP 包含 `LICENSE`、checksums 和 SBOM。

## 7. Milestone 4：长时间运行和证据

### 4.1 分级运行

- [ ] 先运行 60 秒后台 smoke，验证生命周期和状态查询。
- [ ] 再运行至少一小时的真实墙钟 soak，使用 10 个 SSE 观察者、3 个
  Agent 断线、1 次服务重启和 1 次 Anvil 重启。
- [ ] 一小时运行期间并行完成 UI、文档和 PR 准备，不修改正在执行进程所
  使用的代码或虚拟环境。
- [ ] 一小时通过后，从已合并 `main` 启动正式四小时 soak。

### 4.2 指标门槛

- [ ] committed event 零丢失、零 sequence gap。
- [ ] 正常状态 ACK p95 小于 2 秒，max 小于 5 秒。
- [ ] 故障后 30 秒内恢复并清空队列。
- [ ] 服务 RSS 小于 512 MB。
- [ ] 单场新增磁盘小于 250 MB。
- [ ] 三个节点 head hash 一致。
- [ ] transcript、snapshot 和报告 hash 可离线复算。
- [ ] secret scan 为 0。

### 4.3 证据保存

- [ ] 保存后台运行元数据、最终报告和 transcript hash。
- [ ] 更新 M5 acceptance report，明确区分 1 小时预运行和 4 小时正式
  gate。
- [ ] 四小时完成前不创建 `v0.5.0-lan-pilot` tag。

验收产物：

- 一小时实际墙钟报告和离线验证结果。
- 若四小时仍在运行，状态查询能证明其 PID、进度和输出位置。
- 若四小时完成，才允许发布 tag 和 Release 资产。

## 8. Milestone 5：多轮验证

### 5.1 快速反馈

- [ ] 针对改动先运行相关单元测试。
- [ ] 每次修复后重新运行失败测试和相邻回归测试。
- [ ] UI 改动使用浏览器检查桌面和窄屏。

### 5.2 完整矩阵

- [ ] `uv sync --frozen`
- [ ] `uv run python .\tools\check.py`
- [ ] `uv run pytest -m "not integration" -q`
- [ ] 现有 M2、M3、M4 E2E
- [ ] M5 package、chain、demo、soak 集成测试
- [ ] Foundry 19 项测试
- [ ] Local、Network、Live、Pilot 四类 transcript 验证
- [ ] `git diff --check`
- [ ] 文档陈旧路径和秘密扫描

验收产物：

- 每个矩阵项保留命令、退出码和摘要。
- 不以旧日志替代当前提交的验证。
- 任何失败必须修复并重跑后才能进入 PR。

## 9. Milestone 6：PR、合并和清理

### 6.1 PR 交付

- [ ] 确认功能分支包含监督基线、M5 实现和本轮收口提交。
- [ ] 推送 `feat/loveengine-m5-lan-pilot`。
- [ ] 创建标题合规的 PR，说明范围、风险、测试和未完成四小时门槛。
- [ ] 等待 repository/Python、Foundry、旧 E2E、M5 package/pilot 检查。
- [ ] 审阅 PR diff 和远端检查结果；失败则在原分支修复。

### 6.2 合并

- [ ] 所有必需检查通过后使用 merge commit 合并，保留本地
  `cb02c3c` 的祖先关系。
- [ ] `main` 使用 fetch + `merge --ff-only` 同步。
- [ ] 在同步后的 `main` 重新执行 CLI help、仓库检查和快速测试。

### 6.3 清理

- [ ] 确认后台进程不再依赖功能 worktree。
- [ ] 删除 M5 worktree。
- [ ] 删除本地和远端已合并功能分支。
- [ ] 清理运行缓存，但保留验收报告、截图和被引用的 release 证据。
- [ ] 最终确认仅保留主工作目录、`main` 与预期 tag。

验收产物：

- GitHub PR 已合并且远端检查可见。
- 本地 `main` 与 `origin/main` 一致。
- `git status` 干净，`git worktree list` 只含主目录。
- 在主目录运行 `uv run loveengine pilot --help` 成功。

## 10. 发布判定

本轮可以在一小时预运行和全部自动化检查通过后合并 M5 发布候选，
但只有实际四小时 soak 完成、报告通过、资产复核且主线 CI 全绿时，才允许：

1. 创建 `v0.5.0-lan-pilot` tag。
2. 发布确定性 ZIP、`checksums.json`、SBOM、PilotTranscript 和 soak report。
3. 将 master plan 中 M5 状态从 release candidate 改为 completed。
