# 当前决策

这里只保留仍会跨模块影响当前实现或开发方式的决定。完整 D001–D099 历史记录见 [归档](archive/2026-07/decisions/DECISIONS-through-D099.md)。

| 决策 | 状态 | 当前约束 |
|---|---|---|
| D017 | accepted | 模型访问统一经过 LLM Gateway 中立边界。 |
| D035 | accepted | Backend Route 保持薄层，编排进入 Service，资源必须有界清理。 |
| D068 | accepted | Memory/checkpoint 与 PostgreSQL 数据休眠保留，不接入当前产品链路。 |
| D073 | accepted | ShaderGraph fallback 候选只按 strict total-loss 改善提交。 |
| D082 | accepted | Node Lab 是独立可选工具，不属于产品链路。 |
| D089 | accepted | 前端只展示 Backend 可证明的阶段和实际 engine，不推测进度。 |
| D092 | accepted | direct GLSL 共用严格 Parser、ProgramSpec 安全校验和有界 repair。 |
| D095 | accepted | direct 与 ShaderGraph attempt 隔离，并保留 fresh fallback 和 kill switch。 |
| D097 | accepted | 无显式 policy 时使用无授权 `direct_default`。 |
| D098 | accepted | 单人未上线开发采用聚焦测试；全量检查和质量治理显式触发。 |
| D099 | accepted | 归档、历史分析和参考方案不得派生当前任务。 |
| D100 | accepted | 旧计划、分析、进度和完整决策统一归档，当前文档只保留有效事实。 |
| D101 | accepted | 默认只读取任务相关的最近文档；流程规则集中在根 `AGENTS.md`，不跨文档重复维护。 |

新决策从 D102 继续编号。只有长期影响架构、契约、数据、安全或开发方式的选择才新增；普通实现细节写在代码或最近的模块文档中。
