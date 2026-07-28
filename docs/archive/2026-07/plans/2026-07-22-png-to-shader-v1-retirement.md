# PNG-to-Shader V1 分阶段退役与清理计划

> 归档状态：历史已完成且已被取代；下方实施顺序和门禁均是非当前事实，不得执行。

**目标：** 在不破坏当前默认产品链路、Memory、质量证据和历史可追溯性的前提下，逐步解除最小骨架对 V1 命名空间的依赖，完成 `scene_mvp` 替代门禁，最后删除 PNG-to-Shader V1 可执行链路。

**当前结论：** 2026-07-23 已完成第一波共享依赖解耦，并按用户决定正式退役当前分支的旧 Node Lab。`procedural_v1` 仍是默认产品模式，Memory/checkpoint 和正式 benchmark 仍绑定 V1；`scene_mvp` 尚未通过新的真实模型 benchmark 与独立人工门禁。旧 Node Lab 不再是 V1 删除阻塞项，Memory/历史 checkpoint 与产品切换仍需分别决策。

## 1. 已完成

- 通用结构化多模态消息迁入 `agent.app.messages.structured_multimodal`。
- 通用 WebGL1 契约迁入 `shaderforge.contracts.webgl1`，历史 contract id 不变。
- 删除无运行时消费者的 V1 草案、Prompt 草案与旧 V2–V5 方案源文件。
- 删除旧 Node Lab 的 Harness、Provider、Backend API、Frontend 工作台、CLI、benchmark、fixture、测试、配置和当前功能状态。
- 保留旧 Node Lab 的 ADR、历史进度和 evidence registry 记录；其历史报告耐久性仍按 registry 标记为 `partial`。

## 2. 当前保留边界

- `png_to_shader_v1` Graph、State、Node、Parser、Prompt 和产品 Service。
- Backend/Frontend 默认 `procedural_v1` 产品入口与公开 Artifact 读取。
- Agent Memory、LangGraph checkpoint、旧 thread 兼容清理和数据库验收。
- M5 冻结 benchmark、人工盲评、失败证据、历史 run 与审计账本。
- `png_to_shader_min` / `scene_mvp` 的独立实验身份和 no-go 发布状态。

不得因为旧 Node Lab 已退役就连带删除上述能力。

## 3. 剩余门禁

| Gate | 通过条件 | 未通过时禁止 |
|---|---|---|
| G2：Memory 决策 | min 是否使用 Memory、旧 checkpoint 保留期、导出与清理流程有明确决策 | 删除 V1 Memory/清理兼容 |
| G3：质量替代 | 固定 manifest、真实模型与独立人工门禁证明 min 不劣于 V1 | 切默认产品模式 |
| G4：产品切换 | Backend/Frontend 默认切到 min，兼容观察期完成 | 删除 V1 API/Service |
| G5：下线授权 | 用户显式批准最终删除范围，历史证据可追溯 | 删除 V1 可执行目录 |

## 4. Memory/checkpoint 建议

推荐把“新方案是否写 Memory”和“旧数据保留多久”拆成两个决定：

1. `scene_mvp` 在契约稳定前保持无长期 Memory 写入；只保留 run Artifact、过程账本和必要的短期 checkpoint。
2. V1 停止接收新流量后，立即冻结旧 namespace 为只读，不让新方案复用 V1 thread 或 Store key。
3. 冻结时生成清单：namespace/thread 前缀、记录数、最早/最晚时间、导出位置、SHA-256 和负责人。
4. V1 停流后，checkpoint 默认保留 30 天，策略 Memory 默认保留 90 天；若没有合规或复盘需求，再通过显式维护命令分批清理。清理 checkpoint/store 时不删除 `agent_runs`、`agent_events`、`agent_logs`、benchmark 或 Artifact。
5. 清理先 dry-run，再按 project/thread allowlist 执行并输出删除计数；禁止按数据库或表级通配直接清空。

用户确认该策略前，本分支不删除或迁移任何现有 Memory/checkpoint 数据。

## 5. 后续实施顺序

1. 冻结 `scene_mvp` 的 scorer、证据和真实模型质量基线。
2. 决定 Memory/checkpoint 策略并实现专用 dry-run/清理验收。
3. 将 `scene_mvp` 切为默认模式，保留 V1 显式回退和观察期。
4. 完成兼容审计、历史 Artifact 可读性验证和最终下线授权。
5. 删除 V1 Graph/Service/Node/Prompt/Parser 与产品分流，运行全量验证。

## 6. 最终验证

至少包括：

```bash
make check
uv run pytest -q tests/integration_tests
npm --prefix frontend run e2e:procedural-v1
npm --prefix frontend run e2e:memory
make test-scene-mvp-ui
make test-memory-postgres
uv run ruff check src backend tests scripts/docs_check.py
uv run mypy --strict src backend
git diff --check
```

真实模型 benchmark 和人工门禁仍必须使用固定 manifest、显式调用开关与独立证据目录，不进入普通测试。

## 7. 完成定义

- min 和通用事实层不再依赖 V1 业务命名空间。
- Memory/checkpoint 已迁移或有明确、可验证的冻结与清理策略。
- 默认产品路径已完成 min 替换和观察期。
- V1 可执行代码、配置、命令和当前文档已删除。
- 历史 run、失败证据、ADR 和 evidence registry 仍可追溯。
- 适用的主干、Integration、E2E、PostgreSQL、Ruff、mypy 和 docs-check 全部通过。
