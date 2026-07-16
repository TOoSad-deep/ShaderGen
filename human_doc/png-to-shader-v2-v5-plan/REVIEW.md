# PNG 转无贴图 Shader Agent：V2–V5 正式 Review 报告

> Review 日期：2026-07-16
> Review 范围：实施总纲与 V2、V3、V4、V5 四份实现方案
> 结论：**Conditional Go，可进入 V2.0 契约冻结；不可跳过 V2.0 直接开发 V2.1+**

## 1. 结论

- P0：0；
- 阻塞编码的 P1：已全部修订并关闭；
- 剩余项：均为实施阶段需产出的 Manifest、golden fixture、数据标注和测试资产，不构成架构返工；
- 架构方向通过：语义外循环、确定性 Compiler/Renderer/Oracle/Search 内循环、V4 受限结构演化、V5 durable control plane 的职责边界一致；
- 发布尚未通过：当前结论只允许开始 V2.0，不代表 V2–V5 功能或质量门禁已经实现。

## 2. 本轮关闭的问题

| 领域 | Review 发现 | 修订结果 |
|---|---|---|
| 测量与多假设 | 缺少 `instance_count`，TargetHypothesis 未贯穿 Intent/Seed/Candidate/State | 增加版本化 TargetHypothesis、id/hash 和多分支 Graph；V3 增加 hypothesis-bound 搜索与 hypothesis-neutral 跨分支选择 |
| 约束 | 只有单条 Constraint，没有集合级 revision/hash/conflict 契约 | 冻结 `RequestConstraintSet`、sealed payload、canonical hash、revision CAS 和冲突处理；V4 Feedback 生成新 revision |
| State/Checkpoint | V2–V4 State 缺少版本、游标和继承闭环 | 冻结 VersionedRunStateCore；V2/V3/V4 声明 graph/state/checkpoint version、namespace、phase、Budget、Journal/Selection cursor |
| Artifact | V2 opaque id 到 V5 Blob/Binding 之间缺 Resolver | V2 增加 ArtifactCatalog/Resolver、Local 实现和 legacy adapter；V5 只替换存储后端 |
| Genome/Search Hash | layout projection 不完整，Search Manifest 未绑定 topology | 冻结四类 Genome hash 投影与版本；Manifest 绑定 schema/topology/registry/contract/canonicalization/layout |
| Selector | block 接受规则与全局 objective 单调语义混淆 | 区分 BlockAcceptance 与版本化 ObjectiveBestOrder；同一 SelectionKey 下稳定全序单调 |
| Search 恢复 | Journal 没有 Store/CAS/evaluation id | 增加 SearchJournalStore、确定性 evaluation id、CAS、预算去重和恢复协议 |
| Structure Patch | Patch 后可能沿用旧 Manifest/Cache | Patch 后强制重建 topology/layout/semantic hash、Manifest、Compilation 和全部派生 cache key |
| Evaluation revision | 先清空指针再重评会暴露半更新状态；只重评旧 feasible archive | 增加完整 RetainedCandidateIndex、watermark、staging SelectionSnapshot 和 CAS 原子发布；缺失新 Evidence 时补 render/metric |
| Pairwise/HITL | Request/Feedback 缺少 Candidate、Evidence、Profile、revision 强绑定 | Shortlist、Pairwise 和 Feedback 全部绑定 candidate/genome/render hash、EvidenceKey、Profile、constraint、revision、event 和 generation config |
| Durable execution | Migration、Node 副作用、Event seq、Renderer fencing 和 resume 事务不闭合 | 增加 expand/backfill/cutover/rollback、NodeCommit、OperationAttempt、DB seq 分配、双 fencing writer matrix、awaiting-feedback/resume 事务 |
| Renderer 幂等 | preview/target/diagnostic 可能碰撞 | V5 直接复用 V3 `RendererRequestV1/Hash`，绑定 size、pass、program、uniform、capture profile、compiler/renderer/environment |
| 评测协议 | held-out、保护区方向、改善公式、AI 消融、人工排序、取消 p95 不可重复判定 | 增加三分数据集、lower-is-better ProtectedRegionLoss、配对公式/CI、target 内盲评、等价性测试、分层 p95 与 BenchmarkManifest |

## 3. 交叉版本不变量复核

1. V1 只读兼容，新版本不重解释历史 `current_best`。
2. V2 起统一使用 `objective_best_id`；V4 才增加 preferred/final 两个指针。
3. 分数只在完全一致的 EvaluationEvidenceKey/SelectionKey 下比较。
4. TargetHypothesis 不同的结构分数不互比；跨假设只使用 hypothesis-neutral 公共证据。
5. Candidate、Genome、Score、Feedback、Pairwise 和 SelectionSnapshot 均为不可变、内容寻址或版本化记录。
6. Graph State 只保存小状态与已确认 Cursor；大对象和搜索历史进入 Artifact/Journal/Ledger。
7. V5 RendererJob fencing 与 RunJob fencing 独立；Renderer Worker 不直接推进 Run Ledger。
8. Constraint/Profile/revision 变化通过 staging snapshot 发布，不暴露半更新选择指针。

## 4. 进入 V2.0 前必须落地的资产

以下是 Conditional Go 的前置，不应后移到 V2.1：

- JSON Schema/Pydantic models 与 schema compatibility tests；
- TargetHypothesis、RequestConstraintSet、ArtifactRef、Genome Hash、Candidate、State、Budget 的 golden fixtures；
- canonical JSON/hash property tests；
- LocalArtifactCatalog/Resolver 与 legacy read adapter；
- development/validation/release-held-out 数据 Manifest、分组 hash 和关键类最小分母；
- graph/state/checkpoint namespace 序列化与恢复 smoke；
- expected-primitives taxonomy、RenderContract 和 Node registry 版本冻结。

V2.0 完成条件是这些契约和资产可以被测试、序列化、回放和复现，而不是已经生成高质量 Shader。

## 5. 实施建议

建议仍按四份版本方案实施，但在工程管理中使用一个总 Epic、四个阶段 Epic 和小型增量：

```text
V2.0 contracts → V2.1 intent → V2.2 compiler → V2.3 graph
→ V3.0 oracle → V3.1 contracts → V3.2 search → V3.3 AI-off
→ V4.0 patch → V4.1 evolution → V4.2 review/HITL
→ V5.0 ledger → V5.1 recovery → V5.2 renderer/SSE → V5.3 UI/ops
```

第一个编码 PR 只做 V2.0 schema、hash、fixture 和 adapter，不同时接入模型 Prompt、Genome Compiler 或新 Graph。这样可以让后续版本依赖稳定的类型与内容身份，而不是边开发边改根契约。

## 6. Review 后仍需在实现阶段冻结的决策

这些不是未关闭缺陷，但必须由版本化 Manifest 给出具体值：

- release-held-out 的实际样本清单和关键类分母；
- Oracle perturbation 幅度、noise tolerance 和 min effect；
- Benchmark 的硬件、并发、缓存冷热、绝对 SLO 与相对回退阈值；
- AI-on/off 模型快照、价格快照、重复次数和 primary endpoint；
- HumanEvaluation 的 target/candidate 集、rater、tie、主指标和 CI；
- Provider timeout/cancel SLO 与 Reconciler RTO；
- V5 真实模型 Nightly 是阻塞门禁还是 canary。

上述值运行后不得移动；任何调整必须新建 Manifest 版本并完整重跑。

## 7. 最终判定

| 维度 | 判定 |
|---|---|
| 架构职责边界 | Pass |
| 跨版本契约一致性 | Pass |
| 可编码性 | Pass，前提是先完成 V2.0 |
| 可恢复性设计 | Pass |
| 验收可判定性 | Pass，依赖版本化 Manifest |
| 当前产品发布 | Not applicable，尚未实现 |

正式建议：**从 V2.0 开始实现；不再继续扩写最终架构，也不要把 V2–V5 合并成一个大开发任务。**
