# scene_mvp maturity 12/32 draw 单因素重放优化报告

日期：2026-07-23

权威本地证据：`output/diagnostics/scene-mvp/maturity-budget-replay/20260723-v2/report.json`

报告 schema：`scene_mvp_maturity_budget_replay_v1`

报告 SHA-256：`ed8caf780554ef527935c82e78bbf4926e1b09d0beb0e9df47f3db396021f15d`

## 结论

在 run `79f51d8a-1aaa-4f92-b806-cd8a44ddf297` 的两个冻结合成 feature Patch fixture 上，机器 gate 为 `budget32_supported`：

- Arm-12 严格复刻生产 maturity：共享 1 次 raw 后，单批执行 11 次 local draw。
- Arm-32 复用完全相同的前 11 次实际 draw，再以同一参数绑定、固定 `decrease-all → increase-all` 顺序和 strict total-loss acceptance 执行至 31 次 local draw。
- 两个 fixture 的前 11 次 `(parameter_path, direction, before, after, loss)`、第 11 draw 后 best Scene hash 与 best loss 全部一致。
- `underfit_top_left` 两臂都优于 anchor；Arm-32 比 Arm-12 再降低内部 loss `0.000233499`。
- `overfit_top_left` 的 Arm-12 最终 `0.049148339`，仍差于 anchor `0.048350444`；Arm-32 到达 `0.048101946`，将该 Patch 救回。
- 被救回案例的外部 `png_to_shader_score_v1` objective 相对 anchor 改善 `0.001893648`，reference 自动 ROI 最大回退为 `0`。
- 物理 Renderer draw 为 `87/87`，0 模型调用、无隐藏 final/contact-sheet draw；每个被救回 Patch 的额外 local draw 为 `20`，恰好等于预声明的信息阈值。

该结果只支持“在这两个合成 fixture 上，31 local draw 比 11 local draw 有非空收益”。它不能直接把生产 `MAX_PATCH_CANDIDATE_DRAWS` 从 12 改为 32，也不能使 F09 passing。

## 实验身份与前置门禁

本实验是 `offline_no_model_maturity_budget_replay`，durability 为 `local_ignored`，不属于 D058/D059 冻结 benchmark。来源 run 本身也是 `independent_experiment`。实验没有修改 target、scorer、Prompt、Graph、quality preset 或生产预算。

run 79f 的旧 artifact 没有完整 typed Patch，无法追溯重放当时真实模型生成的 Patch；本轮只能复用先前 P0 诊断中已经冻结的两个合成 add-feature fixture。D066 私有 replay bundle 只对未来 run 生效。

## 固定契约

| 项目 | Arm-12 | Arm-32 |
|---|---:|---:|
| raw draw | 同一次共享快照 | 同一次共享快照 |
| local draw 上限 | 11 | 31 |
| 总候选预算语义 | 1 + 11 = 12 | 1 + 31 = 32 |
| round-1 batch | 11 | 16，前 11 与 Arm-12 完全同前缀 |
| 后续 round | 无，复刻生产单批 | 从当前 best 有界 re-propose，每批最多 16 |
| 参数、步长、边界 | 生产 `propose_min_scene_candidates` | 相同 |
| rebase | 生产 `rebase_candidate_proposal` | 相同 |
| acceptance | `accepts_strict_total_loss` | 相同 |
| scorer | `min_scene_composite_v3` | 相同 |
| Renderer | 同一 prepared WebGL1 program | 相同 |

Arm-32 没有引入新参数、新 stage、随机重启或方向交错。后续 round 允许在已更新 best 上再次尝试同一坐标方向，这是 coordinate descent 的轮次扩展；它是本实验唯一新增的搜索深度，不是搜索表示空间扩张。

## 机器 gate

在看到结果前，根据 Kimi 只读审计修正了原设计的两个问题：

1. `color_field` 参数绑定数按生产 model 动态推导，不能假设固定 5 个；当前两个真实 fixture 都是 feature stage，四种 Patch operation 的 stage/raw-only 语义由纯单测锁定。
2. 原完全对称 gate 在 `rescue_count=0` 时会两边同时为空真。冻结后的 `nonempty_clean_rescue_v1` 要求：
   - `budget32_supported`：至少 1 个 rescue；Arm-32 aggregate mean/median 不劣；无逐 Patch 内部实质劣化；所有 rescue 的 external objective/ROI 回退均不超过 `0.01`。
   - `budget12_supported`：没有 clean rescue，且无缺字段或 Renderer 失败。
   - clean/harmful rescue 混合、字段缺失、非有限值或 Renderer 失败：`inconclusive`。

由于 Arm-32 完整复用 Arm-12 前缀并只保留 strict improvement，Arm-32 内部 loss 不劣的两条 gate 腿近乎由构造保证。真正有区分力的是是否出现 non-empty clean rescue，以及为此付出的额外 draw 成本。

## 逐 fixture 结果

| fixture | anchor | raw | Arm-12 | Arm-32 | 12 接受 | 32 接受 | clean rescue |
|---|---:|---:|---:|---:|---|---|---|
| `underfit_top_left` | 0.048350444 | 0.047990985 | 0.047923849 | 0.047690350 | 是 | 是 | 否 |
| `overfit_top_left` | 0.048350444 | 0.054620301 | 0.049148339 | 0.048101946 | 否 | 是 | 是 |

内部 matured loss 的 mean/median 从 Arm-12 的 `0.048536094` 降至 Arm-32 的 `0.047896148`。两个 fixture 的 Arm-12/Arm-32 local draw 都分别为 11/31；Arm-12 接受步数为 3/7，Arm-32 为 12/14。

外部 objective：

| fixture | anchor | Arm-12 | Arm-32 | Arm-32 相对 anchor | 最大自动 ROI 回退 |
|---|---:|---:|---:|---:|---:|
| `underfit_top_left` | 0.048314323 | 0.046080409 | 0.045150501 | -0.003163822 | 0 |
| `overfit_top_left` | 0.048314323 | 0.047942613 | 0.046420675 | -0.001893648 | 0 |

来源 reference 尺寸是 `505×527`，生产 perception/scorer 使用的 target 是 `245×256`。外部 evaluator 因此使用 `perception.target_rgb` 确定性编码的同尺寸 PNG；run 79f reference 不属于固定 benchmark，ROI 是 reference 自动测量结果，不是固定 7 例的 `key_rois`。这进一步限制了本轮结论的外推范围。

## 工程实现与验证

新增 `scripts/run_scene_mvp_maturity_budget_replay.py`：

- 把 12/32 两臂收敛到同一个 `run_maturity_arm()`，唯一输入差异是 local budget 11/31。
- 每次 draw 都同时采集 RGB、metric 与 PNG；最终结果复用已记账 draw，没有额外 Renderer 调用。
- 对 physical draw 与候选账本做等式校验；不一致即 fail closed。
- gate 对缺字段、非有限值和 Renderer 失败返回 `inconclusive`。
- 输出目录 write-once，拒绝覆盖旧证据。

新增 18 个纯单测，覆盖：

- 全接受和全拒绝两条实际 draw 前缀；
- feature 首批 8 个 decrease + 3 个 increase 的生产顺序；
- rebase skip 不消耗 draw；
- Renderer 在共享前缀内及第 12 draw 才失败的非对称分支；
- add/replace/remove/color-field stage 映射；
- unknown operation、gate clean/harmful/zero rescue、缺字段/非有限/失败；
- 每 rescue 额外 draw 只统计被救回 case。

## 限制与下一步

- 样本只有两个合成 add-feature Patch，没有真实模型 Patch 分布、remove/replace/color-field 的真实 Chromium gate，也没有固定 7 例或人工偏好。
- run 79f 的可见结构问题仍是高光、薄亮边和局部镜面细节不足；内部 loss 改善不等于这些视觉结构已经恢复。
- 32 draw 的额外成本达到信息阈值 `+20/rescue`。在 manual 30 Refine 的上界下，若每轮都使用 32 draw，render 占用需要单独重算，不能只看本次质量收益。
- `20260723-v1` 因“每 rescue 额外 draw”错误包含未 rescue case 而被 superseded；其 loss、draw 和 gate 不变，保留不覆盖。权威证据为 `20260723-v2`。

下一步先运行一个显式真实模型 independent experiment，利用 D066 私有 replay bundle 收集真实 typed Patch；随后对相同 anchor/Patch 做 12/32 重放，并扩展固定 7 例与独立人工盲评。在这些门禁通过前，生产继续保持 12 draw，high=`640/9/9`、manual=`1000/32/30` 不变。
