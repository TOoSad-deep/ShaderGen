# scene_mvp run 85506ab8 Agent 优化建议

> 归档状态：历史建议且非批准计划，不得解释为当前待办或默认优化方向。

## 1. 状态与范围

- 日期：2026-07-23
- 状态：运行复盘与待验证建议；不是已批准架构、实现计划或发布证据
- 所属功能：`F09` PNG 转无贴图 Shader Agent V1
- 适用路径：显式实验模式 `scene_mvp`
- 分析对象：`run_id=85506ab8-12c4-4a20-8940-824875ea0f97`
- 输入：`pink_circle.png`，原图 `505×527`，工作分辨率 `245×256`

本文只沉淀该次真实运行暴露出的 Agent 结构提议、候选选择、预算调度、评价函数与可观测问题。单例诊断不能直接改变冻结质量门禁，也不授权扩大 feature 槽、开放任意 GLSL、修改 Graph 拓扑或切换默认生成模式。

## 2. 证据边界

本次分析使用以下事实：

- 本地 run 的 `final/manifest.json`、`final/metrics.json`、参考图、最终渲染和最终 Scene；
- `agent_runs`、`agent_events`、`agent_logs` 中的终态账本；
- 使用相同 `png_to_shader_min_template_v3` 与 `min_scene_composite_v3` 对最终 Scene 做的只读 feature 消融复算；
- 当前实现的 Prompt、typed patch、确定性邻域搜索、路由和预算配置。

完整 run Artifact 位于被忽略的本地 `output/png-to-shader/`，没有登记为 durable evidence。本文件记录的是可审计摘要，不把本地路径或单次数据库记录冒充跨环境可复验的正式 benchmark 证据。

## 3. 总结结论

本次运行的工程链路成功，质量链路失败：

- 输入解析、严格 Scene 契约、固定模板物化、WebGL1 渲染、评分、`current_best` 保护、Artifact 固化和终态账本均正常完成；
- 运行以 `render_budget_exhausted` 停止，`target_reached=false`，不能解释为质量达标；
- 最终图在主体位置、半径和基础粉色渐变上接近参考图，但缺少左上白色条状高光、左上深红暗部、右下月牙高光和外圈反射边缘，材质从凝胶球退化为平滑粉色圆盘；
- 四个 feature 槽没有成为瓶颈：最终只保留两个 feature，其中 `rim.intensity=0`，实际有效结构只有 `radial + shadow`；
- Initial Author 在像素结果上没有优于确定性感知 fallback，五次 Refine 候选全部被拒绝，全部质量收益来自确定性单参数搜索；
- 当前最关键的问题不是继续增加槽位，而是结构候选在获得局部调参机会前就被淘汰，以及 Agent 缺少可操作的空间残差信息。

## 4. 实际运行配置与可比性

| 项目 | 实际值 |
| --- | ---: |
| quality preset | `high` |
| target MAE | `0.04` |
| target loss | `0.02` |
| render budget | `320` |
| LLM budget | `9` |
| Refine budget | `9` |

该配置不同于 D058 冻结的 `target_loss=0.04`，也不同于 D059 记录的默认 MAE/loss `0.08/0.04` 与 high 预算 `160/6/3`。分析时工作树内 YAML 已变为 `0.04/0.02` 与 `320/9/9`，且属于未提交实验改动。

因此：

- 本次 run 可以用于诊断 Agent 行为；
- 不能与冻结 7 例 baseline 直接混算；
- 不能用于调整发布门槛或宣称 v3 真实模型 gate 已通过；
- 后续复现实验必须把实际配置写入独立报告，避免把实验目标冒充冻结默认值。

## 5. 运行过程复盘

### 5.1 感知阶段基本正确

确定性感知得到：

- primitive：`circle`；
- center：约 `[-0.0082, 0.0857]`；
- axes：约 `[0.8449, 0.8449]`；
- color field：`radial`；
- solid/radial/linear 拟合 MAE：`0.09497 / 0.04990 / 0.05162`。

`radial` 明显优于 `solid`，也小幅优于 `linear`，说明基础颜色场选择合理。主体中心和等轴半径与参考图也基本一致。后续失败不能归因于 primitive 或基础颜色场类型选错。

### 5.2 Initial Author 没有带来有效结构增量

Initial Author：

- 调用 1 次；
- 延迟 `17.196s`；
- 消耗 `3,151` tokens；
- 输出通过严格模型契约，无结构修复。

首次工作 Scene 的 loss 为 `0.061546553`。使用相同输入重新渲染确定性感知 fallback，loss 也是 `0.061546553`；首次 trace 中也没有出现独立 fallback 评分。这足以判断 Initial Author 的有效像素结果与 fallback 等价，但不据此声称原始 JSON 字节完全相同。

当前 Initial Prompt 只列出允许能力，并强调“无法确定时沿用 fallback、保守优先”。它没有要求模型先分解基础颜色场与局部高光/暗部，也没有要求检查剩余 feature 槽。因此，严格输出成功不等于完成了有效视觉建模。

### 5.3 确定性搜索单调改善，但只能优化已有结构

最终 loss 从 `0.061547` 降至 `0.043850`，改善约 `28.8%`。各轮 best 大致为：

| 阶段 | best loss | 主要可见动作 |
| --- | ---: | --- |
| 首帧 | `0.061547` | fallback 等价 Scene |
| 第 1 轮 | `0.052912` | base、rim、shadow |
| 第 2 轮 | `0.050895` | base、rim、shadow |
| 第 3 轮 | `0.047553` | base、rim、shadow |
| 第 4 轮 | `0.045965` | base、rim、shadow |
| 第 5 轮 | `0.044667` | base 改善，feature 无改善 |
| 第 6 轮 | `0.043850` | base 改善，预算耗尽 |

每轮 base 最后记录的获胜参数都是 `object.color_field.scale`。确定性搜索还调整了已有 rim/shadow 的位置、尺寸、颜色和强度，但它只能对当前 Scene 的数值字段做邻域搜索，不能增加缺失 feature、改变 feature 类型或建立新的局部高光结构。

这使初始结构成为硬上限：如果 Initial/Refine 没有创建正确的局部结构，增加 render 数只能把错误结构拟合得更好。

### 5.4 五次 Refine 全部被拒绝

五次 Refine 共消耗：

- 延迟约 `35.910s`；
- tokens `14,355`；
- 结构修复 `0` 次。

候选与当时 best 的比较为：

| Refine | 候选 loss | 当时 best loss | 结果 |
| --- | ---: | ---: | --- |
| 1 | `0.055962` | `0.052912` | rejected |
| 2 | `0.053366` | `0.050895` | rejected |
| 3 | `0.048267` | `0.047553` | rejected |
| 4 | `0.047939` | `0.045965` | rejected |
| 5 | `0.054389` | `0.044667` | rejected |

五个 Refine 候选都没有进入最终 Scene，也没有影响后续确定性搜索。当前终态 trace 未保存 Patch 的 `op`、feature id/type 或安全差异摘要，因此无法判断模型是否重复提出同类无效 Patch。

### 5.5 预算调度让 render 先于语义预算耗尽

本次总耗时 `155.398s`：

- render `320/320`；
- LLM `6/9`，其中 1 次 Initial、5 次 Refine；
- Refine `5/9`；
- LLM 累计可见延迟约 `53.106s`，占端到端耗时约 `34%`；
- prepared program 准备耗时约 `3.771s`；
- uniform render p95 约 `445.9ms`。

每次 Refine 后，系统重新执行完整 base sweep，再遍历当前全部 feature。单轮通常消耗约 50 多次 render，最终在完成第 5 次 Refine 后由第 6 轮确定性搜索耗尽全部 draw，剩余 3 次 LLM/Refine 预算无法使用。

增加 high 的总预算没有等比例增加语义探索，主要增加了对同一基础结构的重复坐标搜索。

## 6. 最终结果分析

### 6.1 指标

| 指标 | 最终值 |
| --- | ---: |
| global MAE | `0.035462` |
| foreground MAE | `0.048455` |
| background MAE | `0.008576` |
| geometry mask loss | `0.088925` |
| edge loss | `0.008607` |
| worst tile MAE | `0.061054` |
| composite loss | `0.043850` |

global MAE 低于展示目标 `0.04`，但实际停止条件是 composite loss `<=0.02`，因此质量没有达标。`agent_runs.status=succeeded` 只表示流程成功完成；质量状态必须以 `target_reached=false` 为准。

### 6.2 空间与颜色残差

4×4 tile MAE 为：

```text
0.0221  0.0661  0.0560  0.0152
0.0560  0.0342  0.0259  0.0451
0.0195  0.0383  0.0332  0.0458
0.0080  0.0316  0.0509  0.0201
```

顶部两个 tile 构成 worst-tile 指标，覆盖缺失的顶部高光和暗部；右下 tile 的 `0.0509` 对应缺失的月牙高光。

全图分通道 MAE：

```text
R = 0.0095
G = 0.0557
B = 0.0411
```

红色基调拟合较好，主要误差来自绿色、蓝色和亮度层次。参考图最暗 5% 前景像素中，最终图平均亮约 `0.108`；参考图最亮区域中，最终图平均暗约 `0.04`。这说明结果压缩了动态范围，无法形成透明凝胶所需的深暗部与近白反射。

### 6.3 feature 消融证明槽位不是本次瓶颈

使用同一 reference、模板和 scorer 只读复算：

| Scene 变体 | loss | geometry loss |
| --- | ---: | ---: |
| fallback | `0.061547` | `0.134900` |
| final，无 feature | `0.057188` | `0.172337` |
| final，仅 rim | `0.057188` | `0.172337` |
| final，仅 shadow | `0.043850` | `0.088925` |
| final，完整 | `0.043850` | `0.088925` |

最终 `rim.intensity=0`，因此 `rim-only` 与无 feature 完全一致；`shadow-only` 与完整 final 完全一致。现有四槽未被用满，增加第五到第八槽不能直接解决本次失败。

Shadow 大幅改善 geometry loss，也提示当前 geometry mask 会把参考图的外部软阴影/光晕与实体轮廓混在一起。优化器可能通过扩展外部 shadow 的候选前景掩码降低 geometry loss，而不是恢复主体材质细节。

## 7. 根因

### 7.1 Initial Author 缺少结构分解职责

Prompt 告诉模型“允许使用什么”，没有告诉它“何时必须使用”。在 fallback 已经可渲染时，“保守优先”容易变成复制 fallback，未主动使用 `gaussian_lobe`、`polar_arc`、`edge_line` 或剩余槽位表达局部残差。

### 7.2 结构候选在成熟前被淘汰

当前 Refine 流程是：

```text
current_best
→ 一个 typed patch
→ 单次真实渲染
→ 立即与 current_best 比较
→ 较差则丢弃整个结构
```

一个方向正确的 `add_feature` 或 `replace_feature` 仍需要位置、axes、颜色和强度同时接近正确值，才有机会首帧胜过已经经过大量局部优化的 best。否则新增结构在进入 feature optimizer 前就被移除。

### 7.3 搜索只能精修当前结构

确定性优化器对已有字段有效，并保持 best 单调安全，但不能修复结构缺失。重复执行完整 base 与所有已有 feature 的邻域搜索，会优先消耗 render 预算，而不是增加新的语义假设。

### 7.4 Refine 缺少可操作残差和拒绝历史

Refine Author 能看到参考图、current render 和汇总指标，但不知道：

- 最差 tile 的坐标；
- 局部是偏亮、偏暗还是颜色偏移；
- 哪个 loss 分量主导；
- 上一轮 Patch 的安全摘要和拒绝原因；
- 同一 Patch 是否已经失败。

这提高了重复提出宽泛 color-field 替换或参数不成熟 feature 的概率。

### 7.5 目标与预算实验没有冻结可比性

本次更严格的 `target_loss=0.02` 与扩大的 high 预算没有对应固定 7 例和人工偏好证据。目标更严格只会延长搜索，不会自动提高结构建模能力。

## 8. 优化建议

### P0 前置门禁：冻结实验身份与可比配置

在实现算法改动前，先明确每个 run 属于以下哪一种：

- 冻结 benchmark：使用 D058/D059 对应的冻结目标、预算、manifest 和报告格式；
- 独立实验：可以覆盖目标或预算，但必须记录实际配置和独立实验标识，不进入冻结 gate。

本次 `0.04/0.02 + 320/9/9` 只能作为独立实验。配置漂移不是视觉失败的根因：即使恢复 `target_loss=0.04`，最终 `0.043850` 仍未达标，且关键高光仍明显缺失。该门禁的目的只是恢复证据可比性，不能用降低 target 掩盖结构问题。

### P0：先补齐 Patch 证据、空间残差与拒绝历史

可观测不是末期增强，而是 candidate maturity 和 Prompt 调整的前置条件。五次 Refine 全部被拒绝，但现有终态证据无法区分“结构类型错误”“方向正确但参数不成熟”或“重复提案”。在改变选择流程前，至少记录：

- `patch_operation`；
- `feature_id` 与 `feature_type`；
- 不含完整内容的 Patch 安全指纹；
- raw candidate loss 与各 metric 分量 delta；
- `rejected_reason`；
- 是否与近期已拒 Patch 重复；
- 节点实际 `duration_ms`。

同时向 Refine Author 提供小型、确定性、无案例语义的空间残差摘要：

```text
worst_tiles:
  - row / column
  - mae
  - signed_luminance_bias
  - signed_rgb_bias
dominant_metric_component
active_feature_summary
recent_rejected_patch_summaries
```

摘要只表达测量事实，不把区域命名为“左上高光”等案例特定语义。终态账本不得持久化模型原始响应、完整图片、完整 GLSL、用户输入或 reasoning content。

### P0：为 typed structural patch 增加有界候选成熟阶段

建议把 Refine 候选的选择语义改为：

```text
current_best 保持只读安全锚点
→ 应用一个非重复的合法 typed patch，得到 candidate branch
→ 真实渲染 raw candidate
→ 只对 Patch 影响的参数做一次有界局部优化
→ 用 matured candidate 与 current_best 比较
→ 严格改善才提交，否则整支丢弃
```

Patch-aware 成熟范围：

- `add_feature` / `replace_feature`：只优化该 feature；
- `replace_color_field`：只优化 color-field bindings；
- `remove_feature`：直接重新评分，不执行完整 sweep。

约束：

- 未验证候选不得覆盖 `current_best`；
- 不开放任意 JSON Patch 或 GLSL；
- 每个非重复合法候选最多获得约 12 次局部 render，具体上限在 fixture 与固定 manifest 中冻结；
- 成熟消耗现有 render budget，不引入隐藏预算；
- raw candidate 即使暂时较差也不以同一即时 loss 规则提前淘汰，否则会重新引入本次问题；
- 优先在现有节点内部实现，除非后续证明确需改变 Graph 拓扑；
- 成熟后仍不改善时整支丢弃，后续搜索继续从原 `current_best` 出发。

验收重点：构造一个初始参数略差、经 12 次以内 feature-local 搜索后可胜出的 `add_feature` fixture，证明结构不会因首帧不成熟被过早淘汰；同时覆盖非法、重复、成熟后仍较差和 Renderer 失败分支，证明都不能污染 best 或越过预算。

### P1：让 Initial Author 按证据完成视觉结构分解

在不改变“只输出完整 Scene JSON”的前提下，Prompt 增加以下决策准则：

1. primitive 只负责主体轮廓；
2. 大范围连续颜色变化使用 solid/radial/linear；
3. 只有存在稳定、局部、无法由基础颜色场解释的视觉证据时，才使用 feature 表达局部亮斑、暗斑、边缘弧或外部效果；
4. 输出前必须检查局部残差和剩余槽位，但不得为了使用槽位而虚构 feature；
5. fallback 是安全下界，不是默认答案；没有可靠局部证据时允许沿用，存在明确证据时不得仅因“保守”而忽略；
6. 不针对“粉色球”写特判，使用局部范围、边缘依附、内外区域和亮暗方向等通用视觉事实。

真实模型验收应统计 Initial 与 fallback 的像素等价率、Initial 胜出率、active feature 数、feature 消融边际贡献和非法输出率，不能只验证 JSON 解析成功，也不能把 feature 数增加本身视为进步。

Refine Prompt 使用同一证据规则：局部 tile 主导时优先 add/replace feature，全局同方向偏差时才考虑替换 color field；边缘、主体内部和主体外残差分别映射到适用的 feature 白名单，并避免重复近期已拒指纹。

### P1：按变更范围调度搜索并自适应停止

建议保持总硬预算不变，先调整消费顺序：

- Initial 后最多执行一次完整 base sweep；
- Refine 后只成熟 Patch 改变的结构，不重跑完整 base + 全 feature sweep；
- 只为下一次非重复、合法语义提案保留一次 raw render 和一小批 maturity draw，不机械保证用完全部 LLM/Refine 预算；
- Patch 指纹重复时不再投入 maturity draw；
- 连续若干非重复 Patch 成熟后仍无改善，或单位 render 收益持续低于冻结阈值时，提前停止 Refine 或 finalize；
- 重复次数、停滞阈值和最小保留预算必须通过固定 manifest 冻结，不在生产代码中凭单例硬编码。

本次前五次 Refine 都未改善，因此“剩余三次调用未使用”本身不代表缺陷。真正的问题是 320 次 render 大量消耗在重复全量 sweep 上，同时系统没有证据判断继续调用模型是否值得。

先比较相同总 render/LLM 硬预算下的最终 loss、有效 Patch 接受率、重复提案率和端到端耗时，再决定是否扩大预算或引入新的优化算法。

### P1：分离 primitive silhouette 与外部效果评价

Shadow 消融使 geometry loss 从 `0.172337` 降到 `0.088925`，说明 selection oracle 也存在需要验证的语义偏差。如果不处理该问题，candidate maturity 可能只是更高效地优化错误目标。

不基于单例直接调整所有权重，先修正 geometry 语义：

- primitive silhouette 使用与 Scene primitive 对应的实体区域比较；
- shadow/glow 进入背景或外部效果误差，不应扩大候选 primitive mask；
- worst-tile 继续负责局部残差，并报告具体 tile 与 signed bias；
- 指标调整必须同时跑固定 7 例、反例和人工偏好，避免为了该粉色球破坏通用性。

目标是防止外部 shadow 通过覆盖参考图软边区域获得过高 geometry 奖励，不是移除 shadow 的合法贡献。

## 9. 明确暂不建议

本次运行不支持以下结论：

- 不建议因为该案例把 feature 槽从 4 扩到 8；当前只有 1 个 feature 实际生效，这只证明本次没有用满四槽，不证明四槽对所有未来案例永久充分；
- 不建议立即开放 AI 任意编写 GLSL；当前失败发生在已有 typed 能力未被正确提议和成熟；
- 不建议立即引入 CMA-ES、2000 draw、动态 Compiler、多 program cache 或自动切换 `procedural_v1`；
- 不建议依据单张图直接调高 worst-tile 权重或降低 target；
- 不建议把 `status=succeeded` 与质量通过合并展示。

## 10. 建议实施顺序与门禁

保持单一 active 功能和小增量，建议顺序：

0. 明确冻结 benchmark 或独立实验身份，记录实际目标、预算与配置指纹；
1. 增加 Patch 安全摘要、空间残差摘要与拒绝历史，建立可解释基线；
2. 增加 patch-aware candidate maturity，保持 `current_best` 安全边界并冻结单候选局部预算；
3. 调整 Initial/Refine Prompt，加入有证据门槛的结构分解，并用固定真实模型 manifest 验证；
4. 按 Patch 范围调整搜索消费顺序，增加重复提案与停滞停止条件；
5. 独立修正 geometry mask 语义并重跑固定 7 例与人工盲评。

最小实现增量只包含第 1、2 步：先看清模型提出了什么，再让非重复合法结构获得一次公平但有界的成熟机会。该增量不扩槽、不开放 GLSL、不调整 metric 权重，也不引入新的搜索算法。

每一步至少验证：

- typed contract 与非法 Patch fail-closed；
- best 不被较差或失败候选覆盖；
- render/LLM/Refine 硬预算不越界；
- 非重复 Patch 才获得 maturity 预算，重复与停滞可被稳定识别；
- fixed program 与 uniform schema 不漂移；
- 相同 scorer 下的 deterministic fixture；
- 显式真实模型固定 manifest；
- 独立匿名人工偏好。

在完整门禁通过前，`F09` 继续保持 `active/no-go`，本建议不得被表述为已经实现或已经证明有效。
