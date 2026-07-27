# 主持人操作流程

适用目标：0.6.1-contract-public-pilot candidate（本机 loopback）

主持人负责鉴权写入、关闭 session 和显式 evidence finalize；不持有观察节点或投票
见证者私钥。Operator 页面不是直播平台，也不执行 ProposalGate 或链上交易。

## 启动

```powershell
uv run loveengine pilot quickstart --root .\pilot
```

无浏览器环境增加 --headless。保持终端运行，在另一终端检查：

```powershell
uv run loveengine pilot status --url http://127.0.0.1:8780
```

输出中的 operator_url 是写控制台，dashboard_url 是只读页面，invite_path 和
trust_policy_path 交给观察节点。token_file 只留在主持人机器，不复制到聊天、
命令参数、fixture、日志或 transcript。

## 创建并写入 session

打开 http://127.0.0.1:8780/operator/?lang=zh-CN，把 token file 内容输入页面；
token 只保存在页面内存，不进入 URL/localStorage。

![打开主持人操作台](../../assets/runbooks/operator/operator-01-open-console-zh.png)

创建 session 后逐条发布文字。服务把元数据写入 pilot.sqlite，把 exact text bytes
写入 content-addressed artifact store，并更新事件 hash chain。

![创建 session](../../assets/runbooks/operator/operator-02-create-session-zh.png)

![发布文字](../../assets/runbooks/operator/operator-03-publish-event-zh.png)

完全相同 event ID/内容可幂等重试；同 ID 不同内容、sequence gap 或错误 previous
hash 会拒绝。

## 关闭与 finalize

页面“关闭 session”只禁止后续事件写入，不会 finalize。随后通过鉴权 API 调用：

    POST /v1/live/sessions/{session_id}/evidence/finalize

finalize 重新读取全部 artifact bytes 并复算 hash。GET evidence 只读取已存在 bundle。

![关闭 session](../../assets/runbooks/operator/operator-04-close-session-zh.png)

页面中的 ProposalGate 文案是 manual hold 提示，不是 Gate 执行结果。核心实验 runner
在 ObservationSet、EvidenceBundle 和 dispute reviews 完成后调用 Gate。主持人不签
观察 receipt，也不签 vote。

## 停止

按 Ctrl+C 停止 quickstart。本流程只验证本机 adapter；远程进程托管、Tailscale、
公网和生产运维是独立验收门。
