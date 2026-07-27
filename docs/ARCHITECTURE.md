# ShaderGen 架构

当前产品实现以 `scene_mvp` 最小骨架为唯一在线路径。通用 Node Lab 是独立开发工具，不属于产品请求链路；历史 V1 和 V2-V5 方案仍只在决策、进度归档与 evidence registry 中追溯。

## 分层

```text
Frontend React
  -> Backend FastAPI
  -> backend.app.services.shader_generation
  -> 启动期冻结的 engine policy
     ├─ disabled / production_shadow -> png_to_shader_min LangGraph
     │                                -> ShaderGraph DSL / Compiler
     └─ canary / direct_default（默认） -> parent coordinator
          ├─ direct child -> advisory LayerPlan / canonical ProgramSpec / WebGL1
          └─ fresh fallback child -> 私有 png_to_shader_min LangGraph
  -> 父 run 原子公开 final-render / metrics / manifest
```

- `frontend/`：上传、配置、运行进度、服务端/客户端 Render 和 GLSL 展示；`frontend/src/runStages.ts` 把进度事件收敛为单一可测试的阶段视图模型，只展示后端真实字段。
- `backend/`：HTTP 边界、进度注册、过程账本、生命周期和用例编排。
- `src/agent/`：LangGraph、LLM Gateway、Prompt、Parser、State 和公共 Service。
- `src/shaderforge/`：确定性 Scene、Shader 物化、ProgramSpec、安全校验、WebGL1 渲染、复合评分、优化和 Artifact。
- `src/nodelab/`：Pipeline 无关的节点 Harness，不依赖 Agent、Backend 或 ShaderForge。
- `src/nodelab/http/`：同一命名空间下的独立 FastAPI transport，通过受信任 factory 注入 Pipeline Provider。

Backend 只能通过 `agent.app.services.*` 调用 Agent。Agent 不持有数据库连接池；ShaderForge 不依赖 FastAPI、LangChain 或 React。

## Node Lab

Node Lab 默认以空安全 Application 启动在端口 8090。产品 Backend 不注册 `/api/lab/v1/*`；前端 `/lab` 通过 `VITE_NODE_LAB_API_BASE_URL` 连接独立服务。节点、Fixture、capability、suite、资源与副作用门禁只能由进程启动时的 `NODELAB_APPLICATION_FACTORY=module:callable` 注入，HTTP 客户端不能提交 import path 或 manifest path。

当前实现保留通用 `nodelab`、`nodelab.http` 独立 transport 和工作台，并在 `agent.app.nodes.png_to_shader_min.node_lab` 提供当前 `scene_mvp` 12 节点的显式 Provider/Executor；`agent.app.services.node_lab` 作为受信任 factory 组合根选择具体 Gateway。生产侧适配以不透明 Artifact ID hydration 图片、目标 RGB 和 Render，以带指纹的 JSON 快照恢复 `ShaderGraphCandidateSnapshot`，不让通用 Harness 反向依赖 Agent/ShaderForge，也不让 Node 反向依赖 LLM 实现；模型节点的 AI-off fallback 与真实调用使用不同 binding，真实调用继续经过服务端与请求双重门禁。已退役的 PNG-to-Shader V1 Adapter、benchmark manifest、Fixture 和运行脚本不恢复。详细边界见 `src/nodelab/ARCHITECTURE.md`、`src/nodelab/http/ARCHITECTURE.md` 和 `docs/NODE_LAB_GUIDE.md`。

## scene_mvp Graph

`png_to_shader_min_graph.py` 继续用 12 个节点完成输入登记、确定性感知、严格 ShaderGraph Author、specialized Compiler、真实渲染、复合评分、canvas/node/layer 参数优化、可选 typed layer Refine 和 final Artifact。DSL node 是领域数据，不映射为 LangGraph node。

- 模型 ShaderDocument 与感知直接产出的 fallback 都必须真实编译、渲染后择优。
- Refine 只能从只读 `current_best.document` 派生一个绑定 `base_document_sha256` 的 typed layer patch。
- 候选只有在 `min_scene_composite_v3` 严格改善时才能提交。
- `current_best` 是绑定文档、Compiler、program key、Render、metric、父 hash 与 provenance 的不可变 CandidateSnapshot。
- 同一 run 通过 `compiled_graph_program_cache_v1` 隔离 topology，并在单个 active block 内复用 packed uniform program；cache、compile 和优化 block 都有硬上限。
- `finalize` 直接固化权威 `shader-graph.json`、specialized WebGL1 GLSL 和 Render，不再为默认产品重复执行 MinScene shadow。
- Graph recursion limit 按合法最坏路径推导；未知递归错误 fail-closed。
- 完整拓扑、条件边和安全边界见 `src/agent/app/graphs/ARCHITECTURE.md`。

## LayerPlan/direct GLSL shadow

D088 新增独立离线 harness，用于比较“不提供 LayerPlan”与“只增加 advisory LayerPlan”的 direct GLSL Author。它不注册 LangGraph，不接 Backend/API、产品 `current_best`、公开 Artifact 或 evidence registry：

```text
参考图
  ├─> Arm A: Initial/Refine GLSL Author ───────────────┐
  └─> VisualAnalysis Author ─> advisory LayerPlan ─> Arm B
                                                       │
canonical ShaderProgramSpec ─> 安全校验 ─> WebGL1 prepare/draw
                                                       │
                              真实 Render/metric ─> arm-local current_best
```

LayerPlan 只帮助模型理解视觉分层，不能参与校验、评分或候选接受。`shaderforge.program_spec` 是唯一 ProgramSpec/LayerPlan 类型、规范化、哈希和 attestation 真相；模型只能返回语义字段，可信层绑定参考图、指令、模型、Prompt、实际生效采样身份（Gateway `effective_identity`，kimi 实际 temperature=1，缺有效身份 fail-closed）、角色、父 Spec、content_type、角色输入上下文（Refine 含 current_render 与评估上下文哈希）及结构修复 provenance。候选只有在 ProgramSpec 静态安全校验和真实 WebGL1 draw 后，才能由 Renderer 私有 signer 签发绑定具体 Spec/RGB/PNG/runtime 的 receipt；Runner 只有 verify-only capability。详细产物只写显式指定的本地私有目录（staging + 原子 rename、0700/0600、规范相对路径与 symlink 拒绝、`verify_shadow_run` 复验）。

D090 在单 run 之上新增冻结 suite 层；D093 将默认协议升级为 `manifest_v2.yaml`/`gate_v2.yaml`。四张参考图、instruction、共享预算、两轮 `AB/BA` 顺序和判定阈值保持不变，v2 另绑定四个 Author Prompt、Schema、repair policy、输出/修复上限、Renderer contract 与 SafetyLimits，并把实现身份传入单 run config fingerprint、gate 和 suite report。`scripts/run_layerplan_glsl_shadow_suite.py` 默认拒绝真实模型，只有当前 v2 加显式 `--allow-live-model --output-root <私有目录>` 才会顺序运行；v1 只允许 `--verify` 历史复验。每个单 run 先经 `verify_shadow_run`，suite 再按同样本同轮的 `B-A` loss、顺序效应和 inconclusive 计负规则聚合，并以原子私有报告落盘。

D094 的 v2 真实 suite 自动 gate 已通过。`scripts/run_layerplan_glsl_shadow_review.py` 只接受已递归复验的当前 v2 suite：以 suite hash + sample + round 确定匿名 A/B，公开 `reviewer/` 只含 reference/A/B 图片、静态页面和 template，真实 Arm/run 映射留在父目录私有文件；全部内容带 hash/size，递归 verifier 拒绝 symlink、额外文件、篡改和改名。只有同时存在 A/B current best 的 round 可评；不可配对 round 不伪造图片并继续进入偏好率分母。Agent 不生成投票，人工结果只能由 `A/B/tie` 完整提交。D096 的独立人工结果为 Arm B `5/8=0.625`，达到冻结 `0.5` 门槛；`layerplan_glsl_promotion_evidence.py` 已把 suite、8 个 run、盲评包与 canonical 人工结果组合为可离线递归复验的内容寻址私有 bundle，但 `f42aefb…` 仍位于 `/private/tmp`、未登记 durable，因此生产结论是 `no_go_pending_durable`。

D095 最初以默认关闭方式落地服务端 `ShaderEnginePolicyV1` 与 production-shadow coordinator。D097 根据单人、单环境开发事实把无 policy 文件时的默认值改为 `direct_default`：新请求先运行 direct GLSL，失败时创建全新的私有 ShaderGraph fallback attempt；客户端仍不能选择 engine，`SHADERGEN_DIRECT_GLSL_KILL_SWITCH=1` 仍对新 run 具有最高优先级并强制回到旧引擎。显式 `disabled` 与 `production_shadow` policy 继续可用。

显式 `canary` 以及携带 `PromotionAuthorizationV1` 的 `direct_default` 继续执行
durable registry 校验。Backend 启动时会读取显式
`SHADERGEN_EVIDENCE_REGISTRY_PATH`，拒绝 symlink、重复 JSON key/evidence id，
并要求授权逐字段匹配唯一的 durable promotion entry、不可变 URI/hash、目标
stage 以及当前代码重算的 direct implementation identity；验证回执和 registry
文件 hash 冻结进进程配置。D097 允许不携带授权的 `direct_default` 直接装配，不读取
registry；代码侧接入的父 run runtime 会创建确定性 UUID5 child，使用独立 Renderer、预算和
write-once 私有 attempt；失败只以安全码记录并创建 fresh ShaderGraph fallback
child。选中结果经过 manifest/hash 复验后才把三个公开 Artifact 原子登记到父 run，
未选 child 永不进入公开 index。API/前端返回只读 `engine/representation/engine_run`
discriminator，完整 LayerPlan、ProgramSpec 和失败上下文保持私有；同进程 reader
仍可回退读取切换前的 v1 父 run。direct attempt 只向父进度流发布
`direct_start/direct_completed/direct_failed` 安全事件，异常只暴露预声明失败码；
两种 engine 都失败时，UseCase 保留 `ParentRunFailure` 的父失败码与安全
`attempt_refs`，不得降级为 `internal_pipeline_error`。runtime 提供幂等
`close/aclose` 生命周期契约，attempt-local 资源仍由每次协调执行的 `finally`
关闭；选中 engine 的父响应在进入 API 前再经 strict schema 还原，字段漂移只记录
安全字段路径并收敛为 `response_contract_failed`。完整父 run/child attempt、production shadow、
fallback 与 manifest union 契约见
`docs/superpowers/specs/2026-07-27-direct-glsl-production-rollout.md`。在 durable
证据缺失时只禁止 canary/带授权晋升，不再阻止单环境默认 direct。

紧急回滚是上述 promotion 校验的唯一启动例外：
`SHADERGEN_DIRECT_GLSL_KILL_SWITCH=1` 仍要求 policy YAML 严格解析，但有效阶段先
降为 `disabled`，启动不读取 promotion registry，也不要求验证回执；这样 registry
或 durable 存储故障不能阻止旧引擎恢复。开关回到 `0` 后，无授权
`direct_default` 直接恢复 direct-first；显式携带授权的 policy 仍须重新通过校验。

## HTTP 与进度

`POST /api/shader/generate` 不再包含模式分流，固定执行 `scene_mvp`。客户端可预生成 `run_id`，在 POST 阻塞期间轮询：

```text
GET /api/shader/runs/{run_id}/progress?after=<seq>
GET /api/shader/runs/{run_id}/progress/render
GET /api/shader/runs/{run_id}/artifacts/{final-render|metrics|manifest}
```

`RunProgressRegistry` 是单进程内存状态，重启即失；事件 JSON 不包含图片、ShaderGraph、GLSL、Patch value、用户输入、模型原始响应或 reasoning。终态审计以数据库过程账本和 Artifact 为准。完整 ShaderGraph 只出现在终态 manifest/API 的 `scene` 字段和私有 run 内 `shader-graph.json`。

## Persistence 边界

Backend 当前启动 asyncpg 过程账本连接池，写入 `agent_runs`、`agent_events` 和 `agent_logs`。终态事件、日志和状态使用单事务提交。

旧 LangGraph Memory/checkpoint 的 Python/SQL 实现与已有 PostgreSQL 数据暂时保留，但：

- Backend lifespan 不再打开 saver/store；
- 当前 Graph/Service 不读写旧策略 Memory；
- 前端和 HTTP 不再提供项目 Memory/清除入口。

后续是否迁移、只读归档或按保留期删除，需要单独的数据策略决策。

## Artifact 与安全

- 公开 Artifact 只允许 final render、metrics 和 manifest。
- 默认产品 Artifact 根及 rollout 私有根都显式启用 0700/0600 权限；公开父 run
  final bundle 逐文件与目录 fsync 后原子 rename，并在每次读取时复验文件集合、
  内容与 symlink/替换边界。
- 图片、完整 GLSL、编译器原文和模型原始响应不得进入普通日志。
- 密钥只在服务端 `.env` 或部署 Secret 中；任何 `VITE_*` 都是公开前端配置。
- 历史失败 benchmark、run 和人工证据不得覆盖或删除；当前代码不再提供旧 V1 benchmark 入口。

## 同步规则

Graph 节点、边、路由、循环、终止路径、`current_best` 边界或 `langgraph.json` 变化时，同一次改动必须更新源码 ASCII、Graph Mermaid、路由表和安全说明，并通过 `make docs-check` 与 `uv run langgraph validate`。
