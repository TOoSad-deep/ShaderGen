# ShaderForge 最终技术方案与分阶段实施计划

## 1. 最终结论

ShaderForge 的可落地路线应收敛为：

```text
参考 PNG / 文字目标
  → 结构化 Intent IR
  → 3–5 个 Genome seed
  → 确定性渲染与局部评分
  → 固定拓扑参数优化
  → Agent 在阶段边界提出结构变异
  → 多样性保留、VLM / 人工评审
  → 输出 GLSL、预览图、Genome 与完整复现记录
```

核心原则：

- v1 先做“可复现、可评分、可优化”的垂直闭环，不先做完整产品 UI。
- Agent 负责语义分析、结构假设、停滞恢复和最终选择，不进入 CMA-ES 每一代的内循环。
- Genome 是唯一可搜索中间表示；LLM 默认输出 `GenomePatch`，不让自由 GLSL 成为主路径。
- 内循环只使用确定性指标；VLM 和人工评审只用于晋级、校准和最终选择。
- 通用 GLSL 回吸推迟到 v2 之后，v1 不承担这个成本。

## 2. v1 范围

### 必须支持

- 输入：参考 PNG、文字目标、可选约束。
- 输出：候选预览图、最终 GLSL、Genome、评分明细、运行记录。
- 目标类型：单对象与少量多对象的 2D 视效，例如圆、矩形、渐变、光晕、软边、简单遮挡、背景渐变。
- 优化方式：固定拓扑下的连续参数优化，加有限结构变异。
- 评估方式：全局损失、对象级局部损失、颜色 Lab ΔE、形状 / 边缘 / 位置 / coverage penalty。
- 复现方式：保存 seed、Genome、metric version、renderer version、prompt、模型、预算和谱系。

### 明确不做

- 不做通用 GLSL → Genome 反解析。
- 不让 VLM 参与每一步数值优化。
- 不做复杂时间动画、3D shader、物理材质拟合。
- 不做大而全节点库，v1 节点控制在 8–15 类。
- 不先做完整商业 UI，只保留最小调试界面或 CLI / notebook 入口。

## 3. 系统架构

### 3.1 Target Analyzer

职责：把用户输入转成结构化 `Intent IR`。

输入：

- 参考图片；
- 文字描述；
- 用户硬约束，例如“背景保持透明”“主色必须接近 #E43B32”。

输出：

- 对象列表；
- 区域、颜色、形状、大小、边缘软硬；
- 对象关系；
- 参数范围；
- 硬约束；
- 不确定假设。

关键要求：

- 区分观察事实、推断结果和可优化参数。
- 每个对象必须有稳定 `object_id`。
- 不确定时保留多个解释，不强行压成单一结果。

### 3.2 Intent → Genome Mapper

职责：把语义目标映射成可搜索节点图。

v1 做法：

- 每个语义概念对应固定模板。
- 一次生成 3–5 个 seed。
- 先用廉价评分筛掉明显错误结构。

示例映射：

| 语义概念 | Genome 模板 |
|---|---|
| 实心圆 | `CircleNode + FillNode` |
| 柔和光晕 | `CircleNode + GlowNode + BlendNode` |
| 背景渐变 | `LinearGradientNode` 或 `RadialGradientNode` |
| 软边对象 | `CircleNode` / `RectNode` / `EllipseNode` 的 `edge_softness` 参数，必要时叠加 `BlurNode` |
| 简单噪声纹理 | `NoiseNode + BlendNode` |

### 3.3 Genome v0

Genome 是一个有类型节点图，分离结构参数和连续参数。

v1 节点建议：

- `CanvasNode`
- `CircleNode`
- `EllipseNode`
- `RectNode`
- `RoundedRectNode`
- `FillNode`
- `LinearGradientNode`
- `RadialGradientNode`
- `GlowNode`
- `BlurNode`
- `NoiseNode`
- `BlendNode`
- `TransformNode`
- `MaskNode`

每类节点必须声明：

- 参数名；
- 类型；
- 合法范围；
- 默认值；
- 是否连续可优化；
- 影响对象；
- 影响区域；
- GLSL 生成规则；
- cheap renderer 生成规则。

### 3.4 Renderer / Compiler

职责：把 Genome 渲染成可评分图像，并生成最终 GLSL。

必须提供两条路径：

- cheap renderer：用于高频搜索；
- GLSL / WebGL renderer：用于最终验证。

两条路径必须做 parity 测试，统一：

- 坐标系；
- alpha；
- premultiplied alpha；
- 颜色空间；
- 抗锯齿；
- blend 规则；
- gamma / linear 转换。

Renderer 返回：

```text
RenderBundle
├── image
├── node_masks
├── id_map
├── alpha
├── coverage
└── contribution_maps
```

### 3.5 Oracle / 局部损失

职责：给候选结果提供可优化、可解释、可校准的评分。

评分结构：

```text
ScoreBreakdown
├── total
├── global
├── per_object
│   ├── shape
│   ├── color_lab_delta_e
│   ├── edge
│   ├── pose
│   └── coverage
├── relations
├── background
├── constraints
└── affected_regions
```

核心规则：

- 颜色损失使用目标 mask 或目标 / 候选 mask 并集，避免少画逃避惩罚。
- 对小对象单独评分，避免背景像素淹没前景。
- 保留完整评分向量，总分只作为优化器输入。
- 所有评分绑定 `metric_version`。

### 3.6 Search Engine

职责：在固定拓扑内优化连续参数，必要时触发结构变异。

v1 搜索流程：

```text
seed genome
  → manifest
  → flatten continuous params
  → normalize to [0, 1]
  → block-wise optimization by affected_regions
  → unflatten
  → render
  → score
  → archive best candidates
```

必须实现：

- 稳定参数 manifest；
- `flatten → unflatten` 保真测试；
- 参数归一化；
- affected regions 分块；
- 停滞检测；
- 最大预算；
- 内容哈希缓存。

### 3.7 Agent Controller

职责：在阶段边界做高层决策。

Agent 可以调用的工具：

| 工具 | 作用 |
|---|---|
| `analyze_target()` | 生成 Intent IR |
| `propose_seeds()` | 生成多个 Genome seed |
| `optimize_params()` | 调用确定性搜索 |
| `summarize_archive()` | 读取 top-k、代表样本、趋势和停滞原因 |
| `propose_patch()` | 输出类型化 `GenomePatch` |
| `promote_candidates()` | 选择进入 VLM / HITL 的候选 |
| `stop_or_continue()` | 基于硬预算和摘要做阶段决策 |

Agent 不直接：

- 选择 CMA-ES 每一代父代；
- 改写低层连续参数；
- 判断所有候选的逐像素好坏；
- 决定突破硬预算。

### 3.8 Store / Cache

职责：让所有结果可复现、可比较、可复用。

保存内容：

- input；
- Intent IR；
- Genome；
- GenomePatch；
- RenderBundle 摘要；
- ScoreBreakdown；
- seed；
- lineage；
- prompt；
- model；
- renderer version；
- metric version；
- code version；
- budget；
- artifacts path。

缓存策略：

- 对规范化 Genome 做内容哈希。
- 分层缓存 render、score、VLM judgment。
- 评分版本变化时允许重新评分，并保留旧结果。

## 4. 可行性 Review

### 4.1 总体判断

方案可行，但成立条件是严格缩小 v1 范围：先完成单对象 / 少量对象的垂直闭环，再扩展 Agent、MAP-Elites、VLM 和 UI。若一开始并行开发全部模块，最大风险不是工程量，而是调试时无法判断问题来自 Intent、Genome、Renderer、Oracle 还是 Agent。

### 4.2 关键风险与处理

| 风险 | 等级 | Review 结论 | 处理方式 |
|---|---:|---|---|
| Intent 无法稳定转 Genome | P0 | 必须先解决 | 定义 Intent IR schema 与语义模板，所有 Agent 输出先过 schema 校验 |
| cheap renderer 与 GLSL 不一致 | P0 | 必须先解决 | 节点级和组合级 parity 测试先于优化器 |
| 局部损失无法提供方向 | P0 | 必须先解决 | Renderer 输出 mask / id_map / coverage，Oracle 做对象级评分 |
| 颜色评分被结构指标淹没 | P0 | 必须先解决 | 加局部 Lab ΔE，并用 522 标签或人工排序校准权重 |
| CMA-ES 优化错误参数 | P0 | 必须先解决 | manifest、归一化、flatten/unflatten 保真测试 |
| Agent 频繁介入拖慢系统 | P1 | 可控 | Agent 只在阶段边界介入，硬预算在代码中执行 |
| VLM 排序不稳定 | P1 | 可控 | pairwise、随机左右顺序、固定 rubric，必要时重复判断 |
| MAP-Elites 描述符无效 | P1 | 可推迟 | 先有 AI-off / AI-on 基线，再加多样性 archive |
| 通用 GLSL 回吸太难 | P2 | 不进入 v1 | 自由 GLSL 只作为终局黑箱候选 |

### 4.3 可落地性检查

| 检查项 | 结论 |
|---|---|
| 是否能先做最小闭环 | 能。单对象、固定拓扑、确定性评分即可启动。 |
| 是否依赖不可控模型行为 | 不依赖。LLM 只生成结构候选和 patch，内循环确定性。 |
| 是否能验收每阶段质量 | 能。每阶段都有明确成功门槛和回归测试。 |
| 是否能控制成本 | 能。VLM 不进内循环，Agent 低频调用，render / score 可缓存。 |
| 是否能定位问题 | 能。Store 保存完整谱系与版本，ScoreBreakdown 保留分项指标。 |
| 是否能逐步扩展 | 能。Genome 节点、关系损失、MAP-Elites 和 VLM 都可分阶段加入。 |

### 4.4 Review 后调整

保留：

- Intent IR；
- Genome；
- cheap renderer + GLSL renderer；
- 局部 Oracle；
- 参数 manifest；
- Agent stage-boundary controller；
- Store / Cache。

降级：

- MAP-Elites 从 v1 核心链路降为 v1.5。
- VLM 从评分核心降为晋级评审与校准工具。
- UI 从正式产品界面降为调试入口。

删除 / 推迟：

- 通用 GLSL 回吸；
- 大规模节点库；
- 复杂动画；
- 每代 Agent 决策；
- 端到端黑箱 prompt 规则堆叠。

## 5. 分阶段实施计划

### M0：语言与基准集

目标：把“想要什么”和“系统能搜索什么”对齐。

周期：1–2 周。

交付物：

- `Intent IR` schema；
- `Genome v0` schema；
- 语义模板表；
- 20–50 张分层基准图片；
- schema 校验工具；
- seed 生成器原型。

实施任务：

1. 定义对象、区域、颜色、形状、约束、关系的 Intent 字段。
2. 定义 8–15 类 Genome 节点及参数范围。
3. 定义语义概念到 Genome 模板的映射。
4. 为每个基准图片标注期望对象和核心参数范围。
5. 让 Agent 或规则生成 3–5 个 seed，并通过 schema 校验。

验收门槛：

- 简单目标集中 80% 以上能生成合法 Intent IR。
- 每个 Intent 至少能生成 3 个合法 Genome seed。
- 每个 Genome seed 都能解释其对象映射和参数范围。

不通过时：

- 不进入渲染优化；
- 收缩节点类型；
- 补齐模板，而不是增加 Agent prompt 规则。

### M1：可信渲染与 Oracle

目标：让每个候选都能被稳定渲染和解释性评分。

周期：2–3 周。

交付物：

- cheap renderer；
- GLSL renderer / compiler；
- `RenderBundle`；
- `ScoreBreakdown`；
- 节点级 parity 测试；
- 单变量扰动测试。

实施任务：

1. 为 v0 节点实现 cheap renderer。
2. 为 v0 节点实现 GLSL 生成。
3. 输出 `image`、`node_masks`、`id_map`、`alpha`、`coverage`。
4. 实现 shape、color、edge、pose、background、coverage loss。
5. 加入局部 Lab ΔE。
6. 建立颜色、位置、大小、边缘、背景扰动曲线。
7. 对 cheap renderer 与 GLSL renderer 做 parity 测试。

验收门槛：

- 同一 Genome 在两条渲染路径下 `RGB MAE ≤ 3/255`、`alpha MAE ≤ 3/255`、`mask IoU ≥ 0.98`。
- 颜色逐渐偏离时，颜色损失基本单调增加。
- 只改背景时，前景对象总损失变化小于 2%。
- 删除对象区域时，coverage penalty 增加。

不通过时：

- 不接优化器；
- 先修 renderer / oracle 对齐问题。

### M2：确定性参数优化

目标：在固定拓扑下稳定提升候选质量。

周期：2–3 周。

交付物：

- 参数 manifest；
- `flatten/unflatten`；
- 参数归一化；
- 分块优化；
- 停滞检测；
- AI-off 基线报告；
- render / score 缓存。

实施任务：

1. 每个连续参数登记到 manifest。
2. 实现 `flatten(genome) -> vector`。
3. 实现 `unflatten(vector, manifest) -> genome`。
4. 测试 `flatten → unflatten` 保真。
5. 把所有连续参数归一化到 `[0, 1]`。
6. 按 affected regions 分块优化。
7. 实现最大评估次数、最大时间、连续无提升停止条件。
8. 保存每次 run 的 seed、预算、评分版本和候选谱系。

验收门槛：

- 固定 seed、预算和版本时结果可复现。
- 固定拓扑下，优化后总分稳定优于初始 seed。
- 优化目标对象时，无关区域总损失退化小于 2%。
- AI-off 基线有完整报告。

不通过时：

- 不接 Agent；
- 优先检查 manifest、归一化、局部损失和缓存一致性。

### M3：Agent 结构假设与 GenomePatch

目标：让 Agent 在确定性闭环外层提供结构改进。

周期：2 周。

交付物：

- `AgentController`；
- 工具协议；
- `GenomePatch` schema；
- patch 校验器；
- archive 摘要；
- AI-on vs AI-off 对照报告。

实施任务：

1. 暴露 `analyze_target()`、`propose_seeds()`、`optimize_params()`、`summarize_archive()`、`propose_patch()`。
2. 让 Agent 只在阶段边界读取摘要。
3. 让 Agent 输出类型化 `GenomePatch`。
4. patch 必须声明 `intent`、`changed_nodes`、`expected_regions`。
5. patch 先过 schema、合法性和退化检查，再进入优化。
6. 比较 AI-on 与 AI-off 在相同预算下的质量提升。

验收门槛：

- Agent 不突破硬预算。
- patch 合法率达到 70% 以上。
- AI-on 相比 AI-off 的中位总分提升达到 5% 以上。
- 每次结构变异可复现、可归因。

不通过时：

- 不增加更多 Agent 工具；
- 收窄 patch 类型；
- 增强 archive 摘要，而不是扩大 prompt。

### M4：多样性、VLM 与 HITL

目标：保留有价值的不同结构，并用贵评审校准最终选择。

周期：2–3 周。

交付物：

- MAP-Elites archive；
- 行为描述符；
- 晋级策略；
- VLM pairwise 评审；
- HITL 反馈记录；
- 评分相关性报告。

实施任务：

1. 定义行为描述符：色相、边缘软硬、层数、对称性、拓扑复杂度。
2. 监控 archive 覆盖率和候选视觉距离。
3. 晋级集合包含高分、各格代表、高不确定性和少量随机样本。
4. VLM 使用 pairwise、随机左右顺序和固定 rubric。
5. 人工选择时可选记录原因标签。
6. 用 VLM / 人工排序校准 cheap score 权重。

验收门槛：

- archive 不坍缩到少数格子。
- 晋级候选包含可见多样性。
- cheap score 与 VLM / 人工排序的 Spearman 相关系数达到 0.45 以上。
- 人工反馈能追溯到候选、指标和版本。

不通过时：

- MAP-Elites 降级为 top-k + 多样性采样；
- VLM 只保留最终候选解释，不参与自动选择。

### M5：工程化与产品化

目标：把研究闭环变成可持续使用的工程系统。

周期：2–4 周。

交付物：

- 最小调试 UI；
- run dashboard；
- 失败案例回放；
- benchmark 命令；
- 性能报告；
- 文档。

实施任务：

1. 提供上传图片、启动 run、查看候选和下载 GLSL 的入口。
2. 展示 Intent、Genome、评分明细和谱系。
3. 支持失败案例回放。
4. 建立 nightly benchmark。
5. 汇总耗时、缓存命中、评估次数、Agent 调用次数和 VLM 成本。

验收门槛：

- 非开发者能跑完一个受限目标案例。
- 失败 run 可回放、可定位阶段。
- benchmark 能识别质量回退和性能回退。

## 6. 推荐里程碑顺序

| 顺序 | 里程碑 | 是否阻塞后续 |
|---:|---|---|
| 1 | Intent IR + Genome v0 + 基准集 | 是 |
| 2 | cheap / GLSL renderer parity | 是 |
| 3 | 局部 Oracle 单调性与隔离测试 | 是 |
| 4 | 固定拓扑确定性优化 | 是 |
| 5 | Store / Cache / 复现记录 | 是 |
| 6 | Agent seed 与 GenomePatch | 否，但建议在 M2 后 |
| 7 | MAP-Elites | 否 |
| 8 | VLM / HITL | 否 |
| 9 | 产品 UI | 否 |

## 7. 最小成功版本

最小成功版本定义为：

```text
输入一张简单参考 PNG
  → 系统生成结构化 Intent
  → 生成多个合法 Genome seed
  → 渲染并输出 mask / id_map / coverage
  → Oracle 给出局部评分
  → 固定拓扑参数优化提升分数
  → 输出最终 GLSL、预览图、Genome 和可复现记录
```

只要这个闭环成立，ShaderForge 就有继续扩展的基础。若这个闭环不成立，上层 Agent、MAP-Elites、VLM 和 UI 都只会放大调试成本。

## 8. 立即下一步

先做 M0，不启动并行大模块开发。

第一周只交付三件事：

1. `Intent IR` schema；
2. `Genome v0` 节点词汇表；
3. 20–50 张基准图及期望结构标注。

完成后再进入 renderer 与 oracle。这样风险最小、反馈最快，也最容易判断方案是否真的跑得起来。
