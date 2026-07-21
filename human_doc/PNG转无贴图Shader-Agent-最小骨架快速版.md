# PNG 转无贴图 GLSL Agent — 最小骨架（快速版）

> 状态：快速贯通实施中，2026-07-21。当前已落地严格 scene、确定性感知、模板、真实 WebGL1 渲染、RGB MAE、12 节点/3 路由、Artifact/trace，以及显式 `scene_mvp` Backend/Frontend 入口；模型 Author、prepared program 与 CMA-ES 仍未实现。
>
> 目标：用 1–2 周验证一条可端到端运行的最小技术路径——一个注册进 `langgraph.json` 的 LangGraph Agent，输入单主体粉球类 PNG，自动产出无贴图 GLSL、真实 WebGL1 渲染图和 MAE 报告。
>
> 定位：`png_to_shader_min` 是 F09 下与现有 `png_to_shader_v1` 并行的技术验证图，不是已经完成的产品替换。现有 V1 Backend、Frontend、Memory、Node Lab、benchmark 与失败证据在独立产品切换里程碑通过前全部保留；最小图的 CLI 验收不能作为删除 V1 的依据。
>
> 长期方向：以《PNG转无贴图Shader-Agent-目标架构详细版.md》为 F09 后续算法与演进的权威目标；旧 `png-to-shader-v2-v5-plan` 只作历史参考，不构成本方案的前置阶段或冻结约束。

领域逻辑放在 `src/shaderforge/` 新模块；LLM Gateway、WebGL1 运行契约、Validator、Artifact Store 和现有图像处理能力优先复用。Renderer 与评分热路径只有在满足本方案的新接口和性能门禁后，才称为“复用”。

本文档砍的是算法与优化细节，不砍 Agent 骨架：Graph、Node、State、routing、模型调用、预算、Artifact 和测试全部保留。后续可按《目标架构详细版》逐块升级。

---

## 1. 范围与非目标

### 1.1 本版输入范围

只支持：单主体、纯色或近纯色背景、伪 3D 光泽球/椭球类图片。粉球案例是首个验收样例。

不支持：多主体、遮挡、透明背景、复杂纹理背景、文字、任意拓扑和通用图片复刻。范围外输入必须明确失败或返回未达标结果，不能伪装成功。

### 1.2 LLM 的最小职责

保留两个模型节点，二者只产生经过 schema 校验的 JSON，永不直接生成或修改 GLSL：

- `author_initial`：读取 PNG 和确定性测量，产出初始 scene JSON；
- `author_refine`：在一轮基础与特征优化仍未达标时，读取“原图、当前 best 渲染、当前 scene、MAE”，产出一个 typed patch。

所有模型调用复用 `LLMGateway`，由 Graph Builder 注入；测试使用 Fake Gateway，不 monkeypatch 具体供应商客户端。`llm_call_count` 统计每一次实际 Gateway 调用，包括结构化修复调用。

### 1.3 本版砍掉的能力

- 多假设分支、遮挡、多物体、alpha；
- 输入退化估计、置信度标定与先验；
- 足迹重叠块、邻居联合重拟合；
- LPIPS、SSIM、Delta E、区域硬约束和 spec-test gate；
- Visual Critic 角色、盲评 gate、ES 门禁与跨栈验证；
- 分辨率爬坡、沙箱、并行候选评估；
- Backend/Frontend 产品接入、Memory 和 Node Lab Provider 迁移。

最后一项是本版的明确非目标：最小图完成后，现有 `png_to_shader_v1` 仍是产品路径。若决定替换，必须另行完成第 12 节的产品切换里程碑。

### 1.4 必须保留的核心机制

1. scene JSON 是 Shader 结构与可优化参数的唯一可编辑真相源；Graph State 仍可保存预算、测量摘要、Artifact 引用和路由状态。
2. 先优化几何与底色，再逐个优化高光、阴影等特征；候选只有改善 MAE 才接受，否则回滚。
3. 特征参数从 `reference - render(current)` 的残差或 LLM 粗参数初始化，再由 CMA-ES 精调。
4. `current_best` 与工作候选分离。失败候选、无效 patch 或异常不得覆盖已有 best。

## 2. 先决性能门禁

当前 `PlaywrightWebGL1Renderer.render(fragment_source, width, height)` 每次都会静态校验、创建 WebGL context、编译链接、绘制并编码 PNG，只自动上传 `u_resolution` 和 `u_time`。它不能直接满足“固定程序 + 任意 uniform + 2000 次优化”的热路径。

在实现 CMA-ES 前，先扩展或新增兼容接口：

```python
prepared = await renderer.prepare(
    fragment_source,
    width,
    height,
    uniform_schema=uniform_schema,
)
result = await prepared.render_uniforms(uniform_values, capture_png=False)
```

要求：

- `prepare()` 只静态校验、编译和链接一次；
- `render_uniforms()` 只接受白名单内、类型与长度严格匹配的 `float`、`vec2`、`vec3` uniform；
- 优化热路径优先返回原始 RGB 像素或等价的无损数组，不做 PNG base64 编码；
- 只有接受候选、Refine 对比和最终结果需要保存 PNG；
- prepared renderer 只存在于 Node 依赖和运行时 registry，不进入 State；
- `close()` 幂等，正常路径由 `finalize` 关闭，Graph 外异常由 CLI/Service 的 `finally` 兜底；
- 现有 `render()` API 与 V1 行为保持兼容。

在粉球目标分辨率上运行固定模板 100 次 uniform 渲染性能探针：总耗时不超过 45 秒，P95 不超过 450 ms，且输出无陈旧帧。未通过该门禁时，不进入 2000 次 CMA-ES 实现；先缩小分辨率/预算或优化 Renderer，不能仅修改验收口径。

## 3. Graph 总览

Graph 共 12 个节点：9 个工作节点和 3 个显式决定节点；共有 3 个纯路由函数。特征间循环和外层 Refine 通过条件边表达；CMA-ES 的代数循环在优化节点内部执行，避免上千次 LangGraph 调度。

```text
START
  -> initialize_run
  -> perceive_target
  -> author_initial (LLM，失败时确定性兜底)
  -> materialize_shader
  -> render_and_evaluate
  -> decide_after_render
       |-- optimize_base -> decide_after_base
       |                      |-- optimize_feature -> decide_after_feature
       |                      |                         |-- optimize_feature
       |                      |                         |-- author_refine
       |                      |                         `-- finalize
       |                      |-- author_refine
       |                      `-- finalize
       `-- finalize

author_refine (LLM typed patch)
  -> materialize_shader
  -> render_and_evaluate
  -> decide_after_render

finalize -> END
```

关键语义：

- `render_and_evaluate` 只负责 initial 或 Refine patch 物化后的单次事实验证；固定流向 `decide_after_render`。
- `optimize_base` 和 `optimize_feature` 在节点内部完成多次 uniform 渲染、MAE 计算、预算记账和接受/回滚，完成后分别直接流向 `decide_after_base`、`decide_after_feature`。
- Refine 改变结构后回到 `materialize_shader`，重新生成和编译模板程序，再执行基础与特征优化。
- 每次真实 draw 都增加 `render_count`；每次 Gateway 调用都增加 `llm_call_count`。优化器必须按剩余 render budget 截断 population/代数。
- Graph recursion limit 默认 64，作为显式预算之外的第二道保护。

## 4. 项目结构

| 文件 | 内容 |
|---|---|
| `src/agent/app/graphs/png_to_shader_min_graph.py` | Builder 组合根、顶部 ASCII 图、编译产物 `png_to_shader_min_graph` |
| `src/agent/app/graphs/png_to_shader_min_routing.py` | 3 个纯路由函数 |
| `src/agent/app/states/agent_state.py` | 新增 `PngToShaderMinState`，并同步 State 架构文档 |
| `src/agent/app/nodes/png_to_shader_min/` | 9 个工作 Node 工厂和 3 个薄 decision Node |
| `src/agent/app/prompts/png_to_shader_min_author_initial.yaml` | Initial Author prompt |
| `src/agent/app/prompts/png_to_shader_min_author_refine.yaml` | Refine Author prompt |
| `src/agent/app/contracts/png_to_shader_min.py` | 两个模型角色的严格输出契约 |
| `src/agent/app/parsers/png_to_shader_min.py` | JSON 提取、schema 校验和安全错误 |
| `src/shaderforge/perception/min_perceive.py` | 分割、拟合、颜色采样和候选区域 |
| `src/shaderforge/generation/min_template.py` | scene JSON 到参数化 WebGL1 GLSL，以及最终字面量导出 |
| `src/shaderforge/scene.py` | scene/patch schema、白名单、应用 patch、确定性兜底 scene |
| `src/shaderforge/optimization/min_optimize.py` | CMA-ES ask/tell、残差初始化、预算、接受与回滚 |
| `src/shaderforge/evaluation/mae.py` | 参考图一次解码、RGB MAE 热路径 |
| `src/shaderforge/rendering/` | 增加 prepared program 与 typed uniform 接口，保持旧 API |
| `langgraph.json` | 在保留 `png_to_shader_v1` 的同时注册 `png_to_shader_min` |
| `scripts/run_png_to_shader_min.py` | 可信本地 CLI：输入 PNG，调用 Graph，输出 Artifact 路径 |
| `tests/unit_tests/`、`tests/integration_tests/` | schema、routing、Node fake、Renderer 性能契约和端到端测试 |

模板蓝本可以参考 `output/static_pink_glass_orb.glsl`，但运行时代码不得依赖被忽略的 `output/` 文件。模板必须迁入 `src/shaderforge/generation/` 并受测试和打包规则管理。

新增 package 或 Prompt 后必须同步 `pyproject.toml` 的显式 package/package-data 配置。新增 Graph 时必须同次更新源码 ASCII 图、`src/agent/app/graphs/ARCHITECTURE.md` Mermaid、路由表和 `langgraph.json`。

## 5. State 与 Artifact 边界

```python
class PngToShaderMinState(TypedDict, total=False):
    # 轻量路由/checkpoint 字段
    project_id: str
    phase: str                  # initial / base / feature / refine
    status: str                 # running / done / failed
    stop_reason: str
    render_count: int
    render_budget: int          # 默认上限 2000
    llm_call_count: int
    llm_budget: int             # 默认上限 6，包含结构修复调用
    refine_count: int
    refine_budget: int          # 默认上限 5
    target_mae: float           # 默认 0.05
    current_best_mae: float
    feature_queue: tuple[str, ...]

    # 当前调用的 UntrackedValue/Artifact 引用
    run_id: str
    input_image: bytes
    input_image_ref: str
    measurements: dict
    scene: dict                 # 工作 scene，唯一可编辑 Shader 表示
    current_best: dict          # 已接受 scene、MAE 和 Artifact refs
    pending_patch: dict | None
    current_glsl: str
    current_render_ref: str
    final_manifest_ref: str
    error: str | None
```

约束：

- `input_image`、完整 GLSL、渲染像素、测量大对象和完整 `current_best` 使用 `UntrackedValue`；checkpoint 只保存轻量路由摘要。
- State 不保存任意 `run_dir`、Renderer、Gateway、优化器实例或 Store 实例。
- CLI 接收的图片路径只在可信边界读取，进入 Graph 后使用 bytes 和 `LocalArtifactStore` 引用。
- Artifact Store 继续按 `project_id/run_id` 隔离。CLI 未传 `project_id` 时生成本地 UUID，而不是接受任意输出目录穿透。
- `scene` 是工作候选，`current_best` 是不可被失败候选覆盖的已接受快照。Refine patch 开始前保留 baseline；完整优化轮次无改善时回滚到 baseline。

## 6. 路由表

`decide_after_*` 是图上的纯 decision Node：只根据 State 写入受限 `next_action`/`stop_reason`，不执行渲染、模型调用或文件 IO。条件边调用对应纯路由函数读取动作。

| 决定节点 / 路由函数 | 结果 | 下一节点 | 条件 |
|---|---|---|---|
| `decide_after_render` / `route_after_render` | `optimize_base` | `optimize_base` | 编译和渲染成功、MAE 未达标、render budget 剩余 |
| 同上 | `finalize` | `finalize` | 编译/渲染失败、已达标或 render budget 耗尽 |
| `decide_after_base` / `route_after_base` | `optimize_feature` | `optimize_feature` | 未达标、feature queue 非空、render budget 剩余 |
| 同上 | `author_refine` | `author_refine` | 特征已耗尽、未达标、LLM 与 refine budget 均有剩余 |
| 同上 | `finalize` | `finalize` | 已达标、render budget 耗尽或无 Refine 预算 |
| `decide_after_feature` / `route_after_feature` | `optimize_feature` | `optimize_feature` | 未达标、仍有特征、render budget 剩余 |
| 同上 | `author_refine` | `author_refine` | 特征已耗尽、未达标、LLM 与 refine budget 均有剩余 |
| 同上 | `finalize` | `finalize` | 已达标、render budget 耗尽或无 Refine 预算 |

路由判断使用固定优先级：取消/明确失败 → 已达标 → render budget → feature queue → LLM/refine budget → 下一动作。路由函数遇到未知动作或缺失关键不变量时抛出错误，不能静默 finalize。

## 7. LLM 契约

### 7.1 `author_initial`

- 输入：PNG 原图、分割统计、圆/椭圆拟合、颜色网格采样、亮区/阴影候选区域；
- 输出：初始 scene JSON，只能选择白名单图元、颜色模型和特征；
- 第一次 schema 校验失败时，只有剩余 LLM budget 足够才允许一次结构化修复；
- 修复仍失败或预算不足时，使用确定性兜底 scene，流程继续；
- 每次实际模型请求分别计入 `llm_call_count`。

### 7.2 `author_refine`

- 输入：原图、当前 best PNG、当前 best scene、当前 MAE 和残差摘要；
- 输出：恰好一个 typed patch：`add_feature`、`remove_feature` 或 `swap_model`；
- patch 只能引用白名单模型/特征，必须通过 schema 和 scene 不变量校验；
- 无效 patch 直接回滚并消耗本次模型调用，不额外执行无限修复；
- 合法 patch 先作用于工作 scene，不能直接覆盖 `current_best`；完成基础与特征优化后只有 MAE 改善才整体接受，否则回滚；
- `refine_count` 统计 Refine 轮次，`llm_call_count` 统计实际请求，两项预算同时生效。

默认总 LLM budget 为 6，而不是“1 次 initial + 5 次 refine 再额外修复”。Initial 的结构修复也占这 6 次，因此真实可用 Refine 次数由剩余总预算决定。

## 8. Scene、模板与 GLSL 来源

scene JSON 是严格、版本化、拒绝未知字段的 Pydantic 契约。首版仅支持：

- primitive：`circle`、`ellipse`；
- color field：`solid`、`radial`；
- feature：`polar_arc`、`shadow`、`rim`、`edge_line`；
- patch op：`add_feature`、`remove_feature`、`swap_model`。

示例：

```json
{
  "schema_version": "png_to_shader_min_scene_v1",
  "canvas": {"w": 505, "h": 527, "bg": [1, 1, 1]},
  "object": {
    "primitive": {"type": "circle", "center": [0.0, 0.055], "r": 0.823},
    "color_field": {
      "model": "radial",
      "origin": [-0.35, 0.95],
      "stops": [[0.97, 0.19, 0.39], [1.0, 0.55, 0.68], [1.0, 0.91, 0.94]],
      "ramp": [0.36, 1.15]
    },
    "features": [
      {"id": "arc_main", "type": "polar_arc", "r0": 0.905, "sigma": 0.03,
       "phi": [1.72, 2.56], "intensity": 1.15},
      {"id": "shadow", "type": "shadow", "center": [0.10, -0.86],
       "axes": [0.58, 0.14], "color": [0.05, 0.20, 0.14]}
    ]
  }
}
```

内部 GLSL 的唯一来源是 `min_template`。模板生成符合现有 canonical WebGL1 契约的 `void main()` 版本，保留兼容声明但禁止任何纹理采样。优化参数通过 typed uniform 上传。

`finalize` 生成两个明确区分的文件：

- `final/webgl1.glsl`：由同一 Renderer 实际复验的交付真相源；
- `final/shadertoy.glsl`：从同一 scene 确定性导出的 `mainImage` 适配版。

不得把未经对应运行栈验证的 Shadertoy 导出版称为 WebGL1 验证产物。

## 9. 感知、MAE 与优化

### 9.1 `perceive_target`

只测量，不做模型选型：

- 背景众数色和阈值分割；
- 单主体连通域检查；
- 圆/椭圆代数拟合；
- 颜色网格和代表点采样；
- 亮区、阴影候选 mask 与 bbox；
- 输入是否超出本版支持范围的确定性诊断。

### 9.2 MAE 热路径

不能在 CMA-ES 内环直接调用完整 `evaluate_render()`，因为它还会重复执行边缘、几何、ROI 和候选图测量。

新增轻量 MAE API：参考图在 run 开始时解码一次为规范化 sRGB RGB 数组；每次候选只计算同尺寸 RGB 全局 MAE。接受候选或最终输出时才编码 PNG 和写完整 Artifact。像素尺寸不一致必须 fail closed。

### 9.3 `optimize_base`

- 优化几何、背景和底色约 10 维参数；
- CMA-ES 使用显式 ask/tell，以便异步等待 Renderer；
- 默认约 40 代、约 400 次 draw，但必须被剩余 render budget 截断；
- 每次 draw 立即记账；达到 `target_mae` 时提前停止；
- 只在 MAE 严格改善时接受，否则保留进入节点前的 scene/best。

### 9.4 `optimize_feature`

- 每次从 queue 取一个 feature；
- 使用 LLM 粗参数或 `reference - current render` 残差初始化；
- 优化 5–8 维参数，默认约 20 代、约 200 次 draw；
- 连续 5 代无改善时提前结束该特征；
- 改善则提交 scene，否则回滚该 feature；两种结果都从 queue 消费该 feature，避免死循环。

`pycma` 和 SciPy 当前均未进入锁文件。先用 `uv run --with` 验证最小依赖；若只需 `cma` 的 ask/tell，不引入未被使用的 SciPy。稳定后同步 `pyproject.toml`、`uv.lock` 和许可证/打包检查。

## 10. Node 职责

| 节点 | 职责 |
|---|---|
| `initialize_run` | 校验可信输入、注册 project/run Artifact、写原图、初始化预算和状态 |
| `perceive_target` | 执行确定性测量与范围诊断，不做选型 |
| `author_initial` | 调用 LLM 生成 scene；结构修复失败时使用确定性兜底 |
| `materialize_shader` | scene → 参数化 GLSL、uniform schema/value；执行 Validator |
| `render_and_evaluate` | 编译或复用 prepared program，单次渲染并计算 MAE，保存必要证据 |
| `optimize_base` | 节点内完成基础 CMA-ES、draw 记账、接受或回滚 |
| `optimize_feature` | 节点内完成一个 feature 的 CMA-ES、早停、接受或回滚 |
| `author_refine` | 生成并验证一个 typed patch，建立可回滚工作候选 |
| `finalize` | 从 best scene 生成双版本 GLSL、同栈复验 WebGL1、写 PNG/MAE/manifest、关闭 Renderer |
| `decide_after_render` | 纯状态决策，不做 IO |
| `decide_after_base` | 纯状态决策，不做 IO |
| `decide_after_feature` | 纯状态决策，不做 IO |

Node 不决定自己的下一跳；所有条件分支由 Graph routing 控制。Node 只捕获契约中定义的可恢复业务失败。未知编程错误和不变量破坏必须抛出，由外层 `finally` 清理资源。

## 11. Definition of Done

### 11.1 离线和真实渲染验证

- scene/patch schema、模板、MAE、预算和三个 routing 函数单测通过；
- Fake Gateway + Fake Renderer 下完整 Graph 和全部条件分支通过；
- 真实 Renderer + Fake Gateway 的粉球集成测试通过，且不调用真实模型；
- prepared renderer 100 次性能探针满足第 2 节门禁；
- Renderer 编译失败、不可用、预算耗尽和 LLM 无效输出均有明确终态；已有 best 时失败不得覆盖，没有 best 时写失败 manifest 而不是伪造 Shader；
- Renderer 在正常、可恢复失败和 Graph 外异常路径均关闭。

### 11.2 真实模型验收

真实模型运行必须使用显式开关，不进入普通测试：

- 粉球 PNG 全自动得到 `final/webgl1.glsl`、对应 PNG 和 MAE 报告；
- 全局 sRGB MAE ≤ 0.05；
- 端到端 ≤ 15 分钟；
- 实际 Gateway 调用 ≤ 6；
- 实际 draw ≤ 2000；
- 另选 2–3 张同范围图片，流程不崩溃且 MAE ≤ 0.08。

### 11.3 仓库门禁

- `langgraph.json` 同时注册现有 V1 和 `png_to_shader_min`；
- Graph 源码 ASCII、Graphs Mermaid 和路由表与实现一致；
- `make docs-check` 通过；
- `uv run langgraph validate` 发现并验证两个 Graph；
- 新增定向单元和集成测试通过；
- `make check` 通过；
- 未运行真实模型时在验证记录中明确说明。

这些验收只证明最小技术路径成立，不证明现有产品已经切换，也不改变旧 M5 人工盲评失败证据。

## 12. 里程碑

### M0：契约与 Renderer 性能探针

- 冻结 scene/patch、State、uniform schema、MAE 和预算语义；
- 实现 prepared renderer 最小接口；
- 通过 100 次 uniform 渲染性能门禁；
- 未通过则先调整技术路径，不进入 CMA-ES。

### M1：模板、兜底 scene 与真实渲染

- 把粉球模板迁入源码包；
- 手写 scene → 参数化 GLSL → uniform 渲染；
- Validator、MAE、Artifact 和关闭路径通过。

### M2：确定性感知

- `perceive_target` 对粉球输出圆/椭圆拟合、颜色采样和候选区域；
- 范围外输入有明确诊断。

### M3：Initial Author

- LLM 节点、严格 schema、预算内一次结构修复和确定性兜底；
- Fake Gateway 离线覆盖成功、修复和兜底。

### M4：基础优化

- `optimize_base` 使用 prepared renderer 和轻量 MAE；
- 达标、改善、无改善、预算截断均可验证。

### M5：特征优化

- `optimize_feature` 实现 queue、残差初始化、早停和逐特征回滚；
- feature 循环由 Graph 条件边控制。

### M6：Refine 外环与 CLI 验收

- `author_refine` typed patch、baseline、整轮接受/回滚；
- 12 节点/3 路由 Graph、文档三件套和 `langgraph.json` 同步；
- CLI、离线 Graph、真实 Renderer 和显式真实模型验收完成。

### M7：可选的产品切换，独立实施与验收

只有用户明确决定以最小图替换现有产品路径时才启动：

1. 新建稳定的 Agent Service 和公开结果契约；
2. Backend 接入新 Graph，迁移错误映射、运行账本和 Artifact 白名单；
3. Frontend 接入 scene/MAE/产物响应；
4. 决定 Memory、Node Lab 和 benchmark 的迁移或下线策略；
5. 通过 Backend integration、浏览器 E2E、生命周期和主干门禁；
6. 更新 `docs/FEATURES.md`、架构文档和决策记录；
7. 最后才移除旧 V1 Graph、Node、Prompt、Service、测试和注册。

旧 benchmark 原件和失败证据只增不改，不能因实现下线而删除。

## 13. 升级到详细版的映射

| 最小骨架 | 升级方向 |
|---|---|
| `author_initial` 单次 scene 建模 | 多假设分支和残差裁决 |
| `perceive_target` 只测不选 | 全模型集、插值空间竞争、置信度标定 |
| `author_refine` 单 patch | 完整 typed patch 动作空间和 refit-before-accept |
| 全局 sRGB MAE | MAE/Delta E、分区域、硬约束和盲评 |
| 单 feature 优化 | 足迹重叠块和邻居联合重拟合 |
| 单 WebGL1 同栈验证 | 两层一致性和 ES 门禁 |
| 串行节点内 CMA-ES | 优化器接口抽象、批量或可微代理 |
| CLI 技术验证 | 独立 M7 产品切换 |
