# 2026-07 历史归档

> 归档状态：本目录只用于追溯，不是当前事实、任务来源、实施计划、验收门禁或默认阅读材料。

## 内容

- `decisions/`：D001–D099 完整历史决策。
- `progress/`：早期进度快照和阶段总结。
- `plans/`：已完成、未采用或被替代的实施计划。
- `specs/`：旧功能设计、实验协议和实施报告。
- `analysis/`：单次 run 与历史 suite 分析。
- `evidence/`：历史 evidence/durable/promotion 治理说明；实际 registry JSON 仅为休眠兼容数据。
- `human-doc/`：早期目标架构、方法论和最初 SVG 参考。
- `summaries/`：历史周报。

## 使用边界

- 默认开发不得读取本目录来生成“下一步”、TODO、测试矩阵或质量治理任务。
- 只有追溯旧行为、解释遗留兼容或用户明确要求恢复历史方案时才按需读取精确文件。
- 归档中的路径、命令、状态、模型、测试数量和架构描述允许过期，不要求与当前代码同步。
- 当前入口固定为仓库根 `README.md`、`AGENTS.md`、`PROGRESS.md`、`docs/ARCHITECTURE.md`、`docs/FEATURES.md` 和 `docs/DECISIONS.md`。
