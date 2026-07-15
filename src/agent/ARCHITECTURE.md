# Agent 架构

`src/agent/` 是 ShaderGen 的 LangGraph 智能体模块。当前不作为独立 FastAPI 服务运行，而是在 monorepo 内通过 Python 包被后端调用。

## 模块关系

```text
backend
  -> agent.app.services
  -> agent.app.graphs
  -> agent.app.nodes
  -> agent.app.context / memory / contracts / prompts / parsers / messages / observability

agent.app.graphs
  -> 装配 agent.app.llms.LangChainLLMGateway
agent.app.llms
  -> 实现 agent.app.contracts.llm.LLMGateway
```

Agent 只负责编排、LLM Gateway、Prompt 策略、运行时状态和公共用例服务。确定性 IR、DSL、Renderer、Oracle、Search、Store 等领域能力进入 `src/shaderforge/`。

## LLM Gateway

- Node 通过 `agent.app.contracts.llm.LLMGateway` 调用模型，不 import 具体 LLM 实现。
- `agent.app.llms` 封装 provider、model family、LangChain 客户端、统一响应和错误。
- Graph Builder 把具体 Gateway 注入 Node；测试注入 Fake Gateway。
- `LLMResponse.model_ref` 是业务 State 和模型调用摘要的模型身份来源。

## 当前图

`langgraph.json` 当前只注册一个图：

- `png_to_shader_v1`：入口为 `src/agent/app/graphs/png_to_shader_v1_graph.py:png_to_shader_v1_graph`，用于 F09 的有界分析、生成、真实渲染、评分和修订闭环；M4 已通过 `agent.app.services.png_to_shader_v1` 接入 Backend/Frontend。

## 子模块规范

- `src/agent/app/ARCHITECTURE.md`：App 总体流向和跨模块约束。
- `src/agent/app/contracts/ARCHITECTURE.md`：Gateway 等中立契约。
- `src/agent/app/llms/ARCHITECTURE.md`：LLM Gateway 和客户端实现。
- `src/agent/app/messages/ARCHITECTURE.md`：复用消息构造 helper。
- `src/agent/app/memory/ARCHITECTURE.md`：项目长期记忆模型和 Store 操作。
- `src/agent/app/context/ARCHITECTURE.md`：GSSC Context Builder 和预算策略。
- `src/agent/app/config/ARCHITECTURE.md`：配置和环境变量边界。
- `src/agent/app/graphs/ARCHITECTURE.md`：图、边和装配规则。
- `src/agent/app/states/ARCHITECTURE.md`：State 和 Context 规则。
- `src/agent/app/nodes/ARCHITECTURE.md`：Node 工厂和依赖规则。
- `src/agent/app/prompts/ARCHITECTURE.md`：Prompt YAML 和加载规则。
- `src/agent/app/parsers/ARCHITECTURE.md`：模型输出解析规则。
- `src/agent/app/services/ARCHITECTURE.md`：对后端公开的用例入口。
- `src/agent/app/tools/ARCHITECTURE.md`：Agent 外部工具边界。
- `src/agent/app/observability/ARCHITECTURE.md`：日志、回调、追踪和指标。

## 全局边界

- 后端只调用 `agent.app.services.*`，不要 import Agent 内部图、Node、LLM Gateway、Prompt 或 LangChain 消息类型。
- Prompt 主体放在 `src/agent/app/prompts/*.yaml`。
- 不保留旧目录或旧导入兼容层；新增代码使用当前 `agent.app.*` 路径。
- 不创建 Agent 自己的 `api/`、`storage/`、`workers/`，除非未来独立部署或需要独立队列 worker。
- 每个新增图都必须在 `langgraph.json` 注册，并通过 `uv run langgraph validate`。
