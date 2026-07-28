# Agent App 架构

`agent.app` 是唯一 Agent 应用包。当前产品有两条隔离 engine 路径：

```text
默认 direct:
  services/layerplan_glsl_direct.py
    -> nodes/layerplan_glsl_shadow/
    -> shaderforge.program_spec / rendering / evaluation

服务端显式选择 ShaderGraph:
  services/png_to_shader_min.py
    -> graphs/png_to_shader_min_graph.py
    -> nodes/png_to_shader_min/
    -> shaderforge.public / typed 子包公共根
```

- `contracts/`：LLM 与最小 Scene/Patch 契约。
- `llms/`：Gateway 和 provider/model-family 适配。
- `graphs/`：显式 ShaderGraph engine 的 Graph Builder、节点/边和路由。
- `nodes/`：`png_to_shader_min` Graph Node，以及 direct/shadow 共用的有界 Author helper。
- `prompts/`、`parsers/`、`messages/`：模型输入输出边界。
- `states/`：`PngToShaderMinState`。
- `services/`：对 Backend 的稳定 Graph Service、direct runner 和隔离 attempt 支持。
- `memory/`、`context/`：休眠的旧 Memory 基础设施，当前产品不调用。

Node 不得直接依赖 `agent.app.llms`；Backend 不得越过 `agent.app.services` 导入 Graph/Node/Prompt。ShaderForge 只能从 `shaderforge.public` 或 typed 子包公共根导入。

Artifact 使用 `LocalArtifactStore`/`RunArtifactStore`；Memory 的 `BaseStore` 是不同语义，不得混用。
