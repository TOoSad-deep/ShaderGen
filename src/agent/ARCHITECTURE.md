# Agent 架构

`src/agent/` 是 ShaderGen 的 LangGraph 智能体模块。当前不作为独立 FastAPI 服务运行，而是在 monorepo 内通过 Python 包被后端调用。

## 模块关系

```text
backend 产品 API
  -> agent.app.services.png_to_shader_v1
  -> agent.app.graphs
  -> agent.app.nodes
  -> agent.app.context / memory / contracts / prompts / parsers / messages / observability

backend 诊断 API（仅显式开启）/ CLI / benchmark / tests
  -> agent.app.services.node_lab
  -> agent.app.lab
  -> agent.app.nodes.png_to_shader_v1.integrations.node_lab（Provider + Executor binding）
     -> production Node factory / routing / Prompt / Parser

agent.app.graphs
  -> 装配 agent.app.llms.LangChainLLMGateway
agent.app.llms
  -> 实现 agent.app.contracts.llm.LLMGateway
```

Agent 只负责编排、LLM Gateway、Prompt 策略、运行时状态和公共用例服务。确定性 IR、DSL、Renderer、Oracle、Search、Store 等领域能力进入 `src/shaderforge/`。

产品生成 service 是 Backend 默认路径；Node Lab service 是独立的 transport-free 诊断 Harness，可被 CLI、benchmark 和测试直接调用，但 Backend 只有在 `SHADERGEN_NODE_LAB_ENABLED=true` 时才注册诊断 Route。它不替代产品 Graph，也不进入产品请求链路。

## LLM Gateway

- Node 通过 `agent.app.contracts.llm.LLMGateway` 调用模型，不 import 具体 LLM 实现。
- `agent.app.llms` 封装 provider、model family、LangChain 客户端、统一响应和错误。
- Graph Builder 把具体 Gateway 注入 Node；测试注入 Fake Gateway。
- `LLMResponse.model_ref` 是业务 State 和模型调用摘要的模型身份来源。

## ShaderForge 依赖边界

- 跨多个确定性能力的应用层组合优先使用 `shaderforge.public`；需要精确领域类型或聚焦依赖时，可以从有架构文档的 typed 子包公共根导入，例如 `shaderforge.analysis`、`contracts`、`evaluation`、`generation`、`rendering`、`store` 和 `validation`，不得依赖其私有实现文件。
- 本文中的 ShaderForge Store 只指 `LocalArtifactStore`、run 级 `RunArtifactStore` 及其 `ArtifactRef`。Agent Memory 使用 LangGraph `BaseStore`，Node Lab 使用 `NodeLabStore` 管理实验索引和访问语义，二者都不是 ShaderForge Store。
- 更完整的公共面与职责定义以 `src/shaderforge/ARCHITECTURE.md` 及对应 typed 子包的 `ARCHITECTURE.md` 为准。

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
- `src/agent/app/nodes/png_to_shader_v1/ARCHITECTURE.md`：V1 模型、确定性 Node 和 Node Lab Provider 子架构。
- `src/agent/app/lab/ARCHITECTURE.md`：transport-free Node Lab Harness 内核和安全边界。
- `src/agent/app/benchmarks/ARCHITECTURE.md`：离线 Agent benchmark、真实模型预算和证据边界。
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
