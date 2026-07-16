# PNG to Shader V1 Benchmark

本目录保存 F09 V1 的确定性参考图和验收规格。

## 文件

- `manifest.yaml`：schema v1 样例元数据、canonical contract、PNG SHA-256、目标能力、关键 ROI 和初始容差；字段白名单、非空身份、坐标系统和同 case ROI id 唯一性会严格校验；
- `m5_gate.yaml`：schema v1、运行前冻结的 M5 自动指标和人工盲评门槛；未知字段/版本、bool 或越界整数阈值会 fail closed；
- `images/*.png`：由 `scripts/build_png_to_shader_v1_benchmarks.py` 生成的 10 张参考图；
- `golden/pink_gel.frag`：M1 WebGL1 Renderer 的无贴图 golden Shader。

## 生成

```bash
uv run python scripts/build_png_to_shader_v1_benchmarks.py
```

生成脚本只使用 Python 标准库，固定尺寸、公式和压缩设置。修改公式或尺寸后必须重新生成全部图片、更新 manifest 中对应 SHA-256，并运行 M0 测试。

这些样例只用于验证 V1 的契约、基础视觉层和后续指标单调性，不代表复杂照片的通用复刻能力。

## M1 Renderer smoke

```bash
uv run playwright install chromium
uv run pytest tests/integration_tests/test_webgl1_renderer.py
uv run python scripts/render_m1_golden.py
```

最后一个命令把真实渲染 PNG、standalone HTML、GLSL、Renderer 元数据和 Basic Oracle 分数写入 `output/playwright/m1-golden/`。

## M5 benchmark

AI-off smoke 不调用模型，验证完整 10 例都能跨过静态 Validator、真实 Chromium WebGL1 Renderer 和 Basic Oracle：

```bash
make benchmark-ai-off
```

真实模型基准必须显式允许调用并受整套硬预算限制。模型调用已经发生、但 case 后处理失败时，runner 优先从返回 State 的模型审计恢复实际调用数；无法获得可靠计数时按该 case 的分配上限保守扣账，不能把未知消费记为零：

```bash
make benchmark-png-to-shader QUALITY_PRESET=balanced MODEL_CALL_BUDGET=80
```

每次运行写入 `output/benchmarks/png-to-shader-v1/<suite-run-id>/`。新运行的 config schema v3 在首个模型调用前冻结 manifest、gate、三个结构化角色的模型路由、thinking/response format、Prompt 运行策略、预算、`manifest_key_rois_v1` objective 和 initial 选择策略。GLSL、PNG、指标和审计文件先落盘，最后以原子替换的 `result.json` 提交该 case；缺少有效 `result.json` 时恢复流程会重做该 case，不能把同目录中的半写 Artifact 视为已完成事务。report schema v3 汇总 initial/final、编译率、调用、token、耗时、best 更新、config/manifest/gate hash，以及从逐调用审计取得的 requested/actual model。

自动改善门禁只比较同口径 pair：initial 是首个 `origin=model` 且完成硬约束与评分的候选，final 是最终 `current_best`；runner 对两张冻结 PNG 都使用 manifest `key_rois` 独立重算 objective。`initial_internal_total_loss` / `final_internal_total_loss` 只保留生产选择器内部评分供诊断，不进入改善 gate。确定性候选可以成为 final，但必须报告 `origin` 和 `generator_version`；旧 CandidateRecord 缺少 `origin` 时按模型候选兼容。若没有成功模型 initial，该 case 明确不可比较并跳过盲评包生成。bbox 则直接比较 candidate 测量结果与 manifest `expected_foreground_bbox_uv`。

旧 config schema v1/v2 的完整运行仍可只读复算原结论；不完整的 AI-on 运行不允许跨到 schema v3 继续，以免混合模型路由或 objective 语义。必须换新的 `suite-run-id`，旧 output 不会被覆盖。

自动部分完成后，新式运行只把 `blind-review/reviewer/` 交给评审者，并打开其中的 `index.html`；私有 `assignments.private.json` 和 `evidence-manifest.json` 位于父目录，不得随评审者包分发。manifest 冻结 source render、公开 assets、index、template 与 assignment 的 byte size/SHA-256，首次 `report.json` 再锚定 manifest SHA；evaluate 必须在读取人工 JSON、覆盖报告前完整复验这些证据，不会再次调用模型。评审 JSON 与私有 assignments 必须保持 schema v1、相同 suite run、非空 reviewer 和完全一致且无重复的 10 个 case；非法 choice、缺失、多余或路径漂移都会 hard fail。报告同时显示 final/initial/tie、不同图对数量和 bit-identical case，但这些诊断不会改变冻结阈值：

```bash
make benchmark-gate \
  BENCHMARK_OUTPUT=output/benchmarks/png-to-shader-v1/<suite-run-id> \
  HUMAN_REVIEW=/absolute/path/to/human-review.json
```

没有 `blind_review_evidence_schema` 的历史运行不会被原地迁移或事后补签，仍打开旧 `blind-review/index.html`。evaluate 会重建稳定 A/B 映射，并按冻结 v1 页面/template 逐字节核对 source 与 assets；为避免泄露 assignment，历史包只能单独提供 `index.html` 与 `assets/`，不能把整个 `blind-review/` 目录交给评审者。

失败或中断运行不会删除；使用同一输出目录可按已经提交 `result.json` 的 case 断点续跑，例如：

```bash
uv run python scripts/run_png_to_shader_v1_benchmark.py \
  --mode ai-on \
  --quality-preset balanced \
  --model-call-budget 80 \
  --allow-model-calls \
  --output-dir output/benchmarks/png-to-shader-v1/<suite-run-id>
```

AI-off nightly 默认执行。`PNG_TO_SHADER_AI_BENCHMARK_ENABLED=true` 是 GitHub Actions repository variable 的远端门禁；本地 runner 不读取它，本地真实调用必须显式同时提供 `--allow-model-calls` 和正数硬预算。

本文件只维护 benchmark 协议，不累积日期化运行流水账。当前发布结论见 `PROGRESS.md` 和 `docs/FEATURES.md`；正式 run 的脱敏摘要、文件大小、SHA-256 与耐久性状态见 `docs/evidence/registry.json`。`durability_status=partial` 表示完整报告仍只在本地忽略目录，不能仅凭该索引宣称跨环境可复验。
