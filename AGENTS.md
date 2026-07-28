# AGENTS.md

## 当前目标

ShaderGen 当前处于单人、未上线的快速迭代阶段。默认任务是完成用户明确提出的需求，确认修改范围内代码正确，并贯通相关产品链路。

## 默认工作方式

- 一次只处理 `docs/FEATURES.md` 中一个 `active` 功能，不从历史材料或缺口列表自动派生任务。
- 普通改动运行直接相关的聚焦测试；缺陷修复覆盖对应回归路径；跨组件改动再补一条代表性集成/E2E happy path。
- `make check`、全量集成/浏览器测试仅用于里程碑、准备合并或发布、公共基础设施变化，或用户明确要求。
- benchmark、A/B、shadow、盲评、evidence、promotion、canary 等质量实验或上线治理，只有用户明确发起方案比较或上线准备时才运行或扩建。
- 如果验证或治理工作量预计超过需求实现工作量，先说明收益与成本并取得确认。

## 阅读边界

- 默认只读取本文件、`PROGRESS.md`，以及本次修改目录最近的 README/`ARCHITECTURE.md`；不要遍历全部模块文档。
- 架构、功能状态或长期取舍确有需要时，再按需读取 `docs/ARCHITECTURE.md`、`docs/FEATURES.md`、`docs/DECISIONS.md`。
- `docs/archive/` 只用于用户要求的精确追溯，不能从其中的计划、门禁、命令或“下一步”派生当前任务。
- `docs/evidence/registry.json` 只在用户明确发起质量实验或上线准备时读取。

## 实现边界

- Backend 只能通过 `agent.app.services.*` 调用 Agent；Prompt 只放在 `src/agent/app/prompts/*.yaml`；确定性领域能力进入 `src/shaderforge/`。
- Node Lab 是独立可选工具，产品 Backend 不得隐式注册其 transport。
- 架构、目录、命令、环境变量或 API 契约变化时，只更新受影响的最近文档，不做无关文档同步。
- Graph 节点、边、路由、循环、终止路径、`current_best` 边界或 `langgraph.json` 变化时，同步源码 ASCII 图和 `src/agent/app/graphs/ARCHITECTURE.md`，并运行 `make docs-check` 与 `uv run langgraph validate`。
- 对会影响架构、契约、数据、安全或验收且无法从仓库确认的问题，先询问用户。
- 密钥只放根目录 `.env` 或部署 Secret；任何密钥不得进入 `VITE_*`、示例文件或 Git。
- 历史真实模型 benchmark 与失败证据不得覆盖或删除，除非用户明确授权精确范围。

## 常用入口

```bash
make setup
make dev-agent
make dev-backend
make dev-frontend
make dev-node-lab
make docs-check
```

其他测试和运行命令按任务范围从最近的 README 或 Makefile 选择。
