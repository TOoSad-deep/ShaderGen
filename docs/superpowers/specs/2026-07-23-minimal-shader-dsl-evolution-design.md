# scene_mvp 最小 Shader DSL 演进方案

## 1. 状态与结论

- 日期：2026-07-23
- 状态：待审核草案，未实施
- 当前功能：`F09` 继续是唯一 `active` 功能
- 当前产品事实：仍使用 `png_to_shader_min_scene_v3`、固定模板和单主体最多四个 feature

本方案不改变当前架构、功能状态、质量门禁或运行配置。只有审核通过并形成正式决策后，才进入实现。

建议采用的方向是：

> 用“有序图层 + 层内受限 Shape/CSG 树”替代“单主体 + 最多四个 feature”，并由确定性 Compiler 生成 GLSL。

不建议把第一版做成任意节点 DAG。对用户、LLM 和 API 暴露的结构保持为图层和树；通用节点图只作为 Compiler 内部 IR。

本方案是有条件推进，不是立即整体切换：

```text
先证明现有表示确实成为质量瓶颈
→ 冻结 DSL 契约与 Compiler
→ 多图层 shadow 双跑
→ 层内 CSG
→ 旋转与完整 8 层
→ 产品切换与新质量门禁
```

## 2. 为什么要改

当前 Scene 只能表示：

```text
Canvas
└── 一个 Object
    ├── 一个 circle / ellipse
    ├── 一个 solid / linear / radial ColorField
    └── 最多四个 Feature
```

它无法直接表示：

- 两个或更多独立对象；
- 前后遮挡和明确图层顺序；
- 圆角矩形、圆头线段；
- 镂空、求交和组合轮廓；
- 图层级透明度与 Alpha 合成。

但当前已知质量阻塞不全是表达力问题。rim、弧形高光和双高光仍可能在现有单主体边界内失败。因此，DSL 迁移不能替代评价、搜索和人工质量门禁，也不能用“结构更通用”直接推导“结果一定更好”。

## 3. 设计目标

### 3.1 第一版必须具备

- 最多 8 个有序图层；
- `circle`、`ellipse`、`rounded_box`、`segment`；
- 平移、缩放、旋转；
- 层内 `union`、`subtract`、`intersect`；
- `solid`、`linear`、`radial`；
- `rim`、`shadow`、`glow`；
- 图层 opacity 和固定 `source-over` Alpha 合成；
- 严格 Schema、稳定 ID、确定性规范化和内容哈希；
- 由 DSL 确定性生成 WebGL1 GLSL；
- 保留真实渲染仲裁和 `current_best` 不回退边界；
- LLM 只生成完整 DSL 文档或一个 typed patch，不直接生成 GLSL。

### 3.2 明确不做

- 不开放任意 DAG、循环、自定义函数或 Shader AST；
- 不允许用户或模型提交任意 GLSL；
- 不增加 multiply、screen、add 等混合模式；
- 不增加纹理、噪声、动画、WebGL2 或多 Pass；
- 不在首个增量引入 CMA-ES、2000 draw 或新的 scorer；
- 不同时建设异步任务、取消恢复、分布式 Renderer；
- 不同时建设通用拖线编辑器；
- 不把每个 DSL 节点映射成 LangGraph 节点；
- 不原位修改已有 v3 Scene/template/metric 契约。

## 4. 对外模型：图层列表与层内树

第一版对外只暴露三层概念：

```text
ShaderDocument
├── Canvas
└── Layers（后 → 前）
    └── Layer
        ├── ShapeExpr
        ├── Fill
        ├── Effects
        └── opacity
```

### 4.1 顶层文档

示意结构：

```json
{
  "schema_version": "shader_graph_v1",
  "canvas": {
    "width": 192,
    "height": 192,
    "background": [1.0, 1.0, 1.0, 1.0],
    "color_space": "srgb_encoded_v1",
    "output_alpha": "opaque"
  },
  "layers": []
}
```

约束：

- `layers` 数组顺序固定表示从后向前绘制；
- 图层顺序参与规范化哈希，不得自动排序；
- Canvas 第一版必须是 opaque background；
- 图层内部可以有透明度，但最终输出 Alpha 固定为 `1.0`；
- 所有颜色输入使用 straight RGBA，Compiler 内部转换为 premultiplied 表示后合成。

第一版暂时在 sRGB 编码域执行渐变和 Alpha 合成，以保持与当前 PNG RGB 指标的可比性。若后续改为线性光合成，必须升级颜色契约并重新冻结 benchmark 与阈值。

### 4.2 Layer

示意结构：

```json
{
  "id": "body",
  "visible": true,
  "opacity": 1.0,
  "shape": {},
  "fill": {},
  "effects": []
}
```

约束：

- `id` 在文档内唯一且稳定；
- `opacity` 范围为 `[0, 1]`；
- 每层恰好一个 `shape` 和一个 `fill`；
- 每层最多三个 effect，且 `rim`、`shadow`、`glow` 各最多一个；
- `visible=false` 的图层不参与渲染，但仍参与完整文档哈希；
- 图层间只允许 `source-over`，不开放自定义 blend。

固定层内顺序：

```text
shadow
→ glow
→ fill
→ rim
→ 与前方图层 source-over
```

Effect 一律基于本层最终 ShapeExpr 的 signed distance 或 coverage 计算，不读取其他图层。CSG 产生的孔洞属于本层边界，因此 rim/glow 可以出现在孔洞边缘；shadow 基于最终 coverage 投影。

### 4.3 ShapeExpr

ShapeExpr 是带稳定 `id` 的 discriminated union。每个节点可以携带一个可选 transform：

```json
{
  "translate": [0.0, 0.0],
  "scale": [1.0, 1.0],
  "rotation": [1.0, 0.0]
}
```

`rotation` 使用 `[cos(theta), sin(theta)]`，避免角度周期折返。验证时要求它接近单位向量。

Primitive：

```json
{"id": "s1", "kind": "circle", "radius": 0.5}
```

```json
{"id": "s2", "kind": "ellipse", "radii": [0.5, 0.3]}
```

```json
{
  "id": "s3",
  "kind": "rounded_box",
  "half_size": [0.5, 0.3],
  "corner_radius": 0.08
}
```

```json
{
  "id": "s4",
  "kind": "segment",
  "from": [-0.4, 0.0],
  "to": [0.4, 0.0],
  "radius": 0.04
}
```

`segment` 明确定义为圆头 capsule，不是无宽度数学线段。

Boolean：

```json
{
  "id": "ring",
  "kind": "subtract",
  "base": {"id": "outer", "kind": "circle", "radius": 0.5},
  "cut": {"id": "inner", "kind": "circle", "radius": 0.35}
}
```

规则：

- `union` 和 `intersect` 使用 `left/right`；
- `subtract` 使用有序的 `base/cut`；
- 第一版只支持二元 Boolean；
- Boolean 只能发生在单个 Layer 内；
- 不允许一个 Layer 引用另一个 Layer 的 ShapeExpr；
- 每层最多 4 个 Primitive；
- CSG 深度最多 2；
- 全文最多 32 个 Primitive；
- 所有引用必须可达、无环且类型正确。

所有 ShapeExpr 必须输出统一符号约定的 signed distance：内部为负、边界为零、外部为正。非均匀缩放后的距离校正规则必须由 Compiler 版本冻结，不能由模板自行选择。

### 4.4 Fill

第一版 Fill 使用 Canvas 坐标，避免 CSG 组合后不存在唯一 object-local 空间。

```json
{
  "kind": "solid",
  "color": [1.0, 0.4, 0.6, 1.0]
}
```

```json
{
  "kind": "linear",
  "from": [-0.5, 0.0],
  "to": [0.5, 0.0],
  "start_color": [1.0, 0.2, 0.4, 1.0],
  "end_color": [1.0, 0.9, 0.95, 1.0],
  "spread": "clamp"
}
```

```json
{
  "kind": "radial",
  "center": [-0.2, 0.3],
  "radius": 0.8,
  "inner_color": [1.0, 0.9, 0.95, 1.0],
  "outer_color": [1.0, 0.2, 0.4, 1.0],
  "spread": "clamp"
}
```

第一版不支持多 stop、conic、OKLab 或独立 Paint transform。

### 4.5 Effect

建议最小字段：

```text
rim:
  width, softness, color

shadow:
  offset, blur, spread, color

glow:
  radius, softness, color
```

约束：

- `rim` 第一版固定为 inner rim；
- `shadow` 和 `glow` 位于 fill 后方；
- softness、blur、spread 和 width 使用统一归一化长度单位；
- Effect 颜色包含 Alpha；
- Effect 不得修改其他图层的 geometry 或 fill。

## 5. 坐标、数值和 Alpha 约定

第一版统一采用：

```text
原点：画布中心
X：向右为正
Y：向上为正
长度：按画布短边归一化
旋转：cos/sin，逆时针为正
颜色输入：sRGB straight RGBA
内部合成：premultiplied source-over
输出：合成到 opaque Canvas 后输出 Alpha=1
```

当前模板按画布短边归一化，长期目标文档曾提出按画布高度归一化。为减少迁移误差，本草案暂选短边；如果审核决定改为按高度归一化，必须升级坐标契约，并重新建立迁移 parity 与 benchmark，不能把两种语义混在同一版本中。

必须拒绝：

- NaN、Infinity；
- 零或负 scale；
- 零长度 segment；
- 非正 radius/radii；
- 超出 half size 的 rounded-box corner radius；
- 明显不满足单位长度的 rotation；
- 重复 ID、循环、悬空节点；
- 超出图层、Primitive、深度或 Effect 上限。

边缘 AA 使用像素宽度换算，不使用依赖对象尺寸的固定归一化阈值。WebGL1 禁用导数时，AA 宽度必须显式从 `u_resolution` 推导。

## 6. 兼容性边界

现有 `circle`、`ellipse`、`solid`、`linear`、`radial`、`rim`、`shadow`、`glow` 可以建立确定性迁移映射。

以下现有能力不在本方案原始清单中：

```text
polar_arc
edge_line
gaussian_lobe
```

其中：

- `edge_line` 可以由 `segment + fill` 表达；
- `gaussian_lobe` 可以由独立 ellipse/radial Layer 表达；
- `polar_arc` 没有可靠的等价表达。

因此，产品切换前必须审核以下二选一决策：

1. 在 DSL V1 保留 `polar_arc` 兼容 effect；这是推荐选项。
2. 明确接受该能力回退，并用新 benchmark 和人工门禁证明不影响目标输入域。

未完成该决策前，不得声称旧 MinScene 可以无损迁移。

## 7. Compiler 与 Renderer

### 7.1 Compiler 边界

确定性编译链固定为：

```text
严格解析
→ Schema/类型/预算校验
→ canonical document
→ typed IR
→ resource plan
→ specialized WebGL1 GLSL
→ 静态验证
```

Compiler 不实现运行时任意节点解释器。GLSL 按实际 Layer/CSG 结构静态展开。

编译结果至少包含：

```text
dsl_schema_version
compiler_version
render_contract_id
document_sha256
topology_sha256
parameter_manifest_sha256
glsl_sha256
resource_summary
```

哈希语义：

- `document_sha256` 绑定结构、层序和全部参数；
- `topology_sha256` 只绑定节点类型、连接和层序；
- map key 规范排序；
- layers 数组顺序保持原样；
- 不可达节点在严格校验阶段直接拒绝，不静默保留。

### 7.2 Uniform 资源策略

8 层全部参数长期驻留 uniform 不满足 WebGL1 最低资源边界。第一版采用：

- 非当前优化 block 的参数烘焙为源码常量；
- 当前优化 block 的连续参数提升为 packed `vec4` uniform；
- 自定义 fragment uniform vectors 不超过 14；
- 加 `u_resolution` 后不超过 15，保留至少 1 个最低容量余量；
- 结构或 active parameter manifest 改变时生成新的 program 签名。

禁止：

- 每次 draw 重新编译；
- 用一个巨型 Shader 解释任意节点；
- 依赖动态数组索引或纹理保存 DSL 参数。

### 7.3 Run 内 Program Cache

当前“一 run 一个 program 签名”需要演进为有界 program cache。

建议 key 至少包含：

```text
compiler_version
topology_sha256
active_parameter_manifest_sha256
baked_parameter_sha256
width / height
```

要求：

- 结构候选在独立 branch prepare；
- 被拒候选不能替换或污染 anchor program；
- cache 必须有数量和编译预算上限；
- cache 淘汰、Graph 异常和 Service `finally` 都必须关闭资源；
- Prepared program 只存在于运行时 registry，不进入 State 或公开 Artifact。

具体 cache 数量和 compile budget 不在本草案提前拍值，由 Renderer/Compiler 性能基线后冻结。

## 8. 参数优化和 `current_best`

Compiler 负责生成稳定参数清单：

```text
node:<node_id>.<field>
layer:<layer_id>.opacity
canvas.background.<channel>
```

不再使用依赖数组位置的参数路径。

第一版仍使用确定性、小预算、分 block 优化：

```text
canvas
→ layer geometry
→ layer fill
→ layer effects
→ layer opacity
```

首个 DSL 增量不引入 CMA-ES。只有现有确定性策略在新参数空间得到可比较证据后，才单独评估联合优化器。

`current_best` 必须绑定：

```text
canonical document
document/topology hash
compiler/version
parameter manifest
GLSL hash
render
metrics
parent best hash
provenance
```

候选只有依次通过以下门禁才可替换 `current_best`：

```text
Schema 与资源校验
→ GLSL 静态验证和编译
→ 真实 WebGL1 渲染
→ 版本化评分
→ selection policy 严格改善
```

DSL 迁移期间不同时修改 scorer、阈值或 selection policy，避免无法判断收益来自表示还是评价策略。

## 9. Model Author 与 typed patch

保留当前安全原则：

- Initial Author 输出一个完整 DSL 文档；
- 确定性 fallback 与模型文档分别真实渲染后仲裁；
- Refine 每轮只输出一个 typed patch；
- Patch 从只读 `current_best` 派生；
- 失败、非法、重复或较差候选不能覆盖 best；
- 模型不能直接提交 GLSL。

第一版 Patch 只支持：

```text
add_layer
remove_layer
replace_layer
reorder_layer
```

`replace_layer` 原子替换完整 Layer，包括 ShapeExpr、Fill 和 Effects。第一版不允许模型直接编辑任意子树路径；数值微调交给优化器。

每个 Patch 必须携带：

```text
base_document_sha256
operation
layer_id
typed value
```

应用 Patch 后必须重新校验完整文档、资源预算和哈希。`base_document_sha256` 与当前 best 不一致时直接拒绝。

普通日志和进度事件只保存 operation、layer id、节点类型集合、base/result hash 前缀、compile/render/loss delta，不保存完整 Patch、图片、GLSL、模型原文或 reasoning。

## 10. LangGraph、API 和 UI

DSL graph 是业务数据，不是 LangGraph 拓扑。

在 Compiler shadow 阶段不修改现有 12 节点 Graph。产品切换时可以保留宏观闭环：

```text
initialize
→ perceive
→ author
→ compile/materialize
→ render/evaluate
→ optimize
→ refine
→ finalize
```

State 语义届时从：

```text
scene / materialized / feature_queue
```

迁为：

```text
graph_document / compilation / layer_queue
```

若把 `optimize_feature` 重命名为 `optimize_layer`，必须同次同步源码 ASCII、Mermaid、路由表、安全说明和 `langgraph.json` 相关说明。

API 第一切换版保持 Generate 请求不变。响应和 manifest 版本化增加：

```text
dsl_schema_version
compiler_version
document/topology hash
layer/node count
compile count/cache hit count
resource summary
shader_graph
```

进度事件只增加安全摘要，不传完整 graph。

UI 第一版只提供只读 Layer inspector：

- 按后到前显示图层；
- 展示 geometry、fill、effects 和 opacity；
- 展示拓扑 hash 与资源摘要；
- 不建设拖线编辑器。

## 11. 分阶段推进

### 阶段 0：先建立可验收基线

目标：证明“单主体表示不可达”在目标输入中占有足够比例。

交付：

- 新的版本中立 benchmark manifest；
- 单主体、多个对象、镂空、圆角矩形、segment、透明叠层样例；
- 自动指标和匿名人工评审规则；
- 当前 scene_mvp 在同一输入上的冻结基线。

建议 Go 条件：

- 至少约 20% 的目标集失败可以明确归因为现有表示不可达；
- 多图层/CSG 样例在产品目标中具有真实占比；
- 当前单主体质量缺口与表示缺口能够分开统计。

不满足时暂停大迁移，优先修现有感知、搜索和 scorer。

### 阶段 1：DSL 契约与离线 Compiler

只新增 Schema、canonicalization、Compiler、资源计划和 fixtures。

不修改：

- 当前产品 Scene；
- LangGraph；
- Backend/Frontend；
- `current_best`；
- scorer 和预算。

验收重点：

- 所有节点类型具有真实、互异的像素语义；
- 循环、悬空、类型错误和资源越界 fail closed；
- 相同规范文档生成相同 hash 和 GLSL；
- 8 层、最大 Shape/CSG 结构仍满足源码和资源预算；
- 当前可映射子集的 baked/prepared 渲染保持 parity。

### 阶段 2：多图层 shadow 双跑，不启用 CSG

先启用：

- 最多 4 个 Layer；
- 四种 Primitive；
- Fill、Effect、opacity 和 source-over；
- run 内有界 program cache；
- 稳定 parameter manifest。

新 DSL 只产生对照结果，不参与产品 `current_best`。

验收重点：

- 不同 topology 不串 program；
- 未接受 branch 不污染 anchor；
- 无陈旧 uniform、帧或 Renderer；
- 现有可迁移场景不发生不可解释回归；
- 多对象样例相对单主体基线有明确改善。

### 阶段 3：层内 CSG

按以下顺序开放：

```text
union
→ subtract
→ intersect
```

每个操作独立验证后再开放下一个。重点覆盖：

- signed-distance 符号；
- Boolean 接缝和像素 AA；
- 孔洞的 rim/glow/shadow 语义；
- 非均匀缩放后的边缘宽度；
- Patch 合法率和结构候选成熟成本。

### 阶段 4：旋转、完整 8 层和产品切换

完成：

- 旋转 SDF 误差门禁；
- 运行时图层上限从 4 提升到 8；
- Model Author 和 typed layer patch；
- State、Artifact、API 和只读 UI 切换；
- 必要的 LangGraph 命名和文档同步；
- 真实模型固定样例和匿名人工盲评。

通过自动、集成、E2E 和人工门禁前，F09 继续 `active/no-go`。

## 12. Go / No-Go

### 12.1 可以继续推进

- benchmark 证明现有表示是重要质量上限；
- uniform、源码长度、编译次数和缓存资源预算闭合；
- 多图层和 CSG 分别带来可归因的质量收益；
- `polar_arc` 兼容策略已经确认；
- 当前子集迁移 parity 通过；
- Model patch 合法率、时延和 draw 成本落在冻结预算内；
- 自动指标和独立人工偏好均通过。

### 12.2 应停止或缩小范围

- 主要动机只是修复 rim、弧形高光等现有模板内问题；
- benchmark 无法证明多对象/CSG 的实际需求；
- 8 层只能依赖超出 WebGL1 最低保证的 uniform；
- 必须同时引入 CMA-ES、动态感知和多 program cache 才能跑通；
- 新表示使现有单主体质量明显回退；
- 需要任意 JSON Patch、任意 GLSL 或通用 DAG 才能表达目标；
- 没有 durable 自动证据和独立人工门禁。

## 13. 审核重点

审核时只需要重点确认以下六项：

| 审核项 | 本草案建议 | 原因 |
| --- | --- | --- |
| 对外数据结构 | Layer 列表 + 层内 CSG 树 | 比任意 DAG 更容易校验、生成 Patch 和审核 |
| 颜色域 | V1 暂用 sRGB 编码域 | 保持与当前 PNG 指标可比；线性光合成另行升级契约 |
| 图层上限 | Compiler 验证 8 层，产品先灰度 4 层再开放 8 层 | 分离能力上限和发布风险 |
| `polar_arc` | 作为兼容 effect 暂时保留 | 避免现有弧形高光能力无证据回退 |
| 功能归属 | 方案与 shadow 阶段继续归入 F09 | 避免同时出现两个 `active` 功能；产品切换前再正式审核状态迁移 |
| 坐标长度 | V1 继续按画布短边归一化 | 先保证与当前模板迁移可比；按高度归一化需独立升级契约 |

在这六项确认前，本草案不进入实现，也不改变当前 `scene_mvp` 产品路径。
