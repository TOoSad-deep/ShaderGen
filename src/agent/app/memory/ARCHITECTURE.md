# Memory 架构

`memory/` 保存 Shader Agent 的项目级长期记忆模型和 LangGraph Store 操作。

- namespace 固定为 `("shadergen", "v1", project_id, "memory")`。
- 首期只有 Review 自动晋升，稳定 key 为 `review:{glsl_sha256}`。
- `MemoryItem` 只保存摘要、来源 run、GLSL hash、iteration 和时间，不保存图片、完整 GLSL、Prompt、日志或 reasoning。
- `store.py` 只依赖 LangGraph `BaseStore`，不创建数据库连接、不调用模型、不加载 Prompt。
- 同一 Review 使用 upsert；清除 namespace 时从 offset 0 分页逐项删除。
