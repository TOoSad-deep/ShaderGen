# Agent App 架构

`src/agent/app/` 是 Agent 的内部应用包，承载 LangGraph 图、状态、Node、Prompt、LLM Gateway、工具入口和对后端开放的 service。

## 调用流向

```text
backend/app/services
  -> agent.app.services.shader_generation / png_to_shader_v1
  -> agent.app.graphs（组合具体 Gateway）
  -> agent.app.nodes
  -> agent.app.context / memory / contracts / prompts / parsers / messages / observability

agent.app.llms
  -> 实现 agent.app.contracts.llm.LLMGateway

agent.app.graphs / deterministic nodes
  -> 编排 shaderforge.public 的测量、校验、渲染、评分、选择和 Artifact 能力

agent.app.services.node_lab
  -> agent.app.lab（通用 Provider 协议、Fixture、不可变步骤与 Artifact Harness）
  -> agent.app.nodes.integrations.node_lab 公共 Provider
     -> production Node factory / routing / Prompt / Parser
```

关键依赖方向：`graphs -> nodes -> agent.app.contracts <- agent.app.llms`。

## 目录职责

- `config/`：部署默认值、环境变量解释和 Node 配置。
- `contracts/`：跨 State、Node、Graph、适配器共享的中立契约。
- `graphs/`：LangGraph 图入口、条件边和依赖装配。
- `states/`：图 State 和 Runtime Context 类型。
- `nodes/`：主要 Node 工厂；模型角色只依赖 Gateway 抽象，M3 确定性 Node 可依赖 ShaderForge 公共能力。`nodes/integrations/node_lab/` 是 Node 侧的公共 Provider/Adapter，新 Node 在此登记，不修改 Harness。
- `llms/`：LangChain Gateway、provider 配置、model-family 客户端和统一响应。
- `lab/`：Node Lab transport-free Harness；保存通用 `NodeProvider`/Executor 协议、动态 Registry、严格 Schema、Fixture、LabRun/步骤/Artifact 证据、AI-off benchmark、scenario binding、cold/warm Renderer 生命周期和中断恢复，不直接注册 FastAPI，也不导入任何具体 Node/Graph。
- `messages/`：两个以上 Node 复用的消息构造 helper。
- `memory/`：项目长期记忆结构和抽象 Store 操作。
- `context/`：不访问 Store 的纯 GSSC Context Builder。
- `prompts/`：Prompt YAML 和加载器。
- `parsers/`：模型输出纯解析器。
- `services/`：后端可调用的 Agent 用例入口；M4 的 V1 service 映射 Graph 输入/输出、隔离 checkpoint thread，并提供固定 Artifact 名称访问，不暴露 Graph 或文件路径。Node Lab service 只选择并注入公共 Provider/Gateway，不维护节点目录或 dispatch。
- `tools/`：Agent 可调用的外部动作能力。
- `observability/`：reasoning 日志策略、回调、追踪和指标入口。

## 跨模块约束

- 后端不得越过 `services/` 直接依赖 `graphs/`、`nodes/`、`contracts/`、`llms/`、`messages/` 或 `prompts/`。
- `services/` 把后端输入映射为图 State，把图输出映射为稳定结果类型。
- `graphs/` 负责编排和具体 Gateway 装配，不组装模型消息。
- Node 不得直接依赖 `agent.app.llms`；Node 只使用 `agent.app.contracts.llm.LLMGateway`。
- `llms/` 实现 Gateway 契约，不依赖业务 State、Node、Graph 或 Service。
- `prompts/` 只保存 Prompt 主体和加载逻辑。
- `parsers/` 只保存纯解析逻辑，不调用模型或决定流程。
- `states/` 不保存长期数据、数据库连接、对象存储客户端或 Gateway 实例。
- `memory/` 只依赖 `BaseStore` 接口；Backend 创建/关闭具体 saver、store 和 psycopg pool。
