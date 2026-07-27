# 进度

最后更新：2026-07-27

> 本文件是有界的当前交接页，不是逐会话追加日志。功能状态以 `docs/FEATURES.md` 为准，长期取舍以 `docs/DECISIONS.md` 为准，完整旧记录见 `docs/progress/archive/`。

## 当前状态

- 已按 D083 完成 Node Lab 第一阶段结构收拢：原顶级 `nodelab_service` 迁入 `nodelab.http`，核心 Harness、独立 FastAPI 进程、受信任 Application factory 和 `/lab` 工作台现在位于同一 Python 命名空间；产品 Backend 仍不注册 Lab route，默认服务仍为空安全 Application。
- Node Lab `/lab` 工作台已增加 descriptor 驱动的 Artifact 输入选择器与推荐父步骤：依据 `NodeLabInputExample.inputs` 提供默认值、`artifact_inputs` 字段名到 kind 映射提供候选；scene_mvp 02-12 节点中 `source_artifact_id` / `target_rgb_artifact_id` 已补齐映射，上传/恢复/执行得到 Artifact 后自动填充仍为空或占位符的对应字段，不覆盖用户手写非占位值；同 kind 多个 Artifact 时先按 `sha256` 判断内容等价性，等价副本合并为一个逻辑候选并自动填充（优先与父 Step State 已引用 ID 保持一致），无法证明等价的真实多候选保留占位符并由用户下拉选择；切换节点/示例时按 `base_step_node_id` 自动选择最新成功父 Step，无匹配时保持 Root State；保留 JSON 编辑、格式化与模型门禁。
- Node Lab 的控制台命令、8090 端口、`NODELAB_*` 环境变量和 `/api/lab/v1/*` 契约保持不变；Python 内部导入统一为 `nodelab.http.*`，不保留旧 `nodelab_service.*` shim。结构测试已锁定 core/HTTP/Backend 单向依赖，隔离 wheel 已确认只包含新命名空间。
- 已按 D084 将 HTTP Schema 从 377 行单文件拆为 `schemas/` package，稳定聚合入口继续导出原 Pydantic 名称；执行资源、batch/report、错误和公共类型互相分层，Route 与 OpenAPI 契约未变化。
- `make check` 已加入干净 sdist→wheel 边界检查，避免本地 `build/` 缓存把已删除模块混入 wheel；门禁要求新 Schema 子包完整、旧 `schemas.py` 与 `nodelab_service` 缺席、CLI entry point 正确。
- 已按 D085 将 449 行 HTTP Route 拆为共享依赖、健康、batch、目录、run/step 执行和 Artifact 模块；稳定 `router` façade、18 个 method/path、operationId、tag、错误与上传上限均保持不变。
- Node Lab 工作台已完成空目录与异常状态收口：空 Application 使用整宽接入引导并保留 LabRun/Artifact/DAG 能力，离线可原地重试，在线空目录可重新读取 factory；创建、恢复、执行和上传分别展示忙碌状态，长 ID、错误关闭、ARIA、减少动态效果与窄屏布局已补齐；结果栏的隐式 Grid 行固定按内容分配，长 Output 不再被 State Diff 挤压为单行。
- 已按 D086 将当前 `scene_mvp` 12 个生产 Node 接入独立 Node Lab：Provider/Executor 留在 `agent.app.nodes.png_to_shader_min.node_lab`，受信任 factory 组合根位于 `agent.app.services.node_lab`；图片、目标 RGB、Render 和 CandidateSnapshot 通过同一 LabRun 的不透明 Artifact/带指纹 JSON 快照跨 Step 恢复，AI-off 与 real 模型 binding 分离，旧 V1 Adapter、Fixture 和 benchmark 未恢复。
- 当前 `main` 已先快进吸收 `TOoSad-deep/feature-improve@4768aa5`，再合并 `origin/mvp@6d4aac6`；代码、Graph 文档、ADR、进度和证据冲突已按当前 ShaderGraph 产品事实收口。
- 产品仍只有 `scene_mvp`，`langgraph.json` 只注册 `png_to_shader_min`。默认组合根使用 `shader_graph_v1`：Initial/Refine Author、specialized Compiler、真实 WebGL1、多 program cache、CandidateSnapshot、node/layer 参数 block、Backend/API/UI 已贯通，12 节点拓扑未改变。
- 感知阶段同时保留 legacy MinScene 测量与产品 `fallback_shader_graph`；默认产品使用 ShaderDocument。Memory/checkpoint Python/SQL 与 PostgreSQL 数据继续休眠保留；旧 V1 产品、Adapter、manifest 与 benchmark 入口仍保持删除，通用 Node Lab 不改变该边界。
- mvp 的 acceptance live A/B、strict total-loss 防漂移 helper、私有 MinScene Patch replay 和 12/32 maturity fixture 已合入。由于两分支复用了 `D064–D068`，本次把 mvp 五项决策顺延为 D072–D076，保留现有清理/Memory 决策编号不变。
- D072/D073 证明旧 MinScene 诊断候选空间内 strict total-loss 优于 geometry-first，并纠正生产原本即为 strict total-loss 的事实；当前 ShaderGraph 同样执行 strict total-loss，但旧实验不能外推为 ShaderGraph 质量结论。
- D074 的 replay 契约、legacy `make_min_nodes` 实现和聚焦测试已保留；默认 `make_shader_graph_nodes` 尚未迁移 typed layer patch replay，当前产品 manifest 不包含 `private_replay_bundle`。D075 的 `budget32_supported` 只适用于两个旧 Feature 合成 fixture，D076 明确不授权当前产品预算变化。
- acceptance 与 maturity 两份报告仍存在于来源 mvp worktree 的本地忽略目录，registry 为 `partial`；当前工作树未复制报告，不得把 registry 当作 durable 发布证据。
- 已修复 run `362d2164-3438-4e53-b784-7104d7c269e7` 暴露的 ShaderGraph compile 预算错配：旧固定 16 无法覆盖 manual 30 轮 Refine，D077 改为按 run 推导（manual=45），意外耗尽也会收敛为稳定候选失败而非未分类 500。
- 已修复 run `04b7b4af-2dd0-495d-9ac6-0b34f1eeca23` 暴露的 recursion 上界失真：无效 Refine 过去会重建参数队列，D078 改为复用 current best 的 no-op 过桥，high 连续失败 patch 不再重复优化或撞 85 步上限。

## 当前 active 功能

- `F09`：以 `scene_mvp` ShaderGraph 最小骨架继续完成可发布的 PNG-to-Shader 质量与可靠性闭环。

## 下一步

- Node Lab 下一结构切片处理耦合更强的 955 行 `runner.py`：先抽离 JSON Schema 校验、Fixture executor 与 state diff 等纯逻辑，再评估 Application 执行编排；1147 行 `benchmark.py` 留到 runner 边界稳定后处理。
- 为 Node Lab 后端 OpenAPI 与前端 `frontend/src/api/nodeLab.ts` 增加生成或 parity gate，避免手写类型静默漂移。
- 为当前 scene_mvp Node Lab 增加自动浏览器 E2E：覆盖真实 Chromium Renderer、Artifact 上传、逐节点 DAG、断线恢复和 finalize；真实模型路径继续使用显式双重开关与独立预算，不进入普通 CI。
- 为 ShaderGraph 重新设计 typed layer patch replay、冻结 benchmark manifest、质量指标和人工门禁；不得直接复用旧 MinScene replay/12–32 draw 的发布含义。
- 保留“模型 Initial 仍输给 fallback”的负面质量事实，继续用版本中立的固定小样例验证 Prompt/搜索，不通过放宽 Schema 掩盖问题。
- 参数优化继续评估 rotation/成组参数、typed layer patch 局部成熟和更大搜索；任何预算变化必须使用 ShaderGraph 候选空间重新建立证据。
- 其余项目结构重构仍按 `docs/PROJECT_STRUCTURE_REFACTOR_PLAN.md` 的决策门推进；若未来启用 Memory，必须建立 scene_mvp 新契约、namespace 和迁移验收。

## 未解决缺口

- Node Lab `runner.py` 与 `benchmark.py` 仍然偏大；HTTP Schema 和 Route 已完成内部职责拆分。
- Node Lab OpenAPI 与前端 TypeScript 契约仍分别手写，当前没有自动 parity gate。
- `npm audit` 报告 Vite 8.1.3 的传递依赖 `postcss@8.5.16` 存在 GHSA-r28c-9q8g-f849 高危路径穿越告警且有可用修复；本次结构迁移未改变 `package-lock.json`，依赖升级需独立验证前端构建与页面 E2E。
- 当前 ShaderGraph 产品没有 D074 等价的私有 typed layer patch replay；legacy bundle 不能恢复或证明当前产品候选过程。
- 当前产品缺少 durable 冻结 benchmark 与独立人工偏好门禁；生产模型已切换为 `kimi:k3-256k`（D080），旧 Qwen 证据不外推，且 Initial 仍常由 scorer 判定不如 fallback。
- D072/D075 报告仅为 `partial` 且候选空间已被 D070 替换，不能用于 ShaderGraph 发布或直接把 patch maturity 从 1/12 调到 32 draw。
- `scene_mvp` 仍没有 CMA-ES、2000 draw 生产预算、优化中断/恢复和对应质量证据。
- 服务端仍是阻塞式 API；浏览器停止等待不等于服务端取消。任务化/cancel、outbox/reaper 和多 worker 分布式锁属于后续可靠性设计。
- 历史 V1/Node Lab real-model 完整报告与公开 review package 已按授权随旧 `output/` 删除；registry 对应条目为 `missing`，只能审计定位。
- 当前 scene_mvp Provider 已有 fake Renderer 的完整 Application 集成和真实 HTTP 进程目录 smoke test，但还没有覆盖真实 Chromium Renderer、工作台操作、断线恢复或真实模型的自动浏览器 E2E。
- Renderer registry 由 `finalize` 关闭；用户在浏览器 Node 执行后放弃 LabRun 时，当前没有单 run cancel/close API，资源只能在后续 finalize 或服务进程结束时回收。

## 当前验证基线

- scene_mvp Node Lab 接入后 `make check` 通过：399 个单元测试、docs-check、干净 wheel 边界、LangGraph validate（1 个 Graph）和前端生产构建全部成功。
- scene_mvp Provider 聚焦测试为 5 个单元测试与 1 个 AI-off 全链路集成测试通过；新增 descriptor 声明 `perceive_target`/`author_initial`/`author_refine` 的 `source_artifact_id -> reference_png`、`render_and_evaluate`/`optimize_base`/`optimize_feature` 的 `target_rgb_artifact_id -> target_rgb_npy` 的断言；全量集成为 18 passed、1 skipped。全仓 Ruff、`mypy --strict src backend`（135 个源文件）、`uv lock --check` 和 `git diff --check` 通过。
- 使用真实独立 Uvicorn 进程和 factory 在临时端口完成 HTTP smoke test：health 返回 `pipeline_id=scene_mvp`、`node_count=12`，节点目录完整；测试后进程已正常关闭。未调用真实模型，自动集成 Renderer 使用 fake 实现。
- Node Lab 手工 Playwright 验收覆盖桌面与 390px 窄屏空状态、503 离线与恢复重试、在线目录刷新，以及临时 echo Provider 下的 LabRun 创建、确定性节点执行、Output/State Diff/DAG 和 PNG Artifact 上传；当前 scene_mvp `initialize_run` 真实页面复验确认 Output 可视高度为 260px、内部内容独立滚动，结果栏保留外层滚动且后续区块不再挤压 Output。既有截图保存在本地 `output/playwright/`，尚未登记为 durable evidence。
- `kimi:k3-256k` 真实连通性已验证：文本、JSON mode、`max_output_tokens` 路径均正常，family 固定 temperature=1 并按 D081 默认下发 `reasoning_effort=low`。
- Node Lab 前端 Artifact 输入填充逻辑已抽取为 `frontend/src/utils/nodeLabInputs.ts`（含 `buildExampleInputs`/`materializeExampleInputs`/`fillArtifactInputs`），base_step 推荐逻辑抽取为 `frontend/src/utils/nodeLabBaseStep.ts`；使用 Node 内置 test runner 运行 `frontend/tests/*.test.ts`，32 个测试全部通过；`/lab` 页面构建无类型错误。
- 使用 Playwright（Chromium headless）在真实独立 Node Lab 服务与 Vite 前端上完成 `/lab` Artifact 输入自动填充验收：创建 LabRun、上传 `static/pink_bubble.png` 后选择 `initialize_run`，JSON 中的 `source_artifact_id` 自动从占位符替换为上传返回的 Artifact ID；执行 `initialize_run` 后再切换 `perceive_target`，因上传的 reference_png 与 `initialize_run` 登记的副本 `sha256` 相同，仍可自动填充确定的 `source_artifact_id` 并正确推荐 `base_step_id` 为 `initialize_run` Step；下拉选择器同步显示当前值；复验补充负向用例：上传第二个内容不同的 reference_png 后切换 `author_initial`，占位符保留、下拉不默认选中并提示候选数量，手动选择后 JSON 同步；复验截图保存在 `output/playwright/node-lab-k3-acceptance.png`，均未登记为 durable evidence。
- 产品 scene_mvp 页面 E2E 本次未重跑；本次不改变 Graph、产品 Backend/API 或前端产品链路。

## 最近重要变更

- 2026-07-27：优化 Node Lab `/lab` 节点输入体验：在 scene_mvp Provider 中补齐 02-12 节点的 `source_artifact_id -> reference_png` / `target_rgb_artifact_id -> target_rgb_npy` 的 `artifact_inputs` 映射；前端按映射自动填充或下拉选择 Artifact，同 kind 多 Artifact 时按 `sha256` 内容等价合并副本（优先与父 Step State 一致），真实不同候选保留占位符；切换节点/示例时按 `base_step_node_id` 自动选择最新成功父 Step；新增 `frontend/src/utils/nodeLabInputs.ts`、`frontend/src/utils/nodeLabBaseStep.ts` 及对应测试。
- 2026-07-27：按 D086 将当前 scene_mvp 12 个生产 Node 以 Artifact hydration/带指纹快照接入独立 Node Lab，并用 AI-off 全链路集成与真实 HTTP factory smoke test 验证。
- 2026-07-27：完善 Node Lab 工作台空目录、重试/刷新、分动作反馈、可访问性与响应式布局，修复长 Output 被结果区 Grid 压缩的问题，并以临时 Provider 和 scene_mvp 页面完成浏览器验收。
- 2026-07-27：按 D085 将 Node Lab HTTP Route 拆为共享依赖、健康、batch、目录、执行和 Artifact 模块，并锁定完整 OpenAPI 操作集。
- 2026-07-27：按 D084 将 Node Lab HTTP Schema 拆为公共、执行、batch/report 和错误模块，通过同名 façade 保持 Pydantic/OpenAPI 契约稳定。

## 历史索引

- 完整原始记录：`docs/progress/archive/PROGRESS-2026-07-07--2026-07-15.md`。
- 阶段总结：`docs/progress/archive/STAGE-SUMMARY-2026-07-10.md`。
- 验收证据及耐久性状态：`docs/evidence/registry.json`；维护规则见 `docs/evidence/README.md`。
- 代码演进以 Git commit 为准；历史快照只用于审计，不得作为当前事实来源。

## 维护规则

- 会话结束时原地刷新当前状态、下一步、缺口和验证基线。
- “最近重要变更”最多保留 5 条。
- 主文件 UTF-8 体量不得超过 20,000 bytes；超出内容移入历史归档。冻结失败证据默认不得删除，精确范围的一次性退役清理必须由用户明确授权并记录。
