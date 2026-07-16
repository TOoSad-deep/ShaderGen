# V5：Async Run、Durable Execution 与产品化实现方案

> 状态：已完成正式 Review
> 前置：[实施总纲](./PNG转无贴图Shader-Agent-V2-V5实施总纲.md)、[V4 实现方案](./V4-StructureEvolution-VLM-HITL实现方案.md)
> 对应功能：Async Run/Productization

## 1. 目标与关键判断

V5 不是给 `/generate` 包一层后台任务，而是改造执行和持久化语义：

- 恢复关键数据不能继续依赖 UntrackedValue；
- 事件必须阶段增量落库；
- 单进程 ProjectLock 必须替换为数据库 lease/fencing；
- Artifact 必须跨 Worker；
- Browser Abort 必须升级为服务端 Cancellation；
- Checkpoint、Ledger、Object Store 需要显式一致性协议；
- Renderer 从 Graph Worker 生命周期解耦。

## 2. DeploymentProfile

```text
local_dev
  允许 InMemory Checkpointer + LocalArtifactStore
  不宣称 Durable DoD

production_single_tenant
  默认正式范围
  必须有 PostgreSQL Ledger/Checkpointer、Shared ArtifactStore、Worker
  缺失依赖时启动失败

production_multi_tenant
  只有 Principal/AuthN/AuthZ、Project Ownership、Worker Identity
  全部完成后才可启用
```

当前系统没有登录身份体系，因此不能仅增加 `tenant_id` 就宣称租户隔离。

## 3. Run API

```text
POST   /api/shader/runs
GET    /api/shader/runs/{run_id}
GET    /api/shader/runs/{run_id}/events
GET    /api/shader/runs/{run_id}/events/stream
POST   /api/shader/runs/{run_id}/cancel
POST   /api/shader/runs/{run_id}/feedback
POST   /api/shader/runs/{run_id}/resume
GET    /api/shader/runs/{run_id}/candidates
GET    /api/shader/runs/{run_id}/candidates/{candidate_id}
GET    /api/shader/runs/{run_id}/artifacts/{artifact_id}
DELETE /api/shader/projects/{project_id}/memory
```

POST 使用 multipart，返回 `202 Accepted`。

Idempotency-Key 绑定 deployment principal/project、规范化 body hash 和 TTL；同 key 不同图片/参数返回 409。

生命周期：

```text
queued → running → awaiting_feedback/paused
      → running → succeeded/failed/cancelled
```

`cancel_requested_at` 是字段，不是第二套状态。paused/awaiting_feedback 可同 Run CAS 恢复；cancelled 后继续则创建 child Run。Worker crash recovery 保持原 Run id。

`awaiting_feedback` 和恢复必须是可重放事务，而不是只改一个状态字段：

```text
进入等待：confirm checkpoint
→ Run revision + fencing CAS 写 awaiting_feedback
→ complete 当前 RunJob 并释放 lease

恢复：写入 FeedbackRecord(expected_run_revision)
→ FeedbackCompiler 生成版本化 Constraint/Preference 产物
→ CAS feedback: accepted → applied
→ 原子创建唯一 (run_id, resume_generation) RunJob
→ CAS awaiting_feedback/paused → queued → Worker claim → running
```

若 cancel 与 resume 并发，`cancel_requested_at` 已提交则 cancel 优先；反馈保留审计记录但不得创建恢复 Job。已 cancelled 的 Run 永不原地恢复。

## 4. Backend 模块

```text
backend/app/
├── api/routes/shader_runs.py
├── schemas/shader_runs.py
├── services/run_commands.py
├── services/run_queries.py
├── database/run_repository.py
├── database/event_repository.py
├── database/feedback_repository.py
└── workers/run_worker.py

backend/sql/
└── 002_async_runs.sql
```

Route 只处理 HTTP 校验和 Envelope。Command/Query、lease、cancel、resume、feedback、Artifact authorization 属于 Service/Repository。

## 5. Ledger Schema

```text
RunRecord
  id/project_id/optional_tenant_id/parent_run_id
  status/phase/revision/evaluation_revision/resume_generation
  graph_id/graph_version/state_schema_version/checkpoint_schema_version
  contract/preset/budget/usage/versions
  objective_best_id/preferred_candidate_id/final_selected_id
  active_selection_snapshot_id/renderer_environment_id
  committed_checkpoint_id/checkpoint_revision/next_event_seq
  cancel_requested_at/stop_reason/durability_status/timestamps

RunJob
  job_id/run_id/resume_generation/status/attempt
  lease_owner/lease_epoch/lease_expires_at/heartbeat_at
  available_at/last_error

RunEvent
  run_id/seq/event_id/schema_version
  type/stage/payload/artifact_refs
  causation_id/causation_ordinal/occurred_at/recorded_at

CandidateIndex
  candidate_id/run_id/parent_id
  target_hypothesis_hash/semantic_genome_hash/descriptor/created_by
  evaluation_refs/objective_summary/artifact_manifest_ref

FeedbackRecord
  feedback_id/run_id/type/payload
  expected_revision/evaluation_revision/status/applied_event_seq
  candidate_id/genome_hash/evidence_key_hash/profile_hash
  constraint_set_hash/shortlist_version/idempotency_key

ArtifactBlob
  sha256/uri/size/content_type/state/verified_at

ArtifactBinding
  artifact_id/run_id/candidate_id/kind/schema_version/visibility/blob_sha

OperationAttempt
  attempt_id/operation_id/attempt_no/run_id/type/lease_epoch/status
  budget_reserved/cost_reserved/provider_idempotency_key
  request_hash/result_ref/error_code/started_at/finished_at

NodeCommit
  execution_id/run_id/node_name/parent_checkpoint_id
  checkpoint_id/side_effect_manifest_ref/status/committed_at

RendererJob
  renderer_job_id/run_id/candidate_id/status
  lease_owner/lease_epoch/lease_expires_at/heartbeat_at
  environment_id/idempotency_key/request_hash/request_ref/result_ref
  cancel_requested_at/attempt/timestamps

RunOutbox
  outbox_id/run_id/event_seq/type/payload/status/attempt
```

继续扩展现有 `agent_runs/agent_events`，不建立第二套含义重叠的 Ledger。

`CandidateIndex` 对所有已 materialize Candidate 追加写并作为 V4 revision 重评的事实源；bounded CandidateArchive 只是查询/晋级视图。Run 终态和审计保留期前不得删除索引中的 hash、hypothesis、Evaluation/Constraint refs 或生成序号。

## 6. 001 → 002 迁移

迁移使用 expand → backfill → validate → cutover → contract，不允许一次性替换：

1. **Expand**：增加新列、表、宽松状态约束和兼容读 Adapter；所有 DDL 幂等，先运行升级前重复、孤儿和非法状态检查。
2. **Backfill**：用版本化映射表回填 graph/state/checkpoint version、revision、durability 和 legacy status，并保留 `legacy_status` 审计字段。历史 running V1 Run 由旧同步路径 drain，禁止被新 Worker 接管。
3. **Validate**：比较行数、hash、Artifact 可解析率和状态映射；清理冲突后再 `CREATE UNIQUE INDEX CONCURRENTLY` 建立同 project 单活动 Run 索引。旧 `/generate` 与新 `/runs` 必须通过同一数据库 project-active claim，不能各自判重。
4. **Cutover**：先启用 dual-read/new-write，再小流量开启 Worker；新 Genome Pipeline 只走 `/runs`，旧 `/api/shader/generate` 保留一个版本且只执行 V1。
5. **Rollback**：停止领取新 Job、drain Worker、关闭新入口并恢复旧读路径；扩展列/表保留，不执行破坏性回滚。只有兼容窗口结束并完成备份/恢复演练后才能 Contract 旧字段。

新 Artifact API 同时识别 opaque id 和三个 legacy alias，避免同形路由冲突；Memory DELETE 复用现有路由。`002_async_runs.sql`、映射/回填脚本、OperationAttempt、Blob/Binding、NodeCommit、RunJob、RendererJob 和 Outbox 全部进入 wheel resource 与 migration smoke test。

## 7. Job Lease 与 Fencing

使用 `run_jobs` 作为唯一 lease 事实源，不重复维护 run_leases 或 RunRecord lease 字段。

领取 Job：

```text
SELECT ... FOR UPDATE SKIP LOCKED
→ increment lease_epoch
→ assign lease_owner/expires/heartbeat
```

所有写入携带 fencing token：

- Event；
- Candidate；
- ArtifactBinding；
- Objective/Preferred/Final 指针；
- Budget/Usage；
- Terminal status。

数据库拒绝旧 token。允许 lease 过期时外部计算短暂重叠，但只有一个有效 token 可以提交。

Writer 边界固定如下：

| Writer | 允许写入 | 并发保护 |
|---|---|---|
| Run Worker | Candidate、ArtifactBinding、Event、Budget、选择指针、Run terminal | RunJob fencing token |
| Renderer Worker | RendererJob outcome/error 与 verified、尚未绑定的 Blob | RendererJob fencing token |
| API Command | cancel、feedback、resume 请求 | Run revision CAS + idempotency key |
| Reconciler | orphan/partial 修复、dead-letter、告警状态 | 独立 reconciliation lease + revision CAS |

Renderer Worker 不得直接创建 Candidate、Run ArtifactBinding、选择事件或 terminal 状态。当前 Run Worker 消费 Renderer outcome 后，再用有效 Run fencing token 绑定 Blob 和推进 Ledger；迟到 Renderer 结果不得绑定到终态 Run。

约束：

- 单 Run 唯一活动 Job；
- 同 project 单活动 Run 使用部分唯一索引；active status 冻结为 `queued | running | awaiting_feedback | paused`，terminal status 不进入索引；
- Memory promotion 同样验证 fencing/revision/hash。

## 8. Checkpoint 与 Ledger 协议

LangGraph Checkpointer 与 Ledger 不共享事务。每次 Node/搜索评估使用稳定 `execution_id`，所有业务副作用都必须可按该 id 去重。

```text
1. 由 run/graph/parent checkpoint/node/logical input 派生稳定 execution_id
2. 外部调用和 Artifact 先写 staging，OperationAttempt/RendererJob 保存 outcome ref
3. 写入包含 outcome refs 的版本化 Checkpoint
4. 单个数据库事务执行：Run revision + fencing CAS、NodeCommit 唯一插入、Candidate/Binding/Budget/Event/Outbox 写入、更新 committed_checkpoint_id
5. 恢复只读取 Ledger 已确认的 Checkpoint；若 execution_id 已 committed，直接复用记录结果而不重做副作用
6. 未确认 Checkpoint、staging Blob 和未绑定 outcome 视为 orphan，由 Reconciler/GC 清理
```

终态绑定最后确认的 checkpoint id，但不宣称 Checkpoint 与 Ledger 原子提交。

必须覆盖 Checkpoint 前、Checkpoint 成功/Ledger 失败、外部结果成功/本地提交前、Ledger 事务中断和 terminal 提交前等 Failpoint。`NodeCommit.execution_id`、`OperationAttempt(operation_id, attempt_no)`、Candidate materialization 和 `RunEvent(run_id, causation_id, causation_ordinal)` 均建立唯一约束。

V5 Worker 使用所选 Graph 的 namespace builder，不能使用裸 run id。

## 9. OperationAttempt 与预算

模型供应商和外部 Renderer 无法保证 exactly-once。

```text
operation_id
run_id/type
lease_epoch
status: reserved/running/succeeded/failed/outcome_unknown
budget_reserved/cost_reserved
provider_idempotency_key
request_hash/result_ref
timestamps
```

规则：

- 外部调用前 reserve 预算；
- Provider 支持时传 idempotency key；
- 调用完成但本地未提交时崩溃，标记 outcome_unknown；
- unknown 仍保守计入预算/费用；
- Search evaluation 记录 reservation/result；
- 不重复晋升和重复累计已知内部结果；
- 不承诺外部成本 exactly-once。

`operation_id` 表示一个逻辑外部操作；每次实际重试使用新的 `attempt_id/attempt_no`。同一已知成功 attempt 的内部结果、Budget 和晋升只能累计一次；新的 retry attempt 需要单独 reserve，`outcome_unknown` 仍按一次实际调用计入费用和 Budget。恢复优先按 provider idempotency key 查询或复用 outcome，无法确认时才按策略重试。

## 10. Cancellation Saga

CancellationToken 在以下位置检查：

- Node 开始/结束；
- Model 前后；
- 每次 Search evaluation 前后；
- Renderer Job 前后；
- Artifact promotion 前；
- Selection/Memory/Terminal 前。

取消 Saga：

```text
DB CAS cancel_requested_at
→ Worker 停止新候选
→ Artifact staging/verified
→ Checkpoint confirm
→ DB transaction:
   ArtifactBinding + final event + terminal + outbox
```

模型迟到结果在 token 检查后丢弃；若 Provider 不支持中断，Run 可以先进入 cancelled，但迟到响应不得产生任何新副作用，外部计算停止时间单独计量。Renderer 支持 Job cancel/kill/recycle。

取消时间拆成四个观测量：API acknowledgement、`cancel_requested_at` 提交后停止新副作用、`run.cancelled` 记录、Renderer/Model 外部计算停止。queued、search、renderer、model、awaiting_feedback 分层统计，不混成一个 p95。

部分固化失败时允许 `cancelled + durability_status=partial`，默认 5 分钟内由 Reconciler 收敛为 `complete`；超时进入 dead-letter 并告警，且保留可重放修复记录。首个合法候选前取消返回 `no_candidate_available`。

## 11. Artifact Store

```python
put()
get()
head()
presign()
```

实现 Local 和 S3-compatible。

Blob 与 Binding 分离：

- Blob 可被多个 Run/Cache 复用；
- 权限依据 Binding；
- 上传 staging → SHA/HEAD verify → DB binding → orphan GC；
- Terminal 只引用 verified Blob；
- private compiler/model evidence 不公开。

## 12. Renderer Worker Pool

首个 transport 冻结为 PostgreSQL `renderer_jobs` + 独立 Renderer Worker 进程，不提前拆 HTTP 微服务。

RendererJob 包含状态机、lease epoch、environment id、cancel、attempt 和 request/result hash。

Renderer 的内容幂等身份直接复用 V3 冻结的 `RendererRequestV1` 与 canonical `RendererRequestHash`，V5 不得另造第二套投影。`RendererJob.request_ref` 指向不可变 Request Artifact，transport 字段只负责归属和调度：

```text
sha256(canonical_json(
  semantic_genome_hash + program_bundle_hash + glsl_sha256
  + contract_id + compiler_version + renderer_version
  + renderer_environment_id + render_size + device_pixel_ratio
  + fidelity + pass_set + uniform_bindings_hash
  + capture_profile_hash + canonicalization_version
))
```

preview、target-size、beauty 和 diagnostic pass 必须产生不同 request hash。Candidate/Run 归属通过 ArtifactBinding 表达，不进入可跨 Run 复用的内容 hash。

RendererJob 使用独立于 RunJob 的领取协议：

```text
SELECT renderer_job FOR UPDATE SKIP LOCKED
→ increment renderer lease_epoch
→ assign lease_owner/expires/heartbeat
→ claim CAS
```

Renderer 结果、错误和重放写入都携带 RendererJob fencing token；RunJob fencing 不能替代 RendererJob fencing。两个 Renderer Worker 短暂重复计算时，只有当前 Renderer lease epoch 可以提交结果。

规则：

- Chromium 预热；
- 单 Job wall/resource limit；
- context loss 后重建；
- 同一 `request_hash` 最多重放一次；同 Candidate 的不同尺寸/pass 是不同请求；
- Job 创建按 `request_hash` 和调用方 idempotency key 幂等；
- Graph Worker 不管理浏览器进程；
- 每 Run 固定版本化 `renderer_environment_id`/capability profile，并由队列按能力路由；
- 环境变化后 challenger/incumbent 同时重渲。

环境注册表保存 Chromium/WebGL vendor、扩展、颜色与字体配置、容器镜像和健康状态。固定环境暂时不可用时 Run 进入可恢复等待或同环境重排队；不得静默切换。经策略批准切换环境时，递增 Evidence 版本并对 challenger/incumbent 全量重渲。

指标：health、queue、launch、context loss、p50/p95 latency。

## 13. SSE 与事件

数据库 Ledger 是事实源；LISTEN/NOTIFY 只唤醒。

Event `seq` 由数据库分配：写事件事务先锁定 RunRecord 或对 `next_event_seq` 做 CAS，取得序号后在同一事务写 RunEvent 与 RunOutbox。唯一约束 `(run_id, seq)` 只负责校验，应用进程不得自行从 1 计数。

- 阶段事件增量提交；
- 支持 Last-Event-ID/after_seq；
- 断线回放；
- 15s heartbeat；
- seq gap 先刷新 snapshot；
- 事件只带摘要和 ArtifactRef；
- 图片/GLSL/Genome/Residual 按需下载。

未知纯展示事件可安全忽略；未知且可能改变 Run 状态、选择指针、Budget 或 Feedback 的事件必须触发 snapshot refresh，不能静默忽略。

建议事件：

```text
run.queued/run.started
analysis.completed/intent.created/seed.created
candidate.compiled/rendered/scored
objective_best.updated/preferred.updated/final_selected
search.block_completed/agent.patch_proposed
review.completed/run.awaiting_feedback
run.cancelled/run.completed/run.failed
```

## 14. 前端

新增 `/runs/:runId`：

- 创建后立即导航；
- Browser 关闭不影响 Run；
- Snapshot + event reducer；
- 自动重连/回放；
- Phase/Budget/Events；
- Intent/Genome/Score/Residual/Lineage；
- Reference/Objective Best/A/B；
- Region/Color Lock；
- Accept/Feedback/Cancel/Resume；
- GLSL/PNG/Genome/HTML/Manifest 下载；
- Client WebGL 仅做兼容复核。

## 15. Nightly 与 Dashboard

记录：

- 质量：compile/static/structure/objective/human correlation；
- 性能：queue wait、stage p50/p95、render/search counts；
- Cache：render/metric/VLM hit rate；
- Model：calls/tokens/latency/repair/cost；
- Runtime：cancel/recovery/lease/reaper/crash；
- Versions：Contract/Genome/Compiler/Renderer/Metric/Prompt/Code。

真实模型需要显式开关和预算；普通 CI 使用 AI-off、Fixture 和故障注入。

Nightly 由版本化 `BenchmarkManifest` 驱动，至少冻结：硬件/容器镜像、Worker/Renderer 数、数据与尺寸组合、并发与到达率、缓存冷热、预热、重复次数、故障注入矩阵、百分位算法，以及 Run/queue/render/SSE/recovery 的绝对 SLO、相对基线允许回退、错误率和 flaky 重跑规则。AI-off 与 durable failpoint 是阻塞门禁；真实模型是发布阻塞门禁还是 canary 必须在 Manifest 中显式声明，不能运行后决定。

## 16. 实施增量

### V5.0：Control Plane/Ledger

- DeploymentProfile；
- 002 Schema/Migration；
- 202 Run API/Query/Replay；
- Idempotency/Revision/CAS；
- Wheel SQL/resource smoke；
- 旧 V1 `/generate` 兼容。

### V5.1：Worker/Cancel/Recovery

- RunJob lease/fencing；
- Durable Checkpoint protocol；
- ArtifactRef 化；
- Cancellation Saga；
- OperationAttempt；
- Crash/Cancel/Resume failpoints。

### V5.2：SSE/Store/Renderer

- Incremental Event Ledger/SSE；
- Blob/Binding Store；
- Content Cache；
- RendererJob migration/state/fencing/cancel；
- Renderer Pool/health。

### V5.3：UI/运营

- Run Page；
- Candidate/Residual/Lineage/Pairwise/Feedback；
- Cancel/Resume/Reconnect E2E；
- Nightly/Dashboard；
- 弃用阻塞产品路径。

## 17. 验收门槛

并发与一致性：

- 2+ Worker/Failpoint 下只有一个有效 fencing token 可提交；
- 主动制造 lease 过期重叠时 stale Worker 的写入/晋升全部拒绝；
- Event seq 严格递增且重连无丢失；
- 一个 Terminal winner，CAS loser 安全拒绝；
- 同 project 单活动 Run 跨进程有效；
- Artifact/Score/Budget 不因同一 execution/attempt 的恢复而重复累计；新外部 retry attempt 按预算规则单独计费；
- Event 分配、NodeCommit 和 Renderer outcome 的唯一约束均通过并发冲突测试。

恢复：

- Analysis/Model/Search/Renderer/Terminal 前崩溃均可恢复；
- Confirmed Artifact/Score 可复用；
- Reaper 正确回收 lease；
- Cancelled Run 不在原 id 变回 running；
- 有合法候选时 objective best 可下载；否则返回 `no_candidate_available`；
- `durability_status=partial` 在策略 RTO 内收敛；超时进入 dead-letter 和告警；
- provider 返回后、本地提交前、NodeCommit 前后和 Event 分配时崩溃均通过 failpoint。

取消：

- queued/running/awaiting_feedback/paused 可取消；
- 固定硬件与负载、每个分层场景 ≥100 样本下，cancel API ack p95 ≤500ms；
- `cancel_requested_at` 提交到禁止新副作用 p95 ≤2s，提交到 `run.cancelled` 记录 p95 ≤2s；
- Renderer 外部计算停止 p95 ≤5s；Model 使用版本化 provider timeout/cancel SLO，迟到结果不得晋升；
- 所有 p95 报告计时边界、百分位算法、成功/超时分母和场景分层。

Store/Security：

- Local/Object Store contract 一致；
- 100% 下载 SHA 校验；
- 跨 Worker 无本地路径依赖；
- SSE Last-Event-ID/seq gap/snapshot 通过；
- selected DeploymentProfile 下 project/run 隔离和授权通过。

E2E：

- Create → Disconnect → Reconnect → Complete；
- Compare → Feedback → Resume → Complete；
- Cancel；
- Worker crash → Reaper → Resume；
- Renderer context loss → Rebuild → Replay once；
- Browser close 后 Run 继续；
- 版本化 `usability_protocol_v1` 至少包含 10 名非开发者、冻结任务脚本和目标图片；上传、创建 Run、断线恢复、比较、反馈、取消和下载的核心任务完成率 ≥90%，关键数据丢失/越权/错误候选下载事件为 0，并报告完成时间与求助次数。

性能发布门禁由 `benchmark_manifest_v1` 的非空绝对 SLO 和相对基线共同判定；至少要求 Event 丢失/重复提交为 0、durable failpoint 恢复成功率 100%、stale fencing 写入接受数为 0。Manifest 未冻结或关键指标样本不足时不得宣称 V5 通过。

发布：

- 自动和独立人工门禁通过；
- Nightly 检出冻结质量/性能/成本回退；
- Manifest 可复现最终 GLSL；
- 文档、API、Graph 和功能状态同步。
