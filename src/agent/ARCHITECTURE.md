# Agent 架构

```text
Backend
  -> agent.app.services.layerplan_glsl_direct
     -> direct Authors
     -> shaderforge.program_spec / rendering / evaluation
  -> direct 失败时
     -> agent.app.services.png_to_shader_min
     -> png_to_shader_min Graph
     -> ShaderGraph Nodes
     -> shaderforge typed 子包
```

Agent 负责 LLM Gateway、Prompt、解析、State、Graph 和公共 Service。Backend 只调用 `agent.app.services.*`；Node 不直接依赖具体模型供应商；Graph Builder 是运行资源组合根。

当前唯一注册 Graph 是 `png_to_shader_min`，拓扑和路由见 `app/graphs/ARCHITECTURE.md`。Direct engine 不注册 LangGraph。

只在任务涉及对应目录时读取其 `ARCHITECTURE.md`。`memory/`、`context/` 和历史质量 Harness 当前休眠，不属于默认开发上下文。
