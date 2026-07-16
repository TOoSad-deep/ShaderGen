# V4：StructureEvolution、VLM Pairwise 与 HITL 实现方案

> 状态：已完成正式 Review
> 前置：[实施总纲](./PNG转无贴图Shader-Agent-V2-V5实施总纲.md)、[V3 实现方案](./V3-Oracle-Search实现方案.md)
> 对应功能：StructureEvolution/Review/HITL

## 1. 目标与非目标

V4 在固定拓扑参数搜索停滞后，通过受限 GenomePatch 修改结构，并用晋级评审和人工约束补充纯指标难以覆盖的视觉偏好。

必须交付：

- StructuralStagnationReport 和 ArchiveSummary；
- GenomePatch Validator/Apply；
- StructureEvolutionAgent；
- top-k + novelty CandidateArchive；
- Candidate promotion；
- VLM Pairwise；
- FeedbackRecord、RequestConstraintSet revision、PreferenceSet；
- objective/preferred/final 三指针；
- evaluation revision 和反馈后重评。

不做：

- 任意 GLSL Patch；
- 参数搜索每代调用 Agent；
- VLM 推翻 hard constraints；
- 未证明必要就实现 MAP-Elites；
- 在阻塞 API 中等待跨进程用户反馈，运行时等待留给 V5。

## 2. 推荐目录

```text
src/shaderforge/
├── genome/
│   ├── patches.py
│   ├── patch_validation.py
│   └── patch_apply.py
├── search/
│   ├── structural_stagnation.py
│   ├── archive.py
│   ├── archive_summary.py
│   └── promotion.py
├── review/
│   ├── models.py
│   ├── pairwise.py
│   ├── feedback.py
│   ├── calibration.py
│   └── ARCHITECTURE.md

src/agent/app/
├── graphs/png_to_shader_v4_graph.py
├── graphs/png_to_shader_v4_routing.py
├── states/png_to_shader_v4_state.py
├── nodes/png_to_shader_v4/
├── prompts/propose_genome_patch_v1.yaml
├── prompts/review_candidate_pair_v1.yaml
└── services/png_to_shader_v4.py
```

## 3. StructuralStagnationReport

V3 SearchStagnation 只表示参数级停滞；V4 才判断是否需要结构升级。

触发条件全部满足：

- 已完成最小评估数；
- 连续参数阶段增益低于 epsilon；
- 当前参数块耗尽；
- 残差模式跨阶段持续；
- 残差能映射到结构问题域；
- 仍有结构和复杂度预算。

```python
class StructuralStagnationReport:
    schema_version: Literal["structural_stagnation_v1"]
    run_id: str
    base_candidate_id: str
    evaluation_revision: int
    evaluations_completed: int
    stage_gains: tuple[StageGain, ...]
    unresolved_regions: tuple[ResidualRegion, ...]
    parameter_sensitivity_ref: ArtifactRefV2
    topology_summary: TopologySummary
    protected_regressions: tuple[ProtectedRegression, ...]
    complexity_headroom: int
    recommended_problem_domain: str
    evidence_refs: tuple[ArtifactRefV2, ...]
```

## 4. GenomePatch

```python
class GenomePatch:
    schema_version: Literal["genome_patch_v1"]
    patch_id: str
    patch_hash: str
    base_candidate_id: str
    base_candidate_record_hash: str
    base_genome_id: str
    base_genome_hash: str
    intent: str
    problem_domain: str
    operations: tuple[PatchOperation, ...]
    expected_regions: tuple[str, ...]
    expected_metric_directions: dict[str, str]
    complexity_delta_limit: int
    prompt_version: str
    model_ref: str
    random_seed: int
```

允许操作：

- AddNode；
- RemoveNode；
- ReplaceNode；
- Connect；
- Disconnect；
- UpdateParameterRange。

禁止自由 GLSL。UpdateParameterRange 默认只能收窄；放宽必须被 Node hard domain 截断，或具有 HITL approval。

六级校验：

1. Schema；
2. base candidate record hash 与 Genome id/hash；
3. Reference/port/type；
4. DAG/topology；
5. Contract/hard constraints/complexity；
6. Dry compile。

`apply_patch()` 是纯函数，相同输入得到相同 semantic Genome hash。

`patch_hash` 对除自身外的 canonical Patch 内容计算，并随 child Candidate lineage 持久化。

Patch 改变拓扑或参数布局后，旧派生物一律不得沿用。完整重建流程为：

```text
apply_patch
→ canonicalize genome
→ rebuild topology_hash
→ rebuild parameter_layout_hash
→ rebuild semantic_genome_hash
→ regenerate SearchParameterManifest
→ validate flatten/unflatten against the new layout
→ compile new CompilationBundle
→ derive new render/metric/selection cache keys
→ materialize child CandidateRecordV2
```

新候选必须记录 `base_candidate_id`、base candidate record hash、Patch hash、旧/新 topology hash、旧/新 parameter layout hash 和 manifest hash。任何绑定旧 topology/layout 的 CompilerParameterTable、SearchParameterManifest、normalized vector、render cache、metric cache 或 SelectionResult 都立即失效；不得按 parameter path 猜测复用。

## 5. StructureEvolutionAgent

输入只包含：

```text
intent_ref
base_genome_ref
structural_stagnation_ref
archive_summary_ref
request_constraint_set_ref
budget_remaining
```

不传完整搜索历史。Agent 只能输出类型化 Patch，不能修改 Budget、Acceptance、RenderContract、hard constraints 或选择指针。

默认：

- 每轮 1 个主 Patch；
- 结构化输出最多修复 1 次；
- 每 Run 最多 3 个 Patch round；
- AI-off 可以规则升级，无法安全升级时直接停止。

## 6. CandidateArchive

首期使用 bounded top-k + novelty sampling：

- objective best；
- Pareto/high-score candidates；
- descriptor 代表；
- 高不确定性候选；
- 固定随机种子选择的少量样本。

行为描述符：

- 主色相；
- edge softness；
- layer count；
- symmetry；
- instance count；
- topology complexity。

只有 benchmark 证明 Archive 坍缩，才引入 MAP-Elites。

`CandidateArchive` 是有界搜索/晋级视图，不是事实源。Run 级 `RetainedCandidateIndex` 在 Run 终态前追加保存所有已 materialize Candidate 的 id、hash、hypothesis、Evaluation/Constraint refs 和生成序号；可以 GC 大型中间图像，但不得删除重评所需的内容寻址证据。Selection revision 的 watermark 基于该完整索引，而不是 bounded Archive。

## 7. Candidate Promotion

晋级前必须：

- Runtime 和 hard constraints 通过；
- 真实 WebGL EvidenceKey 有效；
- 位于 top-k、Pareto/epsilon 或 descriptor 代表集合；
- 未超过 VLM/HITL 预算；
- 与已选候选存在足够视觉差异。

Promotion 输出版本化 shortlist，不能直接改变 objective best：

```python
class ShortlistCandidateBinding:
    candidate_id: str
    semantic_genome_hash: str
    render_ref: ArtifactRefV2
    render_sha256: str
    evaluation_record_ref: ArtifactRefV2

class CandidateShortlist:
    schema_version: Literal["candidate_shortlist_v1"]
    shortlist_id: str
    shortlist_revision: int
    shortlist_hash: str
    run_id: str
    target_sha256: str
    evaluation_revision: int
    constraint_set_hash: str
    evaluation_profile_hash: str
    evaluation_evidence_key: EvaluationEvidenceKey
    candidate_index_watermark: int
    candidates: tuple[ShortlistCandidateBinding, ...]
    promotion_policy_version: str
    created_event_seq: int
```

`shortlist_hash` 对除自身外的完整 canonical 内容计算。Shortlist 创建后不可原地修改；增删候选必须生成新 revision/hash。

## 8. VLM Pairwise

```python
class PairwiseReviewRequest:
    schema_version: Literal["pairwise_review_request_v1"]
    request_id: str
    request_hash: str
    run_id: str
    shortlist_id: str
    shortlist_revision: int
    shortlist_hash: str
    evaluation_revision: int
    constraint_set_hash: str
    evaluation_profile_hash: str
    evaluation_evidence_key: EvaluationEvidenceKey
    target_ref: ArtifactRefV2
    target_sha256: str
    candidate_a_id: str
    candidate_a_genome_hash: str
    candidate_a_ref: ArtifactRefV2
    candidate_a_render_sha256: str
    candidate_b_id: str
    candidate_b_genome_hash: str
    candidate_b_ref: ArtifactRefV2
    candidate_b_render_sha256: str
    rubric_version: str
    prompt_version: str
    model_ref: str
    generation_config_hash: str
    orientation_seed: int

class PairwiseReview:
    schema_version: Literal["pairwise_review_v1"]
    review_id: str
    request_id: str
    request_hash: str
    run_id: str
    shortlist_id: str
    shortlist_revision: int
    shortlist_hash: str
    evaluation_revision: int
    constraint_set_hash: str
    evaluation_profile_hash: str
    evaluation_evidence_key: EvaluationEvidenceKey
    target_sha256: str
    candidate_a_id: str
    candidate_a_genome_hash: str
    candidate_a_render_sha256: str
    candidate_b_id: str
    candidate_b_genome_hash: str
    candidate_b_render_sha256: str
    rubric_version: str
    winner: Literal["a", "b", "tie"]
    confidence: float
    dimension_judgments: tuple[DimensionJudgment, ...]
    problem_regions: tuple[str, ...]
    reasons: tuple[str, ...]
    model_ref: str
    prompt_version: str
    generation_config_hash: str
    orientation_seed: int
```

Rubric 固定覆盖：轮廓、实例、颜色、光照、高光、阴影、边缘、材质和背景。

规则：

- 左右顺序随机并记录；
- 低置信可以镜像复审；
- 不得推翻 hard constraints；
- 只在可行 epsilon/Pareto 集中表达偏好；
- 不回写 objective best。

Review 只能应用到与 Request 完全一致的 shortlist revision/hash、candidate id/genome/render hash、EvidenceKey、Profile、evaluation revision 和 constraint set。任一绑定不一致时拒绝结果并重新生成 Request；不得把旧 Review 迁移到新 revision。

`generation_config_hash` 绑定 provider、model snapshot、temperature、top-p、max tokens、response schema、tool policy 和供应商 seed。`request_hash` 对除自身外的完整 canonical Request 计算。

Cache key：request hash + orientation + rubric + Prompt + model + generation config。镜像复审必须使用新的 Request/request hash，不能覆盖原 Review。

## 9. HITL

反馈类型：

```text
candidate_preference
region_lock
color_lock
issue_label
accept_current
increase_budget
approve_complexity
approve_range_expansion
```

```python
class FeedbackCandidateBinding:
    candidate_id: str
    semantic_genome_hash: str
    render_sha256: str
    evaluation_record_ref: ArtifactRefV2

class FeedbackRecord:
    schema_version: Literal["feedback_record_v1"]
    feedback_id: str
    run_id: str
    feedback_type: FeedbackType
    payload: FeedbackPayload
    candidate_bindings: tuple[FeedbackCandidateBinding, ...]
    expected_run_revision: int
    expected_evaluation_revision: int
    constraint_set_hash: str
    evaluation_profile_hash: str
    evaluation_evidence_key: EvaluationEvidenceKey | None
    source_shortlist_id: str | None
    source_shortlist_revision: int | None
    source_shortlist_hash: str | None
    source_event_seq: int
    idempotency_key: str
    request_body_hash: str
    created_by: str
    created_by_role: str
    created_at: str
```

`FeedbackPayload` 是以 `feedback_type` 为 discriminator 的 sealed union；每个反馈类型拥有独立 schema，例如候选偏好必须绑定两个 Candidate，region/color lock 必须绑定规范化坐标、目标对象、容差和 hard/soft 级别，预算或范围批准必须绑定增量、上限和批准范围。禁止使用未校验的任意 JSON 直接生成 Constraint。

FeedbackCompiler 通过 V2 的 canonical merge/冲突规则生成：

- 新的 `RequestConstraintSet` revision/hash；
- PreferenceSet；
- protected regions；
- frozen parameters；
- complexity/range approval。

过期 run/evaluation revision、失配的 shortlist/candidate/genome/render hash、EvidenceKey、Profile 或 constraint set 全部拒绝。幂等键只在 `run_id + created_by + request_body_hash` 一致时复用；同键不同 body 返回冲突。

## 10. 选择指针与 Evaluation Revision

```text
objective_best_id
  同一 evaluation revision 内确定性单调

preferred_candidate_id
  VLM/HITL 在可行 epsilon/Pareto 集中表达偏好

final_selected_id
  最终交付候选
```

Constraint、ROI、权重或 Profile 变化时：

```text
CAS reserve next evaluation_revision
→ freeze retained candidate index watermark
→ create staging SelectionSnapshot
→ invalidate selection cache
→ re-evaluate every retained candidate up to the watermark
→ filter feasibility under the new RequestConstraintSet/Profile
→ select objective/preferred/final inside staging snapshot
→ validate all selected candidates and evidence bindings
→ CAS atomically publish snapshot + evaluation_revision + three pointers
```

```python
class SelectionSnapshot:
    schema_version: Literal["selection_snapshot_v1"]
    snapshot_id: str
    status: Literal["staging", "ready", "published", "superseded", "failed"]
    run_id: str
    base_run_revision: int
    evaluation_revision: int
    constraint_set_hash: str
    evaluation_profile_hash: str
    evaluation_evidence_key: EvaluationEvidenceKey
    selection_policy_version: str
    candidate_index_ref: ArtifactRefV2
    candidate_index_watermark: int
    evaluation_refs: tuple[ArtifactRefV2, ...]
    preference_evidence_refs: tuple[ArtifactRefV2, ...]
    objective_best_id: str | None
    preferred_candidate_id: str | None
    final_selected_id: str | None
    snapshot_hash: str
```

重评对象是 retained candidate index，而不是旧 revision 的 feasible archive；这样放宽 constraint、complexity 或 parameter range 后，旧 revision 下不可行的保留候选也能重新进入竞争。Watermark 之后产生的 Candidate 留给下一次 snapshot，或通过显式的新 staging revision 纳入，不能在重评中途改变集合。

“re-evaluate” 只有在 RawMetric/EvidenceKey 仍兼容时才能复用原始证据；ROI、Profile 或 required pass 变化导致证据缺失时，必须补做目标尺寸 render/diagnostic pass 和 raw metric，再进行 constraint/score 计算。无法补齐证据的 Candidate 在 staging snapshot 中标为 ineligible 并记录原因，不能沿用旧分数。

构建 staging snapshot 时线上已发布三指针保持不变，但必须标记其属于旧 evaluation revision，禁止与 staging 分数混比。V4 只有在 `base_run_revision`、当前 published snapshot 和 revision CAS 全部匹配时，才能在一个 Ledger 事务中发布新 snapshot、evaluation revision 和三指针；接入 V5 Worker 后，同一提交额外携带并校验 fencing token。构建或 CAS 失败时保留旧 published snapshot，记录失败事件，不暴露半更新指针。

最终选择若不是 objective best，保存批准者、理由、旧/新指标和事件。

## 11. Graph

```text
V3 optimize_params
→ structural_stagnation_detected
→ summarize_archive
→ propose_patch
→ validate_patch
→ apply_patch
→ validate/compile/render/evaluate
→ optimize_params
→ select_objective_best
→ decide_next
   ├─ continue_search
   ├─ propose_next_patch
   ├─ promote_candidates
   │   → review_candidates
   │   → deterministic_tradeoff_selector
   ├─ offline_feedback / awaiting_feedback
   │   → apply_feedback
   │   → reserve_revision_and_freeze_candidate_watermark
   │   → create_staging_selection_snapshot
   │   → reevaluate_retained_candidate_index
   │   → select_objective/preferred/final_in_snapshot
   │   → validate_snapshot_bindings
   │   → publish_snapshot_and_three_pointers_by_CAS
   └─ promote_or_skip_memory_v4 → finalize
```

Patch 后候选不能直接晋升，必须重新经过完整确定性闭环。

## 12. State

```python
PngToShaderV4Phase = PngToShaderV3Phase | Literal[
    "structural_stagnation", "patching", "reoptimizing",
    "promoting", "reviewing", "awaiting_feedback", "selection_staging",
]

class PngToShaderV4State(PngToShaderV3State):
    state_schema_version: Literal["state_v4"]
    graph_id: Literal["png_to_shader_v4"]
    graph_version: str
    checkpoint_schema_version: str
    checkpoint_namespace: str
    phase: PngToShaderV4Phase
    structural_stagnation_ref: ArtifactRefV2 | None
    archive_summary_ref: ArtifactRefV2 | None
    current_patch_ref: ArtifactRefV2 | None
    patch_round: int
    preference_set_ref: ArtifactRefV2 | None
    retained_candidate_index_ref: ArtifactRefV2 | None
    candidate_index_watermark: int | None
    current_shortlist_ref: ArtifactRefV2 | None
    staging_selection_snapshot_ref: ArtifactRefV2 | None
    published_selection_snapshot_ref: ArtifactRefV2 | None
    preferred_candidate_id: str | None
    final_selected_id: str | None
    feedback_cursor_ref: ArtifactRefV2 | None
```

V4 继承 V3 的 Measurements/Intent/Genome、SearchJournal cursor、RequestConstraintSet、evaluation revision、objective best、Budget 和 StopReason，并将版本 discriminator 更新为 V4；`checkpoint_namespace` 必须等于 `png-to-shader-v4:{run_id}`。不得复制成第二套同名业务字段。V4 Checkpoint 只增加结构演化、评审、反馈和 SelectionSnapshot 引用。

## 13. Memory

只晋升：

- 已验证的用户约束和偏好；
- 有效 Template/Patch 策略；
- Patch 失败模式；
- 结构停滞与解决摘要。

不写完整 Genome、GLSL、图片、Residual 或逐轮搜索历史。

## 14. 实施增量

### V4.0：Patch Core

- StructuralStagnationReport；
- Patch schema/validator/apply；
- ArchiveSummary；
- Property/dry-compile tests。

### V4.1：StructureEvolution

- Prompt/Parser/bounded node；
- Graph patch routes；
- Patch Budget；
- NodeProvider/Node Lab/model fixtures；
- AI-on vs AI-off same-budget report。

### V4.2：Archive、VLM 与 HITL

- top-k + novelty Archive；
- Promotion shortlist；
- Pairwise Request/Review/Cache；
- FeedbackRecord/Compiler；
- evaluation revision 和 rescore；
- 离线人工/VLM correlation report。

## 15. 验收门槛

- Patch schema/ref/DAG/contract/property tests 通过；
- 同 Patch 结果 hash 100% 可复现；
- Agent 不突破 Budget/Contract/constraints；
- 固定真实模型集 Patch first-pass 合法率 ≥70%，一次结构化 repair 后最终合法率 ≥85%；timeout、refusal 和 parser failure 全部进入分母；
- Patch 失败不影响 objective best；
- Patch 后 topology/layout/semantic hash、Manifest 和 Cache 全部按新 Genome 重建，旧布局派生物复用数为 0；
- 结构消融关闭 Pairwise/HITL，并按总纲的共同预算向量、相同 target/seed/search policy/patch round 做 AI-on vs AI-off 配对实验；中位相对提升 ≥5%，且 target-cluster bootstrap 95% CI 下界 >0；
- sealed release test 至少覆盖 10 个 target、每个 target 固定 4–6 个 Candidate 和至少 3 名独立盲评 rater；客观排序与人工排序只在 target 内比较，预注册主指标为 target-clustered Kendall tau，点估计 ≥0.35 且 bootstrap 95% CI 下界 >0，并报告 inter-rater agreement；
- 独立 pairwise held-out 数据的人类多数偏好预测 accuracy ≥60% 且 95% CI 下界 >50%；Kendall tau 或 Bradley–Terry 得分相关性另行报告，不与排序相关性混算；
- A/B 位置等价性预注册为左右胜率差绝对值不超过 5 个百分点，使用 paired equivalence test；样本量由 power analysis 决定且不少于 30 个镜像样本，只有 90% CI 完整落入 `[-5pp, +5pp]` 才通过，不能用“差异不显著”代替等价；
- Pairwise/Feedback 的 shortlist、Candidate、Genome/render hash、EvidenceKey、Profile、evaluation revision、constraint set 和 generation config 绑定校验 100% 通过；
- SelectionSnapshot 发布具备并发/CAS/failpoint 测试，不出现跨 revision 三指针或半发布状态；
- Pairwise 低置信阈值在 visible validation 上冻结；低于阈值或镜像结果冲突时强制 tie/HITL，不自动选中；
- region lock 后保护区相对退化 ≤2%；
- 反馈完整绑定 revision/metric/prompt/event；
- 新真实模型 benchmark 和独立人工门禁通过。
