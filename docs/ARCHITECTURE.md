# ShaderGen 当前架构

本文只描述默认产品链路和稳定边界。休眠实现仍可能存在于代码中，但不构成当前任务或完成条件。

## 产品链路

```text
Frontend React
  -> Backend parent run
  -> direct_default（无 policy 文件时的默认值）
     -> direct child
        -> VisualAnalysis Author -> advisory LayerPlan
        -> Initial/Refine direct GLSL Author
        -> canonical ShaderProgramSpecV1
        -> 静态校验 -> WebGL1 prepare/draw -> metric
     -> direct 失败时 fresh ShaderGraph fallback child
        -> png_to_shader_min LangGraph
  -> 选中 child 的 final-render / metrics / manifest
```

`SHADERGEN_DIRECT_GLSL_KILL_SWITCH=1` 可让新请求直接使用 ShaderGraph。客户端、HTTP 参数、instruction 和任何 `VITE_*` 都不能选择 engine。

## 组件边界

- `frontend/`：上传、进度、实际 engine/fallback、Render、GLSL 和失败信息。
- `backend/`：HTTP、parent/child attempt 编排、进度、过程账本和生命周期。
- `src/agent/`：LLM Gateway、direct Author/Runner、ShaderGraph fallback、Prompt、Parser 和 State。
- `src/shaderforge/`：ProgramSpec、ShaderGraph、静态校验、WebGL1 渲染、评分、优化和 Artifact。
- `src/nodelab/`：独立可选 Node 调试工具，不进入产品 Backend。

Backend 只能通过 `agent.app.services.*` 调用 Agent。Agent 不持有数据库连接池；ShaderForge 不依赖 FastAPI、LangChain 或 React。

## Direct GLSL

Direct engine 使用三个有界 Author：

1. VisualAnalysis 读取参考图和 instruction，生成 advisory `LayerPlanV1`。
2. Initial 生成 `ShaderProgramSpecV1`。
3. Refine 根据参考图、当前 Render、metric 和可信 incumbent 生成新候选。

LayerPlan 不参与安全校验、评分或接受判断。模型只返回语义字段；`shaderforge.program_spec` 负责 canonical 类型、身份绑定、规范化和哈希。候选必须通过 WebGL1 静态规则并真实 prepare/draw，只有 Renderer receipt 绑定的像素和 metric 可以更新 attempt-local `current_best`。

Direct 成功时：

- `engine=direct_glsl_layerplan_v1`
- `representation=shader_program_spec_v1`
- `min_pipeline.scene=null`
- `renderer_path=direct_program_spec_v1`

完整 LayerPlan、ProgramSpec、Prompt、Render bytes 和原始错误只保存在私有 attempt 边界。

## ShaderGraph fallback

`langgraph.json` 只注册 `png_to_shader_min`。模型或感知产生的 ShaderDocument 必须经 specialized Compiler、真实 WebGL1 渲染和复合评分；候选只有 strict total-loss 改善时才能提交。

Fallback 成功时：

- `engine=shader_graph_v1`
- `representation=shader_document_v1`
- `min_pipeline.scene` 为 `shader_graph_v1`
- `renderer_path=compiled_graph_program_cache_v1`

完整拓扑和路由见 `src/agent/app/graphs/ARCHITECTURE.md`。

## Parent run 与公开 Artifact

每个 HTTP `run_id` 是 parent run。每个 child attempt 使用独立 Renderer、cache、预算和私有 Store；direct 失败只能创建新的 fallback attempt，不能在同一 attempt 内切换表示。

公开 Artifact 仅包括：

```text
GET /api/shader/runs/{run_id}/artifacts/final-render
GET /api/shader/runs/{run_id}/artifacts/metrics
GET /api/shader/runs/{run_id}/artifacts/manifest
```

API 通过 `engine`、`representation`、`engine_run` 报告实际结果。`POST /api/shader/generate` 当前阻塞执行；`RunProgressRegistry` 是进程内状态，重启即失。

## 有界等待

`src/shaderforge/config/runtime_timeouts.yaml` 是模型、Renderer、engine attempt 和前端等待的唯一默认配置源：

- 模型单次 HTTP 默认 3600 秒，Renderer prepare/draw 默认 300/120 秒。
- 每个 direct、fresh fallback 或 production-shadow attempt 默认 7200 秒。
- 前端 fast/balanced/high/manual POST 默认 5/6/8/12 小时，进度 GET 60 秒，POST 后观察 2 小时。

Python 与 Vite 都严格校验正有限数、未知字段和内外层覆盖关系。更长等待不提供服务端取消，也不允许无限 timeout。

## 安全与休眠能力

- 图片、完整 GLSL、ProgramSpec、Prompt 和模型原始响应不得进入普通日志。
- 密钥只在服务端 `.env` 或部署 Secret；`VITE_*` 只能保存公开配置。
- Memory/checkpoint、PostgreSQL 旧数据、shadow/A-B、promotion、canary 和 evidence 相关实现当前休眠。
- 休眠能力只在用户明确发起对应任务时读取其最近模块文档；历史设计从 `docs/archive/` 精确追溯。

Graph 拓扑变化的同步和验证规则以根 `AGENTS.md` 为唯一流程来源。
