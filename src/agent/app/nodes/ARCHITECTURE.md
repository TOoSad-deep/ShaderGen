# Nodes 架构

`src/agent/app/nodes/` 只保存主要 LangGraph Node 工厂。一个 Node 执行一个明确任务，并返回 partial State。

## 当前 Node

- `model_node.py`：`make_model_node(gateway)` 创建基础对话 Node，并把 Runtime Context 映射为 `LLMCallOptions`。
- `generate_glsl_node.py`：`make_generate_glsl_node(gateway, config)` 根据原图生成 GLSL。
- `review_render_node.py`：`make_review_render_node(gateway, config)` 根据原图、渲染图和 GLSL 生成评审结果。
- `prepare_context_node.py`：从 Runtime Store 读取候选 Memory 并调用纯 GSSC Builder。
- `promote_memory_node.py`：把结构化 Review 幂等晋升为项目长期 Memory。

## Node 规则

- Node 通过构造参数接收 `agent.app.contracts.llm.LLMGateway`。
- Node 不得直接依赖 `agent.app.llms`、provider 配置或 model-family 实现。
- Node 负责 Prompt 选择、LangChain 消息组装、Parser 调用、可观测性策略和 partial State 映射。
- Gateway 负责客户端创建、模型调用、耗时、reasoning 提取、usage 和真实模型身份。
- State 和 `model_calls` 中的模型名只使用 `LLMResponse.model_ref`。
- Node 不决定全局流程，不持有数据库连接，不在原地修改 State。
- 两个以上 Node 复用的消息 helper 放入 `app/messages/`；reasoning 日志策略放入 `app/observability/`。
- 不新增把分析、生成、测试和优化混在一起的 `mega_agent_node`。

## 与其他模块的边界

- LLM 抽象从 `app/contracts/` 获取。
- Prompt 主体从 `app/prompts/` 加载。
- 纯输出解析由 `app/parsers/` 完成。
- 图流转和具体 Gateway 装配由 `app/graphs/` 决定。
- Backend 负责 persistence 生命周期；Node 只通过 Runtime Store 抽象读取/写入，不持有连接池。
