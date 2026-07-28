# direct GLSL + LayerPlan 生产灰度与回滚设计

> 归档状态：历史且部分被后续决策覆盖；强制晋升顺序和日常 A/B/benchmark 要求不再适用。

## 1. 目标

本设计定义 D094 之后从独立实验走向生产替换的唯一允许路径：

```text
disabled
  -> production_shadow
  -> canary
  -> direct_default
```

它不是立即切换授权。D096 已确认自动与人工 gate 通过，但 durable 证据仍为
pending；在 durable 完成前，代码最多落地 policy、隔离的 production shadow 和
fail-closed 的 canary runtime，`shader_graph_v1` 继续是唯一实际生产权威输出。
截至 2026-07-27，父 run/direct child/fresh old fallback/原子 v2 manifest、历史
v1 reader 和前端 discriminator 均已实现；真实 registry 尚无 durable promotion
entry，因此这些 authority 分支只能由测试回执验收，实际启动仍 fail-closed。

## 2. 不变语义

1. 参考图是视觉真相。
2. `LayerPlanV1` 只提供分层参考，永久 advisory，不进入 scorer、接受谓词或
   `current_best` 身份。
3. direct engine 的执行真相是 canonical `ShaderProgramSpecV1`；安全校验、真实
   WebGL1 compile/link/draw、receipt verify 和 metric 缺一不可。
4. 现有 `ShaderDocument` 是 `shader_graph_v1` 的可执行 DSL，不重新解释为
   LayerPlan，也不改变其历史 hash、Compiler 或 manifest 语义。
5. 一个 attempt 只能使用一种候选表示；Graph 运行中禁止在
   ShaderDocument 与 ShaderProgramSpec 之间切换。
6. 只有真实 Render 的 `min_scene_composite_v3` strict total-loss 可以更新各
   engine 自己的 `current_best`。

## 3. Engine policy

### 3.1 版本化契约

新增可信服务端 `ShaderEnginePolicyV1`：

```yaml
schema_version: shader_engine_policy_v1
policy_id: direct-glsl-rollout-001
stage: disabled               # disabled | production_shadow | canary | direct_default
shadow_percent: 0             # 0..100
canary_percent: 0             # 0..100
bucket_basis: project_id_v1   # 固定算法，不接受客户端输入
direct_engine: direct_glsl_layerplan_v1
fallback_engine: shader_graph_v1
promotion_authorization: null # canary/direct_default 时必须存在
```

环境变量只允许选择一份受信 policy 文件和执行 kill switch：

```text
SHADERGEN_ENGINE_POLICY_PATH=
SHADERGEN_EVIDENCE_REGISTRY_PATH=
SHADERGEN_DIRECT_GLSL_KILL_SWITCH=1
```

不使用多个松散 ratio 环境变量，避免运行时组合出未签审的状态。policy 在 Backend
启动时严格解析并冻结：

- 缺失 policy：等价于 `disabled`；
- 未知字段、未知 engine、百分比越界、非法阶段组合：启动 fail-closed；
- kill switch 优先级最高，强制所有新请求走 `shader_graph_v1`；
- 客户端请求、`VITE_*`、HTTP header、query 或 instruction 均不能覆盖 engine；
- policy canonical SHA-256 进入 run config fingerprint、过程账本、进度摘要和
  final manifest。

policy 的 Pydantic 解析只证明字段形状合法，不签发生产权限。`canary` 或
`direct_default` 启动时必须再用 `SHADERGEN_EVIDENCE_REGISTRY_PATH` 指向的受信
registry 校验 `PromotionAuthorizationV1`：

- registry 必须是非 symlink 的单一 JSON object，拒绝重复 JSON key 和重复
  `evidence_id`；
- 被引用 entry 必须是 `layerplan_glsl_promotion_evidence`、`durable/passed`；
- D094 suite hash、自动 gate、递归 verifier 版本/结果、人工 manifest/result
  hash、人工 preference/gate、目标 stage 必须逐字段完全一致；
- entry 必须且只能有一个 `promotion_evidence_bundle` Artifact，availability 只
  接受 `release/object_store`，并声明 `immutability_status=immutable`；其 URI 和
  SHA-256 必须与授权完全一致；
- 授权、registry summary 和当前代码重算的 direct implementation identity
  SHA-256 必须三方完全一致。

成功结果形成只读验证回执，绑定 authorization canonical hash、registry 文件
hash、entry、stage、URI/hash 和 implementation identity，并冻结进
`BackendSettings`。缺 registry/entry、`partial`、本地路径、字段漂移或回执与
policy 不一致均使 Backend 启动 fail-closed。启动期不联网抓取私有 Artifact；
可信登记流程必须在签发授权前完成上传后的独立内容复验，registry 负责固化其
不可变 URI/hash。

kill switch 是 promotion runtime verification 的唯一启动例外。Backend 必须先严格
解析 policy YAML 和 `SHADERGEN_DIRECT_GLSL_KILL_SWITCH`；当后者为 `1` 时，有效
stage 在启动期直接冻结为 `disabled`，可跳过 registry 读取和 promotion 验证回执，
避免 registry/对象存储事故反过来阻止紧急回滚。该例外不允许容忍未知 policy 字段、
非法阶段/比例/授权结构或含糊 kill switch 值。开关恢复为 `0` 后，configured
`canary/direct_default` 必须重新通过完整 durable/identity/stage 校验。

### 3.2 稳定分桶

`bucket = uint64(sha256("project_id_v1\0" + policy_id + "\0" + project_id)[:8]) % 10000`。

当 `bucket < percent * 100` 时命中。选择只依赖服务端冻结 policy 与 project id，
同一 policy 下同一项目稳定；更换 `policy_id` 才能显式重排。`run_id` 不进入
canary 分桶，避免失败重试悄然换 engine。

## 4. Run、attempt 与表示冻结

### 4.1 父 run

HTTP `run_id` 是产品父 run，只承载请求身份、policy snapshot、最终选择和公开
Artifact。父 run 记录：

- `selected_engine`
- `policy_id` / `policy_sha256`
- `stage` / `bucket`
- `attempt_refs`
- `fallback_reason`
- `promotion_authorization_sha256`

### 4.2 Engine attempt

每次实际 Graph 执行使用独立、确定性 child attempt id：

```text
uuid5(parent_run_id, "<engine>:<attempt_index>")
```

attempt 在 START 前冻结：

- `engine_id`
- `representation`：`shader_document_v1` 或 `shader_program_spec_v1`
- engine config fingerprint
- 独立 LLM/plan/repair/compile/draw/wall-clock budget
- 独立 Renderer registry、program cache 和私有 Artifact 根

child attempt 不得覆盖父 run 或其他 attempt 的目录。只有父协调器能在验证
attempt manifest 后，把被选结果原子发布到父 run 的三个公开白名单 Artifact。
rollout private store 显式使用 restrictive 权限模式，direct 与 fresh old
ShaderGraph child 的 base/project/run/index/嵌套目录均为 0700、普通文件均为
0600；不得借此粗暴改变历史 public `LocalArtifactStore` 的默认权限。

## 5. 四个阶段

### 5.1 disabled

- 只运行 `shader_graph_v1`。
- direct Graph 不装配到请求执行器。
- 这是缺省和 kill switch 状态。

### 5.2 production_shadow

- `shader_graph_v1` 是唯一权威 attempt，决定 HTTP 成功/失败、GLSL、Render、
  `current_best` 与公开 Artifact。
- 命中稳定 shadow 桶时，额外运行 `direct_glsl_layerplan_v1` child attempt。
- direct attempt 使用独立预算、cache、Renderer 和 `private/shadow/` Artifact；
  失败或超时只能写安全摘要，不能改变父 run 结果。
- shadow 可以异步执行，但必须有有界队列、并发上限、进程关闭 drain/cancel
  语义；队列满时记 `shadow_skipped_capacity`，不得拖慢权威路径。
- shadow 详细产物不通过产品 HTTP 白名单暴露；只登记内容 hash、结果状态、
  loss、耗时、预算与 engine identity。

### 5.3 canary

进入前必须提供受信 `PromotionAuthorizationV1`，绑定：

- D094 suite report SHA-256；
- 递归 verifier 版本与验证结果；
- 人工盲评 evidence manifest/result SHA-256，且 B preference `>=0.5`；
- durable registry entry id 与不可变 URI/hash；
- direct implementation identity；
- policy 允许的最大 canary 百分比；
- 审批时间与新的 ADR id。

命中 canary 桶时，direct attempt 是首选。v1 不做同 attempt 静默跨表示回退：

- direct 成功：父 run 原子发布 direct 结果；
- direct 失败：创建全新的 `shader_graph_v1` child attempt；
- fallback 成功：父 run 返回旧 engine 结果，并显式记录
  `selected_engine=shader_graph_v1`、`fallback_from=direct_glsl_layerplan_v1`
  与安全失败码；
- 两者都失败：父 run 返回稳定失败，不覆盖任何 attempt；
- 任何 fallback 都计入 canary 失败率与成本，不能冒充 direct 成功。

### 5.4 direct_default

- 必须由 canary 运行证据的新 ADR 单独授权。
- `canary_percent` 固定为 `100`，所有新请求默认 direct；若后续需要长期保留桶，
  必须扩展 policy schema 后另立决策，不能复用 canary 字段暗示不同语义。
- `shader_graph_v1` 代码、测试、artifact reader 与 kill switch 不删除。
- kill switch 生效后，所有新父 run 立即回到 old engine；已经启动的 attempt
  按其冻结身份结束，结果带旧 policy snapshot，不在中途换 engine。

## 6. Direct engine 契约

本轮生产 runtime 复用 D088/D094 已验收的单 engine Arm B 内核，不把第二种表示
塞入现有 LangGraph，也不修改 12 节点拓扑。以下是 direct 阶段与原 Graph 职责的
对照，不表示注册了同名 LangGraph node：

| 节点 | direct 职责 |
|---|---|
| initialize_run | 冻结 engine、policy、预算、表示与 attempt identity |
| perceive_target | 复用可信参考图预处理与 metric 目标 |
| author_initial | 独立生成 LayerPlan；参考图 + advisory plan 生成 ProgramSpec |
| materialize_shader | canonical safety 与 program identity，不编译 ShaderDocument |
| render_and_evaluate | 真实 prepare/draw/receipt/metric，形成不可变 direct snapshot |
| optimize_base | 当前版本不调用，ledger 记录为无独立 optimizer |
| optimize_feature | 当前版本不调用，且永不访问 DSL layer/node 参数 |
| author_refine | 参考图 + incumbent render/metric + advisory plan 生成新 ProgramSpec |
| finalize | 固化 ProgramSpec/GLSL/render/metric/direct manifest |
| 三个 decide 节点 | 由 bounded runner 执行等价预算停止与 strict incumbent 语义 |

当前 direct 内核没有调用 ShaderGraph 的 layer/node 参数优化器，也不把其能力
冒充 direct uniform optimizer；它只使用冻结的 Initial/Refine、compile/draw 与
strict incumbent 预算，config fingerprint 和 ledger 显式记录该事实。未来新增
direct uniform optimizer 必须升级实现身份并重新走证据与授权。

Direct snapshot 至少绑定：

- canonical ProgramSpec/source/uniform/tunable manifest hash；
- reference/canvas/metric identity；
- render RGB/PNG hash 与 receipt verify 事实；
- parent snapshot/spec hash；
- author/repair/plan identity；
- compile/draw/LLM/plan ledger；
- `engine_id`、policy 与 attempt id。

## 7. Artifact 与 API discriminator

父 manifest 升级为 discriminated union，旧 reader 保持兼容：

```json
{
  "schema_version": "png_to_shader_manifest_v2",
  "engine": "shader_graph_v1",
  "representation": "shader_document_v1",
  "engine_manifest": {}
}
```

或：

```json
{
  "schema_version": "png_to_shader_manifest_v2",
  "engine": "direct_glsl_layerplan_v1",
  "representation": "shader_program_spec_v1",
  "engine_manifest": {}
}
```

API/前端只新增只读 discriminator 与安全摘要，不允许客户端选 engine。公开白名单
仍为 final-render、metrics、manifest；完整 LayerPlan、ProgramSpec、Prompt、repair
上下文和失败源码保持私有。前端标签按 discriminator 显示“ShaderGraph DSL”或
 “Direct Program”，不能把 LayerPlan 名称冒充 GLSL 执行来源。

父发布器在落盘前必须拒绝非法 engine/representation 配对，并要求
`engine_run.selected_engine/selected_representation` 与顶层 discriminator 完全
一致。公开 final bundle 的本地读取/幂等复验不得先跟随 symlink 再检查：使用 pinned
directory fd、`O_NOFOLLOW`、普通文件 `fstat` 和读取前后 inode/mtime/ctime 复核；
仍以 Artifact 根由单一服务进程独占为部署前提。

## 8. 自动回滚

代码只实现确定性判定和新请求回滚，不在进程内悄然改写已冻结 policy 文件。
监控/部署控制面根据以下窗口触发 kill switch：

- direct fatal/fallback rate 超过 PromotionAuthorization 的上限；
- direct 无有效候选、compile/draw/receipt 失败率越界；
- p95/p99 wall-clock 或资源预算越界；
- artifact 发布/manifest verify 失败；
- policy/implementation/promotion identity 漂移；
- 人工或安全 incident 明确要求停止。

阈值必须在 canary ADR 中随证据预声明；本设计不根据线上结果事后填写数字。
回滚后保留全部失败 attempt、policy snapshot 和统计，不删除或改写历史证据。

## 9. 晋升门禁

### production_shadow 允许条件

- D094 自动 gate supported；
- engine policy、attempt isolation、私有 Artifact 与 kill switch 测试通过；
- direct 仍不影响产品结果。

因此当前允许实现但默认关闭 production shadow。

### canary 允许条件

- D094 自动 gate 递归验签；
- 完整独立人工盲评达到冻结 `>=0.5`；
- 自动与人工证据均为 durable、内容寻址、可跨环境验证；
- production shadow 的隔离、失败率、预算与延迟报告通过另行冻结 gate；
- 新 ADR 签发 `PromotionAuthorizationV1`。

当前人工 preference=`0.625` 已通过，但完整 promotion bundle 仍为
`local_private_not_registered`，所以 canary 条件尚未满足。

### direct_default 允许条件

- canary 达到预声明样本量/时间窗/质量/可靠性阈值；
- fallback、kill switch 和保留桶演练通过；
- 无开放的高优先级正确性/安全问题；
- 新 ADR 明确修改默认 engine。

## 10. 验收

至少覆盖：

1. policy 缺省 old、非法 fail-closed、客户端不可覆盖、稳定分桶；
2. kill switch 后新 run 100% old，运行中 attempt 不漂移；
3. parent/child id、Artifact、cache、Renderer、预算和 ledger 不串；
4. shadow 失败/超时/队列满不影响 old 权威结果；
5. direct candidate 只有 safety + compile/draw + receipt + metric 后可晋升；
6. LayerPlan 不参与 acceptance；较差/非法/Refine 失败保留 incumbent；
7. canary direct 失败创建显式 old child attempt，不覆盖原 attempt；
8. manifest/API discriminated compatibility 与旧 artifact reader；
9. Graph ASCII、Mermaid、路由表、recursion 与 `langgraph.json` 同步；
10. fake LLM 集成、真实 Chromium、Backend/Frontend E2E、`make check`、
    `make docs-check`、`uv run langgraph validate`。

## 11. 明确禁止

- 人工或 durable gate 未完成时启用 canary；
- 在 Graph 中间更换 engine/representation；
- 把 Shadow 候选写进产品 `current_best`；
- 把 LayerPlan 放进 acceptance；
- 复用 child run id、覆盖 attempt 或把 fallback 冒充 direct 成功；
- 共享两引擎的可变 Renderer cache、预算或私有 Artifact；
- 让 HTTP/前端/用户 instruction 选择 engine；
- 重写 `shader_graph_v1` 的历史 ShaderDocument、Compiler 或 manifest 语义；
- 因自动 gate 贴线通过而移动阈值、删除 inconclusive 或跳过人工评审；
- 默认 engine 切换后删除旧路径、kill switch 或历史失败证据。
