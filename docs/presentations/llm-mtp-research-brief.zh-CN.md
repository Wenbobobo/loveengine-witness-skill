# LLM MTP 研究简报

状态：对外分享材料，不属于 LoveEngine Witness Skill 协议或发布承诺。

研究截止：2026-08-18。本文只采用原始论文、模型团队公开技术报告、权重说明和
serving 框架官方文档；其中的性能数字都保留原始测试条件，不作为通用性能承诺。

对应互动页：[LLM MTP：多 token 预测与投机解码](../../showcase/mtp.html)。

## 一句话

Multi-Token Prediction（多 token 预测，MTP）让模型从当前上下文对多个未来位置
提出候选。它并不自动获得“多 token 安全提交权”；在推理服务中，通常仍由目标模型通过
speculative decoding（投机解码）验证候选、接纳可用前缀，并在拒绝处纠正。

这一区分决定了讨论是否严谨：

- MTP 是训练目标、模型模块或候选来源。
- speculative decoding 是候选的验证和提交算法。
- 真实加速是 workload 结果，受接受率、候选成本、显存、batch、上下文和调度影响。

## 1. 为什么普通 LLM 解码是串行的

标准 next-token prediction（NTP）生成时满足：

```text
x(t+1) ~ P(x(t+1) | x(1:t))
x(t+2) ~ P(x(t+2) | x(1:t+1))
```

第二个 token 依赖第一个已经提交的 token，所以连续生成 K 个 token 通常要求 K 次
自回归推进。投机解码的出发点是：让一个更快的路径先提出一段候选，再让目标模型以更少的
串行推进验证这段候选。

经典 speculative decoding 论文给出的重要语义边界是：使用正确的接受与重采样规则时，
可以保持目标模型的采样分布；它不是“草稿模型输出直接替代目标模型输出”。参见
[Leviathan, Kalman, Matias 2023](https://arxiv.org/abs/2211.17192)。

## 2. MTP 的两类模型结构

### 2.1 独立 future heads

Gloeckle 等人使用共享 transformer trunk 和 n 个独立输出 heads。给定当前位置的共享表示，
head 1 预测下一个 token，head 2 预测第二个未来 token，依此类推。训练可写成简化形式：

```text
L = sum(i = 1..n) lambda(i) * CE(head(i)(context), x(t+i))
```

这一形式的优点是为多个未来位置加入监督信号；它的直接输出是多个候选分布，而非已经
一致、可提交的 token 序列。用于推理加速时仍要把这些候选置于验证流程中。

论文在相同参数预算下报告了训练与推理实验：13B 代码模型相对可比 NTP 基线解决的
HumanEval 问题数高 12%、MBPP 高 17%；其 7B、4-token 自投机解码实验报告代码 3.0 倍
速度、平均接受 3 个建议中的 2.5 个。数字均属于该论文的模型、数据和实现条件。
参见 [Better & Faster Large Language Models via Multi-token Prediction](https://arxiv.org/abs/2404.19737)。

同一研究也给出必要的反例：在其 7B 自然语言实验中，预测两个未来 token 的标准选择题
表现大体与 NTP 持平，预测四个未来 token 则有退化。因此不能把“预测更远”当成普遍更优的
默认设置。

### 2.2 保持因果链的顺序 MTP

DeepSeek-V3 的实现与独立 heads 不同。技术报告说明它使用 D 个顺序 MTP modules，
每层以主模型或上一预测深度的表示、以及相应训练 token embedding 为输入，维持各预测深度
的完整因果链；embedding 和 output head 与主模型共享。它将 MTP loss 作为额外训练目标。

该报告还明确：MTP 的首要目的是提升主模型训练表现，因此推理时可以直接丢弃 MTP modules；
也可以将它们复用为 speculative decoding 的候选路径。参见
[DeepSeek-V3 Technical Report, Section 2.2](https://arxiv.org/html/2412.19437)。

公开的 DeepSeek-V3 权重说明中，`num_nextn_predict_layers` 为 `1`，并列出了该 MTP module
与主模型共享 embedding/output head、附加 transformer layer 等结构事实。参见
[DeepSeek-V3 Weight File Documentation](https://github.com/deepseek-ai/DeepSeek-V3/blob/main/README_WEIGHTS.md)。

## 3. speculative decoding 如何把候选变成输出

一轮简化流程如下：

1. 候选路径提出 `k` 个 token。候选可以来自 MTP module、多个 heads、小草稿模型、EAGLE 或
   N-gram 缓存。
2. 目标模型在候选块或候选树上计算验证结果。
3. 接纳与目标模型一致的最长前缀；第一个不接受的位置由目标模型的采样/纠正规则接管。
4. 以新的已提交上下文进入下一轮。

若把每个候选 token 被接纳的概率简化为相同、独立的 `r`，则单次目标验证的期望推进长度约为：

```text
E[advance] = 1 + r + r^2 + ... + r^k
```

其中的 `1` 是目标模型在首个拒绝处产生的纠正 token，或者所有候选均接纳后额外前进的
目标 token。这个公式只用于理解接受率，不能预测真实 TPS：草稿模型耗时、目标验证的矩阵
形状、kernel、KV cache、候选树内存、batch、排队和网络都会改变端到端结果。

## 4. 已公开的进展与证据边界

| 事实或主张 | 证据 | 正确读法 |
| --- | --- | --- |
| MTP 可作为未来位置的训练辅助目标 | Gloeckle et al. 2024 | 表明一类训练设计有实验收益，不表示每个模型都应使用相同 horizon。 |
| 多 heads 可用于候选树验证 | Medusa 2024 | 是一种模型改造与服务组合，不等价于所有 MTP。 |
| DeepSeek-V3 采用顺序 MTP，报告第二 token 接受率 85% 至 90%、1.8 倍 TPS | DeepSeek-V3 technical report | 只属于该模型及报告中的生成条件。 |
| 开放 V3 权重包含 1 个 MTP module | DeepSeek 权重说明 | 证明权重结构可见，不自动证明本地 serving 已启用或更快。 |
| 当前 SGLang 文档支持 model-specific MTP speculative path | SGLang 官方文档 | 证明有实现入口；仍须按模型、硬件和负载验证。 |
| NTP 预训练模型可以通过边缘化表现出 MTP 能力，但后加 heads 不容易 | Mehra et al. 2025 | 说明“在已有模型上补 heads”存在表征专化障碍。 |

DeepSeek-V3 报告的 MTP 评估是值得分享的具体案例：它说明了训练 MTP 和推理候选可以衔接，
但也只报告“next 2 tokens”的配置，而不是任意长度的万能并行解码。报告中第二 token
接受率为 85% 至 90%，对应其解码设计下 1.8 倍 TPS；不要将这两个数字直接放到其他模型、
显卡或高并发集群的预算表中。

SGLang 的当前官方文档把 MTP 列为 built-in multi-token heads 的 model-specific speculative
workflow，也把 EAGLE、独立 draft model 和 N-gram 区分为其他候选来源。文档明确候选深度、
top-k、验证容量会改变内存和接受率，并建议按目标设置 benchmark。参见
[SGLang speculative decoding documentation](https://docs.sglang.io/docs/advanced_features/speculative_decoding)。

## 5. 为什么“给已有 LLM 加 MTP heads”不一定够

[On multi-token prediction for efficient LLM inference](https://arxiv.org/abs/2502.09419)
考察了 NTP 预训练模型的 MTP 能力。研究认为：更大的模型可通过对中间 token 概率进行
边缘化表现出一定 MTP 能力；但隐藏层已对 NTP 专化，信息可能在到达输出层前被压缩或丢失，
使只在冻结 backbone 上训练 MTP heads 的效果受限。联合训练有帮助，但仍与边缘化基线
存在差距。

这带来一个工程结论：

- 已有 model checkpoint 不应仅因“可以加 heads”就假定具有好接受率。
- 从训练开始就加入 MTP objective、为 serving 保留对应模块，和事后附加 heads 是不同工程路线。
- 若没有 MTP 权重，仍可研究其他 speculative decoding 候选来源，但不应把它称作“打开了 MTP”。

## 6. 开发团队的最小验证协议

在讨论“是否采用”前，先建立可复现基线：

1. 固定目标模型 revision、tokenizer、量化方式、GPU/驱动、serving engine revision、
   context 上限、batch/并发和 sampling 参数。
2. 明确候选来源：built-in MTP heads、专用 MTP module、EAGLE、小草稿模型还是 N-gram。
3. 在 fixed seed / greedy 场景检查目标验证路径的输出一致性；在随机采样场景检查接受与
   重采样算法的语义，而不是要求每次文本逐字相同。
4. 用实际提示集和并发梯度同时测量：TTFT、TPOT、端到端 tokens/s、每请求时延分位数、
   接受率、草稿开销、显存峰值、OOM/错误率和单位输出成本。
5. 把没有 speculative decoding 的目标模型路径作为长期基线，并记录回退条件。
6. 每次更新模型、量化、driver、kernel 或 serving engine 后重新对比，不沿用旧数字。

推荐的决策规则是：只有在语义、稳定性和 workload 性能都通过可复现对照后，才把 MTP
视为该服务配置的加速选项。高接受率本身不足以证明端到端收益；漂亮的单请求 demo 也不足以
证明高并发生产收益。

## 7. 分享时可以如何回答常见疑问

**MTP 是不是让模型跳过自回归？**

不是。MTP 让模型提前提出候选；验证阶段仍把最终提交权交给目标模型。它减少的是目标模型
的串行推进次数，而不是否认 token 之间的依赖。

**MTP 与 Medusa、EAGLE 是什么关系？**

它们都可能服务于“快速产生候选 + 目标验证”的大框架，但候选的模型结构和训练方法不同。
MTP 是更宽的未来 token 预测概念；Medusa 是多 heads/候选树框架；EAGLE 是 feature-level
drafting 系列。应具体说明正在使用哪一种候选来源和哪一种验证规则。

**能否承诺 2 倍或 3 倍加速？**

不能。论文和框架文档报告的是特定模型、硬件、batch、提示和参数下的数字。不同 context、
并发、候选深度和显存限制可以让收益显著变化甚至变成负收益。

**这与 LoveEngine Witness Skill 有什么直接关系？**

没有协议依赖关系。本材料只是供团队理解 LLM 推理优化；它不会改变 Witness Skill 的证据、
任务、签名或治理边界，也不把任何 MTP 结果写入 LoveEngine 的发布/试点成熟度结论。

## 来源

1. [Leviathan, Kalman, Matias. Fast Inference from Transformers via Speculative Decoding (2023)](https://arxiv.org/abs/2211.17192)
2. [Cai et al. Medusa: Simple LLM Inference Acceleration Framework with Multiple Decoding Heads (2024)](https://arxiv.org/abs/2401.10774)
3. [Gloeckle et al. Better & Faster Large Language Models via Multi-token Prediction (2024)](https://arxiv.org/abs/2404.19737)
4. [DeepSeek-AI. DeepSeek-V3 Technical Report (v2, 2025)](https://arxiv.org/html/2412.19437)
5. [DeepSeek-V3 Weight File Documentation](https://github.com/deepseek-ai/DeepSeek-V3/blob/main/README_WEIGHTS.md)
6. [SGLang: Speculative Decoding](https://docs.sglang.io/docs/advanced_features/speculative_decoding)
7. [Mehra, Alonso Garcia, Mauch. On multi-token prediction for efficient LLM inference (2025)](https://arxiv.org/abs/2502.09419)
