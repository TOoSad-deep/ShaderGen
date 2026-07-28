# scene_mvp Agent 优化实施情况

> 归档状态：历史实施报告，不是当前任务清单。

## 1. 状态与范围

- 日期：2026-07-23
- 所属功能：`F09`
- 状态：P0 工程机制已实现并通过无模型门禁；真实模型质量收益与人工偏好尚未验证
- 来源建议：`2026-07-23-scene-mvp-run-85506ab8-agent-optimization.md`
- Graph：保持 12 个节点、直接边、条件边、路由结果和 `langgraph.json` 注册不变

本轮实施建议文档定义的最小增量：冻结 run 身份、补齐 Patch/空间残差/拒绝证据，并让非重复合法 typed Patch 在与 `current_best` 比较前获得一次有界、Patch-local 的成熟机会。同时完成与该选择语义直接相关的 Refine 后搜索调度：不再重跑完整 base/feature sweep。

本报告是工程实施与验证摘要，不是冻结 benchmark、真实模型质量或发布通过证据。`F09` 继续 `active/no-go`。

## 2. Run 身份与配置门禁

### 2.1 冻结口径

D058/D059 对应的冻结口径为：

| 项目 | 冻结值 |
| --- | --- |
| target MAE/loss | `0.08/0.04` |
| fast render/LLM/Refine | `48/2/1` |
| balanced render/LLM/Refine | `96/4/2` |
| high render/LLM/Refine | `160/6/3` |

YAML 声明 `run_classification=frozen_benchmark` 时，目标或任一档预算与上述值不一致都会在启动配置加载阶段 fail closed。冻结身份禁止设置 `experiment_id`。

### 2.2 当前独立实验

当前工作树保留既有实验预算，不把它改写为冻结默认值：

| 字段 | 实际值 |
| --- | --- |
| run classification | `independent_experiment` |
| experiment id | `scene-mvp-agent-optimization-20260723` |
| report schema | `scene_mvp_run_report_v1` |
| target MAE/loss | `0.04/0.02` |
| fast | `48/2/1` |
| balanced | `96/4/2` |
| high | `640/9/9` |
| config SHA-256 | `b6c03fcaae77fcaa74bde1988ab345941d9e2557c1d422d203701b9c3b88af06` |

独立实验必须提供 `experiment_id`。run classification、实验 ID、报告版本、实际目标/预算和配置指纹进入进度预算摘要、API `min_pipeline`、metrics、manifest 与运行总账，禁止跨身份混算。

## 3. Patch 与空间残差证据

### 3.1 空间残差

新增不参与评分的确定性摘要：

```text
tile_grid = 4
worst_tiles = top 2 by MAE
signed_luminance_bias = rendered - reference
signed_rgb_bias = rendered - reference
dominant_metric_component
active_feature_summary
```

排序固定为 `mae desc, row asc, column asc`。正 bias 表示候选过亮或对应通道过高，负 bias 表示候选过暗或对应通道过低。该增量没有修改 `min_scene_composite_v3` 的公式、权重或 metric version。

### 3.2 Patch 安全摘要

四类 typed Patch 统一映射为：

```text
patch_operation
feature_id
feature_type
patch_fingerprint
```

指纹使用严格 typed Patch 规范 JSON 的 SHA-256。终态证据追加：

```text
raw_candidate_loss
matured_candidate_loss
best_loss_before / best_loss_after
raw_metric_deltas / matured_metric_deltas
maturity_draw_count / total_candidate_draw_count
accepted / rejected_reason
duplicate_of_recent
duration_ms
```

delta 约定为 `candidate-best`，正值表示变差。最近拒绝窗口固定保留 3 项，完全相同指纹再次出现时不分配 render/maturity draw。

### 3.3 隐私边界

允许持久化：

- typed operation、feature id/type；
- SHA-256；
- 有界数值 metric/delta；
- 安全错误码、拒绝原因、重复标记和节点耗时。

禁止进入 Patch 摘要、终态 trace 和 `agent_events`：

- 完整 Patch value 或 Scene diff；
- 参考图、渲染 RGB/PNG；
- 完整 GLSL；
- 用户输入；
- 模型原始响应；
- reasoning content；
- 供应商异常原文。

## 4. 有界候选成熟

新选择语义为：

```text
只读 current_best
→ 应用一个非重复合法 typed Patch，建立 candidate branch
→ raw draw
→ 只优化 Patch 影响范围
→ matured candidate
→ 严格改善则原子提交，否则整支丢弃
```

Patch 范围与预算：

| Patch | 成熟范围 | 总 draw 上限 |
| --- | --- | ---: |
| add feature | 新增稳定 feature | 12 |
| replace feature | 被替换的稳定 feature | 12 |
| replace color field | color-field bindings | 12 |
| remove feature | 只做 raw 重评分 | 1 |
| 非法/重复 | 不渲染 | 0 |

`12` 的冻结语义是 raw 1 次加局部成熟最多 11 次，全部计入 run 的现有 render budget。候选 branch 内允许 raw loss 暂时差于 best；branch 内参数仍只接受严格下降。Renderer 失败会拒绝整支，不会污染 best。

Refine branch 在 `render_and_evaluate` 内完成成熟和最终选择，随后沿既有边进入 `optimize_base`；该节点识别已完成的 Refine branch 后执行 no-op 过桥，`feature_queue=()`，不再重复全量 base/feature sweep。Graph 拓扑未变，但 `current_best` 安全语义已同步 Builder ASCII 附注、Mermaid、安全说明和路由表。

## 5. Prompt 与调度变化

Refine Prompt 升级为 `min_author_refine_v1_3`：

- 明确 signed bias 是 `rendered-reference`；
- 局部 tile 主导时优先 feature，全局同方向偏差才考虑 color field；
- 主体内部、边缘、外部残差分别映射到已有 feature 白名单；
- 检查 active feature 和近期拒绝摘要；
- 不为占满槽位虚构 feature，不重复近期已拒方向。

本轮没有修改 Initial Author Prompt，也没有加入跨多轮的收益阈值或停滞自适应停止；这些仍需在真实模型固定 manifest 上独立验证。

## 6. Graph 与预算

节点、边和路由结果保持不变。由于 Refine 后不再重新遍历全部 feature，合法最坏节点步数由 D060 的旧公式：

```text
9 + 2F + R × (6 + 2F)
```

修正为：

```text
9 + 2F + 6R
```

其中 `R=min(refine_budget,max(llm_budget-1,0))`、`F<=4`。当前 high 档合法路径为 65 步，增加 4 步框架余量后注入 `recursion_limit=69`。全局 256 防御上限和 `GraphRecursionError` fail-closed 语义不变。

## 7. 核心 Fixture 结果

构造的 `add_feature` fixture 使用灰度目标验证“raw 暂差、成熟后胜出”：

| 阶段 | composite loss |
| --- | ---: |
| current best | `0.023529419` |
| raw feature，`intensity=0.30` | `0.058823531` |
| matured feature，`intensity=0.22` | `0.000000000` |

该候选使用 1 次 raw 加 11 次局部 draw，总计 12，最终严格优于 best 并提交。另有反例证明：

- 完全重复的近期拒绝 Patch：0 draw；
- 非法 Patch：0 draw；
- remove feature：1 次 raw、0 次局部成熟；
- Renderer 失败：1 次失败 draw，best 不变；
- 成熟后仍不改善：整支回滚；
- color-field 候选不触碰 primitive/background/features；
- prepared program 签名和 uniform schema 不变。

## 8. 验证结果

| 命令 | 结果 | 说明 |
| --- | --- | --- |
| `make check` | 通过 | 493 个单元测试、docs-check、2 个 Graph validate、Frontend production build |
| `uv run ruff check src backend tests` | 通过 | 全仓静态规范 |
| `uv run mypy --strict src backend` | 通过 | 148 个源文件 |
| `make docs-check` | 通过 | Graph/文档边界检查 |
| `uv run langgraph validate` | 通过 | 2 个 Graph |
| Renderer/固定质量/递归/进度定向 Integration | `11 passed, 1 skipped` | skip 为显式性能探针；使用真实 Chromium，无模型调用 |
| `make test-scene-mvp-ui` | 通过 | 隔离假 API 页面验收 |
| `uv run pytest -q tests/integration_tests` | `38 passed, 1 skipped, 1 failed` | 唯一失败为 `.env` 中 `TEST_DATABASE_URL` 的占位主机 `HOST` 无法解析；与本轮逻辑无关 |
| `git diff --check` | 通过 | 无空白错误 |

本轮没有调用真实模型，没有执行付费 benchmark，也没有提交匿名人工评审。

## 9. 尚未实施或尚未证明

- Initial Author 的完整视觉结构分解 Prompt；
- 基于多轮收益的自适应停滞停止阈值；
- primitive silhouette 与 shadow/glow 的 geometry 评价拆分；
- 固定 7 例真实模型 benchmark；
- 独立匿名人工偏好；
- CMA-ES、2000 draw、动态 Compiler、多 program cache；
- 任意 GLSL 或更多 feature 槽。

现有 deterministic fixture 只证明候选成熟、安全回滚、预算与证据机制，不证明模型一定会提出正确的高光/暗部结构。当前 geometry objective 仍可能奖励外部 shadow 对候选前景 mask 的扩张，修正前不得仅凭 loss 下降宣称视觉结构问题已经解决。

## 10. 工作树与证据边界

开始本轮前，工作树已经存在 `scene_mvp` v3、YAML 实验预算、递归上限、进度 API 和复盘文档等未提交改动；本轮在该基线上增量实现，没有回退或覆盖它们。`make test-scene-mvp-ui` 刷新了仓库内既有 Playwright 截图，它们只是页面验收副产物，不是算法质量证据。

本报告和单元/集成测试属于仓库内可审计工程证据。没有新增真实模型 run Artifact 或 durable benchmark 条目；本地 output 不得冒充跨环境可复验发布证据。
