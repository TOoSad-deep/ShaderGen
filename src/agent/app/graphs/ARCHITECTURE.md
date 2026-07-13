# Graphs 架构

`src/agent/app/graphs/` 保存 LangGraph 图入口。Graph 负责节点注册、边连接、条件跳转和 `compile()`。

## 当前图

- `main_graph.py`：基础对话图，入口对象为 `graph`。
- `shader_generation_graph.py`：Shader 生成和渲染评审图，入口对象为 `shader_generation_graph`。

## Graph 规则

- Graph 只负责编排流程，不直接组装模型消息。
- Graph 是 LLM 组合根，通过 Builder 把具体 `LangChainLLMGateway` 注入 Node 工厂。
- 测试通过 `build_main_graph(gateway)` 和 `build_shader_generation_graph(gateway)` 注入 Fake Gateway，不 monkeypatch 具体客户端工厂。
- Node 不决定全局流程；条件跳转写在 graph 文件的私有函数中，例如 `_next_after_generate()`。
- 条件边变复杂、需要复用或需要独立测试时，再抽到 graph 附近的边逻辑模块。
- 每个对外运行的新图都必须注册到仓库根目录的 `langgraph.json`。
- 新增或修改图后运行 `uv run langgraph validate`。

## 当前 `shader_generation` 流程

```text
START
  -> prepare_context
  -> operation == generate -> generate_glsl -> END
  -> operation == review -> review_render -> promote_memory -> END
```

Graph Builder 接收 checkpointer 和 Store。`project_id` 由 Agent service 映射为 `configurable.thread_id`；Backend 注入 PostgreSQL 或内存 persistence。
