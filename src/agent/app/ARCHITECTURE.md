# Agent App 架构

`src/agent/app/` 是 Agent 的内部应用包，承载 LangGraph 图、状态、Node、Prompt、LLM Gateway、工具入口和对后端开放的 service。

## 调用流向

```text
backend/app/services
  -> agent.app.services
  -> agent.app.graphs（组合具体 Gateway）
  -> agent.app.nodes
  -> agent.app.context / memory / contracts / prompts / parsers / messages / observability

agent.app.llms
  -> 实现 agent.app.contracts.llm.LLMGateway
```

关键依赖方向：`graphs -> nodes -> agent.app.contracts <- agent.app.llms`。

## 目录职责

- `config/`：部署默认值、环境变量解释和 Node 配置。
- `contracts/`：跨 State、Node、Graph、适配器共享的中立契约。
- `graphs/`：LangGraph 图入口、条件边和依赖装配。
- `states/`：图 State 和 Runtime Context 类型。
- `nodes/`：只依赖 Gateway 抽象的主要 Node 工厂。
- `llms/`：LangChain Gateway、provider 配置、model-family 客户端和统一响应。
- `messages/`：两个以上 Node 复用的消息构造 helper。
- `memory/`：项目长期记忆结构和抽象 Store 操作。
- `context/`：不访问 Store 的纯 GSSC Context Builder。
- `prompts/`：Prompt YAML 和加载器。
- `parsers/`：模型输出纯解析器。
- `services/`：后端可调用的 Agent 用例入口。
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
