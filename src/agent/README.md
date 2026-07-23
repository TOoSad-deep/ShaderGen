# Agent

## 当前状态

`langgraph.json` 只注册 `png_to_shader_min`，产品调用入口是 `agent.app.services.png_to_shader_min`。旧 V1 Graph、Node、Parser、Prompt 和 Service 已删除。

## 开始前

- 当前 active 功能以 `docs/FEATURES.md` 为准。
- 当前进度和下一步以 `PROGRESS.md` 为准。
- Graph/routing 改动先读 `src/agent/app/graphs/ARCHITECTURE.md`。

## Agent 改动门禁

```bash
uv run pytest tests/unit_tests
make docs-check
uv run langgraph validate
```

涉及 Backend/Frontend 时追加对应集成测试或 E2E。不得在普通测试中调用真实模型。
标准命令为 `uv run pytest tests/unit_tests`、`make docs-check` 和 `uv run langgraph validate`。

## 完成交接

会话结束前原地更新 `PROGRESS.md`；架构或长期取舍同步写入 `docs/DECISIONS.md`。

## 按需阅读

- `src/agent/ARCHITECTURE.md`
- `src/agent/app/config/ARCHITECTURE.md`
- `src/agent/app/context/ARCHITECTURE.md`
- `src/agent/app/contracts/ARCHITECTURE.md`
- `src/agent/app/graphs/ARCHITECTURE.md`
- `src/agent/app/llms/ARCHITECTURE.md`
- `src/agent/app/memory/ARCHITECTURE.md`
- `src/agent/app/messages/ARCHITECTURE.md`
- `src/agent/app/nodes/ARCHITECTURE.md`
- `src/agent/app/observability/ARCHITECTURE.md`
- `src/agent/app/parsers/ARCHITECTURE.md`
- `src/agent/app/prompts/ARCHITECTURE.md`
- `src/agent/app/services/ARCHITECTURE.md`
- `src/agent/app/states/ARCHITECTURE.md`
- `src/agent/app/tools/ARCHITECTURE.md`
