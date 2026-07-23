# scene_mvp 固定模板扩展设计

## 1. 状态与定位

- 日期：2026-07-22
- 状态：已实现并通过无模型/真实 Chromium 工程验收；真实模型与独立人工质量门禁尚未执行，未发布
- 所属功能：`F09` PNG 转无贴图 Shader Agent V1
- 适用路径：显式实验模式 `scene_mvp`
- 不影响：默认 `procedural_v1`、现有 V1 Graph、Memory、历史 benchmark 与冻结失败证据

本方案是在 `png_to_shader_min_scene_v2` 三槽固定模板基础上的有界扩展。目标不是建立任意 Shader DSL，而是在不改 Graph 拓扑、不引入 run 内多 program 生命周期的前提下，提高单主体解析式 2D 视觉的通用表达能力。

本方案名称不是产品阶段或历史版本号，也不继承已删除旧 V3 方案的 Oracle、Search、State 或阶段门禁。实现若需要不兼容升级，Scene/template/metric 的正式版本标识由实现增量按各自当前版本顺序确定；本设计不提前用“V3”统称这些契约。

本方案经两轮独立只读 Review 后收敛。Review 的共同结论是：动态 Compiler、最多 8 个逻辑 feature、自动残差语义分解、逐 feature 消融和自动切换 `procedural_v1` 不应与本增量同时实施。

## 2. 问题陈述

当前 v2 存在三类主要限制：

1. `ColorField.model` 声明 `solid | radial`，但固定模板没有消费 model，实际始终执行 radial 公式。
2. `Primitive.type` 声明 `circle | ellipse`，但像素结果只由 `axes` 决定，类型没有独立可验证语义。
3. 每个 feature 固定使用 3 个 `vec4`，在 WebGL1 最低 16 个 fragment uniform vectors 约束下只能安全提供 3 个槽；同时缺少主体内部局部色团和主体外发光能力。

参考粉色凝胶球只是暴露问题的诊断样例。本方案的验收必须覆盖当前 Scene 边界内的多种单主体案例，不能针对单一图片写特判。

## 3. 设计目标

### 3.1 必须实现

- 保持单主体、WebGL1、静态、无贴图边界。
- 保持同一 run 固定 WebGL1 源码和 uniform schema，只 prepare 一次。
- 将 feature 槽从 3 个增加到 4 个，并严格满足 WebGL1 最低 uniform 容量。
- 让 `solid`、`radial`、`linear` 三种颜色场具有真实、互异的像素语义。
- 让 `circle` 与 `ellipse` 的契约语义可验证。
- 增加主体内部 `gaussian_lobe` 和主体外部 `glow`。
- 增加按稳定 id 原子替换 feature 的 Refine 操作。
- 使用通用区域指标替换 v2 的亮度分位数伪 `highlight/shadow` 指标。
- 保持所有候选必须经真实 Renderer 且 objective 严格改善后才能覆盖 `current_best`。

### 3.2 明确不做

- 不增加动态 Shader 源码或 run 内多 program cache。
- 不支持超过 4 个 feature。
- 不增加 `rounded_rect`、`ring`、`superellipse` 或多对象。
- 不增加自定义 GLSL Block、插件系统、DSL、WebGL2、多 Pass 或动画。
- 不做自动残差 feature 分类、动态语义 ROI 或逐 feature 消融。
- 不引入 CMA-ES、2000 draw 或新的优化算法。
- 不在 `scene_mvp` 内自动调用 `procedural_v1`。
- 不新增、删除、重命名 Graph 节点，不修改直接边、条件边或路由结果。

## 4. Scene 契约

实现已分别冻结 `png_to_shader_min_scene_v3`、`png_to_shader_min_template_v3` 与 `min_scene_composite_v3`。这里的 `v3` 只表示三个独立契约各自从当前 v2 顺序升级，不表示项目阶段，也与历史 V3 Oracle/Search 无关。

历史 v2 Artifact 保持原样，不覆盖、不迁移。最终 GLSL 继续烘焙常量，保持自包含。

### 4.1 Primitive

本轮只保留：

```text
circle
ellipse
```

共同参数保持：

```json
{
  "type": "circle | ellipse",
  "center": [0.0, 0.0],
  "axes": [0.8, 0.8]
}
```

语义约束：

- `circle` 必须在解析或验证阶段保证等轴；允许的数值容差必须冻结并测试。
- `ellipse` 允许两个 axes 不同。
- 两者可复用同一椭圆 SDF，但契约验证不得让 `circle` 携带明显不等轴的参数。

### 4.2 Color Field

颜色场使用 discriminated union：

```text
solid
radial
linear
```

建议参数语义：

```json
{
  "model": "solid",
  "color": [1.0, 0.4, 0.6]
}
```

```json
{
  "model": "radial",
  "inner": [1.0, 0.8, 0.9],
  "outer": [1.0, 0.3, 0.5],
  "origin": [-0.3, 0.4],
  "scale": 1.2
}
```

```json
{
  "model": "linear",
  "start": [1.0, 0.2, 0.4],
  "end": [1.0, 0.95, 0.98],
  "direction": [0.15, -0.45],
  "offset": 0.48,
  "scale": 1.0
}
```

约束：

- 类型字段必须进入 uniform 或固定模板分支，不允许只存在于 JSON。
- `solid` 必须忽略空间位置。
- `radial` 使用 object-local `origin/scale`。
- `linear` 使用 object-local `direction/offset/scale`。
- 三种模型必须通过真实 Chromium 像素互异测试。

具体字段可以在实现前为固定 uniform packing 做小幅调整，但不得改变上述视觉语义或增加第四种模型。

### 4.3 Feature

最多 4 个 feature，稳定 id 必须唯一。支持类型：

```text
rim
shadow
polar_arc
edge_line
gaussian_lobe
glow
```

本轮继续使用统一的 8 个数值参数：

```text
center.xy + axes.xy + color.rgb + intensity
```

各类型必须有固定且互异的像素语义：

- `rim`：依附主体 SDF 边界的内部边缘带。
- `shadow`：只作用于主体外背景的暗色 Gaussian。
- `polar_arc`：依附主体边界的局部弧带；不得退化成通用 rim。
- `edge_line`：有限长度的局部线带。
- `gaussian_lobe`：只作用于主体内部的局部颜色团或 haze。
- `glow`：只作用于主体外背景的亮色 Gaussian。

本轮不增加通用 `blend` 字段；每种类型的合成规则固定在模板中。

固定合成顺序：

```text
background
→ shadow / glow
→ primitive color field
→ gaussian_lobe
→ rim
→ polar_arc / edge_line
```

## 5. Uniform 布局与 WebGL1 边界

每个 feature 从 3 个 `vec4` 收紧为 2 个：

```glsl
uniform vec4 u_feature_N_shape;       // center.xy, axes.xy
uniform vec4 u_feature_N_color_power; // color.rgb, intensity
```

四个 feature 的类型统一放入：

```glsl
uniform vec4 u_feature_kinds;
```

Scene 与颜色场类型放入：

```glsl
uniform vec4 u_scene_meta;
```

最坏静态使用量：

```text
基础 Scene                    4 vec4
4 个 feature × 2             8 vec4
feature kinds                 1 vec4
scene meta                    1 vec4
Renderer u_resolution         1 vector
--------------------------------------
合计                         15 vectors
```

要求：

- 物化阶段 fail closed 断言最坏使用量不超过 16。
- 真实 Chromium 验证所有 schema uniform 都 active 且类型一致。
- 所有合法扩展 Scene 必须产生完全相同的 `webgl1_source + uniform_schema` 签名。
- add/remove/replace 只更新完整 uniform 值集，不得触发第二次 prepare。

## 6. Model Author 与 Patch

Initial Author 继续输出完整扩展 Scene；确定性感知 fallback 和模型 Scene 继续分别真实渲染并按统一 objective 仲裁。

Refine 每轮仍只允许一个 typed 操作：

```text
add_feature
remove_feature
replace_feature
replace_color_field
```

要求：

- `replace_feature` 按稳定 feature id 原子替换完整 Feature。
- `replace_color_field` 替换完整 typed ColorField，不能只替换 model 字符串。
- 不开放任意 JSON Patch。
- 不允许 Refine 直接修改 GLSL、预算或 `current_best`。
- 不存在的 id、第 5 个 feature、非法类型或资源越界必须安全拒绝。
- 拒绝或较差候选不得覆盖 `current_best`。

`scene_mvp` 与 `procedural_v1` 继续是两个显式 generation mode。本轮不定义自动切换、预算合并或跨 Graph `current_best` 比较。

## 7. 确定性感知

本轮只增加基础颜色场拟合，不建设新的 inverse-rendering 流水线。

保留：

- 边缘背景估计；
- 主体阈值分割；
- bbox、中心、axes；
- 单主体 scope 判断。

新增：

- 在主体内部排除边缘像素后分别拟合 `solid`、`radial`、`linear`；
- 使用同一确定性误差选择最优基础颜色场并给出初始参数。

暂不做：

- 自动识别 rim、arc、glow、lobe；
- 残差连通域分类；
- 循环增加 feature；
- feature 边际贡献计算。

复杂 feature 结构由 Initial Author 提议，真实 Renderer 决定是否接受。

## 8. 评价函数

评价契约已升级为 `min_scene_composite_v3`，不预占项目阶段名称。

目标保持通用，不使用“左上高光”“右下高光”等案例特定标签。建议分量：

```text
global_mae
foreground_mae
background_mae
geometry_mask_loss
edge_loss
worst_tile_mae
```

说明：

- `background_mae` 使用参考图主体外保护区，防止优化器把白色背景染色。
- `geometry_mask_loss` 比较参考主体 mask 与候选 primitive mask，防止通过缩小或移动主体降低平均误差。
- `edge_loss` 约束主体轮廓和边缘层次。
- `worst_tile_mae` 使用固定网格中误差最大的若干 tile，防止局部高光被整图平均稀释；它不解释视觉语义。
- benchmark manifest 的冻结 key ROI 只用于离线验收，不进入生产 objective。

实现冻结权重为 global/foreground/background/geometry/edge/worst-tile=`0.20/0.25/0.15/0.15/0.10/0.15`，固定 `4×4` 网格取最坏 2 个 tile；无前景证据时停用 background/geometry 并重新归一化。固定 7 例 v3 deterministic fallback 的内部 loss 中位数约为 `0.0402`，据此把新的 `target_loss` 冻结为 `0.04`，不沿用 v2 的 `0.08`。

## 9. 数值优化与预算

继续使用现有有界确定性、单参数、最新 best 重放策略：

```text
geometry
→ color field
→ feature queue
```

本轮仅做必要接线：

- 为 `linear` 和新增 feature 增加白名单参数 binding。
- 四个 feature 时将单 feature batch 从 16 收紧至 10～12；最终值应在实现前按 `fast|balanced|high` 的预算演算冻结。
- 保持每个候选都计入 render budget。
- 保持只有 objective 严格改善才提交。

本轮不增加联合搜索、随机搜索、CMA-ES 或额外 feature 消融 draw。

## 10. Graph、Renderer 与产品边界

Graph 保持现有 12 节点、直接边、条件边和路由结果不变。`current_best` 安全边界、Model/fallback 仲裁、Refine 回环、finalize 和 Renderer 关闭语义不变。

Renderer 保持一个 run 一个 prepared program，不引入多 program cache。最终 WebGL1 和 Shadertoy GLSL 继续烘焙常量。

Backend/Frontend 保持：

- `procedural_v1` 默认；
- `scene_mvp` 显式实验模式；
- 现有 Artifact、进度事件和预算摘要边界。

公开的 scene/template/metric version 必须同步升级；功能状态仍为 `active/no-go`。

## 11. 验收方案

### 11.1 契约与资源

- 扩展 Scene 严格拒绝未知字段、重复 feature id 和第 5 个 feature。
- 最坏 active fragment uniform vectors 精确为 15 且不超过 16。
- 所有合法扩展 Scene 的 program 签名相同。
- 一个 run 在 Initial、fallback、add/remove/replace 后仍只 prepare 一次。

### 11.2 像素语义

- `solid`、`radial`、`linear` 在真实 Chromium 中像素互异。
- 六种 feature 的作用域和像素结果互异。
- 四个槽位都消费 `center/axes/color/intensity`。
- baked GLSL 与 prepared uniform 渲染逐像素一致。
- add/remove/replace 后无陈旧 uniform 和陈旧帧。

### 11.3 Graph 安全

- 模型 Scene 与不同结构的 fallback 继续真实仲裁。
- 满四槽时 `replace_feature` 合法。
- 非法 id、第 5 槽和较差候选不覆盖 `current_best`。
- Refine 后 best objective 保持严格单调。
- render/LLM/Refine 硬预算没有旁路。

### 11.4 通用质量集

本轮覆盖当前 Scene 边界内的 7 个案例：

```text
solid_circle
ellipse_gradient
shadow_disk
rimmed_disk
arc_highlight_orb
color_lobes
pink_gel
```

明确不支持且不得伪装成功：

```text
rounded_rect_glow
neon_ring
dual_disks
```

比较当前基线与固定模板扩展结果时，必须使用同一份外部 benchmark objective，不能直接比较两个内部 metric version 的 loss 数字。冻结要求：

- 7/7 WebGL1 compile/draw 成功；
- 每例 final 不劣于自身 Initial/fallback winner；
- 至少 5/7 相比冻结 v2 baseline 改善；
- 其余案例不得超过预设 global/ROI/bbox 回归容差；
- 真实模型 benchmark 和独立人工盲评通过前，F09 继续 `active/no-go`。

## 12. 实施顺序

每次只处理一个可验证增量：

1. 冻结扩展 Scene、uniform 布局、正式契约版本和最坏资源计算。
2. 实现固定扩展模板，并完成 prepared/baked/像素语义测试。
3. 接入 `linear` 确定性拟合和 type-aware optimizer bindings。
4. 实现 `replace_feature`、完整 `replace_color_field` 和 Model Author 契约。
5. 版本化 `min_scene_composite_v3`，冻结权重与 target。
6. 运行 7 例无模型、真实模型和独立人工质量门禁。

若后续证据证明四槽或 circle/ellipse 是主要质量瓶颈，再分别立项：

- 几何扩展：`rounded_rect`、`ring`；
- 动态结构 Compiler 与 run 内多 program 生命周期；
- 更大参数搜索预算。

这些能力不属于本方案的完成条件。

## 14. 实施与验证结果

- Scene/template/metric 已分别升级为 v3 契约；历史 v2 Artifact 不迁移、不覆盖。
- 四槽模板的最坏 active fragment uniform vectors 为 15/16；真实 Chromium 已验证 prepared/baked 像素一致、三种颜色场互异、六种 feature 互异以及四个槽位均产生像素影响。
- deterministic fallback 会在主体内部排除边缘像素后，以同一像素 MAE 拟合并选择 solid/radial/linear；优化器按颜色场类型绑定参数，circle 半径联动保持等轴。
- Refine 已支持 add/remove/replace feature 和完整 replace color field；不存在 id、第 5 槽、重复 id、破坏稳定 id 或非法 union 均安全拒绝。
- 冻结对照见 `benchmarks/png_to_shader_v1/scene_mvp_fixed_template_v3_baseline.json`：7 个支持案例使用同一外部 `png_to_shader_score_v1` 比较 v2 与 v3 确定性 fallback，v3 为 6/7 改善，剩余 `solid_circle` 的 total-loss 回归 `0.000217`，低于预设 `0.001` 容差；所有 ROI 与 bbox/geometry 均未越过预设回归容差。
- `rounded_rect_glow`、`neon_ring`、`dual_disks` 只作为明确 out-of-scope 案例记录，不进入支持率分母。当前没有运行时自动结构分类或自动切换 `procedural_v1`，产品仍需显式选择 generation mode。
- 上述证据不含真实模型输出和独立人工盲评，因此 F09 保持 `active/no-go`。

## 13. 预期修改边界

预计涉及：

- `src/shaderforge/scene.py`
- `src/shaderforge/generation/min_template.py`
- `src/shaderforge/perception/min_perceive.py`
- `src/shaderforge/evaluation/mae.py`
- `src/shaderforge/optimization/min_optimize.py`
- Min Author contract、parser、prompt 和节点测试
- Scene/template/metric 版本公开契约与相关文档

预计不涉及：

- Graph 节点、边和路由结果
- Renderer 多 program 生命周期
- Backend generation mode
- Frontend 主交互结构
- `procedural_v1`

实现时仍须遵守 Graph 可视化同步规则；如果实际改动触及节点、边、路由结果、循环、终止路径或 `current_best` 安全边界，必须在同一次改动中同步 ASCII 图、Mermaid、路由表和安全说明，并通过 `make docs-check` 与 `uv run langgraph validate`。
