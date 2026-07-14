# Observability 架构

`src/agent/app/observability/` 是 Agent 侧日志策略、回调、追踪和指标入口。

## 当前文件

- `model_reasoning.py`：根据 Node 配置把 reasoning 的字符数和 SHA-256 摘要输出到 `agent.model` logger；普通终端日志不写 reasoning 原文。

## 边界规则

- Gateway 负责提取 reasoning，Observability 只决定是否以及如何输出日志。
- Agent 可以产出结构化 `model_calls`、`events` 和 `logs` 摘要，但不持有后端数据库连接。
- 后端负责请求日志、错误日志、数据库连接和过程数据写入。
- 后续 LangSmith、OpenTelemetry 或 callback 入口放在本目录，不散落到 Node。
- 日志不得包含 API key、base64 图片、reasoning 原文或完整供应商原始响应。
