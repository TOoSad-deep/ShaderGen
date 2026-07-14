# Services 架构

`src/agent/app/services/` 是后端调用 Agent 的公共边界。后端只能通过这一层使用 Agent 能力。

## 当前服务

- `shader_generation.py`：暴露图片生成 GLSL 和渲染评审两个用例。
- `png_to_shader_v1.py`：暴露服务端自动 render/evaluate/review/refine 用例、V1 Memory 清理和固定 Artifact 白名单读取。
- `errors.py`：两个公共用例共享的安全 persistence 异常，避免 service 之间反向导入。

当前公共入口：

- `generate_glsl_from_image()`：原图生成 GLSL。
- `review_shader_render()`：基于原图、当前渲染图和 GLSL 输出评估与修改建议。
- `create_shader_generation_service()`：把 Backend 创建的 checkpointer/store 注入图。
- `clear_project_memory()`：删除项目 checkpoint thread 和 Store Memory。
- `generate_png_to_shader_v1()`：按质量档位执行 M3 Graph，拒绝没有通过硬门禁的终止结果。
- `create_png_to_shader_v1_service()`：把 Backend saver/store 与 LocalArtifactStore 注入独立 V1 Graph。
- `PngToShaderV1Service.read_public_artifact()`：只解析 `final-render`、`metrics`、`manifest`，不接收文件路径。

## Service 规则

- Service 接收简单 Python 参数，不暴露 LangChain 消息类型给后端。
- Service 调用 Graph，不绕过 Graph 直接调用节点或模型。
- Service 把图输出映射为稳定 dataclass。
- V1 成功 dataclass 显式区分已评分 `current_best` 与 `unscored_fallback`；后者仍有 GLSL/render/candidate id，但 `score=None`，且只附加 candidate id 匹配最终结果的 Review。
- Service 可以 re-export 稳定 Parser 函数，但不要 import `nodes/` 中的内部 helper。
- Agent 不直接持有数据库连接池；过程数据通过 service 结果返回给后端统一落库。
- legacy Service 为每次调用设置 `project_id == thread_id`；V1 使用 `png-to-shader-v1:{project_id}` 隔离 checkpoint，同时继续用原 project_id 读取共享 Store Memory。两者都返回 `durable`、`ephemeral` 或 `degraded` memory status。
- 后端只依赖 service 的公共函数和结果类型。
