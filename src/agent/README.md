# Agent

`src/agent/` 是 ShaderGen 的 LangGraph 智能体模块。它当前不独立启动 FastAPI 服务，而是作为 Python 包被后端调用：产品生成走 `agent.app.services.png_to_shader_v1`，Node Lab 则走独立的 transport-free 诊断 Harness service，不属于产品请求链路。

本 README 是 Agent 模块的 harness 入口：只保留接手路径、当前状态判断方式、验证门禁和交接要求。模块说明、目录边界、State/Node/Graph 细则放在对应 `ARCHITECTURE.md`。

## 当前状态

- 当前 active 功能以 `docs/FEATURES.md` 为准；同一时间最多只能有一个 `active`。
- 当前进度和下一步以 `PROGRESS.md` 为准；不要凭历史对话或 `docs/progress/archive/` 判断做到哪了。
- 当前 Agent 不持有数据库连接池，不独立暴露 HTTP API。
- 当前 LangGraph 配置在 `langgraph.json`，只注册 `png_to_shader_v1` 一个图。
- 当前产品调用入口是 `agent.app.services.png_to_shader_v1`。
- 当前诊断 Harness 入口是 `agent.app.services.node_lab`；Backend 默认不注册 `/api/lab/v1/*`，只有使用 `make dev-node-lab` 或在进程启动前显式设置 `SHADERGEN_NODE_LAB_ENABLED=true` 才开放诊断 HTTP/Swagger。
- 当前 Shader Memory 使用任务内 Checkpointer、项目 Store 和纯 GSSC Context Builder；数据库连接由 Backend 生命周期注入。

## 开始前

1. 先读仓库入口规则：`AGENTS.md`。
2. 确认当前任务状态：`docs/FEATURES.md` 和 `PROGRESS.md`。
3. 确认全局边界：`docs/ARCHITECTURE.md`。
4. 确认 Agent 总览：`src/agent/ARCHITECTURE.md`。
5. 只读本次会改目录旁边的 `ARCHITECTURE.md`，例如 `graphs/ARCHITECTURE.md` 或 `nodes/ARCHITECTURE.md`；涉及 Graph、routing 或节点跳转语义时，必须先读 `src/agent/app/graphs/ARCHITECTURE.md`，并把可视化同步纳入同一次改动。

## 当前入口

- LangGraph 配置：`langgraph.json`
- PNG-to-Shader V1 有界图：`src/agent/app/graphs/png_to_shader_v1_graph.py`
- 产品 service：`agent.app.services.png_to_shader_v1`
- 诊断 Harness service：`agent.app.services.node_lab`
- Prompt 文件：`src/agent/app/prompts/*.yaml`
- 模型输出解析：`agent.app.parsers`
- Memory：`agent.app.memory`
- Context Engineering：`agent.app.context`

## Agent 改动门禁

- 文档、功能状态或架构边界变化：`make docs-check`
- Agent 节点、状态、解析器、LLM Gateway 或 service 变化：`uv run pytest tests/unit_tests`
- Graph 配置、节点、边、路由结果或终止语义变化：先同步源码 ASCII 图、Graphs Mermaid 和路由表，再运行 `make docs-check` 与 `uv run langgraph validate`
- 跨后端和 Agent 的行为变化：`uv run pytest tests/integration_tests`
- 收尾前默认主干验证：`make check`；跨组件改动仍需追加对应集成、E2E、PostgreSQL 或 benchmark 检查。

## Graph 改动入口

Graph 可视化的触发条件、ASCII/Mermaid/路由表同步清单、自动检查边界和完成定义统一以 `src/agent/app/graphs/ARCHITECTURE.md` 的“可视化维护工作流”为准；本 README 只负责导航，不重复维护第二份清单。

## 按需阅读

- Agent 总览：`src/agent/ARCHITECTURE.md`
- App 总览：`src/agent/app/ARCHITECTURE.md`
- Config 规则：`src/agent/app/config/ARCHITECTURE.md`
- Graph 规则：`src/agent/app/graphs/ARCHITECTURE.md`
- State 规则：`src/agent/app/states/ARCHITECTURE.md`
- Node 规则：`src/agent/app/nodes/ARCHITECTURE.md`
- PNG-to-Shader V1 Node 子架构：`src/agent/app/nodes/png_to_shader_v1/ARCHITECTURE.md`
- Node Lab Harness 规则：`src/nodelab/ARCHITECTURE.md`
- 离线 Agent benchmark 规则：`src/agent/app/benchmarks/ARCHITECTURE.md`
- Contracts 规则：`src/agent/app/contracts/ARCHITECTURE.md`
- LLM Gateway 规则：`src/agent/app/llms/ARCHITECTURE.md`
- Message helper 规则：`src/agent/app/messages/ARCHITECTURE.md`
- Memory 规则：`src/agent/app/memory/ARCHITECTURE.md`
- Context 规则：`src/agent/app/context/ARCHITECTURE.md`
- Prompt 规则：`src/agent/app/prompts/ARCHITECTURE.md`
- Parser 规则：`src/agent/app/parsers/ARCHITECTURE.md`
- Service 规则：`src/agent/app/services/ARCHITECTURE.md`
- Tool 规则：`src/agent/app/tools/ARCHITECTURE.md`
- Observability 规则：`src/agent/app/observability/ARCHITECTURE.md`

## 完成交接

- 会话结束前原地更新 `PROGRESS.md` 的当前状态、下一步、验证基线和剩余缺口；只有状态、契约、门禁、里程碑或重要缺口变化时才新增最近变更，例行重复验证不得追加会话日志。
- 重要架构取舍写入 `docs/DECISIONS.md`。
- 只有验证命令通过后，才能在 `docs/FEATURES.md` 把功能标记为 `passing`。
- 如果没有自动化检查覆盖跨组件行为，在 `PROGRESS.md` 写明缺口和后续补测方式。
