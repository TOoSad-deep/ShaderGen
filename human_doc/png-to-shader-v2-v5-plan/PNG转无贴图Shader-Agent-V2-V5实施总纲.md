# PNG 转无贴图 Shader Agent：V2–V5 实施总纲

> 状态：已完成拆分后正式 Review
> 日期：2026-07-16
> 适用范围：静态、WebGL1、无贴图、L0–L2 程序化视觉目标

## 1. 文档集

- [V2：Intent IR、Effect Genome 与 Compiler](./V2-IntentIR-Genome-Compiler实现方案.md)
- [V3：Oracle V2 与 Deterministic Search](./V3-Oracle-Search实现方案.md)
- [V4：StructureEvolution、VLM Pairwise 与 HITL](./V4-StructureEvolution-VLM-HITL实现方案.md)
- [V5：Async Run 与产品化](./V5-AsyncRun-产品化实现方案.md)
- [正式 Review 报告](./REVIEW.md)

本文件只保存跨版本不变量、依赖关系、兼容策略和最终 Definition of Done。版本内部契约、Graph、任务和验收只在对应版本文档维护。

## 2. 当前基线

V1 已具备 WebGL1 无贴图契约、确定性图像测量、静态 Validator、真实 Chromium Renderer、Basic Oracle、Candidate/current_best、三模型角色有界 Graph、Artifact、Memory、Node Lab 和固定 benchmark。

当前正式自动门禁已通过，但人工偏好仍暴露以下结构问题：

- topology 和 instance count 保护不足；
- ring/hollow、轮廓和镂空可能被低频 affine 近似破坏；
- shadow、rim、highlight 等小语义层可能在总像素损失中被稀释；
- 自动 objective 与人类结构感知不完全一致。

V2 启动前先冻结 M6.2 的逐例诊断和结构标签，但不无限期依靠 Prompt 调优延迟架构迁移。

## 3. 总体演进路线

```text
V1
PNG → Measurements → VLM/LLM → Free GLSL → WebGL → Basic Oracle

V2
PNG → MeasurementsV2 → RequestConstraintSet → Intent IR
    → SeedPlan → Effect Genome → Deterministic Compiler → WebGL

V3
Genome → SearchParameterManifest → Cheap/WebGL Render
       → Oracle V2 → Deterministic Search → AI-off Objective Best

V4
Structural Stagnation → GenomePatch → Re-optimize
    → Candidate Promotion → VLM/HITL → Final Selection

V5
Async Run → Durable Worker/Checkpoint → SSE/Cancel/Resume
    → Shared Artifact/Renderer Pool → Replay/Dashboard/Nightly Gate
```

| 版本 | 主要交付 | 产品定位 | 建议周期 |
|---|---|---|---:|
| V2 | Intent、Genome、Compiler、Seed | 内部 Alpha | 4–5 周 |
| V3 | Oracle、Search、Cache、AI-off | Beta | 3–4 周 |
| V4 | Patch、Archive、VLM、HITL | Release Candidate | 2–3 周 |
| V5 | Async Run、恢复、SSE、正式 UI | 正式版本 | 3–5 周 |

表内周期是净开发关键路径估算，假设 2 名 Agent/后端工程师、1 名 Shader/评测工程师和 0.5 名 UI/QA 支持，且不包含 F09/M6.2 收口、外部人工评审等待和真实模型配额等待。V2 与 V3 必须串行；V5 控制面设计可在 V3 后期开始，产品 UI 切换等待 V4 数据契约稳定。发布排期应在 12–17 周净工期上增加 25% 风险与稳定化缓冲，即约 15–22 周；团队配置或外部依赖变化时重新估算。

## 4. 跨版本不变量

### 4.1 V1 历史不可破坏

- V1 Artifact、Candidate、Score 和 benchmark 只读兼容；
- 不用新指标重新解释历史 V1 current_best；
- 不执行破坏性 Artifact 或数据库批量迁移；
- 旧格式通过只读 Adapter 加载；
- 失败 benchmark 和人工证据只增不改。

### 4.2 RenderContract 稳定

继续使用 `webgl1_static_no_texture_v1`：

- GLSL ES 1.00；
- `precision mediump float`；
- 静态 `u_time = 0`；
- 禁止纹理采样；
- 坐标、DPR、Y 翻转、alpha 和颜色空间统一；
- 真实 WebGL compile/link/draw/capture 是最终事实。

Agent 版本变化不创建新的 RenderContract。只有运行时语义真实变化时才升级 contract id。

### 4.3 语义外循环与确定性内循环

Agent 负责：

- 视觉层解释和不确定性；
- Strategy/Template 选择；
- SeedPlan；
- 参数停滞后的 GenomePatch；
- 晋级候选 Pairwise 评审。

ShaderForge 负责：

- Measurements、Intent/Genome/Patch 校验；
- Compiler、Renderer、Oracle、Search；
- Cache、Archive、Selector、Artifact；
- Budget、Stop 和 Acceptance。

模型不参与参数搜索；编译是否成功、预算是否耗尽、候选能否晋升不得由模型决定。

### 4.4 State 与 Artifact

Checkpoint 只保存：

- run/project/graph/schema id；
- 当前阶段、游标和小型摘要；
- ArtifactRef；
- 选择指针；
- Budget 使用量；
- StopReason 和版本。

图片、完整 Intent/Genome/Patch、GLSL、Render、Residual、Score、搜索轨迹和私有模型证据进入 Artifact Store。

所有 V2+ State 必须实现同一个版本化 Envelope；版本文档只能在其上扩展，不能删减恢复字段：

```python
class VersionedRunStateCore:
    state_schema_version: str
    graph_id: str
    graph_version: str
    checkpoint_schema_version: str
    checkpoint_namespace: str
    project_id: str
    run_id: str
    run_revision: int
    phase: str
    evaluation_revision: int
    budget_state: BudgetStateV2
    stop_reason: str | None
```

State 中的 Cursor 只指向已由对应 Store/CAS 确认的 Journal、Feedback 或 Selection Snapshot；未确认的外部副作用不能仅靠 State 恢复。

### 4.5 版本与 Hash

最终 Manifest 至少绑定：

```text
input hash
contract id
intent schema
genome schema
topology hash
parameter layout hash
semantic genome hash
compiler/renderer/metric/search versions
evaluation profile/revision
prompt/model versions
random seed and budget
graph/state/checkpoint versions
code/build version
candidate lineage and artifact hashes
```

不同 EvaluationEvidenceKey 的分数禁止直接比较。

## 5. Graph 与 Checkpoint 版本矩阵

| graph_id | state schema | checkpoint namespace | 状态 |
|---|---|---|---|
| `png_to_shader_v1` | `state_v1` | `png-to-shader-v1:{project_id}`；兼容裸 project id 清理 | 产品/历史基线 |
| `png_to_shader_v2` | `state_v2` | `png-to-shader-v2:{run_id}` | Alpha |
| `png_to_shader_v3` | `state_v3` | `png-to-shader-v3:{run_id}` | Beta |
| `png_to_shader_v4` | `state_v4` | `png-to-shader-v4:{run_id}` | RC |

规则：

- `langgraph.json` 一次只注册当前 product-active Graph；
- 未发布版本通过 Builder、Node Lab 和 benchmark 运行；
- 切换产品 Graph 前先停止接收旧版本新 Run，并处理在途 Run；
- 历史查看优先读取 Ledger/Artifact，不要求旧 Graph 永久注册；
- V5 Worker 使用所选 Graph 的 namespace builder，不能写裸 run id。

## 6. 选择语义

V1 历史字段保留 `current_best`；V2 起统一使用 `objective_best_id`。V4 起再拆分为三类选择指针：

- `objective_best_id`：同一 evaluation revision 内确定性单调；
- `preferred_candidate_id`：VLM/HITL 在可行 epsilon/Pareto 集中表达偏好；
- `final_selected_id`：最终交付选择。

VLM/HITL 不覆盖 objective best。最终选择若牺牲 raw objective，必须记录批准者、原因、旧/新指标和事件。

Constraint、ROI、权重或 EvaluationProfile 改变时递增 `evaluation_revision`，禁止跨 revision 比较。

## 7. 功能状态顺序

遵守一次只有一个 active 功能：

| 顺序 | 功能 | 版本 |
|---:|---|---|
| 1 | F09 M6.2 人类偏好诊断收口 | V1 |
| 2 | F02 Intent IR | V2.0–V2.1 |
| 3 | F03 Genome/Compiler/Graph | V2.2–V2.3 |
| 4 | F04 Oracle V2 | V3.0 |
| 5 | 从现有 F05 拆出的 Deterministic Search | V3.1–V3.3 |
| 6 | 新功能：StructureEvolution/Review/HITL | V4 |
| 7 | 新功能：Async Run/Productization | V5 |

V3/V4 开始前，先把当前同时包含 Search 与 VLM/HITL 的 F05 拆成两个互不重叠的功能。

## 8. 统一量化协议

- 数据拆成 `development`、`visible_validation`、`sealed_release_test`；按同源效果、近重复图和派生变体分组切分，Manifest、样本 hash 和分组规则版本化。sealed 集只在发布候选冻结后使用，任何基于其结果的调参都会触发新 sealed 版本；当前 10 例只作为回归集，不作为发布统计证据。
- topology、instance、ring、hollow、required-layer 分别报告 exact match、macro recall/F1 和 95% CI，并为每个关键正类冻结最小样本数，禁止用大量负类稀释结果。
- 所有保护指标先通过版本化映射转为 `[0,1]` 且 lower-is-better 的 `ProtectedRegionLoss`。相对退化定义为 `max(0, L_candidate-L_incumbent)/max(L_incumbent, epsilon)`，同时报告绝对 delta；IoU、coverage 等 higher-is-better 指标不得直接代入该公式。逐子指标 hard gate、`epsilon` 和绝对阈值在评测前冻结。
- 人工评测只在 target 内比较候选：每个 target 使用冻结数量的候选、至少 3 名独立且盲测的 rater、随机顺序和显式 tie policy；先聚合 target 内排序或 Bradley–Terry 分数，再按 target 做 cluster bootstrap 95% CI，并报告 inter-rater agreement。
- 独立 pairwise 数据预先冻结一个 primary metric 及阈值，可选 pairwise accuracy、Kendall tau 或 Bradley–Terry 相关性；不直接计算 Spearman，也不允许运行后择优挑指标。
- A/B 位置偏差冻结允许偏差 margin，并对镜像对使用 equivalence test/CI；只有完整 CI 落入 margin 才通过，样本数由功效分析决定。
- AI-on/off 至少拆成结构消融与成本实验。结构消融关闭 Pairwise/HITL，固定 target、seed、search policy、patch round、cheap/WebGL evaluation 数和模型生成参数；成本实验使用冻结价格快照比较 quality-at-fixed-cost。共同预算以版本化向量表达：wall time、render/evaluation 次数、model calls/tokens 和货币成本，Primary endpoint 与 CI gate 预先冻结。
- 所有质量、性能和恢复实验绑定 `BenchmarkManifest`：代码/模型/Prompt/Compiler/Renderer/Metric 版本、硬件镜像、数据 Manifest、并发、缓存冷热、预热、重复次数、随机 seed、聚合公式和阈值。
- p95 必须记录硬件、负载、环境、分层场景、样本数、计时起止点和百分位算法；每个报告分层少于 100 个样本时只作观察值，不作发布 p95 门禁。
- 真实付费 Run 前冻结阈值和统计方法，运行后不得移动；变更必须新建协议版本并重新运行完整对照。

## 9. 发布策略

1. V2/V3 首先只在 Node Lab、CLI 和 benchmark 启用；
2. V2 以结构合法、可编译、可追溯为首要门槛；
3. V3 自动、结构和人工门禁通过后，Genome 路径成为默认；
4. V4 Pairwise/HITL 先离线再接产品；
5. V5 上线时旧 `/generate` 保留一个版本，仅继续执行 V1 同步路径；
6. 新 UI 稳定后弃用阻塞产品路径；
7. 历史 V1 Run 永久只读回放。

## 10. 最终 Definition of Done

- PNG 可生成区分事实、推断、不确定和约束的 Intent；
- Intent 可生成多个合法、可解释 Genome；
- Genome 可确定性编译为 WebGL1 无贴图 GLSL；
- 每个参数有稳定 path、范围、单位、问题域和 affected regions；
- Oracle 分离拓扑、实例、形状、颜色、边缘、coverage 和语义层；
- 固定拓扑 Search 可复现并改善 Seed；
- AI-off 独立输出 Genome、GLSL、PNG、Score 和 Manifest；
- Agent 只在结构阶段提出受限 Patch；
- VLM/HITL 只在硬约束后参与偏好选择；
- objective best 在同一 evaluation revision 内单调；
- Run 可异步、取消、暂停、恢复和回放；
- Worker/Renderer 崩溃不丢失已确认证据；
- 已有合法候选时 objective best 可下载；无候选时返回 `no_candidate_available`；
- 自动、人工、性能、成本和恢复门禁全部通过；
- 历史 V1 产物不被新版本错误重解释。
