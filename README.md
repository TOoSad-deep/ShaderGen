# ShaderGen

ShaderGen 将参考图片生成可执行的 WebGL1 Fragment Shader。当前产品只有一条链路：

```text
PNG
→ LangGraph: prepare → LayerPlanV1
→ Initial/Refine → LayeredShaderSpecV1 / LayerPatchV1
→ compile → ShaderProgramSpecV1
→ validate → WebGL1 prepare → draw
→ receipt/attestation → evaluate → select → finalize
→ artifacts
```

`ShaderProgramSpecV1` 是当前 Layered compiler 的执行 IR，不是旧模型 Author
链路。模型只负责 LayerPlan、Layered Initial 与单层 Refine；所有哈希、编译、
验证、receipt 和 attestation 由确定性代码产生。

父运行最多执行 3 个彼此隔离的 fresh Direct attempt。成功时只公开选中
attempt 的 render、metrics 和 manifest；全部失败返回
`direct_attempts_failed`。

## 启动

```bash
make setup
make dev-agent
make dev-backend
make dev-frontend
```

Agent Server 默认监听 `http://127.0.0.1:2024`，Backend 默认监听
`http://127.0.0.1:8088`，Frontend 默认监听 `http://127.0.0.1:5173`。
产品 Frontend 的运行时间线展示两个父级生命周期阶段和 Direct attempt 的
16 个真实节点。Agent Server 则从 `langgraph.json` 加载单节点 JSON-safe
Studio adapter；它用于保护私有 graph state 和运行时资源边界，不代表核心
attempt 只有一个节点。

## 验证

```bash
uv run pytest tests/unit_tests
uv run pytest tests/integration_tests/test_layerplan_glsl_direct_full_chain.py
uv run pytest tests/integration_tests/test_layered_direct_real_renderer.py
npm --prefix frontend run test
npm --prefix frontend run build
```

当前架构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，功能状态见
[docs/FEATURES.md](docs/FEATURES.md)。
