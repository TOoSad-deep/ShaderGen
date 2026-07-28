# Agent

默认产品请求由 Backend 调用 direct Service；失败时由 Backend 创建一个 fresh direct attempt 重试，不自动执行 `langgraph.json` 注册的 `png_to_shader_min`。ShaderGraph Service 仅在服务端明确选为主 engine 时运行。

## 开始前

只读取根 `AGENTS.md`、`PROGRESS.md` 和本次修改目录最近的 `ARCHITECTURE.md`。Graph/routing 改动再读取 `app/graphs/ARCHITECTURE.md`，不要预先遍历全部子模块文档。

## 边界

- Backend 只通过 `agent.app.services.*` 调用 Agent。
- Node 通过 `LLMGateway` 使用模型；Prompt 只放 `app/prompts/*.yaml`。
- 渲染、评分、Scene、优化和 Artifact 属于 ShaderForge。
- Memory/context 与历史质量 Harness 当前休眠，只有对应任务才读取。

## 验证

普通改动运行相关测试。Graph 拓扑或 `langgraph.json` 变化时额外运行：

```bash
make docs-check
uv run langgraph validate
```

跨 Backend/Frontend 行为再补一条相关集成/E2E。全量检查和真实模型调用遵循根 `AGENTS.md`。
