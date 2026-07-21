# Ultra dataset 探索性运行

本目录把仓库根目录 `dataset/` 的 16 张 PNG 固定为一套在线 Ultra 探索集。它通过正在运行的 Backend 产品 API 发起真实模型请求，用于观察端到端成功率、质量分数、模型调用数、耗时和最终 Artifact；它不是 F09 M5 正式 benchmark，也不构成发布质量门禁。

运行前先确认 Backend 已加载预期的 `png_to_shader_runtime_policy.v2.yaml`，并且 `http://127.0.0.1:8088/health` 可用。每个 case 都会创建独立 `project_id`，runner 使用多进程并行提交请求。

先用两个结构差异明显的样本冒烟：

```bash
uv run python scripts/run_ultra_dataset_benchmark.py \
  --cases twitter-blue-check,shiny-rectancle \
  --concurrency 2 \
  --model-call-budget 80 \
  --allow-model-calls
```

再运行 16 个样本全量探索集：

```bash
uv run python scripts/run_ultra_dataset_benchmark.py \
  --concurrency 2 \
  --model-call-budget 640 \
  --allow-model-calls
```

并发默认是 `2`，runner 硬限制为 `1..4`。建议先保持 `2`：进程并行只负责同时提交独立 HTTP 请求，实际吞吐仍受 Backend、Renderer、模型供应商限流和本机资源约束。只有在两例冒烟稳定、供应商配额明确且本机资源充足时才提高到 `3` 或 `4`。每例按 Ultra 最坏情况预留 40 次模型调用，因此两例至少需要预算 80，全量 16 例至少需要预算 640；实际计费和调用次数以供应商及逐例 manifest 为准。

默认单请求硬超时为 2520 秒，整套 wall-time 为 21600 秒。自定义并发或超时时，runner 会在发起模型调用前检查整套 wall-time 能否覆盖所有批次。结果默认写到新的 `output/benchmarks/ultra-dataset/<run-id>/`，禁止覆盖已有目录；`report.json` 会在每个 case 完成后原子刷新。运行中断后不会自动复用旧目录续跑，可用 `--cases` 在新运行中补跑指定 case。

边界说明：

- `report.status=completed` 只表示收到全部所选 case 的终态，不表示全部生成成功或达到质量阈值；同时查看 `failed_cases` 和 `threshold_met_cases`。
- `threshold_passed` 当前仅表示产品评分 `total_loss <= 0.12`，是探索性观测，不是 M5 gate。
- 这套数据没有冻结的 ROI、expected primitive、golden Shader、人工盲评或 release-held-out split，不能替代 `make benchmark-png-to-shader` 及其正式证据链。
- 必须显式传入 `--allow-model-calls`，并用 `--model-call-budget` 给整套运行设置最坏情况硬预算；不要把真实模型运行加入普通测试或 CI。
- runner 会校验最终 manifest 中的 runtime-policy SHA-256；配置身份不一致会把该 case 标为失败。
