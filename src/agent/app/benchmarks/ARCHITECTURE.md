# Agent Benchmarks 架构

`src/agent/app/benchmarks/` 保存显式离线运行、可产生模型费用并输出冻结证据的 Agent benchmark。它不是 Backend-facing Service，也不会被在线 Graph 或 HTTP 路径导入。

## 当前模块

- `model_roles.py`：通过 Node Lab Application 执行 PNG-to-Shader V1 的五个生产模型角色；fixture 模式默认离线，real 模式要求 CLI、服务端环境和 Gateway 三重门禁，并执行 semantic/repair/token/wall/cost 全套硬预算。

## 边界规则

- 可以依赖 `agent.app.services.node_lab` 组合独立 Harness，但在线 service 不得反向依赖本包。
- 必须通过生产 `NodeProvider`、Node、Prompt 和 Parser 运行，不复制节点语义。
- 失败、中断、恢复、manifest、source fingerprint 和报告语义只增不改；不得覆盖 M5 发布证据。
- `__init__.py` 不聚合具体 runner，CLI 和测试从明确模块导入，避免普通 Agent 导入加载 benchmark 依赖。
