# PNG-to-Shader V1 Node Lab API 设计

> 2026-07-15 更新：本文保留阶段 A–D 的历史设计语境；其中“Lab/Service 维护 PNG-to-Shader Registry 和具体 Adapter”的目录边界已被 D032 的通用 `NodeProvider` 方案取代。当前以 `src/agent/app/lab/ARCHITECTURE.md`、`src/agent/app/nodes/ARCHITECTURE.md` 和 `docs/DECISIONS.md` 为准。

- 日期：2026-07-14
- 状态：设计完成；阶段 A–D 已实现，M5 发布证据边界保持不变
- 功能归属：`F09` 的可观测性、教学与验收子里程碑
- 目标流水线：`png_to_shader_v1`
- API 契约版本：`node_lab_api_v1`
- 主要消费者：人工调试、Agent/Codex 自动化、模块化测试、版本化 benchmark

## 1. 背景

当前 `png_to_shader_v1_graph` 已经形成由 20 个节点组成的有界闭环，但产品 HTTP 边界主要暴露完整生成、最终 Artifact、Legacy Review 和 Memory 清理。用户可以看到最终 GLSL、渲染图、评分和停止原因，却不能稳定地手动构造某个节点的输入、只执行该节点、查看状态差异并判断错误究竟来自模型、Parser、Validator、Renderer、Oracle、Selector、路由、Artifact 还是最终响应适配。

F09 当前仍为 `active`，正式自动质量门禁与人工偏好门禁均为 no-go。这里需要特别区分：

- 代码或契约缺陷，例如内部结果正确但 HTTP 响应适配失败；
- 基础设施或预算终止，例如 Memory/Renderer 不可用或 wall-time 耗尽；
- 正常的候选拒绝，例如编译失败、改善不足或保护区域退化；
- 视觉质量不足，例如流程完整执行但 final 没有优于 initial。

Node Lab 的目的不是绕过门禁，而是把这些失败面逐层变成可输入、可观察、可复现和可比较的实验。它也不能只是一套给人点击 Swagger 的 HTTP 页面：同一份节点契约还必须能被 Agent/Codex、单元测试、集成测试和 benchmark runner 直接、稳定、批量地调用。

## 2. 目标

1. 让全部 20 个图节点都能通过稳定的调试契约单步执行。
2. 允许用户手动输入图片、GLSL、结构化分析、评分、候选记录、预算和路由状态。
3. 每次执行返回节点业务输出、状态差异、Artifact、诊断、模型 provenance、成本与耗时。
4. 把生产图中合并的 `render_and_evaluate` 进一步拆成 Validator、Renderer 和 Oracle 独立能力，便于隔离错误。
5. 同时支持固定 fixture、自定义 mock 响应和显式真实模型调用。
6. 允许从历史步骤重试或分支，不原地覆盖已有实验结果。
7. 保持 Backend、Agent、ShaderForge 的既有分层，不让 Route 直接 import Node、Prompt、Gateway 或领域算法实现。
8. 第一版可直接通过 FastAPI OpenAPI/Swagger 操作，不依赖先完成新的前端页面。
9. 提供不经过 HTTP 的 Python Application API，供测试和 benchmark 注入 Fake Gateway、Renderer、clock、Store 和 fixture。
10. 提供版本化 batch manifest、CLI runner、逐 case Artifact 和聚合报告，支持无模型 microbenchmark 与显式 AI-on benchmark。
11. 保证 Python、HTTP 和 CLI 三种入口使用同一 Registry、Adapter、请求/响应 Schema 和结果序列化。
12. 允许自动化调用者通过机器可读 descriptor 发现前置条件、默认 fixture、可用指标和下一步动作，无需解析自然语言日志。

## 3. 非目标

- 不把 Node Lab 注册为默认生产 API。
- 不把每个私有 helper、Parser 内部函数或 dataclass 方法都暴露为 HTTP endpoint。
- 不把 20 个节点拆成 20 个微服务。
- 不允许客户端传入 Python import path、函数名或任意 filesystem path。
- 不把原始 `PngToShaderV1State` 直接冻结为长期 HTTP 契约。
- 不通过 Node Lab 绕过预算、Validator、Selector 或 Artifact hash 校验。
- 不返回供应商私有 reasoning、密钥、原始异常、完整供应商响应或普通日志中的完整编译器原文。
- 不把 Node Lab 的测试通过视为 F09 质量门禁通过。
- 不让 benchmark 通过 FastAPI 绕一圈来冒充模块测试；HTTP 只验证 transport 契约，核心 benchmark 默认调用同一 Application API。
- 不替换现有 M5 固定 10 例产品质量 benchmark、冻结 gate 或人工盲评；Node Lab benchmark 提供模块级证据并可被 M5 runner 复用。
- V1.0 不实现任务队列、跨 worker 取消、outbox/reaper 或生产级分布式锁。
- V1.0 不提供真实项目 Memory 写入；`promote_validated_strategy` 先提供完整 preview，实际写入需在实现前另行确认。

## 4. 当前事实与设计约束

### 4.1 当前图共有 20 个节点

```text
START
  -> initialize_run
  -> prepare_context
  -> measure_target
  -> visual_analysis
  -> persist_visual_analysis
  -> author_initial
  -> materialize_candidate
  -> render_and_evaluate
  -> decide_after_render
     -> prepare_compile_repair -> author_compile_repair -> materialize_candidate
     -> select_current_best
        -> prepare_measurement_seed -> materialize_candidate
        -> decide_after_selection
        -> load_current_best -> visual_critic -> persist_visual_review
           -> author_visual_refine -> materialize_candidate
        -> finalize
     -> finalize
  -> finalize
  -> promote_validated_strategy
  -> END
```

### 4.2 State 不是完整恢复真相源

`PngToShaderV1State` 只把 phase、iteration、current candidate/best 摘要、计数器和停止原因放入 checkpoint。图片、完整 GLSL、渲染 PNG、TargetMeasurements、VisualAnalysis、VisualReview、Score、CandidateRecord、ContextPack、模型审计和最终结果均是 `UntrackedValue`。

因此 Node Lab 不得宣称“直接恢复任意 LangGraph checkpoint 即可继续”。Node Lab 必须建立自己的版本化 JSON-safe 快照，并把大对象转换为 Artifact 引用，再通过节点 Adapter 重建当前节点所需的内部类型。

### 4.3 Artifact 是候选事实来源

生产闭环中，Candidate 的 GLSL、Author、provenance、compile、render、metrics、selection、review 和 manifest 以 Artifact 为真相源。Node Lab 必须继续校验候选 id、GLSL hash、render hash 和 metrics 绑定，不能只相信用户提交的摘要。

### 4.4 同一时间仍只有 F09 active

Node Lab 是 F09 的调试与验收子里程碑，不新增并行 `active` 功能，不改变 `docs/FEATURES.md` 当前状态，也不因为设计完成而把 F09 标记为 `passing`。

### 4.5 现有 M5 benchmark 继续保持独立

当前 `shaderforge.benchmark` 负责固定数据集加载、AI-off、冻结 gate 和人工盲评包，真实模型预算、逐 case 恢复和目录编排由 `scripts/run_png_to_shader_v1_benchmark.py` 负责。现有 benchmark 明确不把评测逻辑放入 Backend，也不通过产品 HTTP API 运行模型闭环。

Node Lab 应抽出可复用的模块执行契约和证据格式，但不能反向要求 M5 benchmark 依赖 FastAPI。后续 M5 runner 可以调用 Node Lab Application API 或复用其 Adapter；原 manifest、gate、suite-run-id、失败保留和人工盲评语义不变。

## 5. 核心决策

### 5.1 产品 API 与实验 API 分离

```text
/api/shader/*       稳定产品边界
/api/lab/v1/*       默认关闭的本地实验边界
```

Node Lab 不扩张 `/api/shader/generate` 的请求体，也不让产品客户端依赖实验字段。

### 5.2 用一个通用步骤接口覆盖 20 个节点

不是为 20 个节点各写一套重复的路由，而是通过 allowlist Node Registry 暴露：

```text
POST /api/lab/v1/runs/{lab_run_id}/steps
```

请求中的 `node_id` 只能取 Registry 已登记的 20 个名字。Registry 为每个节点提供独立输入 Schema、输出 Schema、前置条件、执行类型、副作用、错误码和源码定位。

### 5.3 为核心确定性能力增加友好接口

通用步骤接口用于真实图节点；专用能力接口用于理解和隔离一个节点内部的确定性阶段。特别是生产节点 `render_and_evaluate`，在 Lab 中必须可分别调用：

```text
normalize target -> measure -> validate -> render -> evaluate
```

这些能力接口复用 `shaderforge.public`，不复制算法。

### 5.4 每一步形成不可变快照

一个 LabRun 不是一条只能向前修改的 State，而是一组由 `base_step_id` 连接的不可变步骤：

```text
root
  -> step-001 measure
     -> step-002 author
        -> step-003 render
        -> step-004 render with overrides
     -> step-005 mock author retry
```

重试和分支只需选择不同的 `base_step_id`，不覆盖历史结果，也不需要提供单独的 fork API。

### 5.5 Node Adapter 隔离 HTTP DTO 与内部 State

Node Lab 不直接把 HTTP body `dict` 传给内部 Node。每个节点由 Adapter 完成：

1. 校验 Lab 输入 DTO；
2. 从 Artifact 读取图片、GLSL 或渲染图；
3. 把 API 形态恢复为 `TargetMeasurements`、`CandidateRecord`、`ScoreBreakdownV1` 等内部类型；
4. 调用 allowlist 中的节点或稳定领域能力；
5. 把 partial State 规范化成 JSON-safe 输出；
6. 计算 `state_diff`；
7. 写入 Lab 专用 Artifact 和步骤快照。

Backend Route 只能调用 Backend Node Lab service；涉及模型的步骤由 Backend service 调用 `agent.app.services.node_lab`，不能直接 import `agent.app.nodes.*`。

### 5.6 Application API 是唯一执行真相源

`agent.app.services.node_lab` 提供稳定的 Python Application API，至少包含 `describe_nodes()`、`execute_step()`、`validate_suite()` 和 `run_suite()` 四类用例。HTTP Route、CLI runner、单元/集成测试和 Agent/Codex 自动化全部调用这层服务，不在各入口重复组装 Node 或转换结果。

依赖必须显式可注入：Gateway、Renderer factory、Evaluator、Artifact Store、Memory Store、clock 和 fixture registry。普通测试使用 Fake/fixture；真实 Renderer 集成测试使用临时 Lab root；真实模型只允许独立 benchmark runner 显式开启。

### 5.7 Benchmark 在运行前冻结 manifest 和证据口径

Node Lab benchmark 使用版本化 manifest 描述 target、case、fixture、执行模式、重复次数、Renderer 生命周期、预算、期望不变量和冻结阈值。runner 在第一步执行前写入不可变 config，并记录 manifest、gate、fixture、相关源码和执行环境 fingerprint。

失败、中断和被拒绝的 case 都保留；恢复运行必须匹配原 config hash。阈值不得依据同一轮结果动态移动，比较报告必须引用独立 baseline report 的 SHA-256。

## 6. 总体架构

```text
人工 / 远程自动化
  -> HTTP API / Swagger
  -> backend Node Lab service ---------+
                                        |
单元测试 / 集成测试 / Agent/Codex       |
  -> Python Application API ------------+-> agent.app.services.node_lab
                                        |   -> Node Registry + Adapter
版本化 benchmark                         |   -> shaderforge.public / Agent Node
  -> CLI / batch runner ----------------+   -> Lab Snapshot + Artifact Store
                                            -> 统一 ExecutionResponse / Report
```

HTTP、Python 和 CLI 入口只负责 transport 与参数来源，执行语义以 Application API 为准。实验路径不得写入 `output/png-to-shader/` 的产品 run，也不得使用产品 `run_id` 索引冒充产品生成结果。benchmark 默认不启动 Backend，只有 transport contract suite 才通过 TestClient 或 HTTP 调用。

## 7. 标识与版本

| 字段 | 含义 |
|---|---|
| `schema_version` | 请求或响应的完整契约版本，例如 `node_lab_execution_request_v1` |
| `pipeline_id` | 固定为 `png_to_shader_v1` |
| `lab_run_id` | 一次实验容器，UUID；不等于产品 `run_id` |
| `step_id` | 一次不可变节点执行，UUID |
| `base_step_id` | 本步骤读取的父快照；根步骤为 `null` |
| `suite_run_id` | 一次 batch/benchmark 运行标识；冻结后不可改名 |
| `case_id` | manifest 中的稳定 case 标识 |
| `attempt_id` | 同一 case 的 warmup 或重复测量标识 |
| `node_id` | allowlist 中的 20 个节点名 |
| `artifact_id` | Lab 内部不透明 Artifact 标识；客户端不能构造路径 |
| `candidate_id` | V1 候选标识，例如 `candidate-0001` |
| `project_id` | Memory 隔离维度；仅 Context preview 使用真实项目读取 |
| `source_product_run_id` | 可选，只读导入既有产品 run 的来源记录；不得复用为 `lab_run_id` |
| `execution_fingerprint` | 请求、fixture、依赖版本、相关源码和环境快照的稳定摘要 |

所有快照和 Artifact descriptor 必须带自身 `schema_version`。API 版本、Pipeline 版本、Prompt 版本、Metric 版本和 Render contract id 分开记录，不能混成一个 `version` 字段。

## 8. API 总览

### 8.1 健康与目录

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/lab/v1/health` | 返回 Lab 与真实模型服务端开关；不检查或调用模型 |
| `GET` | `/api/lab/v1/nodes` | 返回全部节点摘要 |
| `GET` | `/api/lab/v1/nodes/{node_id}` | 返回节点完整 descriptor、输入输出 JSON Schema、示例和源码引用 |

V1.0 不建立第二套 `/contracts/*` 目录；20 个 node descriptor 和八个 capability descriptor 的 Schema、示例、预算/浏览器/模型标记与源码引用就是机器可读契约真相源。

### 8.2 LabRun、步骤与 Artifact

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/lab/v1/runs` | 创建 LabRun 和 root snapshot |
| `GET` | `/api/lab/v1/runs/{lab_run_id}` | 查看实验元数据；步骤 DAG 与 Artifact 使用独立列表端点读取 |
| `GET` | `/api/lab/v1/runs/{lab_run_id}/steps` | 按创建顺序列出步骤 DAG |
| `POST` | `/api/lab/v1/runs/{lab_run_id}/steps` | 选择 `node_id`、父步骤和覆盖输入，执行一个节点 |
| `GET` | `/api/lab/v1/runs/{lab_run_id}/steps/{step_id}` | 查看完整步骤响应和安全快照 |
| `POST` | `/api/lab/v1/runs/{lab_run_id}/artifacts` | 上传参考图、渲染图、GLSL 或 JSON fixture |
| `GET` | `/api/lab/v1/runs/{lab_run_id}/artifacts` | 列出本实验 Artifact descriptor |
| `GET` | `/api/lab/v1/runs/{lab_run_id}/artifacts/{artifact_id}` | 按不透明 id 读取 Artifact；不接受路径 |

删除与保留策略不在 V1.0 实现范围；实现前必须先确定保留期限，不能静默清除失败证据。

### 8.3 独立确定性能力

| 方法 | 路径 | 对应能力 |
|---|---|---|
| `POST` | `/api/lab/v1/runs/{lab_run_id}/capabilities/normalize-target` | 原始图片规范化为 V1 reference PNG |
| `POST` | `/api/lab/v1/runs/{lab_run_id}/capabilities/measure-target` | reference PNG 生成 `TargetMeasurements` |
| `POST` | `/api/lab/v1/runs/{lab_run_id}/capabilities/validate-shader` | GLSL 静态 WebGL1 无贴图契约校验 |
| `POST` | `/api/lab/v1/runs/{lab_run_id}/capabilities/render-shader` | 使用 Chromium/WebGL1 编译并渲染；普通调用为 cold 生命周期 |
| `POST` | `/api/lab/v1/runs/{lab_run_id}/capabilities/evaluate-render` | reference/render/measurements 生成 `ScoreBreakdownV1` |
| `POST` | `/api/lab/v1/runs/{lab_run_id}/capabilities/select-current-best` | current best 与 candidate 的纯选择决定 |
| `POST` | `/api/lab/v1/runs/{lab_run_id}/capabilities/decide-after-render` | `decide_after_render` 纯路由 |
| `POST` | `/api/lab/v1/runs/{lab_run_id}/capabilities/decide-after-selection` | `decide_after_selection` 纯路由 |
| `POST` | `/api/lab/v1/runs/{lab_run_id}/steps`（`node_id=prepare_context`） | 只读项目 Memory 并构造 ContextPack Artifact |

八个独立 capability 和 `prepare_context` 节点都要求先显式创建 `lab_run_id`，确保输入输出 Artifact 始终属于可追踪实验。Context 不另建隐式全局 capability，统一使用通用步骤接口和同一 LabRun 的不可变快照。

### 8.4 模型角色接口

V1.0 不再增加 `/roles/*` 别名。五个模型角色统一通过 `POST /api/lab/v1/runs/{lab_run_id}/steps` 执行，由 `node_id` 选择角色；descriptor 提供 fixture/mock/real、Prompt preview 和输入示例。这样不会形成第二套请求 Schema、预算默认值或错误语义。

### 8.5 Batch 与报告接口

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/lab/v1/batch-suites` | 返回三个固定 AI-off suite id，不暴露 manifest 路径 |
| `POST` | `/api/lab/v1/batch-manifests/validate` | 按固定 suite id 校验 manifest、Artifact hash 和 profile，不执行节点 |
| `POST` | `/api/lab/v1/batches` | 同步运行小规模固定 AI-off batch，返回完整机器可读报告 |
| `GET` | `/api/lab/v1/batches/{suite_run_id}` | 读取已完成或恢复后重新聚合的机器可读报告 |

V1.0 的 HTTP batch 只允许 `node_lab_ai_off_v1`、`node_lab_scenario_ai_off_v1` 和 `node_lab_renderer_warm_ai_off_v1`，不接收客户端 manifest 路径，也不提供 `execution_mode=real` 或模型开关。报告 comparison 继续由 CLI 对独立报告 SHA 执行；真实模型 suite 在阶段 C 通过同一 Application API 的 CLI runner 实现，并继续要求显式调用开关和整套硬预算。

### 8.6 Python Application API 与 CLI

Application API 是测试和 benchmark 的首选入口，概念接口为：

```text
NodeLabService.describe_nodes(filter) -> NodeDescriptor[]
NodeLabService.execute_step(request, dependencies) -> ExecutionResponse
NodeLabService.validate_suite(manifest) -> SuiteValidationResult
NodeLabService.run_suite(manifest, dependencies) -> BenchmarkReport
NodeLabService.compare_reports(baseline, candidate) -> ComparisonReport
```

测试可注入 Fake Gateway、fake clock、临时 Store 和固定 Renderer；benchmark 可注入真实 Renderer 或显式真实 Gateway。HTTP Route 和 CLI 只把输入转换为这些公共请求对象，再原样返回同一响应模型。

建议 CLI：

```bash
uv run python scripts/run_node_lab_benchmark.py \
  --manifest benchmarks/node_lab/png_to_shader_v1/manifest.yaml
```

真实模型额外要求：

```bash
uv run python scripts/run_node_lab_model_benchmark.py \
  --manifest benchmarks/node_lab/png_to_shader_v1/model-manifest.yaml \
  --execution-mode real \
  --allow-model-calls
```

CLI 输出必须适合自动化解析：stdout 只打印 suite id、状态和 report 路径；详细过程写结构化事件/Artifact，退出码区分配置错误、case 失败、gate 失败和运行器内部错误。

## 9. 通用请求契约

### 9.1 创建实验

```json
{
  "schema_version": "node_lab_run_create_v1",
  "pipeline_id": "png_to_shader_v1",
  "project_id": "可选 UUID",
  "title": "学习 Validator 和 Renderer",
  "source_product_run_id": null
}
```

`source_product_run_id` 只允许通过产品 Artifact 白名单和内部 run 索引只读导入现有 `final-render`、`metrics` 与 `manifest`；完整 GLSL 仍由用户从产品响应重新上传，V1.0 不为导入功能扩大产品 Artifact 白名单。不得从客户端路径复制任意文件。

### 9.2 执行一步

```json
{
  "schema_version": "node_lab_execution_request_v1",
  "node_id": "decide_after_render",
  "base_step_id": "可选父步骤 UUID",
  "execution_mode": "deterministic",
  "effect_mode": "lab_commit",
  "allow_model_call": false,
  "preview_only": false,
  "input_overrides": {
    "render_status": {
      "kind": "inline",
      "value": "success"
    }
  }
}
```

### 9.3 ValueRef

节点输入允许三种来源：

```json
{
  "kind": "inline",
  "value": {"任意": "受节点 Schema 校验的 JSON"}
}
```

```json
{
  "kind": "artifact",
  "artifact_id": "..."
}
```

```json
{
  "kind": "step_output",
  "step_id": "...",
  "json_pointer": "/outputs/score_breakdown"
}
```

图片和渲染 PNG 必须使用 Artifact；完整 GLSL 推荐使用 Artifact，也允许在明确字符上限内 inline；结构化小对象可以 inline 或引用历史步骤输出。

### 9.4 执行模式

| `execution_mode` | 行为 |
|---|---|
| `deterministic` | 不允许任何模型调用；用于 ShaderForge、路由和持久化节点 |
| `fixture` | 使用仓库内版本化固定模型输出，不调用模型 |
| `mock` | 使用用户上传的模型响应 Artifact，完整经过真实 Parser/绑定检查 |
| `real` | 通过真实 `LLMGateway` 调用模型，必须满足双重显式开关 |

模型节点还支持 `preview_only=true`：组装并返回 Prompt 版本、System Prompt、消息 section 清单、输出 JSON Schema、图片 hash/尺寸和预算计算，但不调用 Gateway。图片不以内嵌 base64 返回，Context 中的原始 Memory 内容默认不回显。

### 9.5 副作用模式

| `effect_mode` | 行为 |
|---|---|
| `preview` | 模型节点等价于安全 `preview_only`，不调用 Gateway；策略节点只返回 Memory payload。响应仍保存为 Lab 步骤证据，但不产生产品/项目副作用 |
| `lab_commit` | 只允许写 `output/node-lab/{lab_run_id}` 和步骤快照 |
| `project_commit` | 预留枚举；V1.0 拒绝。未来若允许真实 Memory 写入，必须追加服务端和请求级确认 |

### 9.6 Benchmark manifest

一个 suite 可以针对单节点、独立 capability、多节点 scenario、完整 pipeline 或 HTTP transport。示例：

```yaml
schema_version: node_lab_benchmark_manifest_v1
suite_id: validator-regression-v1
pipeline_id: png_to_shader_v1
target:
  kind: capability
  id: validate-shader
execution_mode: deterministic
runner:
  warmup_runs: 1
  repetitions: 5
  concurrency: 1
  renderer_lifecycle: not_applicable
budgets:
  max_wall_time_seconds: 30
  max_model_calls: 0
cases:
  - case_id: valid-pink-gel
    fixture_id: golden-pink-gel-frag-v1
    expected:
      outcome: success
      invariants: [schema_valid, static_validation_valid]
  - case_id: reject-texture2d
    fixture_id: invalid-texture2d-frag-v1
    expected:
      outcome: rejected
      violation_codes: [texture_sampling_forbidden]
gate:
  thresholds:
    invariant_pass_rate: 1.0
    p95_duration_ms_max: 100
```

约束：

- `target.kind` 只能是 `node | capability | scenario | pipeline | transport`；
- `fixture_id` 必须来自版本化 Fixture Registry，并在冻结 config 中记录 SHA-256；
- Renderer benchmark 必须声明 `cold_per_case | warm_per_suite`，二者报告不可直接混比；
- 性能 benchmark 默认 `concurrency=1`，并单独记录 warmup，不把 warmup 纳入统计；
- real 模式必须冻结 role/model、Prompt、thinking、response format、调用预算和 token/费用预算；
- gate 阈值必须在运行前冻结，报告同时保留未门禁的原始指标；
- suite 可引用现有 `benchmarks/png_to_shader_v1/manifest.yaml` 的 case 和 hash，不复制或改写 M5 固定参考图。

## 10. 通用响应契约

```json
{
  "schema_version": "node_lab_execution_response_v1",
  "pipeline_id": "png_to_shader_v1",
  "lab_run_id": "...",
  "step_id": "...",
  "base_step_id": "...",
  "suite_run_id": null,
  "case_id": null,
  "attempt_id": null,
  "node_id": "decide_after_render",
  "execution_fingerprint": "64 位 SHA-256",
  "execution_status": "completed",
  "outcome": "success",
  "phase": "routing_after_render",
  "inputs_resolved": {},
  "outputs": {},
  "state_diff": {
    "set": {},
    "removed": []
  },
  "artifacts": [],
  "diagnostics": [],
  "provenance": {},
  "environment": {
    "source_fingerprint": "...",
    "worktree_dirty": true,
    "python_version": "...",
    "platform": "...",
    "renderer_profile": "not_applicable"
  },
  "usage": {
    "model_calls": 0,
    "input_tokens": null,
    "output_tokens": null,
    "model_latency_ms": 0,
    "renderer_duration_ms": 0,
    "duration_ms": 12.4
  },
  "next_actions": ["select_current_best"]
}
```

### 10.1 `execution_status`

- `completed`：节点代码按契约执行完成；业务结果可以是拒绝或停止。
- `blocked`：输入 Schema 合法，但缺少必要的前序证据或副作用权限。
- `failed`：Renderer、Gateway、Store 或内部不变量导致该步未形成有效输出。

### 10.2 `outcome`

- `success`：业务能力成功。
- `rejected`：Validator/Selector 等正常拒绝，不是 HTTP 失败。
- `stopped`：预算、取消或停止条件导致确定性终止。
- `degraded`：例如 Memory 读取失败但 Context 仍可构造。
- `skipped`：节点按业务规则无需执行。

静态校验不通过、候选未晋级和路由决定 finalize 均应返回 HTTP 200 与结构化 outcome；只有请求边界、依赖不可用或内部不变量失败使用非 2xx。

手动单步执行的 suite/case/attempt 字段为 `null`；batch 中必须完整填写。`execution_fingerprint` 必须覆盖规范化请求、所有输入 Artifact hash、fixture、contract/Prompt/metric 版本、依赖配置和相关源码 fingerprint，不能只记录 Git commit。工作区存在未提交修改时必须显式 `worktree_dirty=true`，报告比较不得把不同 source fingerprint 的结果标成严格同版本回归。

## 11. Node Descriptor

每个节点 descriptor 至少包含：

```json
{
  "node_id": "visual_analysis",
  "display_name": "视觉结构分析",
  "category": "model_role",
  "description": "...",
  "execution_kinds": ["lab_run_step", "friendly_role"],
  "determinism": "model",
  "supports_batch": true,
  "test_profiles": ["unit", "integration"],
  "benchmark_profiles": ["node", "scenario"],
  "default_fixture_ids": ["visual-analysis-success-v1"],
  "benchmark_metrics": ["schema_pass", "latency_ms", "token_usage"],
  "cold_start_sensitive": false,
  "requires_browser": false,
  "requires_model": true,
  "input_schema": {},
  "output_schema": {},
  "required_state_fields": [],
  "produced_state_fields": [],
  "artifact_reads": [],
  "artifact_writes": [],
  "side_effects": ["model_call"],
  "max_model_calls_per_execution": 2,
  "success_invariants": [],
  "expected_outcomes": [],
  "error_codes": [],
  "source_refs": []
}
```

目录必须由显式 Registry 维护，不通过反射自动开放新增函数。任何新 Graph 节点只有在 descriptor、Adapter 和测试同时存在时才可进入 Lab allowlist。

自动化调用者可以按 `category`、`determinism`、`supports_batch`、`requires_browser`、`requires_model` 和 `benchmark_profiles` 过滤目录。fixture、metric 和 prerequisite 都使用稳定 id，不能要求 Agent 解析中文描述后猜测如何调用。

## 12. 20 个图节点目录

| # | `node_id` | 类型 | 主要输入 | 主要输出 / state diff | 依赖与副作用 | 判断正确的核心条件 |
|---:|---|---|---|---|---|---|
| 1 | `initialize_run` | 确定性 + Artifact 写 | `project_id`、原图、质量档、可选 budget/acceptance/instruction | 规范化 PNG、冻结的 contract/policy、run id、全部初始计数器 | `normalize_target_png`、Lab Artifact Store、clock；写 source/reference/config | 自定义预算不超过 high 上限；reference 可解码；config 与输出一致 |
| 2 | `prepare_context` | 只读 Memory + 确定性 | `project_id`、当前状态摘要、Memory status | `context_pack`、`selected_memory_ids`、Memory status、事件 | LangGraph Store、GSSC Builder；只读项目 Memory | 同项目隔离；Memory 失败时明确 `degraded`；token 预算可解释 |
| 3 | `measure_target` | 确定性 + Artifact 写 | 规范化 reference PNG | `TargetMeasurements`、measurement Artifact、事件 | `measure_target`、Lab Artifact Store | `image_sha256` 匹配；尺寸、palette、probe、edge、ROI 均可序列化 |
| 4 | `visual_analysis` | 有界模型节点 | reference、measurements、render contract、instruction、Context、预算 | 严格 `VisualAnalysis`、实际模型身份、模型审计、计数和事件 | Prompt、Gateway、Parser、一次受限修复；真实模式产生费用 | 版本正确；未知字段拒绝；预算和 timeout cap 生效；不输出 GLSL |
| 5 | `persist_visual_analysis` | Artifact 写 | 已通过 Parser 的 `VisualAnalysis` | analysis Artifact ref、phase、事件 | Lab Artifact Store | 写入内容与结构化输出 hash 一致；非法分析不能持久化 |
| 6 | `author_initial` | 有界模型节点 | reference、measurements、VisualAnalysis、instruction、Context、预算 | 完整 `ShaderAuthorResult`、GLSL、Candidate provenance、模型审计 | Initial Prompt、Gateway、Parser、本地固定绑定修复 | mode/version 正确；GLSL 完整且无 fence；provenance 的 GLSL hash 匹配 |
| 7 | `materialize_candidate` | 确定性 + Artifact 写 | 模型 Author 或确定性 seed、GLSL、provenance、candidate sequence、父候选上下文 | 新 `candidate_id`、`CandidateRecord`、GLSL/Author/provenance/manifest | Lab Artifact Store | candidate sequence 单调；模型 parent 规则正确；确定性 seed 保持独立 root/origin/generator_version；所有 Artifact hash 与 manifest 一致 |
| 8 | `render_and_evaluate` | 确定性 + 浏览器 + Artifact 写 | Candidate、GLSL、reference、measurements、budget、clock | Validation、Compile、Render、Score、更新后的 Candidate、事件和 stop reason | Validator、受限 smoothstep 修复、WebGL1 Renderer、Oracle、Store | 静态失败不启动 Renderer；无陈旧帧；图片尺寸一致；metrics 使用 API object 形态 |
| 9 | `decide_after_render` | 纯路由 | `render_status`、cancelled、stop reason、model/repair 计数和 budget | `next_action=select|compile_repair|finalize`、可选 stop reason | 纯函数，无副作用 | 固定优先级与预算边界完全匹配生产路由 |
| 10 | `prepare_compile_repair` | 纯状态转换 | 当前 Author 结果、compile repair 计数和 budget | `previous_author_result`、`repair_budget`、phase | 纯函数，无副作用 | remaining 不小于 0；previous 结果未被修改 |
| 11 | `author_compile_repair` | 有界模型节点 | 旧 Author、旧 GLSL、静态/WebGL diagnostics、repair budget、Context | 新 Author、完整 GLSL、provenance、模型审计、repair 计数 | Compile-repair Prompt、Gateway、Parser、scope guard | mode 正确；诊断绑定；禁止越界视觉重写；预算计数正确 |
| 12 | `select_current_best` | 确定性 + Artifact 写 | candidate、可选 current best、AcceptancePolicy | `CurrentBestDecision`、可选 best 更新、no-improvement 计数、selection Artifact | `select_current_best`、Lab Artifact Store | 只有硬约束、score、最小改善和保护证据全部满足才晋级 |
| 13 | `prepare_measurement_seed` | 确定性 + Artifact 写 | 规范化 reference PNG、与其绑定的 `TargetMeasurements`、attempted 标记 | 确定性 Author、完整 GLSL、provenance、origin/generator version、事件 | `build_measurement_affine_seed`、Lab Artifact Store | 只允许一次；测量 hash 必须匹配 reference；输出不泄漏完整 GLSL；后续 materialize 必须形成独立 root |
| 14 | `decide_after_selection` | 纯路由 | cancelled、stop reason、best loss、无改善/视觉/模型计数、policy/budget | `next_action=visual_critic|finalize`、stop reason | 纯函数，无副作用 | 质量阈值、停滞、视觉预算、两次模型调用余量按固定优先级判断 |
| 15 | `load_current_best` | Artifact 读 + 完整性检查 | current best record、LabRun Artifact | GLSL、render、score、residual、Candidate 输入摘要和 evidence binding | Lab Artifact Store、SHA-256 | 只读 best；GLSL/render hash 必须匹配；缺失 score/render 明确失败 |
| 16 | `visual_critic` | 有界模型节点 | reference/render、GLSL、candidate/binding、measurements、analysis、score、Context | 严格 `VisualReview`、模型身份和审计 | Critic Prompt、Gateway、Parser、绑定校验 | candidate、GLSL、render 三方 hash 一致；Critic 不输出 GLSL；continue 必须可执行 |
| 17 | `persist_visual_review` | Artifact 写 | current best、VisualReview | 更新后的 CandidateRecord、review Artifact、事件 | Lab Artifact Store | review candidate id 必须等于 current best；manifest 原子更新 |
| 18 | `author_visual_refine` | 有界模型节点 | current best、review、binding、reference/render、GLSL、score/residual、Context | 新 Author、完整 GLSL、provenance、模型审计、visual refinement 计数 | Visual-refine Prompt、Gateway、Parser、绑定校验 | base candidate/domain/protected regions 正确；旧 best 不被原地覆盖 |
| 19 | `finalize` | Artifact 读写 + Renderer 清理 | best 或最近 WebGL-valid fallback、stop reason、计数、measurements、clock | `final_result`、final GLSL/render/metrics/manifest、事件 | Lab Artifact Store、hash 校验、Renderer lifecycle | 只从 best/fallback Artifact 读取；已评分结果必须有 metrics；允许明确失败结果 |
| 20 | `promote_validated_strategy` | Memory 副作用 | current best、final result、Author Artifact、project/run id、Memory Store | Memory preview 或 promotion event、Memory status | Agent Memory Store；V1.0 默认 preview | 只有成功、已评分、通过硬门禁且有 render/metrics 的 best 才能形成 promotion payload |

## 13. `render_and_evaluate` 的 Lab 拆分

生产图保持一个节点，避免改变现有 Graph；Lab 额外提供以下独立能力：

### 13.1 `validate-shader`

输入：GLSL、可选 `max_shader_chars`，上限不得超过 high preset。

输出：`valid`、`source_chars`、`contract_id`、完整 violation 列表、error/warning 分组。静态无效属于正常 `rejected` outcome。

### 13.2 `render-shader`

输入：已通过或用户明确选择继续测试的 GLSL、width、height、renderer replay 上限。

输出：CompileResult、Renderer metadata、duration、PNG Artifact、PNG SHA-256。每次独立调用创建并关闭自己的 browser/page 生命周期，因此像素应与生产契约一致，但启动耗时不能直接与生产图的复用 Renderer 性能比较。

默认不允许跳过静态 Validator。若为了学习编译器错误显式设置 `allow_static_invalid=true`，响应必须标记 `non_production_path=true`，不得生成可用于 Selector 的 CandidateRecord。

### 13.3 `evaluate-render`

输入：reference PNG、render PNG、`TargetMeasurements`、可选 MetricWeights。

输出：完整 `ScoreBreakdownV1` API 形态。`roi_losses`、`protected_region_losses` 和 `effective_weights` 必须始终是 JSON object，不允许 pair-list 泄漏到 API。

尺寸不一致属于输入契约失败，不静默 resize。

## 14. 模型节点的教学与费用控制

### 14.1 Prompt preview

`preview_only=true` 至少返回：

- role、mode、Prompt id/version/SHA-256；
- System Prompt 文本；
- HumanMessage 中的 section 名和每段结构化摘要；
- 输出 JSON Schema；
- reference/render 图片的 artifact id、SHA-256、content type 和尺寸；
- ContextPack token 估算和选中 Memory id；
- 本阶段 timeout cap、剩余 wall-time、剩余模型调用数和是否允许 JSON repair。

不返回图片 base64、供应商 reasoning、密钥或原始 Memory 正文。

### 14.2 Fixture

Fixture 必须：

- 有版本号和 SHA-256；
- 绑定具体 Prompt version、角色和 mode；
- 同时包含至少一个成功样例和典型失败样例；
- 经过真实 Parser，不允许直接注入已解析对象绕过 Parser。

### 14.3 Mock

用户上传模型原始文本或 JSON Artifact，Gateway 不被调用；Adapter 把内容交给真实 structured-output Parser。本模式用于理解重复 key、未知字段、错误版本、候选绑定、JSON repair 和本地受限归一化。

### 14.4 Real

真实模型调用必须同时满足：

1. 服务端 `SHADERGEN_NODE_LAB_REAL_MODEL_ENABLED=true`；
2. 请求 `execution_mode=real`；
3. 请求 `allow_model_call=true`；
4. 本 LabRun 的剩余调用和 token/时间预算足够；
5. 节点 descriptor 声明允许真实模型。

任一条件不满足返回 `403 real_model_not_allowed`，不能静默退回 fixture，也不能自动调用其它模型。

### 14.5 模型 benchmark 额外规则

- 普通单元/集成测试只能使用 fixture/mock/Fake Gateway；
- AI-on suite 必须按 manifest 冻结 requested/actual model 记录规则、Prompt、thinking、response format 和最大修复次数；
- 报告分别统计业务语义调用与 JSON repair，不能只给总调用数；
- 记录 parse pass rate、schema issue 分类、绑定失败率、timeout、tokens、模型延迟和费用估算；
- 不把输出文本完全相同作为模型节点正确性的必要条件，优先检查严格 Schema、角色边界、证据绑定和下游 Validator/Oracle 指标；
- 同一 case 的多次 real 重复运行必须保留独立 attempt，不选择性丢弃失败输出；
- Node Lab AI-on 结果只作为模块诊断，不替代 M5 initial/final、pink-gel gate 和人工盲评。

## 15. 快照与 Artifact 布局

建议的 Lab 专用布局：

```text
output/node-lab/{lab_run_id}/
├── run.json
├── uploads/
│   └── {artifact_id}/payload
├── steps/
│   └── {step_id}/
│       ├── request.json
│       ├── response.json
│       ├── state-before.json
│       ├── state-after.json
│       └── artifacts/
└── indexes/
    ├── steps.json
    └── artifacts.json

output/benchmarks/node-lab/{suite_run_id}/
├── config.json
├── manifest.snapshot.yaml
├── environment.json
├── cases/{case_id}/attempts/{attempt_id}/
│   ├── execution.json
│   └── artifacts/
├── report.json
├── report.md
└── comparison-inputs.json
```

规则：

- 所有写入使用临时文件、`fsync` 和原子 replace；
- `artifact_id` 到相对路径的映射只由服务端维护；
- 拒绝绝对路径、`..`、symlink 逃逸和跨 LabRun id 读取；
- State 快照不内嵌图片、完整 GLSL、完整 compiler log 或模型原始响应；
- 大对象只保存 `artifact_id`、SHA-256、字节数和 content type；
- 结构化对象必须用显式 `to_dict()`/API adapter，不能对领域 dataclass 直接 `asdict()` 后假定 JSON 形态稳定；
- 步骤写入失败不得生成半完成的 head；
- 导入产品 run 时复制到 Lab root 或保存受控只读引用，不能修改原 Artifact。
- benchmark config、manifest snapshot 和 environment 必须在首个 case 前写入；
- 同一 `suite_run_id` 只允许在 config hash 完全一致时恢复，已完成 attempt 不覆盖；
- 聚合报告引用逐 attempt 文件，不把失败或中断 case 从分母中删除。

### 15.1 Benchmark 证据与指标

所有 profile 都先报告 correctness，再报告性能或视觉质量：

| profile | 主要指标 |
|---|---|
| `micro` | Schema/invariant pass、输出 hash 稳定性、单函数 duration |
| `node` | outcome 分布、state diff、Artifact 完整性、节点 duration、依赖调用计数 |
| `scenario` | 多节点成功率、停止原因、current_best 单调性、每阶段耗时 |
| `pipeline` | compile/render 成功率、initial-final loss、best 更新、总模型调用/token/费用 |
| `transport` | Application API 与 HTTP JSON 等价、状态码、上传/下载和序列化耗时 |

节点专项至少包括：

- TargetMeasurements：固定图片输出 hash、尺寸和 ROI 稳定性；
- Validator：预期 valid/rejected、violation code 精确率和耗时；
- Renderer：compile/render 成功率、cold/warm profile、像素 hash/RMSE、环境元数据；
- Oracle：各 loss 分量、总分、尺寸错误和重复计算稳定性；
- Selector/Router：给定 fixture 的决定准确率与原因分布；
- 模型角色：Parser pass、结构化修复率、绑定/越权错误、tokens、延迟和下游可用率；
- Artifact/Finalize：hash/引用完整性、旧 pair-list 等兼容边界、无假成功；
- 完整 Graph：candidate 数、best 更新、stop reason、质量改善和失败阶段。

聚合报告必须同时包含原始计数、分母、p50/p95/max、失败 case 列表和 Artifact 引用。样本数不足时不得输出具有误导性的 percentile；环境或 source fingerprint 不一致时，comparison 标记为 `non_comparable`，而不是给出回归结论。

## 16. 错误语义

错误响应沿用 FastAPI `detail` envelope，并增加 Lab 定位字段：

```json
{
  "detail": {
    "message": "缺少 visual_analysis 前置输出。",
    "code": "node_prerequisite_missing",
    "lab_run_id": "...",
    "step_id": null,
    "node_id": "author_initial",
    "stage": "input_resolution",
    "retryable": false,
    "missing_fields": ["visual_analysis"]
  }
}
```

| HTTP | `code` | 场景 |
|---:|---|---|
| 400 | `unsupported_artifact_type` | 上传类型不受支持 |
| 403 | `real_model_not_allowed` | 真实模型双重开关未满足 |
| 403 | `effect_not_allowed` | 请求 `project_commit` 或越权副作用 |
| 404 | `node_not_found` | 节点不在 allowlist |
| 404 | `lab_run_not_found` | LabRun 不存在 |
| 404 | `artifact_not_found` | Artifact id 不存在或不属于该 LabRun |
| 409 | `node_prerequisite_missing` | 缺少前序证据或类型化状态 |
| 409 | `artifact_integrity_failed` | hash、candidate 或 evidence binding 不一致 |
| 413 | `artifact_too_large` | 图片、GLSL 或 fixture 超过限制 |
| 422 | `input_contract_invalid` | 节点输入 Schema 不合法 |
| 422 | `mock_response_invalid` | mock 内容无法通过目标角色 Parser；完整诊断放在响应，不泄漏敏感原文 |
| 500 | `internal_invariant_failed` | Adapter 或节点违反内部不变量 |
| 502 | `model_response_failed` | 真实供应商响应无法满足结构化契约 |
| 503 | `renderer_unavailable` | Chromium/WebGL worker 不可用 |
| 503 | `memory_unavailable` | Context preview 需要的 Store 不可用且请求要求严格模式 |
| 504 | `model_timeout` | 模型阶段超时 |
| 504 | `renderer_timeout` | 渲染阶段超时 |

Provider 原始异常、数据库异常和 filesystem 路径只进入受控内部日志，不进入 HTTP body。

## 17. 安全、隐私与成本

- Node Lab 默认不注册路由：`SHADERGEN_NODE_LAB_ENABLED=false`。
- 第一版只支持本地开发；没有鉴权时不得在公网环境启用。
- 真实模型默认关闭；普通测试、`make check` 和文档检查永不调用模型。
- 用户上传图片沿用产品 8MB 上限；GLSL、JSON fixture 和步骤总量设置独立硬上限。
- 不允许通过 Node Lab 读取任意产品 Artifact；只允许白名单导入。
- 不返回 reasoning；Prompt preview 不等于模型思维链。
- 普通事件只保存安全错误码、字段路径、hash、耗时和用量。
- 完整 GLSL、图片、mock 原始响应和 compiler 原文只作为私有 Lab Artifact。
- LabRun 累计记录模型调用数、input/output tokens、模型耗时和估算费用；超预算拒绝继续真实调用。
- `project_id` 只用于同项目 Context/Memory 隔离；不同项目数据不得引用。
- batch 对 case 数、repetitions、concurrency、Artifact 总字节数和总 wall-time 设置硬上限；HTTP batch 禁止 real 模式。
- CLI AI-on 在首个调用前冻结全局与逐 case 预算，已消耗调用从持久证据计算，恢复时不得重置计数。
- 自动化判断只能依赖稳定 code、enum、数字和 JSON pointer；中文 `message` 只用于人读，不作为测试断言主键。
- 失败 benchmark Artifact 默认保留，任何清理命令必须独立、显式并拒绝删除仍被 baseline/report 引用的 suite。

## 18. 建议的使用流程

### 18.1 第一轮：完全无模型

1. 创建 LabRun。
2. 上传一张 benchmark PNG。
3. 执行 `initialize_run`，观察图片规范化、contract 和预算冻结。
4. 执行 `measure_target`，逐项理解 bbox、palette、probe、edge 和 ROI。
5. 手动提交一段 GLSL 到 `validate-shader`，分别测试合法、纹理采样、WebGL2、倒序 smoothstep 和无界循环。
6. 对合法 GLSL 执行 `render-shader`，检查 compile、metadata、PNG 和 hash。
7. 执行 `evaluate-render`，观察 global、edge、geometry、probe、ROI 和 protection loss。
8. 构造两个 CandidateRecord，执行 `select-current-best`，理解最小改善和保护区退化规则。
9. 手动修改 budget/counter，执行两个路由接口，理解每种 stop reason。

### 18.2 第二轮：Fixture 与 Mock

1. 用 fixture 执行 `visual_analysis`，阅读 Prompt preview 和严格输出。
2. 修改一个 ROI purpose 为未知值，观察 Parser/repair 行为。
3. 用 fixture 执行 `author_initial`，检查 GLSL/provenance/hash。
4. 让 GLSL 编译失败，依次执行 prepare repair 和 mock compile-repair Author。
5. 构造 reference/render/binding，执行 Critic 和 visual-refine。

### 18.3 第三轮：显式真实模型

1. 先用 `preview_only` 查看将要发送的内容、预算和最大调用数。
2. 只运行一个角色，不直接启动完整闭环。
3. 比较 fixture、mock 和 real 的结构化输出、耗时、token 和错误。
4. 确认单节点行为后，再通过现有产品 API 运行完整 `procedural_v1`。

### 18.4 Agent/Codex 模块化测试

1. 通过 `describe_nodes()` 查询目标节点的 Schema、fixture、前置条件和指标。
2. 使用 Application API 和临时 Lab root 执行 deterministic/fixture case，不启动 Backend。
3. 读取结构化 `execution_status`、`outcome`、diagnostics、state diff 和 Artifact hash，不解析终端日志。
4. 对失败节点从同一 `base_step_id` 注入最小 override 重试，保留原失败步骤。
5. 需要验证 HTTP 时只增加 transport profile，比较 Application API 与 HTTP 响应的规范化 JSON。

### 18.5 版本化 benchmark

1. 先执行 `validate_suite()`，冻结 manifest、fixture、gate、依赖与 source fingerprint。
2. 运行 AI-off micro/node/scenario suite，确认 correctness 后再观察性能。
3. Renderer 分别运行 cold/warm profile，不混合统计。
4. 如需模型，使用新的 suite-run-id、显式 `--allow-model-calls` 和整套预算。
5. 聚合 report 后与独立 baseline SHA 比较；环境不同则标记不可严格比较。
6. 模块 benchmark 通过后仍需运行原 M5 固定 10 例和人工盲评，不能替代发布 gate。

## 19. 代码落点与当前状态

```text
backend/app/
├── api/routes/node_lab.py        # 阶段 B 已创建，默认关闭
├── schemas/node_lab.py           # 阶段 B 已创建
└── services/node_lab.py          # 阶段 B 已创建

src/agent/app/
├── lab/
│   ├── registry.py               # 阶段 A 已创建
│   ├── adapters.py               # 阶段 B.1 已创建确定性切片，阶段 C 扩充
│   ├── fixtures.py               # 阶段 A 已创建，后续扩充 fixture
│   ├── models.py                 # 阶段 A 已创建
│   ├── runner.py                 # 阶段 A 已创建
│   ├── benchmark.py              # 阶段 B 已创建 scenario/warm/中断恢复 runner
│   ├── suites.py                 # 阶段 B 已创建 HTTP suite allowlist
│   └── store.py                  # 阶段 A 已创建
└── services/
    ├── node_lab.py               # 公共 Application API 与 Executor 装配
    ├── node_lab_deterministic.py # 阶段 C 确定性节点 Adapter
    ├── node_lab_model.py         # 阶段 C 模型角色 Executor
    └── node_lab_model_benchmark.py # 阶段 C 独立模型 runner

benchmarks/node_lab/png_to_shader_v1/
├── manifest.yaml
├── scenario-manifest.yaml
├── renderer-warm-manifest.yaml
├── model-manifest.yaml            # 阶段 C 已创建
└── fixtures/

scripts/
├── run_node_lab_benchmark.py     # 阶段 B 已创建
├── run_node_lab_transport_benchmark.py # 阶段 B 已创建
└── run_node_lab_model_benchmark.py # 阶段 C 已创建

tests/
├── unit_tests/
│   ├── test_node_lab_registry.py # 阶段 A 已创建
│   ├── test_node_lab_schemas.py  # 阶段 A 已创建
│   ├── test_node_lab_adapters.py
│   ├── test_node_lab_runner.py   # 阶段 A 已创建
│   ├── test_node_lab_benchmark.py
│   ├── test_node_lab_stage_c_adapters.py
│   ├── test_node_lab_model_adapters.py
│   ├── test_node_lab_model_benchmark.py
│   └── test_node_lab_api.py
└── integration_tests/
    ├── test_node_lab_step_flow.py
    ├── test_node_lab_benchmark_flow.py # 阶段 B 已创建
    └── test_node_lab_stage_c_flow.py   # 阶段 C 已创建
```

阶段 B 已创建 Backend Route/Schema/Service、CLI、八例 capability manifest、五步 scenario、cold/warm Renderer 与 transport report，并从 `shaderforge.public` 接入确定性能力。阶段 C 已接通全部 20 节点、模型 preview/fixture/mock/real、只读 Context/Memory preview 与独立模型 benchmark。阶段 D 又补齐真实 node/pipeline target、逐节点 CLI、descriptor 示例、失败 Fixture、HTTP DAG/Artifact 目录和浏览器工作台；仍不写产品 run 或真实项目 Memory。

### 19.1 阶段 A 已实现证据

- Python Application API 可列出 20 个节点、创建 LabRun、执行 Fixture 步骤、按 `base_step_id` 分支、重启后读取步骤，以及上传/读取不透明 Artifact。
- Registry 与生产 Graph 节点集合逐名一致；每个 descriptor 已包含 Schema、测试/benchmark profile、metrics、模型/浏览器依赖和源码引用。
- Fixture 带版本与内容 SHA-256；阶段 A 只提供 `decide_after_render` 的最小 AI-off 成功 Fixture，用于验证 Harness，当时不代表其余节点已可执行。
- 步骤证据先写 request/response/state 文件，最后提交索引；失败 Executor 也保存安全错误类型，但不保存原始异常文本。
- 阶段 A 单独交付时不是 Node Lab API v1 完成态；其中确定性 Adapter、AI-off suite/CLI 和 HTTP/Swagger 已由阶段 B.1 覆盖，每节点 Fixture 及其余完整验收项仍后置。

### 19.2 阶段 B.1 已实现证据

- 八个确定性 capability 可由同一 Application API 执行；阶段 B.1 当时只接通 `measure_target`、`render_and_evaluate`、`decide_after_render`、`select_current_best` 和 `decide_after_selection`，其余节点当时明确为 `planned`，现已由阶段 C 补齐。
- 固定八例 manifest 覆盖 normalize、measure、Validator 正反例、真实 Renderer cold、Oracle、Selector 和 routing；runner 冻结 manifest/相关生产源码/依赖版本/环境 hash，把输入输出 Artifact 复制进逐 attempt 目录并复核 hash，保留失败分母、精确恢复和 comparison。
- `/api/lab/v1/*` 默认不注册；显式 `SHADERGEN_NODE_LAB_ENABLED=true` 后提供 discovery、LabRun、步骤、capability 和同 LabRun Artifact 上传/下载，TestClient 验证 HTTP 与 Application API 语义一致。
- 真实 Chromium AI-off smoke 已覆盖普通 Renderer cold 生命周期；阶段 B.1 当时尚未实现 scenario、transport、warm、HTTP batch 与失败中断证据。

### 19.3 阶段 B.2 已实现证据与剩余边界

- 五步 `normalize -> measure -> validate -> render -> evaluate` scenario 使用只指向先前步骤响应的 JSON Pointer binding；每步响应和输入/输出 Artifact 都复制进 attempt 并复核 hash。
- `renderer_warm` 使用独立 20 次 measured suite 和一次 warmup；整个 suite 只启动一个 Renderer，warmup 不进入 p50/p95/max，恢复时追加 rewarmup，cold/warm 不混报。
- `CancelledError` 或 `KeyboardInterrupt` 在退出前写独立 interruption JSON；恢复不覆盖 interruption，且中断继续计入失败分母。只有完整 `execution.json` 被视为已完成 attempt。
- transport profile 独立对照 Application API 与 HTTP 上传、执行、下载，规范化排除不透明 id/时间后比较领域响应，并按 direct/HTTP 分段记录 p50/p95/max；transport 时钟不写回 capability `duration_ms`。
- HTTP batch 仅允许三个固定 AI-off suite，同步返回和读取 report；未知 suite、任意 manifest 路径和真实模型入口均 fail closed。自动集成测试贯通 HTTP、Backend service、Agent Application API、Renderer Adapter、ShaderForge 与自包含证据。
- 阶段 B 已完成。其后阶段 C 已接通模型路径、其余节点、Context/Memory preview 与独立模型 benchmark；阶段 D 已补齐 node/pipeline target 和前端工作台。

### 19.4 阶段 C–D 已实现证据与 M5 取舍

- 20 个节点 descriptor 全部为 `available`；exact Executor 注册覆盖 15 个 deterministic 和五个模型节点的 fixture/mock/real，输入/输出必需字段由 runner 前后校验。
- `prepare_measurement_seed` 复用生产 `build_measurement_affine_seed`，从规范化 reference 与已绑定测量生成私有 Author/GLSL/provenance Artifact；`materialize_candidate` 验证 deterministic binding，并保持 `parent_candidate_id=null`、`origin=deterministic` 与 `generator_version`。
- 模型 preview 不调用 Gateway，也不返回 base64、Memory 正文或 reasoning；fixture/mock 经过生产 Parser，real 在 step 分配前执行双开关和 Gateway 门禁。
- Context 使用只读 MemoryReader，完整 ContextPack 进入私有 Artifact；`promote_validated_strategy` 只返回 preview，任何 `project_commit` 都在副作用前拒绝。
- 独立模型 runner 默认 fixture 5/5 离线，通过固定 manifest 冻结图片、Fixture、Prompt、requested model、价格版本和整套硬预算；real 额外要求显式 CLI/环境/Gateway。`max_output_tokens` 下推 provider，调用前预留 token/cost/wall 预算；中断单独落盘、恢复不重置累计用量，失败与历史中断都保留在分母。报告按五角色分别聚合 Parser/Schema/binding/timeout、model latency、token、费用与 requested/actual model；样本不足 20 时 p95 为 `null`。
- 20 个 descriptor 均有机器可读输入示例；五个模型角色另有 parser-rejected Fixture。HTTP/OpenAPI 提供命名示例、完整步骤读取、DAG 摘要和 Artifact descriptor 列表；逐节点 CLI 直接调用公共 Application API，并以稳定单行 JSON/退出码服务自动化。
- deterministic runner 的 `node` target 真实调用 `execute_step()`；固定 `pipeline` profile 用真实 `base_step_id` 映射串联和分支 production Adapter。source fingerprint 纳入 deterministic service、production nodes、Prompt 与 Parser，避免模块漂移未被比较发现。
- 阶段 D 保留 M5 runner/manifest/gate/盲评独立，防止既有发布证据语义漂移；同时交付 `/lab` 工作台：左侧 descriptor 目录、中间 JSON/模式/父步骤编辑、右侧 Output/State Diff/诊断、Artifact 区和底部不可变 DAG。页面只消费 API/descriptor，不复制节点规则。

## 20. 测试设计

### 20.1 Registry 与 Schema

- 精确登记 20 个节点，没有缺失或额外节点；
- 每个 descriptor 都有输入/输出 Schema、前置条件、副作用、错误码和源码引用；
- Graph 新增或删除节点时，Registry 一致性测试失败；
- 任意未知 `node_id` 返回 404，不能通过反射执行。

### 20.2 确定性能力

- normalize/measure 的输出与固定 PNG golden 一致；
- Validator 覆盖合法 Shader 和所有关键违规；
- Renderer 覆盖成功、compile failure、worker unavailable、无陈旧帧；
- Oracle 覆盖尺寸不一致和 API object 形态；
- Selector 与生产纯函数结果逐字段一致；
- Routing 与图的条件边映射一致。

### 20.3 模型节点

- fixture/mock 使用 Fake Gateway 或直接 mock raw output，不连接外网；
- Prompt preview 证明没有 Gateway 调用；
- real 模式在任一显式开关缺失时均返回 403；
- Parser、本地归一化、最多一次 JSON repair、预算和 timeout cap 与生产节点一致；
- 响应中不出现 reasoning、key、base URL 或供应商原始异常。

### 20.4 步骤 DAG 与 Artifact

- 同一父步骤可以产生两个分支，父快照不变；
- `step_output` 引用严格校验 LabRun、step 和 JSON pointer；
- 跨 LabRun Artifact 引用失败；
- hash 不一致阻止 Critic、Refine、Selector 和 finalize；
- 原子写入失败不产生半完成步骤；
- 服务重启后能从 Lab 自有快照恢复可序列化实验历史，不依赖 `UntrackedValue` checkpoint。

### 20.5 三种入口一致性

- Application API 是主行为测试面，不依赖 FastAPI 或 CLI；
- HTTP TestClient 对同一 request 返回与 Application API 等价的规范化 response；
- CLI 对同一 manifest 生成相同 config/case/report schema 和 hash 引用；
- transport profile 单独统计上传、序列化和下载，不污染节点执行 duration；
- HTTP/CLI 不得拥有 Application API 中不存在的修复、默认值或模型回退逻辑。

### 20.6 Benchmark runner

- manifest、fixture、gate 和 source fingerprint 在首个 case 前冻结；
- micro/node/scenario/pipeline/transport profile 路由正确；
- warmup 不进入统计，cold/warm Renderer 不混比；
- 每个 attempt 原子保存，失败和中断进入报告分母；
- 恢复时 config hash 漂移明确拒绝；
- baseline/candidate fingerprint 不兼容时输出 `non_comparable`；
- AI-on 缺少任何显式开关或预算时在调用 Gateway 前失败；
- Node Lab report 不修改或覆盖 M5 report、gate 或人工评审文件。

### 20.7 建议验证命令

实现阶段至少运行：

```bash
uv run pytest tests/unit_tests/test_node_lab_registry.py
uv run pytest tests/unit_tests/test_node_lab_schemas.py
uv run pytest tests/unit_tests/test_node_lab_adapters.py
uv run pytest tests/unit_tests/test_node_lab_runner.py
uv run pytest tests/unit_tests/test_node_lab_benchmark.py
uv run pytest tests/unit_tests/test_node_lab_api.py
uv run pytest tests/integration_tests/test_node_lab_step_flow.py
uv run pytest tests/integration_tests/test_node_lab_benchmark_flow.py
uv run python scripts/run_node_lab_benchmark.py \
  --manifest benchmarks/node_lab/png_to_shader_v1/manifest.yaml
uv run ruff check backend src/agent src/shaderforge tests
uv run mypy --strict src/
make docs-check
make check
```

真实模型 smoke 不进入上述命令，必须有单独显式开关、固定 fixture 输入和硬预算。

## 21. 实施顺序

### 阶段 A：共享 Harness 内核

1. 建立 Node Registry、descriptor、请求/响应 Schema 和公共错误 envelope。
2. 建立可注入依赖的 Python Application API，先不依赖 FastAPI。
3. 建立 Fixture Registry、LabRun、Artifact 和不可变步骤快照。
4. 用 Application API 完成最小 deterministic/fixture 单元测试。

状态：已完成。当前以 Fixture 路径证明 Application API、快照、Artifact 和失败证据语义；真实确定性领域 Adapter 从阶段 B 开始接入。

### 阶段 B：确定性能力与 AI-off benchmark

1. 实现 normalize、measure、validate、render、evaluate、select 和 routing Adapter。
2. 实现 batch manifest、CLI runner、逐 attempt Artifact、report 和 comparison。
3. 建立 micro/node/scenario/transport profile 与 AI-off golden。
4. 在 Application API 稳定后添加 HTTP/Swagger 包装和 transport parity 测试。

状态：已完成。阶段 B.1 覆盖八个确定性 capability、五个节点 Adapter、micro/node/renderer_cold、CLI/report/comparison 和默认关闭的 HTTP/Swagger；阶段 B.2 补齐 scenario、transport、Renderer warm、HTTP batch、中断恢复和 benchmark-flow 集成检查。该阶段的所有路径保持 AI-off。

### 阶段 C：模型角色与全部 20 节点

1. 实现 Prompt preview、fixture 和 mock Parser 路径。
2. 接入三个模型角色 Adapter，并复用 bounded model 预算语义。
3. 补齐 persist/materialize/load/finalize，以 `base_step_id` 支持重试和分支。
4. 为 `promote_validated_strategy` 实现 preview；真实 project commit 继续关闭。
5. 最后增加双重开关保护的 real 模式和独立 AI-on 模块 benchmark。

状态：已完成。20 个节点全部接通；模型路径支持安全 preview、fixture、mock 和 fail-closed real；Context 只读、Memory 只 preview；独立模型 runner 默认离线并提供全预算 real gate。

### 阶段 D：M5 复用与可选前端

先评估现有 M5 runner 是否可以复用 Node Lab Application API/Adapter，保持原 manifest、gate、suite 输出和盲评语义。只有 API、CLI 和 benchmark 报告稳定后，再评估 Node Lab 页面：左侧节点目录、中间输入编辑器、右侧输出/Artifact/差异、底部步骤 DAG。前端不重新实现节点规则，只消费 OpenAPI 和 descriptor。

状态：已完成。M5 继续作为独立发布证据链，不改造冻结 manifest、gate、suite 输出或盲评语义；Node Lab 侧已补齐真实 node/pipeline benchmark、稳定 CLI/报告、机器可读示例和 `/lab` 工作台。

## 22. 验收标准

Node Lab API v1 只有同时满足以下条件才可视为工程实现完成：

1. Python Application API 不启动 Backend 也能描述节点、执行步骤和运行 suite。
2. HTTP API 默认关闭，关闭时不注册 `/api/lab/v1/*`。
3. Node Registry 精确覆盖生产图 20 个节点，并提供机器可读 fixture/metric/benchmark metadata。
4. 每个节点可通过 fixture、mock 或 deterministic 模式在无真实模型条件下完成至少一个成功/停止/拒绝样例。
5. Validator、Renderer 和 Oracle 可独立调用，且结果与生产公共能力一致。
6. Python、HTTP 和 CLI 对同一请求产生等价的规范化结果，transport 开销单独统计。
7. 每步返回 JSON-safe 输入摘要、输出、state diff、Artifact、diagnostics、provenance、usage、fingerprint 和 next action。
8. 重试或分支不会覆盖父步骤、产品 run 或产品 Artifact。
9. 大对象不进入 State JSON；Artifact id 无法逃逸到任意路径。
10. deterministic/fixture batch 可以从冻结 manifest 生成逐 attempt 证据、JSON/Markdown report 和 comparison。
11. benchmark 记录 source/environment/fixture/config hash，失败和中断保留在分母中，配置漂移禁止恢复。
12. Renderer cold/warm、模型语义/repair 调用和 transport duration 分开统计。
13. 模型 real 模式有环境和请求/CLI 双重显式开关、固定 manifest 与硬预算。
14. 不返回 reasoning、密钥、原始供应商异常或普通事件中的完整编译器原文。
15. `promote_validated_strategy` 在 V1.0 只 preview，不写真实项目 Memory。
16. Node Lab benchmark 不覆盖现有 M5 report、gate 或人工评审，模块通过不改变发布结论。
17. 所有聚焦测试、`make docs-check` 和 `make check` 通过。
18. `docs/FEATURES.md` 仍只保留 F09 一个 active；Node Lab 完成不改变 M5 no-go 质量结论。

## 23. 已采用的实现边界

实现全过程采用以下保守边界：

1. 第一版先交付 Python Application API、CLI 和 Swagger；在三者契约稳定后，阶段 D 新增只消费 descriptor/API 的 Node Lab 工作台，不复制节点规则或扩大产品权限。
2. `promote_validated_strategy` 在 V1.0 继续只允许 preview，不写真实项目 Memory；升级权限与清理语义需要后续独立决策。
