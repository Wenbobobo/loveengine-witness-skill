# LoveEngine documentation

## Start here

- [Repository README](../README.md)：项目边界、版本状态和本机入口。
- [Meeting Q&A](../QA.md)：基于现有 33:42-51:06 会议片段的代码事实回答。
- [Core architecture and data flow](architecture/witness-core-and-data-flow.zh-CN.md)：
  角色、时序、存储、hash、合约和失败模式。
- [Developer experiments](development/integration-guide.md)：从 package trust 到可选
  governance 的五个实验。

## Active authority

1. [Engineering master plan](specs/love-engine-master-plan.md)
2. [Pre-enterprise Remote Lab SPEC](specs/love-engine-pre-enterprise-remote-lab.md)
3. [API documents](api/README.md)
4. [Role runbooks](development/participant-runbook.zh-CN.md)

0.6.1 是工作 candidate，最新发布 tag 是 v0.6.0-contract-public-pilot。精确
candidate `9e5058e` 的本机四小时和共享 Linux remote lab 已通过；后续候选改用
900 秒工程验收。公司直播、Tailscale 直接服务、公网和测试网仍不属于 0.6.1
已完成能力。

## Development and operations

- [Repository structure](development/repository-structure.md)
- [Contract-team v2 comparison](development/contract2-comparison-and-recommendations.md)
- [Operator](development/runbooks/operator-flow.zh-CN.md)
- [Observation node](development/runbooks/observation-node-flow.zh-CN.md)
- [Voting witness](development/runbooks/voting-witness-flow.zh-CN.md)
- [Read-only viewer](development/runbooks/public-viewer-flow.zh-CN.md)
- [Publisher](development/runbooks/publisher-flow.zh-CN.md)
- [Shared remote lab](development/runbooks/remote-lab-flow.zh-CN.md)
- [Technical architecture article](articles/loveengine-technical-architecture.zh-CN.md)

## Historical evidence

M3-M6 runbooks, supervision notes, acceptance reports and closeout plans are historical
records, not current authority:

- development/m3-demo-runbook.md
- development/m3-acceptance-report.md
- development/m4-skill-supervision-and-next-stage-gaps.md
- development/m5-acceptance-report.md
- archive/planning/2026-06-23/m5-release-closeout-plan.md
- archive/planning/2026-06-24/m6-demo-docs-publication-plan.md

Implemented specs live under archive/specs/implemented/. Superseded plans and older source
materials remain under archive/ and are not rewritten.

## Preserved sources

- reference/source-materials/current/：当前业务来源约束。
- reference/source-materials/meeting-transcripts/：原样会议转写材料。
- reference/contracts/contract-team-v2/：原样协作者合约输入。
- [Source inventory](kb/source-inventory.md) / kb/sources.json：来源、状态和角色清单。
