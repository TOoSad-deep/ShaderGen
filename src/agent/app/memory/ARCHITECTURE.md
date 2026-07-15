# Memory 架构

`memory/` 保存 Shader Agent 的项目级长期记忆模型和 LangGraph Store 操作。

- namespace 固定为 `("shadergen", "v1", project_id, "memory")`。
- V1 自动闭环只把经过 Renderer、Oracle 和 current_best Selector 验证的策略写入稳定 key `strategy:{glsl_sha256}`；不再新增 Review Memory。
- `MemoryItem` 只保存摘要、来源 run、GLSL hash、iteration 和时间，不保存图片、完整 GLSL、Prompt、日志或 reasoning。
- `MemoryItem` 和 Context Builder 继续只读解析历史 `kind="review"` 记录，保证已有项目可恢复；Memory 公共 API 不再提供 Review 写入口。
- `store.py` 只依赖 LangGraph `BaseStore`，不创建数据库连接、不调用模型、不加载 Prompt。
- 同一 Strategy 使用 upsert；Strategy summary 确定性附加 changed domain、metric version 和 total loss，不把图片、完整 Shader 或模型 reasoning 写入 Memory。
- 清除 namespace 时从 offset 0 分页逐项删除。
