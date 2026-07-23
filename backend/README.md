# Backend

Backend 负责 FastAPI HTTP 边界、应用生命周期、过程账本和 `scene_mvp` 用例编排。

## 当前契约

- `POST /api/shader/generate` 接收图片、可选 `project_id/run_id`、`quality_preset` 和 `instruction`；不再接收 `generation_mode`。
- `GET /api/shader/runs/{run_id}/progress` 增量返回白名单节点事件。
- `GET /api/shader/runs/{run_id}/progress/render` 返回运行中最近渲染帧。
- `GET /api/shader/runs/{run_id}/artifacts/{artifact_name}` 只允许 `final-render`、`metrics`、`manifest`。
- 旧 V1 Artifact fallback 和 `DELETE /projects/{project_id}/memory` 已删除。

## 分层

- Route 位于 `app/api/routes/`，只处理 HTTP 边界。
- `app/services/shader_generation.py` 负责锁、进度、scene_mvp 调用、总账和公开响应。
- `app/services/agent_process_store.py` 原子写入 `agent_runs/events/logs`。
- `app/main.py` 只启动 asyncpg 过程账本与 scene_mvp Service。
- `app/database/agent_memory.py` 及 Memory SQL 保留为休眠基础设施，不再进入当前 lifespan。

进度事件不包含图片、Scene、GLSL、模型原始响应或 reasoning。运行中 PNG 使用独立字节端点；终态仍以数据库过程账本和 Artifact 为准。

## 验证

```bash
uv run pytest tests/unit_tests
uv run pytest tests/integration_tests/test_shader_run_progress_api.py
make check
```
