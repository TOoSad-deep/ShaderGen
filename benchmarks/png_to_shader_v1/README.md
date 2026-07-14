# PNG to Shader V1 Benchmark

本目录保存 F09 V1 的确定性参考图和验收规格。

## 文件

- `manifest.yaml`：样例元数据、PNG SHA-256、目标能力、关键 ROI 和初始容差；
- `m5_gate.yaml`：运行前冻结的 M5 自动指标和人工盲评门槛；
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

真实模型基准必须显式允许调用并受整套硬预算限制：

```bash
make benchmark-png-to-shader QUALITY_PRESET=balanced MODEL_CALL_BUDGET=80
```

每次运行写入 `output/benchmarks/png-to-shader-v1/<suite-run-id>/`。新运行的 config schema v2 在首个模型调用前冻结 manifest、gate、三个结构化角色的模型路由、thinking/response format、Prompt 运行策略和预算；每个 case 原子保存 AI-off/AI-on 的 GLSL、PNG、指标、安全模型审计和父候选关系；report schema v2 汇总 initial/final、编译率、调用、token、耗时、best 更新、config/manifest/gate hash，以及从逐调用审计取得的 requested/actual model。旧 schema v1 若 AI-on 不完整会拒绝续跑，避免 dotenv 模型快照不可靠时混用模型。

自动部分完成后打开 `blind-review/index.html`，在不知道 A/B 对应 initial 还是 final 的情况下完成全部 10 项并下载 JSON。最终门禁只重新读取原运行的 `config.json`，不会覆盖基准配置或再次调用模型：

```bash
make benchmark-gate \
  BENCHMARK_OUTPUT=output/benchmarks/png-to-shader-v1/<suite-run-id> \
  HUMAN_REVIEW=/absolute/path/to/human-review.json
```

失败或中断运行不会删除；使用同一输出目录可按已经落盘的 case 断点续跑。AI-off nightly 默认执行，AI-on 只有 `PNG_TO_SHADER_AI_BENCHMARK_ENABLED=true` 或手动 workflow 勾选时才消耗模型预算。

2026-07-13 的正式 run 为 `output/benchmarks/png-to-shader-v1/m5-20260713-balanced-v3/`：AI-off/AI-on 的 compile、static 与 traceability 均为 10/10，但 initial-final 改善只有 1/10，pink-gel 专项也失败。2026-07-14 独立评审完成 10/10 选择，9 个为平局、1 个偏好 final，final 偏好率 10% 低于 50%；原始 JSON 归档于 `blind-review/human-review.json`。自动与人工门禁均失败，最终 gate 为 `failed`。该 no-go 证据不能通过调整同轮阈值改写。
