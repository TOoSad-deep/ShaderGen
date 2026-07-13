# Contracts 架构

`src/agent/app/contracts/` 保存跨 State、Node、Graph 和具体适配器共享的中立契约。

## 当前文件

- `llm.py`：定义 `LLMGateway`、`LLMCallOptions`、`LLMResponse`、token usage、thinking 类型和统一错误。

## 边界规则

- Contracts 不依赖 `llms`、`nodes`、`graphs`、`services` 或后端。
- Node 只通过 `LLMGateway` 使用模型能力。
- 共享运行类型先进入 Contracts，不放在具体 model-family 适配器中。
- 契约变化影响 Node、Graph 或 Service 时，必须同步测试和对应架构文档。
