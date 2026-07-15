# ShaderForge Benchmark 架构

`benchmark/` 保存 F09 M5 的确定性数据集加载、AI-off baseline、聚合门禁和人工盲评包生成能力。它不调用模型，也不决定 Agent 的运行策略。

## 当前能力

- 校验固定 manifest 中每张 PNG 的 SHA-256、尺寸、bbox 与关键 ROI；
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

## 边界

- 真实模型调用、预算、逐样例恢复和输出目录编排属于 `scripts/run_png_to_shader_v1_benchmark.py`；
- runner 必须在首个模型调用前冻结 config schema v3，其中包含 objective 与 initial 选择策略；report schema v3 同时保存 objective loss、生产内部 loss 和候选来源，并以逐调用审计为准区分 requested/actual model；
- 旧 CandidateRecord 缺少 `origin` 时按 `model` 兼容；旧 config schema v1/v2 的完整运行仍可只读评估，但不完整 AI-on 不允许续接到新 objective，必须使用新的 suite run；
- 若一个 case 没有成功的模型候选，objective pair 明确为不可比较且不生成盲评包，不允许确定性 seed 同时占据 initial 与 final 制造虚假改善或偏好证据；
- benchmark 阈值只能在运行前由版本化配置冻结，不能根据同一轮结果动态移动；
- 新式 `assignments.private.json` 只供 gate 解码，评审人只接收并打开 `blind-review/reviewer/index.html`；没有 evidence schema 标记的历史 run 保持只读，继续使用旧 `blind-review/index.html`，evaluate 通过冻结 v1 页面/template 和稳定映射逐字节兼容校验，不给历史产物事后补签；
- 证据校验和可观测字段只增强审计强度，不改变已冻结的人工偏好分母、50% 阈值或历史 run 结论；有效的 schema v1 评审继续兼容；
- 失败样例必须保留安全事件、候选和产物引用，不保存或公开 reasoning 文本；
- nightly 默认运行 AI-off smoke；AI-on 受仓库变量和密钥显式控制，避免无意消耗模型预算。
