# Refine Selection 与真实 WebGL 链路加固方案

状态：`implemented`

范围：F09 Layered Direct GLSL 当前未提交实现上的三项加固：

1. uniform-only 搜索遇到候选级非硬失败后继续推进；
2. `current_best` selection、`min_delta`、patience 与 MAE/loss 双目标统一；
3. 用一条真实 Chromium WebGL1 多参数 E2E 锁住完整证明链。

本方案不引入新优化算法、不扩大默认 draw/LLM 预算，也不运行 benchmark。

实施结果（2026-07-29）：

- uniform candidate 非硬失败已改为有界 probe recovery，硬资源失败仍立即终止；
- selection 已改为 target-relative MAE/loss excess dominance，policy 升级为
  `direct_optimization_policy_v2` 并增加 `min_delta_mae`；
- optimizer 升级为 `uniform_coordinate_v2`，summary 升级为同时包含
  MAE/loss before、after 与 delta 的 `uniform_optimization_summary_v2`；
- 真实 Chromium WebGL1 多参数 E2E 已覆盖 float + vec3、prepared program
  复用、真实 draw、fresh receipt/attestation、hash 边界和资源释放；
- Python/Frontend 聚焦回归、Ruff、Mypy、前端构建均已通过。

## 1. 目标与不变量

### 1.1 非硬失败只淘汰当前 probe

候选级失败包括 trusted uniform Patch 应用失败、候选重复、静态
compile/validation 失败，以及不涉及 renderer/global budget 的证明失败。

这些失败必须：

- 保留 incumbent；
- 记录稳定失败码和 private trace；
- 把当前 move 作为 failed probe 推进 search state；
- 继续尝试配对方向或下一个 component；
- 消耗一个本地 probe 槽，保证异常候选不会形成无限循环；
- 不伪增真实 draw、evaluated 或 accepted ledger。

以下硬失败仍立即终止：

- `renderer_unavailable`；
- `draw_budget_exhausted`；
- `compile_budget_exhausted`；
- 已设置的 attempt 级 `refinement_blocked`。

若一个 session 没有 material improvement，且期间存在候选级失败，最终 reason
使用 `candidate_failures_exhausted`，不再错误报告为 `local_optimum`。

### 1.2 selection 使用双目标超额支配

对任一候选定义：

```text
mae_excess  = max(0, mae  - target_mae)
loss_excess = max(0, loss - target_loss)
```

Initial 没有 incumbent，直接成为 `current_best`。后续候选只有同时满足下列条件
才可替换 incumbent：

```text
candidate.mae_excess  <= incumbent.mae_excess
candidate.loss_excess <= incumbent.loss_excess
and 至少一个 excess 严格下降
```

这个规则意味着：

- 两个指标都未达标时，不接受以另一维退化换来的单维改善；
- loss 已达标时，允许 loss 在目标区间内波动以继续降低 MAE；
- MAE 已达标时，同理允许其在目标区间内波动以继续降低 loss；
- 两个 excess 都归零时命中现有双 target 早停，不再继续 selection。

不使用加权和或任意 tie-breaker，避免重新引入一个没有校准依据的隐式目标。

### 1.3 material improvement 同时认识 MAE 与 loss

`DirectOptimizationPolicy` 增加：

```text
min_delta_mae = 0.001
```

在候选已通过双目标 selection 的前提下：

```text
material =
    loss_delta >= min_delta_loss
    or mae_delta >= min_delta_mae
```

因此：

- MAE 的有效改善可以重置 patience；
- 仅在目标区间内重新分配、但两个 delta 都不足的候选仍算 minor improvement；
- 未被选择的候选永远不算 material；
- policy schema/fingerprint 升级并进入结果契约。

### 1.4 真实 WebGL E2E 必须证明的边界

测试使用真实 `PlaywrightWebGL1Renderer`，而不是 fake renderer。输入 Layer 至少
包含一个 `float` 和一个 `vec3` tunable，并覆盖：

```text
LayerPlan
→ LayeredShaderSpec
→ tunable_manifest flatten
→ trusted UniformPatch
→ source prepare
→ uniform binding draw
→ fresh receipt
→ attestation
→ metric/selection
→ final DirectAttemptResult
```

断言至少包括：

- optimizer 实际产生并评估候选；
- 最终候选来自 `uniform_optimize`；
- source SHA-256 与父候选一致；
- binding/spec SHA-256 与父候选不同；
- receipt 绑定最终 spec 且可验证；
- prepared program 被复用，候选仍发生真实 draw；
- ledger、summary 和 stop reason 自洽；
- renderer 最终被关闭。

Chromium 或 WebGL1 运行环境缺失属于测试环境失败，不以 `skip` 隐藏。

## 2. 实现切分

### 2.1 非硬失败恢复

主要修改：

- `src/shaderforge/uniform_optimization/search.py`
- `src/shaderforge/uniform_optimization/models.py`
- `src/agent/app/nodes/layered_direct/uniform_optimization_nodes.py`
- 对应独立 unit/graph regression tests

search state 增加有界失败计数或等价状态，使 failed probe 能前进，同时保持
move ordinal 单调和 draw ledger 真实性。

### 2.2 双目标 selection

主要修改：

- `src/agent/app/contracts/layerplan_glsl_direct.py`
- `src/agent/app/nodes/layered_direct/candidate_nodes.py`
- Refine feedback/progress/result policy projection
- 对应独立 selection regression tests

selection comparator 应提取为纯函数，避免 Refine 和 uniform 对同一候选使用
不同规则。

### 2.3 真实多参数 WebGL E2E

主要修改：

- `tests/integration_tests/test_layered_direct_uniform_real_renderer.py`
- 必要的最近 README/ARCHITECTURE 测试入口说明

测试数据必须确定性生成，不写入仓库输出目录，不依赖外部模型和网络。

## 3. Subagent 边界

- Subagent A：只负责 uniform search 非硬失败恢复和专属测试。
- Subagent B：只负责双目标 selection、policy v2 和专属测试。
- Subagent C：只负责真实 WebGL E2E 与最近测试文档。
- 主 Agent：审查三方语义、解决集成冲突、补跨模块回归并运行最终门禁。

共享 graph state 或公共 contract 如发生冲突，由主 Agent 统一合并，subagent
不得覆盖无关未提交改动。

## 4. 验收矩阵

非硬失败：

- `+step` 候选级失败后仍测试 `-step`；
- 当前 component 两侧失败后继续下一 component；
- failed probe 不增加真实 draw/evaluated；
- failed probe 会消耗本地有界槽，不可能无限循环；
- renderer/global budget failure 仍立即停止；
- 最终 reason 不把失败耗尽伪装成 local optimum。

双目标 selection：

- lower-loss 但 MAE excess 变差：拒绝；
- lower-MAE 但 loss excess 变差：拒绝；
- loss 已达标，loss 在目标内波动且 MAE excess 下降：接受；
- MAE 已达标，对称场景接受；
- 两个 excess 同时下降：接受；
- MAE-only material improvement 重置 patience；
- 双方只有 minor improvement：增加 non-improving streak；
- policy 非 finite、越界 `min_delta_mae` fail closed。

真实 WebGL：

- `float + vec3` manifest 可被展平和搜索；
- 至少一次真实 uniform candidate draw；
- source 不变，binding/spec 改变；
- receipt、attestation 和 executable 校验成功；
- Direct graph 正常 finalize 并释放 renderer。

## 5. 验证命令

实现过程中运行各自聚焦测试。集成后运行：

```bash
uv run ruff check src backend tests
uv run mypy <本次受影响源码>
uv run pytest <三组聚焦测试>
uv run pytest tests/integration_tests/test_layered_direct_uniform_real_renderer.py
make docs-check
uv run langgraph validate
make check-wheel
```

前端契约未变化时不扩大全量浏览器测试。
