# Agent

`src/agent/` 是 ShaderGen 的 LangGraph 智能体模块。它当前不独立启动 FastAPI 服务，而是作为 Python 包被后端通过公共 service 调用。

本 README 是 Agent 模块的 harness 入口：只保留接手路径、当前状态判断方式、验证门禁和交接要求。模块说明、目录边界、State/Node/Graph 细则放在对应 `ARCHITECTURE.md`。

## 当前状态

- 当前 active 功能以 `docs/FEATURES.md` 为准；同一时间最多只能有一个 `active`。
- 当前进度和下一步以 `PROGRESS.md` 为准；不要凭历史对话判断做到哪了。
- 当前 Agent 不持有数据库连接池，不独立暴露 HTTP API。
- 当前 LangGraph 配置在 `langgraph.json`，注册 `agent`、`shader_generation` 和 `png_to_shader_v1` 三个图。
- 当前后端公共调用入口是 legacy 的 `agent.app.services.shader_generation` 和 V1 的 `agent.app.services.png_to_shader_v1`。
- 当前 Shader Memory 使用任务内 Checkpointer、项目 Store 和纯 GSSC Context Builder；数据库连接由 Backend 生命周期注入。

## 开始前

1. 先读仓库入口规则：`AGENTS.md`。
2. 确认当前任务状态：`docs/FEATURES.md` 和 `PROGRESS.md`。
3. 确认全局边界：`docs/ARCHITECTURE.md`。
4. 确认 Agent 总览：`src/agent/ARCHITECTURE.md`。
5. 只读本次会改目录旁边的 `ARCHITECTURE.md`，例如 `graphs/ARCHITECTURE.md` 或 `nodes/ARCHITECTURE.md`。

## 当前入口

- LangGraph 配置：`langgraph.json`
- 基础对话图：`src/agent/app/graphs/main_graph.py`
- Shader 生成/评审图：`src/agent/app/graphs/shader_generation_graph.py`
- PNG-to-Shader V1 有界图：`src/agent/app/graphs/png_to_shader_v1_graph.py`
- 后端公共 service：`agent.app.services.shader_generation`、`agent.app.services.png_to_shader_v1`
- Prompt 文件：`src/agent/app/prompts/*.yaml`
- 模型输出解析：`agent.app.parsers`
- Memory：`agent.app.memory`
- Context Engineering：`agent.app.context`

## Agent 改动门禁

- 文档、功能状态或架构边界变化：`make docs-check`
- Agent 节点、状态、解析器、LLM Gateway 或 service 变化：`uv run pytest tests/unit_tests`
- Graph 配置或边变化：`uv run langgraph validate`
- 跨后端和 Agent 的行为变化：`uv run pytest tests/integration_tests`
- 收尾前完整验证：`make check`

## 按需阅读

- Agent 总览：`src/agent/ARCHITECTURE.md`
- App 总览：`src/agent/app/ARCHITECTURE.md`
- Graph 规则：`src/agent/app/graphs/ARCHITECTURE.md`
- State 规则：`src/agent/app/states/ARCHITECTURE.md`
- Node 规则：`src/agent/app/nodes/ARCHITECTURE.md`
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

- 会话结束前更新 `PROGRESS.md`，记录做了什么、验证结果和剩余缺口。
- 重要架构取舍写入 `docs/DECISIONS.md`。
- 只有验证命令通过后，才能在 `docs/FEATURES.md` 把功能标记为 `passing`。
- 如果没有自动化检查覆盖跨组件行为，在 `PROGRESS.md` 写明缺口和后续补测方式。
