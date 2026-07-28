# Backend

Backend 负责 FastAPI HTTP 边界、parent/child attempt 编排、进度、过程账本和生命周期。Node Lab transport 独立位于 `src/nodelab/http/`，产品 Backend 不注册 `/api/lab/v1/*`。

## API 契约

- `POST /api/shader/generate` 接收图片、可选 `project_id/run_id`、`quality_preset` 和 `instruction`；不接受客户端 engine 选择。
- `GET /api/shader/runs/{run_id}/progress` 返回白名单进度事件。
- `GET /api/shader/runs/{run_id}/progress/render` 返回最近运行中 Render。
- `GET /api/shader/runs/{run_id}/artifacts/{artifact_name}` 只允许 `final-render`、`metrics`、`manifest`。
- Generate 响应通过 `engine`、`representation`、`engine_run` 报告实际 child attempt。

direct 成功返回 `scene=null` 和 `renderer_path=direct_program_spec_v1`；ShaderGraph fallback 成功返回 `shader_graph_v1` Scene 和 `renderer_path=compiled_graph_program_cache_v1`。兼容 reader 可以读取缺少 engine discriminator 的旧响应，新代码不得根据 `scene` 猜测 engine。

## Engine 编排

- 未配置 `SHADERGEN_ENGINE_POLICY_PATH` 时使用无授权 `direct_default`。
- `SHADERGEN_DIRECT_GLSL_KILL_SWITCH=1` 使新请求直接使用 ShaderGraph。
- direct 失败创建 fresh、隔离的 ShaderGraph child；同一 attempt 不切换表示。
- 每个 child 使用独立 Renderer、cache、预算和私有 Store；只有选中 child 的三个白名单 Artifact 发布到 parent run。
- 其他 policy 模式和上线治理当前休眠，普通 Backend 改动不读取 evidence registry。

## 代码边界

- `app/api/routes/`：HTTP 薄层。
- `app/services/shader_generation.py`：请求、进度、账本和公开响应。
- `app/services/engine_rollout*.py`：parent plan、direct/fallback 和 child 生命周期。
- `app/services/agent_process_store.py`：过程账本。
- `app/core/engine_policy.py`：服务端 policy schema 和默认值。
- `app/main.py`：生命周期和依赖组合。

进度和日志不得包含图片、完整 GLSL、ShaderGraph、ProgramSpec、Prompt、模型原始响应或 reasoning。

## 配置与验证

服务端配置以根目录 `.env.example` 和 `app/core/` 中的 settings 实现为准。测试按根 `AGENTS.md` 选择：普通改动运行相关 Backend 测试，跨 Backend/Agent/Frontend 行为再补一条 happy path。
