# ShaderGen

ShaderGen 将参考图片生成可执行的 WebGL1 Fragment Shader。当前产品只有一条链路：

```text
PNG
→ LayerPlanV1
→ LayeredShaderSpecV1 / LayerPatchV1
→ deterministic ShaderProgramSpecV1
→ static validation
→ WebGL1 compile/link/draw
→ metrics + artifacts
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
make dev-backend
make dev-frontend
```

Backend 默认监听 `http://127.0.0.1:8088`，Frontend 默认监听
`http://127.0.0.1:5173`。

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
