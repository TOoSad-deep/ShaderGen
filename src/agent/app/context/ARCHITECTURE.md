# Context 架构

`context/` 是纯 Context Engineering 模块，实现 Gather、Select、Structure、Compress。

- 输入是普通 State 字典、已校验 `MemoryItem` 和 `ContextPolicy`。
- 输出是固定 schema 的 `ContextPack`，不访问 Store、不调用模型、不加载 Prompt。
- 历史数据以 JSON 数据块进入 HumanMessage，并明确标记为非指令。
- 默认历史预算 2,000 token、候选 50 条、历史 Review 3 条；参数集中在 `ContextPolicy`。
- 当前 GLSL hash 对应 Review 优先于历史 Review；超预算内容确定性丢弃，不调用 LLM 摘要。
