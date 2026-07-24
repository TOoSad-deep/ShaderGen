# ShaderGen 架构

当前实现以 `scene_mvp` 最小骨架为唯一在线路径。历史 V1、Node Lab 和 V2-V5 方案不再是当前架构事实；其审计记录保留在 `docs/DECISIONS.md`、进度归档和 evidence registry。

## 分层

```text
Frontend React
  -> Backend FastAPI
  -> backend.app.services.shader_generation
  -> agent.app.services.png_to_shader_min
  -> png_to_shader_min LangGraph
  -> ShaderGraph Author / typed layer patch
  -> ShaderForge DSL / Compiler / bounded program cache / metric / optimizer
  -> Artifact
```

- `frontend/`：上传、配置、运行进度、服务端/客户端 Render 和 GLSL 展示。
- `backend/`：HTTP 边界、进度注册、过程账本、生命周期和用例编排。
- `src/agent/`：LangGraph、LLM Gateway、Prompt、Parser、State 和公共 Service。
- `src/shaderforge/`：确定性 Scene、Shader 物化、WebGL1 渲染、复合评分、优化和 Artifact。

Backend 只能通过 `agent.app.services.*` 调用 Agent。Agent 不持有数据库连接池；ShaderForge 不依赖 FastAPI、LangChain 或 React。

## scene_mvp Graph

`png_to_shader_min_graph.py` 继续用 12 个节点完成输入登记、确定性感知、严格 ShaderGraph Author、specialized Compiler、真实渲染、复合评分、canvas/node/layer 参数优化、可选 typed layer Refine 和 final Artifact。DSL node 是领域数据，不映射为 LangGraph node。

- 模型 ShaderDocument 与由感知 MinScene 转换的 fallback 都必须真实编译、渲染后择优。
- Refine 只能从只读 `current_best.document` 派生一个绑定 `base_document_sha256` 的 typed layer patch。
- 候选只有在 `min_scene_composite_v3` 严格改善时才能提交。
- `current_best` 是绑定文档、Compiler、program key、Render、metric、父 hash 与 provenance 的不可变 CandidateSnapshot。
- 同一 run 通过 `compiled_graph_program_cache_v1` 隔离 topology，并在单个 active block 内复用 packed uniform program；cache、compile 和优化 block 都有硬上限。
- `finalize` 直接固化权威 `shader-graph.json`、specialized WebGL1 GLSL 和 Render，不再为默认产品重复执行 MinScene shadow。
- Graph recursion limit 按合法最坏路径推导；未知递归错误 fail-closed。
- 完整拓扑、条件边和安全边界见 `src/agent/app/graphs/ARCHITECTURE.md`。

## HTTP 与进度

`POST /api/shader/generate` 不再包含模式分流，固定执行 `scene_mvp`。客户端可预生成 `run_id`，在 POST 阻塞期间轮询：

```text
GET /api/shader/runs/{run_id}/progress?after=<seq>
GET /api/shader/runs/{run_id}/progress/render
GET /api/shader/runs/{run_id}/artifacts/{final-render|metrics|manifest}
```

`RunProgressRegistry` 是单进程内存状态，重启即失；事件 JSON 不包含图片、ShaderGraph、GLSL、Patch value、用户输入、模型原始响应或 reasoning。终态审计以数据库过程账本和 Artifact 为准。完整 ShaderGraph 只出现在终态 manifest/API 的 `scene` 字段和私有 run 内 `shader-graph.json`。

## Persistence 边界

Backend 当前启动 asyncpg 过程账本连接池，写入 `agent_runs`、`agent_events` 和 `agent_logs`。终态事件、日志和状态使用单事务提交。

旧 LangGraph Memory/checkpoint 的 Python/SQL 实现与已有 PostgreSQL 数据暂时保留，但：

- Backend lifespan 不再打开 saver/store；
- 当前 Graph/Service 不读写旧策略 Memory；
- 前端和 HTTP 不再提供项目 Memory/清除入口。

后续是否迁移、只读归档或按保留期删除，需要单独的数据策略决策。

## Artifact 与安全

- 公开 Artifact 只允许 final render、metrics 和 manifest。
- 图片、完整 GLSL、编译器原文和模型原始响应不得进入普通日志。
- 密钥只在服务端 `.env` 或部署 Secret 中；任何 `VITE_*` 都是公开前端配置。
- 历史失败 benchmark、run 和人工证据不得覆盖或删除；当前代码不再提供旧 V1 benchmark 入口。

## 同步规则

Graph 节点、边、路由、循环、终止路径、`current_best` 边界或 `langgraph.json` 变化时，同一次改动必须更新源码 ASCII、Graph Mermaid、路由表和安全说明，并通过 `make docs-check` 与 `uv run langgraph validate`。
