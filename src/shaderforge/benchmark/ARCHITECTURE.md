# ShaderForge Benchmark 架构

`benchmark/` 保存 F09 M5 的确定性数据集加载、AI-off baseline、聚合门禁和人工盲评包生成能力。它不调用模型，也不决定 Agent 的运行策略。

## 当前能力

- 校验固定 manifest 中每张 PNG 的 SHA-256、尺寸、bbox 与关键 ROI；
- 从 `TargetMeasurements` 生成固定椭圆 Shader，验证无模型条件下 Validator、WebGL1 Renderer 和 Basic Oracle 可运行；
- 以运行前冻结的 `m5_gate.yaml` 聚合 compile、静态校验、initial/final 改善、current_best 单调性、证据可追溯性和粉色凝胶局部门槛；
- 用稳定 hash 随机化 initial/final 的 A/B 位置，生成不在页面中泄露映射的静态人工盲评包；
- 人工结果缺失时返回 `pending_human_review`，不会把自动指标冒充成人工结论。

## 边界

- 真实模型调用、预算、逐样例恢复和输出目录编排属于 `scripts/run_png_to_shader_v1_benchmark.py`；
- runner 必须在首个模型调用前冻结 config schema v2，并在报告中以逐调用审计为准区分 requested/actual model；旧 config 若缺少可靠模型路由且运行不完整，不允许跨环境续跑；
- benchmark 阈值只能在运行前由版本化配置冻结，不能根据同一轮结果动态移动；
- `assignments.private.json` 只供 gate 解码，评审人只打开 `blind-review/index.html`；
- 失败样例必须保留安全事件、候选和产物引用，不保存或公开 reasoning 文本；
- nightly 默认运行 AI-off smoke；AI-on 受仓库变量和密钥显式控制，避免无意消耗模型预算。
