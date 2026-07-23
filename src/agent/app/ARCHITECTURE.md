# Agent App 架构

`agent.app` 是唯一 Agent 应用包。当前产品路径：

```text
services/png_to_shader_min.py
  -> graphs/png_to_shader_min_graph.py
  -> nodes/png_to_shader_min/
  -> shaderforge.public / typed 子包公共根
```

- `contracts/`：LLM 与最小 Scene/Patch 契约。
- `llms/`：Gateway 和 provider/model-family 适配。
- `graphs/`：Graph Builder、节点/边和路由。
- `nodes/`：`png_to_shader_min` Node。
- `prompts/`、`parsers/`、`messages/`：模型输入输出边界。
- `states/`：`PngToShaderMinState`。
- `services/`：对 Backend 的稳定公共用例。
- `memory/`、`context/`：休眠的旧 Memory 基础设施，当前产品不调用。

Node 不得直接依赖 `agent.app.llms`；Backend 不得越过 `agent.app.services` 导入 Graph/Node/Prompt。ShaderForge 只能从 `shaderforge.public` 或 typed 子包公共根导入。

Artifact 使用 `LocalArtifactStore`/`RunArtifactStore`；Memory 的 `BaseStore` 是不同语义，不得混用。
