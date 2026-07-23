# scene_mvp 固定 7 例多尺度 tile no-regression guard A/B

日期：2026-07-23

## 结论

按预先声明的验收标准，本实验的 offline replay 形式**未通过离线接入门禁**：“4×4/8×8 全 tile 最大回退 guard”在本离线形式下不采用、不进入生产设计：

1. **没有可保护的对象**。在 Arm A（total_loss 严格改善即接受）下，`ellipse_gradient/upper_color` 改善 `-0.014184`、`arc_highlight_orb/highlight_upper_left` 基本持平（`-0.000052`）；7 例全部 ROI 回退都低于冻结的 `0.01` 容差。本次实验与校准报告在两例实质 ROI 回退上的差异，把怀疑定位到 acceptance/轨迹差异；但校准使用 geometry-first 字典序 acceptance、本次 Arm B 又是沿 Arm A 候选流的离线 replay，建立因果必须用同一候选预算的 geometry-first vs strict-total live 单因素直接 A/B。
2. **guard 要么无效、要么灾难性误拒**。在声明的五个容差上，guard 与 Arm A 终态相同的案例没有任何保护收益；其余案例把首个改善候选就整体拦截：`t≤0.005` 时 4/7（`shadow_disk`、`rimmed_disk`、`color_lobes`、`pink_gel`）全部改进被吞，`t=0.01` 时 `shadow_disk`、`pink_gel` 仍被全拒，`t=0` 时 6/7 全拒、唯一部分接受的 `solid_circle` 也损失改进。
3. **误拒破坏的是明确改善**。`color_lobes` 被拦截路径包含 `cool_lower_right -0.013347`、`warm_upper_left -0.004138` 的 ROI 改善；`pink_gel` 被拦截路径包含双高光 ROI `-0.012588/-0.013711` 与 shadow ROI `-0.011381` 的改善。tile guard 没有语义选择性，无法区分“有害回退”和“有益的整体重分配”。
4. guard 附带发现并记录：Arm A 接受的单步最大 tile 回退为 `0.0056–0.0214`（均在 8×8 网格），即真实改善路径天然伴随局部 tile 级补偿。

因此生产 scorer、Prompt、Graph、预算和目标继续保持不变，不接入该 guard。本实验的看片与 ROI 结论来自自动代理，不等于人工偏好 gate。

## 实验身份与边界

- 实验类型：`offline_no_model_fixed_7_tile_guard_ab`
- 固定案例：`solid_circle`、`ellipse_gradient`、`shadow_disk`、`rimmed_disk`、`arc_highlight_orb`、`color_lobes`、`pink_gel`
- 输入 manifest：`benchmarks/png_to_shader_v1/manifest.yaml`
- 对照 baseline：`benchmarks/png_to_shader_v1/scene_mvp_fixed_template_v3_baseline.json`
- Renderer：Playwright Chromium WebGL1
- 模型调用：0
- 候选机制：与既有 geometry 局部搜索完全相同的 stage 顺序（base 32 draw + existing shadow 32 draw）、方向交错与提案参数
- 总物理 Renderer draw：455（448 候选 + 7 fallback）
- Arm A：现有“total_loss 严格改善即接受”
- Arm B：total_loss 严格改善 **且** 4×4 与 8×8 全部非重叠 tile 对 reference 的 RGB MAE 最大回退 ≤ 显式容差
- 预先声明的容差 sweep：`0`、`0.001`、`0.0025`、`0.005`、`0.01`，看到结果后未调整
- guard 输入只有 tile MAE；benchmark ROI 只用于事后评价，未进入 guard
- 人工门禁：未执行；本报告中的看片结论只是工程分析，不是独立人工偏好票

执行命令：

```bash
uv run python scripts/run_scene_mvp_tile_guard_ab.py \
  --output-dir output/diagnostics/scene-mvp/tile-guard-ab/20260723-v1
```

权威报告：`output/diagnostics/scene-mvp/tile-guard-ab/20260723-v1/report.json`

SHA-256：

```text
d844dc6bf47bb37451807d083b1677dc497ead1d6a19fc361454ccf94b09c5d3
```

### 重放设计声明

Arm A 实跑时记录每个已评估候选的 Scene、内部指标、渲染像素与 PNG；Arm B 各容差臂在**同一批候选流**上离线重放，只改变 acceptance 谓词，不重新渲染、不重新生成候选，因此 draw 预算与 Arm A 相同。这与生产的 live guard 搜索存在轨迹差异：live guard 拒绝早期候选后会改变后续 candidate generation，其后续轨迹未验证，本实验结论只覆盖 offline replay 形式，不外推到 live guard。观测事实：`shadow_disk`、`rimmed_disk`、`color_lobes`、`pink_gel` 四例中 guard 连 Arm A 的**第一个**改善候选就拦截（首个接受步 tile 回退 `0.0068–0.0214`）。

## 数值结果

Arm A 相对 fallback（负值表示改善）：

| 案例 | 内部 total Δ | 外部 objective Δ | 关键 ROI Δ | A 接受步最大 tile 回退 |
|---|---:|---:|---|---:|
| `solid_circle` | `-0.025177` | `-0.010199` | 无变化 | `0.006944`@g8(6,2) |
| `ellipse_gradient` | `-0.026705` | `-0.014341` | `upper_color -0.014184`、`lower_color -0.011994` | `0.005621`@g8(5,6) |
| `shadow_disk` | `-0.007499` | `-0.004578` | `shadow +0.007319`、`subject -0.006788` | `0.021421`@g8(4,1) |
| `rimmed_disk` | `-0.024730` | `-0.009068` | `rim_left +0.008094`、`center -0.001899` | `0.006765`@g8(1,3) |
| `arc_highlight_orb` | `-0.021901` | `-0.009975` | `highlight_upper_left -0.000052` | `0.005953`@g8(6,2) |
| `color_lobes` | `-0.025904` | `-0.013698` | `cool_lower_right -0.013347`、`warm_upper_left -0.004138` | `0.009096`@g8(4,4) |
| `pink_gel` | `-0.009773` | `-0.007646` | `center_haze +0.002415`、双高光 `-0.012588/-0.013711`、`shadow -0.011381` | `0.018832`@g8(7,4) |

Arm A 的所有 ROI 回退（`shadow_disk/shadow +0.0073`、`rimmed_disk/rim_left +0.0081`、`pink_gel/center_haze +0.0024`）都低于冻结 `0.01` 容差；两个发布阻塞 watch ROI 没有回退。

Arm B 相对 Arm A 的结果（每格：guard 拦截候选数 / 其中 A 已接受数 / 终态内部 total Δ vs A）：

| 案例 | t=0 | t=0.001 | t=0.0025 | t=0.005 | t=0.01 |
|---|---|---|---|---|---|
| `solid_circle` | 16/10/`+0.001295` | 8/6/`+0.000000` | 6/5/`+0.000000` | 2/2/`+0.000000` | 0/0/`+0.000000` |
| `ellipse_gradient` | 47/21/`+0.026705` | 8/7/`+0.000000` | 4/4/`+0.000000` | 1/1/`+0.000000` | 0/0/`+0.000000` |
| `shadow_disk` | 47/16/`+0.007499` | 47/16/`+0.007499` | 47/16/`+0.007499` | 47/16/`+0.007499` | 47/16/`+0.007499` |
| `rimmed_disk` | 40/20/`+0.024730` | 40/20/`+0.024730` | 40/20/`+0.024730` | 40/20/`+0.024730` | 0/0/`+0.000000` |
| `arc_highlight_orb` | 34/17/`+0.021901` | 34/17/`+0.021901` | 3/3/`+0.000000` | 1/1/`+0.000000` | 0/0/`+0.000000` |
| `color_lobes` | 46/21/`+0.025904` | 46/21/`+0.025904` | 46/21/`+0.025904` | 46/21/`+0.025904` | 0/0/`+0.000000` |
| `pink_gel` | 42/18/`+0.009773` | 42/18/`+0.009773` | 42/18/`+0.009773` | 42/18/`+0.009773` | 42/18/`+0.009773` |

`t=0` 全臂合计拦截 272 个候选，其中 123 个是 Arm A 已接受的改善步。

保护/误拒判定（相对 Arm A）：

- 保护：所有容差臂对 `upper_color`、`highlight_upper_left` 的 ROI 变化为 `0`（无回退可保护）；在 guard 全拒的臂上 watch ROI 反而比 Arm A 差（如 `t=0` 时 `upper_color +0.014184`），即 guard 自己造成了它本想防止的损失。
- 误拒：`color_lobes` 在 `t≤0.005` 丢失全部改进（内部 total `+0.025904`、外部 `+0.013698`）；`shadow_disk`、`pink_gel` 在全部声明容差下丢失全部改进；`solid_circle` 在 `t=0` 部分误拒（`+0.001295`）。

## 视觉检查

每例 contact sheet 为八列：reference、fallback、A:total、B:t=0、B:t=0.001、B:t=0.0025、B:t=0.005、B:t=0.01。

- `ellipse_gradient`：Arm A 去除 reference 中不存在的 shadow、上部颜色更贴近 reference；`t=0` 保留带错误 shadow 的 fallback，`t≥0.001` 与 A 相同。
- `solid_circle`：A 与 `t≥0.001` 去除错误 shadow；`t=0` 仍残留 shadow。
- `arc_highlight_orb`：A 与 `t≥0.0025` 去除 shadow；所有臂都没有恢复左上弧形高光。
- `color_lobes`：A 与 `t=0.01` 的蓝紫 lobe 位置明显比 fallback 接近 reference；`t≤0.005` 保留带 shadow 的 fallback，误拒肉眼可见。
- `shadow_disk`：所有 B 臂与 fallback 几乎一致；A 仅轻微变化，主体明暗结构仍未恢复。
- `rimmed_disk`：`t≤0.005` 保留 shadow，`t=0.01` 与 A 相同；任何臂都没有显著 rim。
- `pink_gel`：所有臂外观接近，双高光均未恢复。

视觉结论与数值一致：guard 的保护收益不可见，误拒损失在 `color_lobes` 等案例上肉眼可见；关键结构（rim、弧形高光、双高光）在 Arm A 与所有 Arm B 下同样未恢复。

## 下一步

1. 两次实验在两例实质 ROI 回退上的差异把怀疑定位到 acceptance/轨迹；在决定 acceptance 策略前，必须用同一候选预算的 geometry-first vs strict-total live 单因素直接 A/B 建立因果，而不是在 total-loss acceptance 上叠加 tile guard。
2. 不接入 4×4/8×8 tile MAE 最大回退 guard；如仍需要局部保护，候选形式（相对回退比例、语义区域 guard、ROI 加权 guard）必须重新做同样的预声明 A/B，且不得把 benchmark ROI 注入生产 scorer。
3. rim、弧形高光和双高光缺失在两种 acceptance 下都未解决，仍是发布阻塞；这属于模板/特征表达能力问题，不是 acceptance 问题。
4. 本实验不修改 `min_scene_composite_v3` 权重或版本，不调整 `target_loss`，Arm B 重放证据只用于离线分析。
