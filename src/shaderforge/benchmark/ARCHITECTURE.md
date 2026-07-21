# ShaderForge Benchmark 架构

`benchmark/` 保存 F09 M5 的确定性数据集加载、AI-off baseline、聚合门禁和人工盲评包生成能力。它不调用模型，也不决定 Agent 的运行策略。

## 当前能力

- 只接受 manifest/gate schema v1，并校验字段白名单、canonical contract、非空身份、坐标系统、每张 PNG 的 SHA-256/尺寸/bbox/关键 ROI、ROI id 唯一性，以及整数阈值的类型与非负范围；未知版本和未知字段 fail closed；
- 从 `TargetMeasurements` 生成固定椭圆 Shader，验证无模型条件下 Validator、WebGL1 Renderer 和 Basic Oracle 可运行；
- 以运行前冻结的 `m5_gate.yaml` 聚合 compile、静态校验、initial/final 改善、current_best 单调性、证据可追溯性和粉色凝胶局部门槛；
- 用稳定 hash 随机化 initial/final 的 A/B 位置；新式公开包只写入 `blind-review/reviewer/`，私有 assignment 与 evidence manifest 留在父目录，评审者目录不得包含角色映射；
- 新式 evidence manifest 冻结 source render、公开 assets、index、template 和私有 assignment 的 byte size/SHA-256；首次 `report.json` 再锚定 manifest SHA，evaluate 必须在读取 human review 和覆盖报告前依次复验 config/report 锚点、manifest 与逐文件内容；
- 人工证据载入时严格校验 review 与 assignments 的 schema、suite run、非空 reviewer、A/B 角色、渲染路径和 exact case 集合；重复、多余、缺失或非法 choice 一律 hard fail，不再静默跳过；
- runner 基于冻结 initial/final PNG 的 SHA-256 汇总 final win、initial win、tie、不同图对数量和 bit-identical case，帮助区分评审偏好不足与 final 根本未变化；
- AI-on 的 model initial 与 final 都由 runner 使用 manifest 冻结的 `key_rois` 独立重算 `manifest_key_rois_v1` objective；gate 不再比较生产选择器内部可能使用动态保护区的两种 loss；
- bbox gate 直接比较 candidate 测量 bbox 与 manifest 的 `expected_foreground_bbox_uv`，不再把参考图自动测量 bbox 当作期望；
- 候选证据显式区分 `origin=model` 与 `origin=deterministic`；确定性候选必须带 `generator_version`，可以成为 final，但不能冒充首个模型 initial；
- 人工结果缺失时返回 `pending_human_review`，不会把自动指标冒充成人工结论。
- M6.2 诊断通过 `m6_2_diagnostics.py` 只读绑定既有 M5 report/run-evidence、Candidate suite/Artifact render、GLSL/provenance hash、原始 `input/source.bin`、规范化 `input/reference.png`、发布门禁共用的匿名 A/B 解码语义，以及 V2 development 的 topology/instance/hole/required-layer 标签；标签必须由 source bytes SHA-256 反向证明，deterministic provenance 必须绑定 normalized reference。`measurement_affine_seed_v1` v2 能力策略只声明单实例、无孔、solid 与 `base_fill`，一阶 RGB affine 不冒充 Gaussian `color_lobe`。结果只报告 `supported | unsupported | unknown`，不把能力覆盖误写成像素保真结论。
- `m6_2_selector_replay.py` v2 只读重放旧正式 run 的 `model initial → affine seed` 单一选择点：严格要求 source report 的 `config_sha256` 等于实际 `config.json` bytes，suite config 与每个 run-evidence 的 acceptance policy 完全一致，run-evidence Candidate 与 Artifact `manifest.json` 完全一致，metrics 与 score vector 完全一致；成功 compile 必须字段封闭、`success=true`、`draw_error=null`、static valid/零 violations，source chars、Candidate hard constraints、GLSL/render hash 也必须一致。随后同时调用未启用 admission 的真实 Selector 基线与显式 `offline_replay` admission 的同一 Selector。报告固定 `production_enabled=false`，并用 case/decision/report 交叉校验防止重算 hash 后伪造 accepted/status/reason/codes，不顺序推演后续模型路径。

## 边界

- 真实模型调用、预算、逐样例恢复和输出目录编排属于 `scripts/run_png_to_shader_v1_benchmark.py`；
- 全局模型调用预算必须 fail-closed：后处理异常优先恢复实际调用数，无可靠计数时按 case 分配上限扣账；异常与取消都保存不含异常原文和 reasoning 的安全失败证据；
- case Artifact 在 `result.json` 之前写入，`result.json` 通过原子替换提交完成状态；缺失该文件的目录不是已完成事务，恢复时必须重做；
- runner 必须在首个模型调用前冻结 config schema v3，其中包含 objective 与 initial 选择策略；report schema v3 同时保存 objective loss、生产内部 loss 和候选来源，并以逐调用审计为准区分 requested/actual model；
- 旧 CandidateRecord 缺少 `origin` 时按 `model` 兼容；旧 config schema v1/v2 的完整运行仍可只读评估，但不完整 AI-on 不允许续接到新 objective，必须使用新的 suite run；
- 若一个 case 没有成功的模型候选，objective pair 明确为不可比较且不生成盲评包，不允许确定性 seed 同时占据 initial 与 final 制造虚假改善或偏好证据；
- benchmark 阈值只能在运行前由版本化配置冻结，不能根据同一轮结果动态移动；
- `scripts/run_m6_2_structure_diagnostics.py` 只能写入既有 suite 和 run Artifact 根之外的新路径，并以 exclusive create 保证目标已存在时绝不覆盖；报告缺少 source/normalized reference/run-evidence/Candidate render/GLSL/provenance/人工选择/标签任一锚点都必须 fail closed。它是诊断证据，不改变 Selector、Prompt、M5 manifest/gate 或旧 run 结论；
- `scripts/run_m6_2_seed_admission_replay.py` 同样只能在 suite、run Artifact 和 diagnostic 根之外 exclusive-create；replay 中 human preference 只用于事后汇总，不进入 Selector 输入。v1 报告因缺少 strict compile/config 与完整交叉字段校验只作为错误产物保留，不再作为当前证据；当前只认 v2。Replay 只证明原选择点会被 admission 接受或拒绝，不能证明新最终 Candidate、人工偏好率或发布 gate；
- 新式 `assignments.private.json` 只供 gate 解码，评审人只接收并打开 `blind-review/reviewer/index.html`；没有 evidence schema 标记的历史 run 保持只读，继续使用旧 `blind-review/index.html`，evaluate 通过冻结 v1 页面/template 和稳定映射逐字节兼容校验，不给历史产物事后补签；
- 证据校验和可观测字段只增强审计强度，不改变已冻结的人工偏好分母、50% 阈值或历史 run 结论；有效的 schema v1 评审继续兼容；
- 失败样例必须保留安全事件、候选和产物引用，不保存或公开 reasoning 文本；
- nightly 默认运行 AI-off smoke；AI-on 受仓库变量和密钥显式控制，避免无意消耗模型预算。

## V2.0 数据边界

- `v2_dataset.py` 只负责严格加载和审计 V2 数据 Manifest、expected-primitives taxonomy 与 split readiness，不运行 Intent、Genome、Compiler、模型或质量评分；
- Manifest 固定 `development`、`validation`、`release-held-out` 三份，样本路径以仓库 `benchmarks/` 为安全根，并逐项复验 SHA-256、图片尺寸、结构标签和 taxonomy 引用；每个 `source_suite_id` 必须精确对应一条带来源 URL、license id/URL 和内容寻址 provenance 文档的 `source_records`，记录漂移即 fail closed；`visual_family`、`hash_group` 或相同图片内容均不得跨 split；
- 当前 V1 固定 10 例只能进入 `development/regression`。`validation` 已有 41 张带 CC0 来源链的可见实图，覆盖基础结构、金属按钮和爆炸/烟雾，并以五个完整 visual family/hash group 计数；采用 `sealed_release_test` 访问策略的 `release-held-out` 仍显式标为 `not_populated`，不得借用 development 或 validation 分母；
- readiness 分别报告 `multi_instance`、`ring`、`hollow`、`required_highlight`、`required_rim`、`required_outline` 的实际正例分母。每类冻结下限为 10，状态不可用、空 split 或任一类不足均不得宣称该 split ready；完整 `V2DatasetReadiness.ready` 仍表示 validation 和 release-held-out 同时就绪；
- 实施准入必须用相同 `gate_stage` 调用 `load_v2_dataset_manifest()` 与 `evaluate_v2_dataset_stage_gate()`：V2.1 Intent、V2.2 Genome/Compiler 和 `v2_3_graph_conformance` 只要求 validation，并在读取图片前强制 release-held-out 为 `not_populated`；只有 `v2_3_release_candidate` 同时要求 validation 和 release-held-out。StageGate 拒绝裸 Manifest/Readiness，复验磁盘内容并绑定 dataset version 与 Manifest/taxonomy SHA-256；
- taxonomy v1 冻结首期 Effect Genome 节点与历史 expected-primitives 标签的映射；每项 `node_kind@node_version` 必须与 `effect_node_registry_v0` 精确匹配，不能只满足集合覆盖。当前只声明模板覆盖关系，不伪造 validation/release-held-out 样本或发布证据。
- V2 loader、Manifest/Readiness、StageGate 及其报告类型从 `shaderforge.benchmark` 公共根导出；单元门禁实际构建 wheel，并在排除仓库源码的解释器中从 wheel 导入这些公共类型，防止源码树通过而发布包缺失。

## V2.1 Intent conformance

- `v2_1_intent_gate.py` 只接受与同一 StageGate 绑定的完整 development 10 + validation 41 outcome 闭包；缺失、重复、额外 case、身份/hash 漂移或任何 release-held-out outcome 均 fail closed。
- 报告冻结 current 10 Intent 合法 10/10、validation Intent 合法率至少 80%、validation instance exact，以及六个关键类的 TP/FP/FN、recall、F1、macro recall/F1 和 Wilson 95% CI；每类 recall 至少 90%，分母来自已验证 Manifest，instance exact 当前只报告、不发明方案外阈值。
- `scripts/run_v2_1_intent_benchmark.py` 固定为 `fixture/no-model`、模型预算 0、`quality_claim=conformance_only_not_vlm_quality`。它在任何 config/Artifact 写入前统一冻结并复验 51 张 source 的 SHA-256，关闭 StageGate 后二次读图的 TOCTOU；输出目录 exclusive-create，所有成功/失败、Measurements、ConstraintSet、Interpretation、Intent 和 outcome 均内容寻址保留，失败不得移出分母。
- 当前实跑 51/51 Intent 合法、validation instance exact 41/41，六类 recall/F1 与 macro 均为 1.0。fixture 使用 stage-verified taxonomy 和冻结标签构造 conformance 输入，因此结果不代表真实 VLM 视觉质量，不读取 release-held-out，也不启用 production admission。

## V2.2 Genome/Compiler conformance

- `v2_2_compiler_gate.py` 只接受同一 V2.2 StageGate、同一 config SHA-256 和同一 V2.1 input outcomes SHA-256 绑定的完整 development 10 + validation 41 outcome 闭包；缺失、重复、额外 case、身份漂移或任何 release-held-out outcome 均 fail closed，失败 case 始终保留在 51/153 分母内。
- 每个合法 Intent 必须产生恰好三个 `TypedEffectGenome`；三者 semantic genome hash 必须唯一，结构签名至少两种，全部结果必须通过 Seed diversity gate、双次确定性编译一致性检查与静态 Validator。任一阶段失败均保存稳定 `failure_code`，不得用部分成功缩小分母。
- `scripts/run_v2_2_compiler_benchmark.py` 从本次运行内 exclusive-create 的 V2.1 fixture/no-model 内容寻址产物恢复 Intent，绑定 Manifest、taxonomy、V2.1 config/report/outcomes hash；模型开关固定关闭、预算与调用数固定为 0，且只读取 development 与 validation。
- 默认报告只声明 static conformance，并以 `webgl_requested=false`、`webgl_compiles_and_draws=null` 明示未运行浏览器；只有显式传入 `--with-webgl` 才对全部 153 个编译结果执行 WebGL1 compile/link/draw，并把真实成功数纳入 ready 门禁。

## V2.3 Graph conformance

- `v2_3_graph_gate.py` 是 development/validation 的纯聚合门禁，不是 release candidate 门禁；它只接受独立 `v2_3_graph_conformance` StageGate 和完整 development 10 + validation 41 outcome 闭包，明确拒绝任何 release-held-out outcome。
- 每个可行 Intent/hypothesis 固定保留三次 seed attempt 分母；outcome 必须声明 `hypothesis_count` 与 `expected_seed_attempt_count=hypothesis_count×3`，并要求完整终态、每个 seed 恰好对应一个正式 `CandidateRecord` 或 `CandidateAttemptRecord` immutable Artifact 闭包。runner 只通过公开 strict `load_typed_candidate_artifacts` / `load_candidate_attempt` 恢复证据，逐条交叉 run、hypothesis、semantic Genome 与 evidence refs；重复、错绑、旧 failure payload 或 tamper 一律失败。受 D060 约束，supported solid branch 必须产生 objective best；当前缺少 typed topology receipt 的 ring/hollow/multi branch 必须以 capability/constraint 一致的 reason code 闭合为 expected-unsupported/no-candidate，不得用 generic exception，也不得为通过门禁而放宽 hard constraint。
- v2 report 不再用一个终态 reopen bool 冒充恢复证据。配置按 Manifest 顺序在 development/validation 各冻结一个 solid/single/zero-hole 代表，分别对 `measured`、`interpreted`、`seeding`、`compiled`、`rendered`、`evaluated`、`materialized`、`selected` 八个中间 checkpoint 注入落盘后崩溃；随后重建 StateStore、Catalog、runtime 与 production Graph，逐 phase 比较副作用计数、完整预算、Artifact closure、游标、evaluation revision 和最终 semantic projection。split 必须逐 phase 1/1，总计必须逐 phase 2/2，缺 phase、重复 phase 或任何子证据失败均阻断。
- 报告分别保存 development 与 validation 的 case/动态 seed/Artifact/restart/CAS 指标，再给出总计；若真实可行 hypothesis 增加则 seed 分母同步增加。fixture/no-model 必须模型调用数为 0、`production_admission_enabled=false`。
- `scripts/run_v2_3_graph_benchmark.py` 只能通过 production V2 Graph Builder 与 production node runtime 执行，不得在 runner 复制 pipeline；输出目录 exclusive-create，配置绑定 Manifest、taxonomy、V2.1 outcomes 与 V2.2 outcomes SHA-256。release-held-out 仍由独立 `v2_3_release_candidate` StageGate 管理，当前 Graph conformance 不读取、不填充也不解封。
- `v2_release_handoff.py` 与 `scripts/run_v2_release_operator_handoff.py` 只供独立发布保管人在仓库外封存环境使用：先以固定 10 的生产分母、完整来源/许可和三 split 污染检查冻结 package，再以 Ed25519 将 Manifest/taxonomy/referenced-package SHA、freeze label 与预期 code/config SHA 绑定。readiness attestation 使用 exclusive-create，只允许输出六类聚合分母、内容 hash、版本、时间和 blocker 类别计数；stdout/attestation 禁止出现 release case id、文件名、路径、来源 URL、逐例标签或逐例结果。开发侧只接收 attestation、公钥和后续 aggregate-only evaluation，并用独立可信渠道预登记的公钥 SHA-256 调用 `verify`；consumer 还必须显式提供预期 code/config SHA、freeze label 与固定 `v2_3_release_candidate` stage，阻止同一保管人旧 RC 证据跨版本重放。不得把 attestation 自带身份作为唯一信任根，也不接收 release package、freeze 文件或签名私钥。详细职责与模板见 `docs/V2-RELEASE-OPERATOR-HANDOFF.md`。
- Graph conformance runner 以 `langsmith_tracing_enabled=false` 和 `tracing_context(enabled=False)` 双重关闭离线 tracing；其 `_FixtureRenderer` 只是冻结 reference PNG 的确定性 Graph 控制流 fixture，配置/摘要明确标记 `deterministic_reference_png_fixture_not_chromium`，真实 Chromium 证据来自独立 integration test，不得由本门禁伪报。该 runner 继续固定 fixture/no-model 与模型调用数 0；真实模型验证不得复用或改变它的 schema、报告或通过条件。
- 当前 v2 report 完整实跑 development 10 + validation 41 均 ready：51/51 case、162/162 动态 seed Attempt 与正式 immutable Artifact 闭包、20/20 supported branch best、18/18 objective-best case、33/33 expected-unsupported/no-candidate case、102/102 unsupported Attempt，八个 restart phase 均为 2/2，模型调用 0、production admission 关闭；代表 case 冻结为 development `solid_circle` 与 validation `freegameui_medium_circle_blue_gold_01`，配置 SHA-256 为 `ab02eee3316f43199282da7c690ada369950407d177aef5f98222c72ece0864d`，outcomes SHA-256 为 `3b9fa32033e9cea1b0cb258d129801b65d38f32450d1c42190165c1d02e6aad2`，并分别绑定 V2.1 `cbd83ca7cfa9eb818e906b34e40027180f8531eeae9b12e50f79245c7d492918` 与 V2.2 `3cf35d653b78d7437879eb543c2011f411e68386390af8bd043d7c7a8b234ce3` outcomes。

## V2.3 strict actual-render structure gate

- `v2_3_actual_chromium_replay.py` 使用内置 `PlaywrightWebGL1Renderer` 对每个 Candidate 的完整 RenderPlan 逐项重放，禁止 factory 注入和相同 beauty source 去重。beauty 要求 RGB MAE 不超过 `1/255` 且 alpha 完全一致；diagnostic 要求最大 RGBA 通道差不超过 1，并通过公开 `project_visible_delta_mask_v3()` 复算 canonical packed-bit mask。
- 当前 formal chain 只接受 diagnostic compilation/GLSL/render v3、RenderPlan v3、Rendered Evidence/Verification v4 和 metric v3.2；benchmark config v3 把这些 Schema 与 `stable_instance_ordinal_first_match_v1` policy 纳入 config SHA。旧 strict-v3 产物保持审计但已 superseded，不能与新报告混合。
- Candidate receipt 绑定 renderer 模块源码、host、contract、实际浏览器/WebGL 字段、逐项 persisted environment/ref/hash 与像素比较；Agent collector 必须覆盖 confirmed State 中 `hypothesis×3` 的全部 Candidate，并验证单一 actual environment 与单一 persisted environment。成功 receipt 由调用 runner 独立持久化，不能只保存聚合数字。
- `v2_3_rendered_structure_gate.py` 的正式 `evaluate_v2_3_rendered_structure_gate()` 只接受同进程 strict collector 签发的不可序列化 capability。普通 `V2_3RenderedGraphCaseOutcome` tuple 只能进入模块私有统计内核，不能用于 admission；fake outcome、PIL 图像、fixture renderer 或伪 ref 即使统计完美也不能生成 ready 正式报告。
- Outcome v4 显式绑定全部 Candidate refs、逐 Candidate actual replay receipt hashes/root、actual/persisted environment 和 selected Candidate v3 structure evidence/verification；Split/Gate v5 继续冻结 development 10 + validation 41 分母，任何 State、Artifact、replay 或 projection 失败都保留为 non-ready case。
- 完整本地入口为 v2 `uv run python scripts/run_v2_3_rendered_structure_benchmark.py --output <new-dir> --suite-run-id <unique-id>`。它先在新输出目录运行当前 V2.1/V2.2 fixture/no-model 输入门禁并绑定两份 outcomes SHA-256，再通过 high-level development Service 运行 State v4/Graph 2.4；config、ordered outcomes、逐 Candidate actual replay receipts、Gate v5 report、summary 与文件 SHA-256 清单均持久化。默认每 case Graph 预算是 wall 300000ms、render 512、candidate 64、artifact 512MiB，可用四个 `--case-*` 参数收紧；suite Graph render 上限为 case×51，独立 replay 另有同量上限，总 actual render 上限为两者之和。summary 保存 Graph 实测调用、成功 replay receipt item 调用及失败 replay 的保守未闭合上界；model/token/cost 不提供可变入口并固定为 0。v2 的 hypothesis 分母由 production `build_intent_variants()` 的 feasible variants 派生并在 fixture policy v2 留下 source/rejection 聚合证据；runner/config v1 因误用 raw Measurements hypotheses 已 superseded，旧 `strict-v1` 目录必须原样保留且不得混入新报告。
- `scripts/run_v2_3_graph_benchmark.py` 的 reference-PNG Graph v2 报告只保留为 superseded control-flow audit，不能进入 strict actual-render gate，也不能替代上述 runner 的 Chromium、Candidate v3 或 capability 证据。

## V2.3 真实模型可见集验证

- `v2_3_real_model_validation.py` 与 `scripts/run_v2_3_real_model_validation.py` 构成独立、版本化的真实模型验证面。它只处理可见的 development 10 + validation 41，强制使用 `v2_3_graph_conformance` StageGate 并拒绝读取或填充 `release-held-out`；报告恒定 `production_admission_enabled=false`、`release_ready=false`、`vlm_quality_claim=not_evaluated`，因此不能充当 release candidate、VLM 质量或匿名盲评证据。
- runner 不复制 pipeline：每例都以 `execution_mode=real` 调用当前 `PngToShaderV2DevelopmentService` 及其 production 22-node Graph，并在 durable operation 已提交后对同一 Service 执行一次 resume，复验未新增 model call/token/cost。Renderer 仍标记为确定性 reference PNG 控制流 fixture，所以报告只记录真实 Interpretation 调用、Intent/structure/Graph 终态，不宣称 Chromium 像素质量。
- 运行授权必须同时提供 `--execution-mode real`、`--allow-model-calls`、`--enable-real-model`、唯一 `--suite-run-id` 和显式 `module:callable` durable provider factory；provider/model/prompt version+SHA/pricing policy identity 在首调前冻结。缺 factory、身份错绑、已有输出目录或任一开关缺失均在调用前 fail closed。
- case 与 suite 都必须显式给出 wall time、model calls、model tokens、render calls、candidate attempts、artifact bytes、cost 七维硬预算；model token 再拆成 input/output 两个上限，所以 CLI 共需八个数值字段。每例模型调用上限固定为 1，suite 必须逐维覆盖 51 例最坏情况。outcome 分开记录 used/reserved，split 与 suite 报告聚合两者并复验硬预算；parse、output budget、Interpretation validation、provider indeterminate、identity、operation、Service 与 resume 失败均保留在完整分母内。
- 默认测试只用 durable fake gateway，覆盖成功、parse failure、output budget、identity mismatch、预算预检、报告 tamper、resume 零新增调用、完整 51 例遍历及 wheel 公共导入，不联网也不计费。原 `scripts/run_v2_1_intent_benchmark.py`、`scripts/run_v2_2_compiler_benchmark.py` 和 `scripts/run_v2_3_graph_benchmark.py` 继续是 fixture/no-model 门禁，模型调用数必须为 0，真实报告也不能被它们的 conformance report schema 接受。
