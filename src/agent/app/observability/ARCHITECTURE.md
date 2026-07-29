# Observability 架构

`src/agent/app/observability/` 是 Agent 侧日志策略、回调、追踪和指标入口。

## 当前文件

- `model_reasoning.py`：根据 Node 配置把 reasoning 的字符数和 SHA-256 摘要输出到 `agent.model` logger；普通终端日志不写 reasoning 原文。

LLM Gateway、结构化 Author 和 Direct runner 会在各自异常收敛边界输出安全分类
日志。日志只包含稳定事件名、模型引用、错误类型/错误码、是否可重试和阶段；
不得写异常消息、Prompt、模型输出、reasoning 或供应商原始响应。Backend 注入的
request/run/project/attempt/stage 上下文负责把这些日志关联到一次产品运行。

## 边界规则

- Gateway 负责提取 reasoning，Observability 只决定是否以及如何输出日志。
- Agent 可以产出结构化 `model_calls`、`events` 和 `logs` 摘要，但不持有后端数据库连接。
- 后端负责请求日志、错误日志、数据库连接和过程数据写入。
- 非预期异常的终端诊断保留类型链与仓库内栈位置，不保留异常消息、locals 或
  源码/输入内容。
- 后续 LangSmith、OpenTelemetry 或 callback 入口放在本目录，不散落到 Node。
- 日志不得包含 API key、base64 图片、reasoning 原文或完整供应商原始响应。
- 私有 attempt/structured-author graph 必须通过禁 tracing 的包装入口调用；
  Studio 注册入口还必须在启动时配置 `hide_inputs` / `hide_outputs`，且只返回
  safe summary。
