# Memory 架构

> 状态：休眠兼容模块。当前 Backend、Graph 和 Frontend 均不调用；只有恢复历史项目 Memory 的明确任务才读取本文。

- `memory/` 保存项目级长期记忆模型和 LangGraph Store 操作。
- namespace 固定为 `("shadergen", "v1", project_id, "memory")`。
- `MemoryItem` 不保存图片、完整 GLSL、Prompt、日志或 reasoning。
- Store 只依赖 LangGraph `BaseStore`，不创建数据库连接或调用模型。
- 旧 `kind="review"` 仅保持只读兼容，不提供新的 Review 写入口。
