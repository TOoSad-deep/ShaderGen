# PNG-to-Shader V2.3 Development Service

本目录是尚未接入 Backend、也未注册到 `langgraph.json` 的 V2.3 Application
Service 组合根。它不改变 V1 产品链路。

## fixture 与 real 调用链

`PngToShaderV2DevelopmentService.invoke()` 接收 source image bytes 和版本化请求元数据，
先调用真实 `measure_target_v2()`。source、规范化 reference、mask、edge、Measurements、
RequestConstraintSet、IntentBuildContext 与恢复上下文均写入同一
run 的 `ArtifactCatalog`；Graph State 只保存小字段和 `ArtifactRefV2`。Service 随后以
`fixture/no-model` runtime 调用 production V2 Builder，且固定
`production_admission_enabled=false`。

`real` 使用同一 Service 与同一 22-node production V2 Builder，但在 Graph 前执行严格的
两阶段 Interpretation bootstrap：先以 `visual_interpretation_ref=null`、真实 Measurements
与 preliminary RequestConstraintSet 初始化 State；再由
`VisualInterpretationGatewayAdapter` 调用注入的 `DurableLLMGateway`，物化 prompt/raw/
typed Interpretation/audit，提交模型 receipt，最后用 State CAS 绑定 Interpretation 与最终
RequestConstraintSet。Graph 因此只恢复已冻结 ref，不复制模型逻辑，也不改变节点或路由。
real 路径的 fixture factory 只提供 RequestConstraintSet 与 IntentBuildContext 前置条件；
fixture Interpretation 不参与验证、持久化、模型请求或恢复闭包。

恢复上下文作为 RequestConstraintSet 的唯一特定 kind evidence ref 被 State 间接锚定，
所以 `resume(run_id=...)` 能从本地 Catalog 和最后确认 State 重建 Context、reference 与
runtime。它不依赖调用进程内缓存。当前 State Store/Catalog 都是单机文件实现，不能宣称
跨机器或分布式恢复。

Service 在首个 source/config Artifact 之前先原子创建
`ServiceRunJournalV2` 和 wall reservation。journal 保存 canonical config/request metadata、
source SHA、policy hash、Catalog 实际去重字节数，并以单调 phase/refs 分别确认 source、config、
metadata、measurement bundle、Intent context、preliminary constraint、State initialize、model
commit、resume context、final constraint、closure CAS 与 Graph finalize。若进程落在 Catalog
commit 与 journal commit 之间，恢复使用 `LocalArtifactCatalog.list_refs()/total_size_bytes()`
重新对账，再以内容寻址幂等重放；real 路径不会降级为 fixture Interpretation，也不会产生第二个
provider operation。完整 State 建立后，恢复会逐 kind、schema、run、size、SHA 和 typed
payload 校验 source、bundle、nested mask/edge、normalized reference、Interpretation、
constraints、context 与 State 的交叉 identity。

model commit 之后的 resume-context 与 final-constraint 各使用一个固定 durable put slot。slot
冻结 canonical payload SHA/size、Catalog 起点、State artifact budget 起点，并依次推进
`prepared -> reserved -> put -> committed`；reserve 前后、put 后、commit 前后任一崩溃都能在
首次 resume 对账，第二次及以后 resume 的 ref、budget 用量和 revision 保持不变。

## 预算与 real mode 边界

- Service 使用可注入 monotonic clock，并以 `asyncio.wait_for` 执行剩余
  `wall_time_ms` deadline。任何 Graph/Renderer/model side effect 前，独立、原子、可恢复的
  `LocalServiceWallTimeLedgerStore` 先持久化本次全部剩余 wall-time reservation；正常返回
  按实际 elapsed 提交，timeout 按 reservation 上限提交。进程级崩溃使 reservation 留存；
  同一 monotonic 时基可按持久起点保守结算，时基回退/不可用则全额结算。每次 resume（包括
  terminal）都以 ledger 为 authoritative source，把 State `used/reserved` 对账到零 reservation
  和相同 used。该外层 ledger 与 Graph 内部
  Budget CAS 分开，避免 Graph 把仍存活的 Service deadline 当成孤立 Node reservation；
  结算后实际 elapsed 同步写入 Graph State 的七维 Budget。
- Graph 前产生的 Artifact bytes 不做事后估算：metered Catalog 按实际去重快照持续 checkpoint，
  初始 Budget 从快照建立。最终 run manifest 在写前按精确 bytes reserve；manifest ref 与结算
  起点先进入 journal，解决 put 后、budget commit 前的崩溃窗口。terminal journal 是持久结果
  index；重复 resume 不再 put/charge，七维 budget 与 run/budget revision 均保持不变。
  已结算的 typed 模型失败同样写入 `terminal_failure`（status + 七维 budget snapshot）；后续
  resume 在新 wall session 之前重抛同一失败，provider 调用、budget 与 revision 保持不变。
- `RealModelCallPolicyV1` 要求显式 provider/model/pricing identity、input/output token、成本与
  audit/output Artifact 上限；Service config 的 `real` 模式必须同时打开调用开关、provider
  开关并携带该策略，且注入 adapter 的策略必须逐字段相同。
- `ModelCallReservationV1` / `ModelCallReceiptV1` 冻结 provider/model、prompt、pricing、
  Measurements/constraints identity、完整 request SHA-256，以及调用前最坏
  token/cost/output Artifact bytes；request digest 覆盖 Prompt name/version/text、实际
  multimodal messages（包括 normalized image 与 Context）、输出 schema 和模型 options；receipt
  必须保持同一 identity，实际 provider model 必须等于冻结 model，output 必须是可由
  Catalog 严格解析的 `VisualInterpretationV2`，并绑定 call audit ref。
- `LocalRealModelOperationStore` 在副作用前冻结 stable `invocation_id`、完整 input identity、
  最坏 reservation、调用前账本与 Catalog byte 起点。外部 provider 必须实现相同 invocation id 的 durable
  `recover()` 与 `invoke_once()` 去重协议；普通 `LLMGateway.ainvoke()` 不具备该保证，不能直接
  进入严格 Service。崩溃可从 State reservation、provider receipt、部分内容寻址 Artifact 或 budget
  commit 后恢复，重复 `resume()` 不重复计费/调用。若 provider 无法给出可信完成结果，Service
  以 token/cost reservation 上限提交 typed `provider_indeterminate` failure 并安全终止；这里只声明单机 Service 的可恢复编排，不伪称跨供应商事务
  exactly-once。
- Parser 只能产生严格 `VisualInterpretationV2`。任何真实 Artifact 写入前，内存 preflight 会先构造
  prompt snapshot、raw response、typed Interpretation 与 audit 的全部 canonical payload/ref，并与
  `max_output_artifact_bytes` 比较；oversize 不写这些 Artifact。写入后按 operation Catalog 起点的
  实际去重 delta 结算，部分写入可恢复。parse、oversize、provider indeterminate、receipt identity/
  cost 无效及越权 evidence 都形成 typed committed failure，清空 reservation 并安全终止；重复恢复
  不再调用 provider，也不得把失败输出伪装为 Interpretation。

## 禁止事项

- 不得从 Backend Route 导入本包；当前只有开发/验证调用者可显式创建 Service。
- 不得在本包打开 production admission、项目 promotion 或 Memory 写入。
- 不得把图片、GLSL、render 或模型 raw response 放入 State；只允许 Catalog ref。
- 不得绕过 Graph 调用 Compiler、Renderer、Evaluator 或 Selector。
