# Services 架构

当前产品只通过 `png_to_shader_min.py` 调用 `scene_mvp` Graph。

- Service 组合 Graph、`LLMGateway`、`LocalArtifactStore` 和 run 级 Renderer registry。
- `generate_png_to_shader_min()` 接收简单 Python 参数并返回稳定 dataclass，不向 Backend 暴露 LangChain 类型。
- `read_public_artifact()` 只接受 `final-render`、`metrics`、`manifest` 白名单名。
- Graph 正常终止由 `finalize` 关闭 Renderer，Service `finally` 对越过 Graph 的异常执行幂等兜底。
- 默认组合根执行 ShaderGraph 产品 Node，并通过同一 `MinRendererRegistry` 持有 run-scoped 有界 program cache；Prepared handle 不进入 State。显式 legacy Builder 才可注入非权威 `ShaderGraphShadowRunner`。
- Backend 只依赖本包公共接口；Agent Service 不持有数据库连接池。
- 旧 V1 Memory/checkpoint 基础设施未删除，但不再由产品 Service 打开或调用。
