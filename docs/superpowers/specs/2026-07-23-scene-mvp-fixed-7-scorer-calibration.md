# scene_mvp 固定 7 例 scorer 方向校准

日期：2026-07-23

## 结论

本轮在冻结的 7 个支持案例上完成无模型、真实 Chromium 的 geometry-first 反事实校准。生产 scorer、Prompt、Graph、预算和目标均未修改。

结果不支持“geometry 分量整体错误”或“直接降低 geometry 权重”：

- 7/7 的 `geometry_mask_loss`、内部复合 loss 和外部 `png_to_shader_score_v1` 总分都改善。
- geometry-first 经常删除确定性感知 fallback 默认添加、但参考图并不存在的 shadow，并修正主体轮廓；这些是合理的整体改善。
- 但是 6/7 至少有一个像素、语义区域或关键 ROI 出现方向相反的微小变化；其中 `ellipse_gradient` 和 `arc_highlight_orb` 两例达到实质回退阈值。
- `rimmed_disk`、`arc_highlight_orb` 和 `pink_gel` 的 fallback 与 geometry-first 结果都没有恢复参考图的 rim、弧形高光或双高光。geometry-first 可以显著降低总分，却没有解决当前发布阻塞的关键结构表达。

因此当前根因收敛为：geometry 对整体轮廓和错误 shadow 有用，但加权总和缺少局部结构的 no-regression 保护。下一步应先离线验证多尺度 tile 回退 guard，而不是降低 target、直接重配 geometry 权重，或扩大 maturity 搜索预算。

## 实验身份与边界

- 实验类型：`offline_no_model_fixed_7_scorer_calibration`
- 固定案例：`solid_circle`、`ellipse_gradient`、`shadow_disk`、`rimmed_disk`、`arc_highlight_orb`、`color_lobes`、`pink_gel`
- 输入 manifest：`benchmarks/png_to_shader_v1/manifest.yaml`
- 对照 baseline：`benchmarks/png_to_shader_v1/scene_mvp_fixed_template_v3_baseline.json`
- Renderer：Playwright Chromium WebGL1
- 模型调用：0
- 每例逻辑搜索预算：base 32 draw + existing shadow 32 draw
- 总物理 Renderer draw：462
- 搜索目标：`geometry_mask_loss` 优先，其次 `total_loss`
- 人工门禁：未执行；本报告中的看片结论只是工程分析，不是独立人工偏好票

执行命令：

```bash
uv run python scripts/run_scene_mvp_scorer_calibration.py \
  --output-dir output/diagnostics/scene-mvp/fixed-7-scorer-calibration/20260723-v2
```

权威报告：`output/diagnostics/scene-mvp/fixed-7-scorer-calibration/20260723-v2/report.json`

SHA-256：

```text
722fdb75bfe7402b45299d4d6199bb429392f7cbdd913457d89c248d224dcbdd
```

`20260723-v1` 是首次运行，原件保留不覆盖；v2 只增加了“任意方向冲突”和“实质方向冲突”的分层判定，渲染搜索结果不变。

## 方法

### 主体语义代理

每例使用确定性感知 fallback 的解析 circle/ellipse 作为诊断代理，拆分：

- 主体内部：归一化椭圆距离 `<=0.90`
- 主体边缘：距离 `(0.90, 1.10]`
- 主体外效果：距离 `>1.10` 且 reference 距背景超过生产阈值 `0.05`
- 主体外保护背景：距离 `>1.10` 且 reference 不属于前景

该解析椭圆不是人工真值，只用于区分主体内部、边缘和主体外投影/光效。

### 冲突分层

- 任意方向冲突：内部复合 loss 改善，同时任一像素、语义区域或关键 ROI 代理恶化超过 `1e-6`。
- 实质方向冲突：像素/语义区域恶化超过 `0.005`，或关键 ROI 恶化超过冻结 baseline 的 `0.01` 容差。

任意方向冲突用于暴露补偿关系，实质方向冲突用于避免把浮点噪声或极小变化误报为质量阻断。

## 数值结果

| 案例 | 内部 loss Δ | geometry Δ | global MAE Δ | 外部 objective Δ | 结论 |
|---|---:|---:|---:|---:|---|
| `solid_circle` | `-0.025178` | `-0.121754` | `-0.005904` | `-0.010199` | 整体改善，只有极小 foreground 补偿 |
| `ellipse_gradient` | `-0.013388` | `-0.127866` | `+0.006849` | `-0.004589` | 实质冲突 |
| `shadow_disk` | `-0.006169` | `-0.038864` | `-0.000435` | `-0.004049` | 整体改善，shadow ROI 小幅回退 |
| `rimmed_disk` | `-0.020316` | `-0.118196` | `-0.000037` | `-0.004932` | 整体改善，两个 ROI 接近容差 |
| `arc_highlight_orb` | `-0.016061` | `-0.117277` | `+0.003715` | `-0.003784` | 实质冲突 |
| `color_lobes` | `-0.024056` | `-0.119342` | `-0.004121` | `-0.011422` | 全部自动代理同向 |
| `pink_gel` | `-0.008703` | `-0.042998` | `-0.002491` | `-0.006523` | 整体改善，center haze 小幅回退 |

负值表示 geometry-first 更好，正值表示回退。

关键 ROI 回退：

| 案例 | ROI | Δ |
|---|---|---:|
| `ellipse_gradient` | `upper_color` | `+0.019894` |
| `shadow_disk` | `shadow` | `+0.005786` |
| `rimmed_disk` | `center` | `+0.006594` |
| `rimmed_disk` | `rim_left` | `+0.009643` |
| `arc_highlight_orb` | `highlight_upper_left` | `+0.011853` |
| `pink_gel` | `center_haze` | `+0.002668` |

其中 `ellipse_gradient/upper_color` 与 `arc_highlight_orb/highlight_upper_left` 超过冻结 `0.01` ROI 回归容差。

## 视觉检查

三列 contact sheet 顺序为 reference、fallback、geometry-first。

- `solid_circle`、`ellipse_gradient`、`rimmed_disk`、`arc_highlight_orb` 和 `color_lobes`：geometry-first 去除了 reference 中不存在的默认 shadow，整体方向合理。
- `shadow_disk` 与 `pink_gel`：保留了主体外软影，但仍没有恢复正确的主体明暗结构。
- `rimmed_disk`：两种候选都缺显著 rim。
- `arc_highlight_orb`：两种候选都缺左上弧形高光，geometry-first 的该 ROI 还回退 `0.011853`。
- `pink_gel`：两种候选都缺双高光；geometry-first 的 aggregate 分数改善不能解释为关键结构通过。

这说明 aggregate objective 可以正确处理大面积轮廓/背景，同时对小面积高价值结构保护不足。外部 objective 也在 7/7 改善，证明仅增加另一套全局加权总分不能自动解决该问题。

## 下一实验

下一步仍保持 scorer、Prompt 和模型输入不变，只做 acceptance 单因素 A/B：

1. 对 baseline 与候选计算固定 `4×4` 和 `8×8` 全 tile MAE 向量。
2. 对照当前“只要求 total loss 严格改善”，实验“total loss 改善且任一 tile 回退不超过显式容差”的 guard。
3. 在同一批候选、同一 draw 预算上比较最终内部 loss、外部 objective、关键 ROI、误拒率和 contact sheet。
4. guard 只有在不破坏 `solid_circle`、`color_lobes` 等明确整体改善，并能保护 `ellipse_gradient/upper_color`、`arc_highlight_orb/highlight_upper_left` 时，才进入生产设计。

该实验完成前，不修改 `min_scene_composite_v3` 权重或版本，不把 benchmark ROI 注入生产 scorer，也不调整 `target_loss`。
