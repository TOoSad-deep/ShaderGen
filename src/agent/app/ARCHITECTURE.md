# Agent App 架构

`src/agent/app/` 是 Agent 的内部应用包，承载 LangGraph 图、状态、Node、Prompt、LLM Gateway、工具入口和对后端开放的 service。

## 调用流向

```text
backend/app/services（产品路径）
  -> agent.app.services.png_to_shader_v1
  -> agent.app.graphs（组合具体 Gateway）
  -> agent.app.nodes
  -> agent.app.context / memory / contracts / prompts / parsers / messages / observability

agent.app.llms
  -> 实现 agent.app.contracts.llm.LLMGateway

agent.app.graphs / deterministic nodes / Node Lab provider capabilities
  -> shaderforge.public（跨能力聚合入口）
  -> shaderforge.analysis / contracts / evaluation / generation / rendering / store / validation（typed 子包公共根）

backend 诊断 Route（仅显式开启）/ CLI / benchmark / tests
  -> agent.app.services.node_lab
  -> nodelab（通用 Provider 协议、Fixture、不可变步骤与 Artifact Harness）
  -> agent.app.nodes.png_to_shader_v1.integrations.node_lab 公共 Provider
     -> production Node factory / routing / Prompt / Parser

离线模型角色 benchmark CLI
  -> agent.app.benchmarks.model_roles
  -> agent.app.services.node_lab + 同一生产 Provider
```

关键依赖方向：`graphs -> nodes -> agent.app.contracts <- agent.app.llms`。

## 目录职责

- `config/`：部署默认值、环境变量解释和 Node 配置。
- `benchmarks/`：只供显式 CLI/测试调用的离线 Agent benchmark；保存真实模型硬预算、恢复与报告编排，不进入在线产品路径。
- `contracts/`：跨 State、Node、Graph、适配器共享的中立契约。
- `graphs/`：LangGraph 图入口、条件边和依赖装配。
- `states/`：图 State 和 Runtime Context 类型。
- `nodes/`：按 Pipeline/版本保存生产 Node。当前 `nodes/png_to_shader_v1/` 统一容纳 `model/`、`deterministic/` 和 `integrations/node_lab/`；模型角色只依赖 Gateway 抽象，确定性 Node 可依赖 ShaderForge 公共能力，新 Node 在同一功能命名空间的 Provider 登记，不修改 Harness。
- `llms/`：LangChain Gateway、provider 配置、model-family 客户端和统一响应。
- `messages/`：两个以上 Node 复用的消息构造 helper。
- `memory/`：项目长期记忆结构和抽象 Store 操作。
- `context/`：不访问 Store 的纯 GSSC Context Builder。
- `prompts/`：Prompt YAML 和加载器。
- `parsers/`：模型输出纯解析器。
- `services/`：M4 的产品 V1 service 映射 Graph 输入/输出、隔离 checkpoint thread，并提供固定 Artifact 名称访问，不暴露 Graph 或文件路径；`node_lab.py` 只保留 V1 CLI/benchmark 显式装配 Provider/Gateway 的插件 helper，不维护节点目录或 dispatch，也不进入产品请求链路。独立 HTTP transport 位于 `nodelab_service`，产品 Backend 不注册其 Route。
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
- 跨多个 ShaderForge 能力的组合优先使用 `shaderforge.public`；需要精确类型或聚焦依赖时，只从 `shaderforge.analysis`、`contracts`、`evaluation`、`generation`、`rendering`、`store`、`validation` 等 typed 子包公共根导入，不越过其 `__init__.py` 依赖私有实现文件。
- `ShaderForge Store` 在本层专指 `LocalArtifactStore` 和 run 级 `RunArtifactStore`；它不包含 Memory 使用的 LangGraph `BaseStore`，也不包含管理 LabRun/步骤/Artifact 索引和访问策略的 `NodeLabStore`。
