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
5. 只读本次会改目录旁边的 `ARCHITECTURE.md`，例如 `graphs/ARCHITECTURE.md` 或 `nodes/ARCHITECTURE.md`；涉及 Graph、routing 或节点跳转语义时，必须先读 `src/agent/app/graphs/ARCHITECTURE.md`，并把可视化同步纳入同一次改动。

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
- Graph 配置、节点、边、路由结果或终止语义变化：先同步源码 ASCII 图、Graphs Mermaid 和路由表，再运行 `make docs-check` 与 `uv run langgraph validate`
- 跨后端和 Agent 的行为变化：`uv run pytest tests/integration_tests`
- 收尾前完整验证：`make check`

## Graph 可视化完成定义

以下任一变化都会触发 Graph 可视化维护：`add_node`、`add_edge`、`add_conditional_edges`、routing 返回值或含义、循环/重试、START/END/finalize 路径、`current_best`/fallback 边界，以及 `langgraph.json` 的图注册。

完成 Graph 相关开发前必须逐项满足：

1. 对应 `*_graph.py` 的 Builder 上方 ASCII 图已同步，读源码即可看出主路径、分支、循环和终止点。
2. `src/agent/app/graphs/ARCHITECTURE.md` 中同名 `graph-diagram:<stem>` Mermaid 区块已同步；新增图必须新增完整区块。
3. 条件结果、下一节点或安全语义变化时，同步条件路由表与 `current_best`、fallback、Memory 晋升等说明。
4. 新增对外图时同步 `langgraph.json` 和“当前图”清单。
5. `make docs-check`、`uv run langgraph validate`、对应 routing/Graph 定向测试通过；收尾仍以 `make check` 为准。

`make docs-check` 会从 `*_graph.py` 静态提取字面量节点、直接边和条件边，发现缺失的源码图、Mermaid 区块或连线。动态生成的 path map、routing 函数内部语义、隐式终止及路由表文字无法完全由静态检查判断，仍必须由开发者同步并用定向测试验证。

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
