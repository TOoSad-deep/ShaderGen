# 功能清单

状态值：`not_started`、`active`、`blocked`、`passing`。

规则：

- 同一时间最多只有一个功能可以是 `active`。
- 只有验证命令通过后，功能才能变为 `passing`。
- `passing` 行必须包含证据。
- `blocked` 行必须在 `evidence` 中写明阻塞原因和解除条件。
- `not_started` 行的验证列写实现该功能时必须创建并通过的目标命令。

| id | 行为 | 验证 | 状态 | 证据 |
|---|---|---|---|---|
| H01 | 新 agent 会话可以只通过仓库文件理解项目用途、命令、目标架构、决策、当前进度和下一个功能。 | `make check` | passing | 2026-07-22：`make check` 验证 454 个单元测试通过，docs-check、LangGraph validate（2 个 graph）和前端生产构建均成功；架构与进度已同步 `scene_mvp` v2 packed feature、WebGL1 uniform 边界、分档预算、复合 loss 及 CMA/真实模型质量缺口。 |
| H02 | Node Lab Harness 可以通过通用 Provider 隔离诊断生产 Node target、场景流水线、Renderer、HTTP transport、五个模型角色 fixture 和本地工作台，不复制生产节点语义。 | `make benchmark-node-lab-ai-off && make benchmark-node-lab-model && make test-node-lab-ui` | passing | 2026-07-16：三项门禁均已实际通过；模型角色只使用离线 fixture，工作台只连接假 API，未调用真实模型。H02 通过不改变 F09 的质量 gate。 |
| F01 | 用户可以为 Shader 任务提交 Idea、需求、参考设计和测试规划输入。 | `uv run pytest tests/unit_tests/test_shader_task_input_contract.py && npm --prefix frontend run build` | not_started | 来自架构 SVG 的用户输入层。 |
| F02 | Routing 和 Agent 分析可以从用户输入产出结构化 Intent IR。 | `uv run pytest tests/unit_tests/test_routing.py tests/unit_tests/test_intent_ir.py` | not_started | 来自架构 SVG 的核心处理层。 |
| F03 | DSL 节点图和 Renderer 可以产出 GLSL 以及可渲染画面。 | `uv run pytest tests/unit_tests/test_dsl_renderer.py && npm --prefix frontend run build` | not_started | 来自架构 SVG 的核心处理层。 |
| F04 | Oracle 可以用全局评分以及局部颜色、形状、边缘损失评价渲染结果。 | `uv run pytest tests/unit_tests/test_oracle.py` | not_started | 来自架构 SVG 的核心处理层。 |
| F05 | Search Engine 可以调优 Shader 参数，并记录 VLM/HITL 评审结果。 | `uv run pytest tests/integration_tests/test_search_review_store.py` | not_started | 来自架构 SVG 的核心处理层和数据评测层。 |
| F08 | 同一 project_id 的 V1 运行可以复用经过确定性验证的策略记忆，不同项目互不泄漏，并可清除 checkpoint 与长期记忆。 | `make test-memory-postgres && uv run pytest tests/integration_tests/test_png_to_shader_v1_graph.py tests/integration_tests/test_png_to_shader_v1_api.py && npm --prefix frontend run e2e:memory && make check` | passing | 2026-07-15：V1 Graph/Service 覆盖策略晋升、项目隔离与清除；隔离 PostgreSQL 资源重建验收、Memory 浏览器 E2E 和主干验证通过。旧 Review Memory 仅保留只读兼容、不再产生新记录；清除操作兼容删除旧 Graph 遗留的裸 project checkpoint。 |
| F09 | 用户上传 PNG 后，系统可以在 WebGL1 无贴图契约下执行有界的分析、生成、真实渲染、评分和修订闭环，并返回 current_best GLSL 与证据。 | `make check && uv run pytest -q tests/integration_tests && npm --prefix frontend run e2e:procedural-v1 && npm --prefix frontend run e2e:memory && make test-scene-mvp-ui && make benchmark-ai-off && make benchmark-gate BENCHMARK_OUTPUT=<run-dir> HUMAN_REVIEW=<review.json>` | active | 2026-07-23：显式 `scene_mvp` 已升级为 v3 Scene/template/metric，并补齐 run 冻结/独立实验身份、配置指纹、Patch 安全指纹、worst-tile signed residual、最近拒绝历史和 typed Patch 有界成熟；单候选最多 12 次现有 draw，只有 matured loss 严格改善才原子提交，重复/非法/Renderer 失败不能污染 best。Graph 拓扑和单 run 单 prepared program 不变，high/manual 合法路径分别为 65/197 步、注入 69/201。真实 Chromium 既有门禁验证三颜色场、六 feature、四槽及 baked/prepared 像素语义；固定 7 例 geometry-first 校准为 7/7 aggregate 改善、2/7 实质局部冲突，多尺度 tile no-regression guard A/B 的离线 replay 形式未通过预声明接入门禁（strict total-loss 下无 watch ROI 回退可保护、声明容差高误拒），acceptance live 单因素直接 A/B 在固定 7 例、当前确定性候选生成与 32+32 draw 搜索契约内把两例实质 ROI 回退归因到 geometry-first 字典序 acceptance（唯一实验变量，不外推为真实模型或其他搜索空间普遍结论）：strict total-loss 两项 aggregate 双优、6/7 严格更优且无实质 ROI 回退，机器 gate outcome 为 `strict_total_supported`（D064，生产 acceptance 未改；output run iteration 与 report schema 是独立版本轴，权威产物为 `20260723-v2`/schema `_v2`，旧 v1 与 v2-schema-v1 均标 superseded 并保留）。当前 `0.04/0.02 + high 640/9/9 + manual 1000/32/30` 仅是独立实验配置，manual 禁止进入冻结 gate；D065 已核实生产 acceptance 自始为 strict total-loss（Arm G 仅是诊断脚本语义，无生产切换对象，五处比较已收口到纯函数 `accepts_strict_total_loss`），真实模型固定 7 例、匿名人工质量门禁及 CMA-ES/2000 draw 尚未完成，既有正式 run 人工偏好仍为 30%（门槛 50%），因此 F09 继续 active、灰度 no-go。 |
