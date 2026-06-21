# ADR-0001：使用链上 SkillRegistry 作为 Skill 版本信任根

状态：`accepted for M3 pilot`  
日期：2026-06-22

## 背景

M2 已能校验本地 manifest 和 package hash，但多个 Agent 仍需要一个独立于 Relay Hub 和文件镜像的方式判断：

- 哪个 Publisher 发布了某个版本；
- package/manifest hash 是否正确；
- 版本是否 active、deprecated 或 revoked；
- 当前推荐版本和替代版本是什么。

## 决策

M3 新增非核心辅助合约 `SkillRegistry`。

Registry 只保存：

- Publisher 地址；
- `skillId`、version、package 和 manifest 的 bytes32 hash；
- active/deprecated/revoked 状态；
- previous/replacement version；
- 发布区块和时间。

Registry 不保存 Skill 文件、endpoint、任务正文、证据、日志或私钥。文件可以由 Relay Hub 或其他镜像提供，Agent 以链上 hash 进行最终校验。

Registry 不设全局 owner。每个 Publisher 只能管理自己的命名空间；M3 使用隔离测试 Publisher。

## 备选方案

### GitHub 或普通文件仓库作为唯一来源

优点是实现简单。缺点是仓库权限、tag、release 和废弃状态都属于链下可变元数据，而且 GitHub 身份与 EVM Publisher 身份不一致。M3 不采用。

### Publisher 签名的链下 release descriptor

能够验证文件发布者，但废弃和替代状态仍依赖链下传播。它可以作为未来 `ReleaseRegistry` adapter，但不是 M3 信任根。

### 全部 Skill 文件上链

公开可审计，但 gas 和数据规模不合理，也无法解决大文件分发。M3 不采用。

## 影响

- 增加一个辅助合约和 Publisher signer。
- Agent 安装 Skill 前需要读取指定 chain 和 Registry。
- Relay Hub 不是信任根；它提供错误文件时会被 hash 校验拒绝。
- Registry 与四个 UAS 核心业务合约解耦。

