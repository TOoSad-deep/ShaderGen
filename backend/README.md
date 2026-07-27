# Backend

Backend 负责 FastAPI HTTP 边界、应用生命周期、过程账本和 `scene_mvp` 用例编排。

通用 Node Lab 由 `src/nodelab/http/` 独立启动；Backend 不导入该子包，也不注册 `/api/lab/v1/*`。

## 当前契约

- `POST /api/shader/generate` 接收图片、可选 `project_id/run_id`、`quality_preset` 和 `instruction`；不再接收 `generation_mode`。
- `GET /api/shader/runs/{run_id}/progress` 增量返回白名单节点事件。
- `GET /api/shader/runs/{run_id}/progress/render` 返回运行中最近渲染帧。
- `GET /api/shader/runs/{run_id}/artifacts/{artifact_name}` 只允许 `final-render`、`metrics`、`manifest`。
- Generate 响应的 `min_pipeline.scene` 当前返回权威 `shader_graph_v1` 文档，`renderer_path=compiled_graph_program_cache_v1`；`shader_graph_shadow` 仅保留为旧 run/显式 legacy Builder 的可选兼容摘要。
- 旧 V1 Artifact fallback 和 `DELETE /projects/{project_id}/memory` 已删除。
- Backend 启动时从 `SHADERGEN_ENGINE_POLICY_PATH` 严格解析并冻结
  `ShaderEnginePolicyV1`；D097 后未配置时默认 `direct_default`，所有新请求先运行
  `direct_glsl_layerplan_v1`，失败才创建独立的私有 `shader_graph_v1` fallback
  attempt。显式 `canary` 或携带 `PromotionAuthorizationV1` 的 policy 仍必须通过
  `SHADERGEN_EVIDENCE_REGISTRY_PATH` 指向非 symlink 的受信 registry。启动校验要求
  授权逐字段精确匹配唯一 `layerplan_glsl_promotion_evidence` durable entry：
  D094 suite、递归 verifier、人工 manifest/result/preference、不可变 bundle
  URI/hash、目标 stage 与当前代码计算的 direct implementation identity 缺一不可；
  只在 policy YAML 中自称 `durable` 不构成权限。验证回执与 registry 文件 hash
  一起冻结进 `BackendSettings`，缺 entry、`partial/local`、重复 id/key、hash 或
  identity 漂移均使启动 fail-closed。无授权 `direct_default` 不读取 registry，直接
  装配父 run/direct child/fresh old fallback runtime；`canary` 仍不能绕过上述校验。
  `SHADERGEN_DIRECT_GLSL_KILL_SWITCH=1` 对新请求和启动恢复具有最高优先级：
  Backend 仍严格解析 policy YAML 的 schema/阶段/比例/授权字段，但先把有效阶段降为
  `disabled`，因此不要求读取 promotion registry 或生成验证回执，避免 durable
  存储故障阻止紧急回滚启动。kill switch 恢复为 `0` 后，无授权
  `direct_default` 直接恢复 direct-first；显式携带授权的 `canary/direct_default`
  必须重新通过完整 registry 校验。该配置
  server-side 灰度边界，HTTP、header、query、instruction 与前端均不能选择 engine。
  `production_shadow` 命中稳定 project 桶后，只有在权威 ShaderGraph 响应契约完成且
  已离开项目锁时才以 `put_nowait` 提交独立 direct child attempt；有界 queue、固定
  worker、attempt timeout 与 shutdown drain/cancel 均不能拖慢或改变产品响应。
  详细 LayerPlan/ProgramSpec/render/metric 只原子写入
  `output/png-to-shader-shadow/<parent_run_id>/<attempt_id>/` 的 0700/0600 私有
  write-once 目录；每个文件先在同目录以 0600 临时文件完成 flush/fsync，再 atomic
  replace，目录保持 0700。递归 verifier 拒绝 symlink、额外文件、改名与篡改；该目录
  不注册到公开 Artifact API。默认 `disabled` 不启动 worker、不构造模型/Renderer、
  不落目录。
  `canary/direct-default` runtime 使用确定性 UUID5 child、独立 private store、
  fresh Renderer/cache/预算和 write-once attempt；direct 失败不会在同 attempt
  切表示，而是新建旧 ShaderGraph fallback child。只有选中 child 的 render、
  metrics 和 v2 manifest 会原子发布到父 run。API 返回只读
  `engine/representation/engine_run`，历史 v1 父 run 仍由公开旧 reader 兼容读取；
  private child 不在该 reader 的 store 中。当前 policy schema 要求
  `direct_default` 的 `canary_percent=100`；若携带授权，其授权上限也必须为 `100`。
  保留桶需要未来版本
  另行定义和实现，不能由现有字段暗示。
  BackendSettings 冻结时先拒绝 rollout private root 与 production-shadow root
  相同或任一方向嵌套；lifespan 取得默认产品 Service 的实际 `artifacts.base_root`
  后，再拒绝两个私有根与该公开根相同或嵌套。测试替身若没有可证明的
  `artifacts.base_root`，只执行前一项可证明检查，不猜测路径。

## 分层

- Route 位于 `app/api/routes/`，只处理 HTTP 边界。
- `app/services/shader_generation.py` 负责锁、进度、scene_mvp 调用、总账和公开响应。
- `app/services/agent_process_store.py` 原子写入 `agent_runs/events/logs`。
- `app/main.py` 只启动 asyncpg 过程账本与 scene_mvp Service。
- `app/database/agent_memory.py` 及 Memory SQL 保留为休眠基础设施，不再进入当前 lifespan。

进度事件不包含图片、ShaderGraph、GLSL、模型原始响应或 reasoning。运行中 PNG 使用独立字节端点；终态仍以数据库过程账本和 Artifact 为准。

## 验证

```bash
uv run pytest tests/unit_tests
uv run pytest tests/integration_tests/test_shader_run_progress_api.py
make check
```
