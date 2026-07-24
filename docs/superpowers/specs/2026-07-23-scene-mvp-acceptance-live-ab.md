# scene_mvp 固定 7 例 acceptance live 单因素直接 A/B

日期：2026-07-23

## 结论

按预先冻结的判定门槛，机器可读 gate 的 outcome 为 `strict_total_supported`：在固定 7 例、当前确定性候选生成与 32+32 draw 搜索契约内，本实验证据**支持 strict total-loss acceptance 优于 geometry-first 字典序 acceptance**（不外推为真实模型或其他搜索空间的普遍结论）：

1. **strict-total 在两项 aggregate 上同时不劣且实际更优**。内部 total loss 均值/中位数：geometry-first `0.023662/0.022113`，strict-total `0.019688/0.016091`；外部 `png_to_shader_score_v1` objective 均值/中位数：geometry-first `0.030689/0.026700`，strict-total `0.027260/0.022563`。逐案例看，strict-total 在 6/7 案例内部 total loss 与外部 objective 严格更低，`solid_circle` 两臂轨迹与终态完全相同。
2. **geometry-first 在 live 直接对照下复现了两例实质 ROI 回退，strict-total 没有**。geometry-first 相对 fallback 的实质回退（> 冻结 `0.01` 容差）为 `ellipse_gradient/upper_color +0.019894` 与 `arc_highlight_orb/highlight_upper_left +0.011853`，与校准报告数值一致；strict-total 相对 fallback 的全部 ROI 回退都低于容差。两臂之间的实质 ROI 差异同样只有这两个 watch ROI，方向都是 geometry-first 更差（T−G 分别为 `-0.034078` 与 `-0.011906`）。
3. **因果边界**：两臂共享候选生成器、参数范围、阶段顺序、每 stage 32 draw 预算与同一 fallback 初始快照，各自 live 生成/评估候选且候选数完全相同（各 448 次）。在固定 7 例、当前确定性候选生成与 32+32 draw 搜索契约下，轨迹差异的唯一实验变量是 acceptance；在该契约内，两例实质 ROI 回退可归因于 geometry-first 字典序 acceptance——它为了压低 `geometry_mask_loss` 接受了牺牲局部颜色的候选。该归因不外推为真实模型或其他搜索空间下的普遍结论；之前的校准与 tile guard A/B 之间的差异只能把怀疑定位到 acceptance/轨迹，本次 live 直接 A/B 在该契约内补上了因果证据。
4. **geometry 分量的局部优势不转化为整体优势**。geometry-first 在 `ellipse_gradient`、`shadow_disk`、`rimmed_disk`、`arc_highlight_orb`、`color_lobes` 的 `geometry_mask_loss` 更低，但除了 `solid_circle`（平）外这些案例的内部 total loss、外部 objective 和关键 ROI 全部更差；`pink_gel` 的 geometry 分量也是 strict-total 更低。
5. **未解决的问题不变**：rim、弧形高光和 pink-gel 双高光在两臂下都没有恢复，仍属模板/特征表达能力缺口，不是 acceptance 问题。

因此：生产 scorer、Prompt、Graph、预算、目标和 `current_best` 代码本次均未修改。本实验是 independent no-model diagnostic，不是 D058/D059 冻结 benchmark，不能使 F09 passing；contact sheet 代理看片只是工程分析，不构成独立人工盲评。

> D073 事实纠正（2026-07-23）：本文与 D072 曾把 Arm G 描述为"既有/生产 geometry-first acceptance"，并留下"是否把生产 acceptance 换成 strict-total 需要独立决策"的待办。经逐调用点核对，该表述不准确：旧 MinScene 产品的全部 acceptance 比较点自始只按 `min_scene_composite_v3` 的 `total_loss` 严格改善提交，Arm G 的字典序谓词只存在于诊断脚本（本脚本 `geometry_first_accepts` 与 `run_scene_mvp_run_diagnostics.py::_run_geometry_local_search`），其搜索循环结构也不同于生产单批 rebase 循环。因此不存在"生产 acceptance 切换"对象；本实验结论的正确解读是：在固定 7 例旧候选契约内，strict total-loss 语义优于被测的 geometry-first 诊断语义。上述冻结数值、gate 与证据身份不受影响，且不得外推为当前 ShaderGraph 质量结论，详见 D073/D076。

## 实验身份与边界

- 实验类型：`offline_no_model_fixed_7_acceptance_live_ab`
- 固定案例：`solid_circle`、`ellipse_gradient`、`shadow_disk`、`rimmed_disk`、`arc_highlight_orb`、`color_lobes`、`pink_gel`
- 输入 manifest：`benchmarks/png_to_shader_v1/manifest.yaml`
- 对照 baseline：`benchmarks/png_to_shader_v1/scene_mvp_fixed_template_v3_baseline.json`
- Renderer：Playwright Chromium WebGL1
- 模型调用：0
- Arm G：`geometry_mask_loss` 优先、`total_loss` 其次的字典序严格改善（沿用诊断脚本 `_run_geometry_local_search` 的 geometry 局部搜索语义；**不是生产 acceptance**——旧 MinScene 产品自始为 strict total-loss，见文末 D073 纠正）
- Arm T：仅 `total_loss` 严格改善
- 共享条件：同一 fallback scene 与同一次 fallback 渲染快照、同一候选生成器与参数范围、相同 stage 顺序（base 32 draw + existing shadow 32 draw）、相同方向交错、相同每案例候选预算；两臂各自基于本臂 incumbent 实时生成/评估候选，禁止 offline replay
- RNG/seed：候选生成无 RNG、确定性提案；Renderer 每次 draw 完整上传 typed uniform，无跨 draw 状态；两臂执行顺序固定为 G 后 T
- 总物理 Renderer draw：903（共享 fallback 7 + Arm G 448 + Arm T 448）
- 预先冻结的判定门槛：逐 case 外部 objective 与 ROI 实质回退容差均固定为 `0.01`（ROI 容差与冻结 baseline `max_roi_loss_regression` 一致）；aggregate mean/median 双向判定规则对称、不可依结果变更；代理看片不构成人工盲评。看到结果后未调整任何门槛
- benchmark ROI 与 4×4/8×8 tile 摘要只用于事后评价，不进入任何 acceptance

执行命令：

```bash
uv run python scripts/run_scene_mvp_acceptance_live_ab.py \
  --output-dir output/diagnostics/scene-mvp/acceptance-live-ab/20260723-v2
```

权威报告：`output/diagnostics/scene-mvp/acceptance-live-ab/20260723-v2/report.json`（`scene_mvp_acceptance_live_ab_v2` schema：机器 gate/decision 与显式 fail-closed 输入校验）

SHA-256：

```text
2daa4c77b274efed7ede863444b4ce6d5141bf92168075f722e7b0ded00cdd11
```

注意：output run iteration（`20260723-v1`/`v2` 等目录代次）与 report schema（`scene_mvp_acceptance_live_ab_v1`/`_v2`）是两个独立版本轴，互不推导。证据谱系（全部原件保留不覆盖）：`20260723-v1` 是加入机器可读 gate 之前的探索性首次运行，标记为 superseded exploratory artifact；`20260723-v2-gate-draft-superseded` 是 gate 字段定稿前的中间运行；`20260723-v2-schema-v1-superseded` 已包含完整 gate 字段但 schema 标签仍为 `_v1` 且缺显式 fail-closed 校验，已原样改名保留。本文档、PROGRESS、D072 与 FEATURES 只引用当前 `20260723-v2`（schema `_v2`）的数字与 SHA；其与此前各版的逐案例指标、aggregate 与 ROI 数值经逐字段比对完全一致（除 schema/generated_at/SHA），gate/decision 与 schema 标签是纯后处理与元数据新增。

## 机器可读 gate 与 decision

`report.json` 的每个案例带 `gate` 字段，分别检查逐 case 外部 objective 的双向 0.01 实质回退（`external_objective_material_regression_t_vs_g` / `g_vs_t` 布尔与 delta）和逐 ROI 的双向 0.01 实质回退映射（`roi_material_regressions_t_vs_g` / `g_vs_t`）；`summary.decision` 汇总机器可读 outcome。`evaluate_decision` 对缺失 aggregate 或 case gate 字段显式 fail closed（`ValueError`），不依赖偶然 `KeyError`：

- `outcome`：`strict_total_supported | geometry_first_supported | inconclusive`，本次为 `strict_total_supported`。
- `tolerances`：ROI 与外部 objective 实质回退容差均固定为 `0.01`，规则对称、不可依结果变更。
- `internal_total_loss` / `external_objective`：两项 aggregate 的 mean/median 与双向 `not_worse` 布尔。
- `per_case_external_objective_material_regression` 与 `per_case_roi_material_regressions`：逐 case/ROI 的 arm-to-arm 实质回退映射及每项布尔判定。

规则：一臂成立当且仅当两项 aggregate 的 mean/median 都不劣于另一臂且无任何逐 case 实质回退；两臂同时成立（完全持平）或同时不成立（互有优劣/回退）时如实判 `inconclusive`。

## 数值结果

逐案例终态（负的 T−G 表示 strict-total 更好）：

| 案例 | G total | T total | T−G total | T−G 外部 | T−G 关键 ROI |
|---|---:|---:|---:|---:|---|
| `solid_circle` | `0.008100` | `0.008100` | `+0.000000` | `+0.000000` | 完全相同轨迹 |
| `ellipse_gradient` | `0.022113` | `0.008796` | `-0.013316` | `-0.009752` | `upper_color -0.034078`、`lower_color -0.009238` |
| `shadow_disk` | `0.044089` | `0.042760` | `-0.001329` | `-0.000529` | `subject -0.008019`、`shadow +0.001532` |
| `rimmed_disk` | `0.020504` | `0.016091` | `-0.004414` | `-0.004136` | `center -0.008493` |
| `arc_highlight_orb` | `0.024167` | `0.018327` | `-0.005840` | `-0.006191` | `highlight_upper_left -0.011906` |
| `color_lobes` | `0.017665` | `0.015817` | `-0.001848` | `-0.002276` | `cool_lower_right +0.001449` |
| `pink_gel` | `0.028997` | `0.027926` | `-0.001070` | `-0.001123` | `highlight_lower_right -0.007774` |

实质 ROI 回退（> `0.01`）：

- 相对 fallback：geometry-first 有 `ellipse_gradient/upper_color +0.019894`、`arc_highlight_orb/highlight_upper_left +0.011853`；strict-total 无。
- 两臂之间（任一方向）：同样只有这两个 watch ROI，且都是 geometry-first 更差；其余案例/ROI 的两臂差异都低于容差。

内部六分量终态、每阶段候选数、接受序列与 tile 摘要见 `report.json` 的 `cases[].arms` 与 `cases[].comparison`；两臂候选数均为每例 64（base 32 + shadow 32）。

## 视觉检查

每例 contact sheet 为四列：reference、fallback、G:geometry-first、T:strict-total。

- `ellipse_gradient`：geometry-first 保留 reference 中不存在的 shadow、上部颜色偏离；strict-total 去除 shadow 且整体更接近 reference，与 `upper_color` ROI 数值方向一致。
- `arc_highlight_orb`：geometry-first 保留 shadow；strict-total 去除。两臂都没有恢复左上弧形高光。
- `solid_circle`：两臂终态一致，都去除了 fallback 的错误 shadow，与完全相同的轨迹记录吻合。
- `shadow_disk`：两臂都保留主体外软影、外观接近，主体明暗结构均未恢复。
- `rimmed_disk`：两臂都去除 shadow，均无显著 rim。
- `color_lobes`：两臂都去除 shadow；strict-total 的蓝紫 lobe 位置略更接近 reference。
- `pink_gel`：两臂外观接近，双高光均未恢复。

视觉结论与数值一致：strict-total 在保留 geometry-first 全部整体改善（去错误 shadow、轮廓修正）的同时避免了两例局部颜色牺牲；关键结构缺失是两臂共同的模板能力缺口。

## 下一步

1. ~~是否把生产 acceptance 从 geometry-first 字典序改为 strict total-loss~~ 已由 D073 关闭：旧 MinScene 产品 acceptance 自始为 strict total-loss，Arm G 仅是诊断脚本语义，无生产切换对象；本实验证据应理解为支持旧候选空间中的既有语义优于被测替代语义。
2. 在真实模型固定 7 例 benchmark 与独立人工盲评通过前，acceptance 变更不应单独视为质量改进证据；自动代理看片不替代人工偏好 gate。
3. rim、弧形高光、双高光缺失仍是发布阻塞项，属于模板/特征表达能力问题，后续按模板能力增量独立处理。
