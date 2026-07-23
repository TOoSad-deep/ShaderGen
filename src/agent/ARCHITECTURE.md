# Agent 架构

当前数据流：

```text
Backend
  -> agent.app.services.png_to_shader_min
  -> agent.app.graphs.png_to_shader_min_graph
  -> agent.app.nodes.png_to_shader_min
  -> shaderforge typed 子包
```

Agent 负责 LangGraph 编排、LLM Gateway、Prompt、严格解析、状态和公共用例 Service。渲染、评分、Scene、优化和 Artifact 存储属于 ShaderForge。Backend 只通过 `agent.app.services.*` 调用 Agent。

Node 依赖 `agent.app.contracts.llm.LLMGateway`，不得直接依赖 `agent.app.llms` 实现。Graph Builder 是模型和运行资源的组合根。图片、GLSL、Render、Scene 与 trace 等大对象使用 `UntrackedValue`。

当前 Graph：`png_to_shader_min`，对应 `src/agent/app/graphs/ARCHITECTURE.md` 的拓扑与路由表。

子模块规范：

- `src/agent/app/config/ARCHITECTURE.md`
- `src/agent/app/context/ARCHITECTURE.md`
- `src/agent/app/contracts/ARCHITECTURE.md`
- `src/agent/app/graphs/ARCHITECTURE.md`
- `src/agent/app/llms/ARCHITECTURE.md`
- `src/agent/app/memory/ARCHITECTURE.md`
- `src/agent/app/messages/ARCHITECTURE.md`
- `src/agent/app/nodes/ARCHITECTURE.md`
- `src/agent/app/observability/ARCHITECTURE.md`
- `src/agent/app/parsers/ARCHITECTURE.md`
- `src/agent/app/prompts/ARCHITECTURE.md`
- `src/agent/app/services/ARCHITECTURE.md`
- `src/agent/app/states/ARCHITECTURE.md`
- `src/agent/app/tools/ARCHITECTURE.md`

Memory/context 包暂时保留以支持后续保留策略决策，但当前产品 Graph 和 Backend lifespan 不调用它们。
