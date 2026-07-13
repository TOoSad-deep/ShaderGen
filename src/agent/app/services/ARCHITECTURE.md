# Services 架构

`src/agent/app/services/` 是后端调用 Agent 的公共边界。后端只能通过这一层使用 Agent 能力。

## 当前服务

- `shader_generation.py`：暴露图片生成 GLSL 和渲染评审两个用例。

当前公共入口：

- `generate_glsl_from_image()`：原图生成 GLSL。
- `review_shader_render()`：基于原图、当前渲染图和 GLSL 输出评估与修改建议。
- `create_shader_generation_service()`：把 Backend 创建的 checkpointer/store 注入图。
- `clear_project_memory()`：删除项目 checkpoint thread 和 Store Memory。

## Service 规则

- Service 接收简单 Python 参数，不暴露 LangChain 消息类型给后端。
- Service 调用 Graph，不绕过 Graph 直接调用节点或模型。
- Service 把图输出映射为稳定 dataclass。
- Service 可以 re-export 稳定 Parser 函数，但不要 import `nodes/` 中的内部 helper。
- Agent 不直接持有数据库连接池；过程数据通过 service 结果返回给后端统一落库。
- Service 为每次调用设置 `project_id == thread_id`，并返回 `durable`、`ephemeral` 或 `degraded` memory status。
- 后端只依赖 service 的公共函数和结果类型。
