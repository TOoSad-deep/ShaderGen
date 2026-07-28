# ShaderGen

ShaderGen 把参考图片转换为可评估的无贴图 WebGL1 fragment shader。当前产品只有 `scene_mvp`：React 负责上传和展示，FastAPI 负责编排，默认执行 LayerPlan + direct GLSL；一次 direct attempt 内会进行结构修复，仍失败则创建一个全新、隔离的 direct attempt 重试，不自动降级到 ShaderGraph。

## 快速开始

```bash
make setup
make dev-agent
make dev-backend
make dev-frontend
```

Node Lab 是可选调试工具，需要时运行 `make dev-node-lab`。日常验证按 [AGENTS.md](AGENTS.md) 选择与改动相关的测试，不默认运行全量检查或质量实验。

## 当前产品链路

```text
Frontend
  -> Backend parent run
     -> direct_glsl_layerplan_v1（默认）
        -> advisory LayerPlan
        -> canonical ShaderProgramSpecV1
        -> WebGL1 校验、渲染与评分
     -> direct 失败时 fresh direct_glsl_layerplan_v1 retry（最多一次）
  -> final-render / metrics / manifest
```

- 无 `SHADERGEN_ENGINE_POLICY_PATH` 时使用无授权 `direct_default`。
- `SHADERGEN_DIRECT_GLSL_KILL_SWITCH=1` 可让新请求直接使用 ShaderGraph。
- `POST /api/shader/generate` 不接受客户端 engine 或 generation mode 选择。
- API 通过 `engine`、`representation` 和 `engine_run` 报告实际执行来源。
- direct 成功时 `min_pipeline.scene=null`；两次 direct attempt 都失败时请求明确返回 `direct_attempts_failed`。
- ShaderGraph 只在服务端 policy 或 kill switch 明确把它选为主 engine 时运行，不再作为 direct 失败后的自动 fallback。

## 配置

- 服务端变量及说明：[.env.example](.env.example)
- 前端公开变量：[frontend/.env.example](frontend/.env.example)
- 本地服务端密钥放根目录 `.env`；前端本地配置放 `frontend/.env.local`。
- `VITE_*` 会进入浏览器产物，不能包含秘密。

## 文档入口

- 开发规则：[AGENTS.md](AGENTS.md)
- 当前交接：[PROGRESS.md](PROGRESS.md)
- 当前架构：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- 产品功能：[docs/FEATURES.md](docs/FEATURES.md)
- 长期决策：[docs/DECISIONS.md](docs/DECISIONS.md)
- Backend：[backend/README.md](backend/README.md)
- Frontend：[frontend/README.md](frontend/README.md)

`docs/archive/` 不参与默认开发上下文，只在精确追溯旧行为时读取。
