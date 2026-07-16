# Node Lab 使用与逐节点学习指南

Node Lab 是 PNG-to-Shader V1 的本地实验工作台。它让人工、Codex、测试和 benchmark 使用同一份节点契约、Fixture、Artifact 与不可变步骤语义；它不会替代产品 `/api/shader/*`、M5 固定 10 例门禁或人工盲评。

## 安全边界

- 普通 Backend 默认不注册 `/api/lab/v1/*`；只有 `make dev-node-lab` 显式开启。
- 默认执行 deterministic 或 fixture，不调用真实模型。
- `project_commit` 一律拒绝；交互式 LabRun 只写 `output/node-lab/` 下的私有证据，不写产品 run 或真实项目 Memory。模块 benchmark 另写 `output/benchmarks/node-lab*`，两者都不能覆盖 M5 证据。
- 图片、完整 GLSL、渲染图、ContextPack 和 mock 原文以同一 LabRun 内的不透明 Artifact ID 传递。
- Real 单步调用需要环境开关和当前请求/CLI 开关同时允许；它使用父 State 的 `BudgetPolicy`，缺失时采用 balanced 默认值。只有独立 real-model benchmark 额外冻结 suite manifest、整套 token/时间/费用预算和价格版本。

## 运行类型、预算与输出

| 场景 | 是否调用真实模型 | 显式门禁 | 预算范围 | 默认输出 |
|---|---|---|---|---|
| 浏览器、Swagger、CLI 的 deterministic/fixture/mock 单步 | 否 | Node Lab transport 必须显式开启 | descriptor 输入上限和 Lab 副作用门禁 | HTTP：`output/node-lab/http`；CLI：`output/node-lab/cli` |
| 浏览器、Swagger、CLI 的 ad-hoc real 单步 | 是 | `SHADERGEN_NODE_LAB_REAL_MODEL_ENABLED=true`，且请求 `allow_model_call=true` 或 CLI `--allow-model-call` | 父 State `BudgetPolicy`；缺失时 balanced。没有冻结 suite 总 token/费用预算 | 对应 LabRun 目录 |
| `make benchmark-node-lab-ai-off` | 否 | 无模型开关 | 固定 AI-off suite、attempt 与 Renderer/transport 预算 | `output/benchmarks/node-lab*` |
| `make benchmark-node-lab-model` | 否 | 固定 fixture | 固定五角色 manifest 和离线 attempt 预算 | `output/benchmarks/node-lab-model` |
| `run_node_lab_model_benchmark.py --execution-mode real` | 是 | 环境开关与 `--allow-model-calls` 同时提供 | 固定 manifest 下的调用、provider 输出 token、总 token、wall time、费用和价格版本硬预算 | `output/benchmarks/node-lab-model` |

ad-hoc real 适合受控诊断单个生产模型节点，不等价于正式模型角色 benchmark，也不能引用单步结果宣称整套 manifest 通过。HTTP 使用 `execution_mode="real"` 与 `allow_model_call=true`；CLI 使用相同 execution mode 和 `--allow-model-call`。

## 最快开始：浏览器工作台

分别打开两个终端：

```bash
make dev-node-lab
make dev-frontend
```

然后访问：

- Node Lab 工作台：`http://127.0.0.1:5173/lab`
- Swagger：`http://127.0.0.1:8088/docs`

推荐第一次按以下顺序操作：

1. 新建 LabRun，保留默认的空 Initial State。
2. 从 descriptor 的“调用示例”开始，不先手写复杂对象。
3. 上传参考 PNG，记录页面返回的 Artifact ID。
4. 选择 `initialize_run` 或 `measure_target`，把 Artifact ID 填入输入 JSON。
5. 执行后在右侧观察 `output`、`state_diff`、`usage` 和 `next_action`。
6. 后续步骤把刚生成的 step 选为 `base_step_id`；想比较不同输入时，从同一个父步骤分支。
7. 模型节点先使用 fixture 或“仅预览 Prompt/Schema”，确认输入绑定后再考虑 mock；不要从 real 开始学习。

页面底部 DAG 中，选中卡片只切换查看的输出；中间的 `base_step_id` 决定下一步从哪个不可变快照继续。这两个概念不要混淆。

## 20 个生产节点

| 顺序 | 节点 | 先观察什么 |
|---:|---|---|
| 1 | `initialize_run` | 输入归一化、预算、render contract 与根 Artifact |
| 2 | `prepare_context` | 项目 Memory 如何被裁剪为私有 ContextPack；Lab 只读 |
| 3 | `measure_target` | 图片尺寸、代表像素、ROI 与确定性测量 |
| 4 | `visual_analysis` | Prompt preview、严格 VisualAnalysis Parser、fixture/mock |
| 5 | `persist_visual_analysis` | 分析 Artifact 的 hash 与状态绑定 |
| 6 | `author_initial` | 首个 GLSL Author 结果、provenance 与结构化修复 |
| 7 | `materialize_candidate` | Candidate ID、GLSL hash、父候选和 manifest |
| 8 | `render_and_evaluate` | Validator、WebGL1 compile/render、Oracle 与证据绑定 |
| 9 | `decide_after_render` | 成功、编译修复、停止之间的纯路由 |
| 10 | `prepare_compile_repair` | 旧 Author 证据与剩余修复预算 |
| 11 | `author_compile_repair` | 只修编译问题的模型角色与 bounded call |
| 12 | `select_current_best` | 硬约束、最小改善、保护区和 current_best 单调性 |
| 13 | `prepare_measurement_seed` | 规范化 reference + TargetMeasurements 如何生成不调用模型的 affine seed、确定性 provenance 和独立根候选 |
| 14 | `decide_after_selection` | 质量阈值、停滞、预算和取消状态 |
| 15 | `load_current_best` | 从 Artifact 按 hash 重建 Critic 所需证据 |
| 16 | `visual_critic` | reference/render/score 绑定与问题域选择 |
| 17 | `persist_visual_review` | Review 与 current_best candidate 的一致性 |
| 18 | `author_visual_refine` | 从 current_best 而非失败候选生成视觉修订 |
| 19 | `finalize` | 只从 best 或明确 fallback 生成最终结果 |
| 20 | `promote_validated_strategy` | Memory 晋升预览；V1.0 不写真实 Memory |

八个独立 capability 适合先理解底层确定性事实：normalize、measure、validate、render、evaluate、select，以及 render/selection 两个 routing。它们可在 Swagger 或 Python Application API 中单独调用；生产节点实验统一走通用 step 接口。

## 新 Node 接入规则

Node Lab 内核只认 `NodeProvider` 协议，不认 PNG-to-Shader 的具体文件、Graph 或节点 ID。新增生产 Node 时：

1. 在生产 Graph 中正常注册 Node，并完成 Graph 可视化与路由测试。
2. 在所属功能命名空间的 Provider registry 声明 descriptor、Schema 和机器可读示例；当前路径是 `src/agent/app/nodes/png_to_shader_v1/integrations/node_lab/registry.py`。
3. 若 Node 只消费 JSON-safe State 并返回 JSON-safe partial State，可用 `DirectNodeExecutor` 绑定 callable。当前 V1 的 15 个非模型 descriptor 统一由 `DeterministicNodeExecutor` 负责 Artifact hydration、依赖生命周期、副作用门禁和输出投影，五个模型 descriptor 统一由 `ModelRoleExecutor` 提供 fixture/mock/real binding；不要把 descriptor 数量误写成独立 Adapter 数量。
4. 不修改 `src/agent/app/lab/` 或 `agent.app.services.node_lab`。一致性测试会要求 Provider descriptor 与生产 Graph 节点集合完全相等，且所有声明的 execution mode 都有 Executor。

这里的“解耦”是指 Harness 不感知 Node 实现；Node Lab 仍会通过 Provider 调用真实生产 Node，以保证它测试的是生产语义，而不是第二套模拟实现。

`prepare_measurement_seed` 应从已经包含规范化 `reference_artifact_id` 和 `target_measurements` 的父步骤执行；它把完整 GLSL、Author 和 provenance 留在私有 Artifact，只公开 ID/hash/摘要。随后从该步骤执行 `materialize_candidate`，得到的 `CandidateRecord` 必须保持 `parent_candidate_id=null`、`origin=deterministic` 和 `generator_version=measurement_affine_seed_v1`，即使父快照中已经存在 model current candidate，也不能把 seed 接到其后。

## 逐节点 CLI

CLI 直接调用公共 Python Application API，不经 FastAPI。默认数据目录为 `output/node-lab/cli`；所有子命令使用同一个 `--root` 才能恢复同一 LabRun。

列出 descriptor：

```bash
uv run python scripts/run_node_lab_cli.py nodes
uv run python scripts/run_node_lab_cli.py nodes --node-id measure_target
```

创建 LabRun 并上传参考图：

```bash
uv run python scripts/run_node_lab_cli.py create-run \
  --project-id tutorial \
  --initial-state '{}'

uv run python scripts/run_node_lab_cli.py upload <LAB_RUN_ID> \
  benchmarks/png_to_shader_v1/images/solid_circle.png \
  --kind reference_png \
  --content-type image/png
```

把上一步返回的 Artifact ID 填入输入；复杂 JSON 可写成 `@request.json`：

```bash
uv run python scripts/run_node_lab_cli.py execute-step <LAB_RUN_ID> measure_target \
  --execution-mode deterministic \
  --inputs '{"reference_artifact_id":"<ARTIFACT_ID>"}'

uv run python scripts/run_node_lab_cli.py execute-step <LAB_RUN_ID> visual_analysis \
  --execution-mode fixture \
  --fixture-id visual-analysis-success-v1 \
  --base-step-id <MEASURE_STEP_ID> \
  --inputs '{}'
```

读取步骤、DAG 和 Artifact：

```bash
uv run python scripts/run_node_lab_cli.py get-step <LAB_RUN_ID> <STEP_ID>
uv run python scripts/run_node_lab_cli.py list-steps <LAB_RUN_ID>
uv run python scripts/run_node_lab_cli.py list-artifacts <LAB_RUN_ID>
uv run python scripts/run_node_lab_cli.py download-artifact \
  <LAB_RUN_ID> <ARTIFACT_ID> --output /tmp/node-lab-artifact.bin
```

同一命令第二次使用相同 `base_step_id` 和不同 `--inputs`，就是一次不覆盖父步骤的对照分支。

## HTTP / Swagger 最小流程

启动后先确认路由和真实模型开关：

```bash
curl -s http://127.0.0.1:8088/api/lab/v1/health
curl -s http://127.0.0.1:8088/api/lab/v1/nodes
```

创建 Run、上传 Artifact、执行节点：

```bash
curl -s -X POST http://127.0.0.1:8088/api/lab/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{"project_id":"tutorial","initial_state":{}}'

curl -s -X POST http://127.0.0.1:8088/api/lab/v1/runs/<LAB_RUN_ID>/artifacts \
  -F kind=reference_png \
  -F file=@benchmarks/png_to_shader_v1/images/solid_circle.png

curl -s -X POST http://127.0.0.1:8088/api/lab/v1/runs/<LAB_RUN_ID>/steps \
  -H 'Content-Type: application/json' \
  -d '{
    "node_id":"measure_target",
    "execution_mode":"deterministic",
    "effect_mode":"lab_commit",
    "inputs":{"reference_artifact_id":"<ARTIFACT_ID>"}
  }'
```

`GET /runs/{lab_run_id}/steps` 返回 step ID 与 DAG 摘要；`GET /runs/{lab_run_id}/artifacts` 返回 descriptor 列表。Artifact 内容仍需通过带 Artifact ID 的同 Run 下载端点读取。

## 模块测试与 benchmark

```bash
make benchmark-node-lab-ai-off
make benchmark-node-lab-model
make test-node-lab-ui
```

- AI-off suite 分离 capability、真实 node target、scenario/pipeline、Renderer cold/warm 与 HTTP transport。
- 模型 suite 默认用五角色固定 fixture，聚合 Parser、Schema/binding、timeout、latency、token、费用和模型身份；不会调用真实模型。
- 每个 attempt 独立落盘；失败和中断保留在分母，恢复不能覆盖既有证据。
- Node Lab 报告只用于模块诊断，不写入或修改 M5 report、gate 和人工评审文件。

## 如何读步骤响应

- `execution_status`：Harness 是否完成执行；`completed` 不等于候选被接受。
- `outcome`：领域结果，可能是 `success`、`rejected` 或 `stopped`。
- `input_summary`：安全输入摘要；大对象不会内联。
- `output`：节点对外状态补丁。
- `state_diff`：相对 `base_step_id` 的顶层 added/changed/removed。
- `artifacts`：本步骤产生的私有证据 descriptor。
- `diagnostics`：稳定、安全、可机器判断的诊断，不含供应商原始异常。
- `usage`：模型调用、token、浏览器启动等用量。
- `provenance` 与 `execution_fingerprint`：实现、Fixture、Prompt、源码和配置身份。
- `next_action`：建议路由；它不会自动替你执行下一步。

常见稳定错误：`input_contract_invalid` 表示缺字段或类型错误；`node_prerequisite_missing` 表示父快照不完整；`artifact_not_found` 常见于跨 LabRun 使用 ID；`real_model_not_allowed` 表示真实模型门禁未同时开启；`effect_not_allowed` 表示请求了 V1.0 禁止的产品副作用。
