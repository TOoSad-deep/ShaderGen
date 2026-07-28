# scene_mvp run `79f51d8a` P0 无模型诊断实验

> 归档状态：历史单次实验，诊断方向与建议不自动进入当前 process。

日期：2026-07-23
完整 run id：`79f51d8a-1aaa-4f92-b806-cd8a44ddf297`

## 结论

本轮没有修改生产 scorer、Prompt、Graph 或停止目标，也没有调用真实模型。实验复现了原 run 的精确基线，并得到两个足以约束后续实现方向的结论：

1. 当前 geometry objective 存在明显的视觉语义错位风险。受限的实际模板搜索把 `geometry_mask_loss` 从 `0.111565` 降到 `0.089958`、复合 loss 从 `0.048350` 降到 `0.046294`，但同时把 foreground MAE 从 `0.047052` 恶化到 `0.051803`、global MAE 从 `0.034252` 恶化到 `0.035905`，输出仍缺失参考图的左上镜面高光和细亮边。继续增加搜索预算可能更充分地优化错误方向，因此不能先用更大 maturity 预算掩盖 scorer 语义问题。
2. 12 draw 的方向顺序不是稳定根因；32 draw 在两个合成 fixture 上都优于 12 draw，并把 overfit fixture 从拒绝区救回，但 current/interleaved 两种 32 draw 几乎打平。现有证据支持“预算截断比方向顺序更重要”，不支持直接把生产策略改成某一种顺序，也不足以证明 32 draw 可在固定 7 例上改善人工视觉质量。

因此本轮决策是：保留生产配置和算法不变，先把 scorer 语义诊断扩展到固定 7 例，并补齐真实被拒 typed Patch 的私有可重放证据；之后再决定是否调整 geometry 定义和 maturity 预算。`target_loss=0.02` 不下调，本 run 仍是独立实验且不进入冻结 gate。

## 实验边界

- 固定输入：原 reference、final Scene、`png_to_shader_min_template_v3`、`min_scene_composite_v3` 和公开 metric background。
- Renderer：本地 Playwright Chromium WebGL1 prepared renderer。
- 模型调用：0。
- 物理 Renderer draw：245。
- 输出目录只增不改；`20260723-p0-no-model-v1` 为早期试跑，以下结论只使用 `20260723-p0-no-model-v2`。
- 本实验只覆盖一个真实 run 和两个合成 Patch fixture，不是固定 7 例 benchmark，也不是人工盲评。

执行命令：

```bash
uv run python scripts/run_scene_mvp_run_diagnostics.py \
  --run-dir output/png-to-shader/a7611e43-8bb8-4b6a-ae91-4fbebb2b0e59/79f51d8a-1aaa-4f92-b806-cd8a44ddf297 \
  --output-dir output/diagnostics/scene-mvp/79f51d8a-1aaa-4f92-b806-cd8a44ddf297/20260723-p0-no-model-v2
```

权威结果：`output/diagnostics/scene-mvp/79f51d8a-1aaa-4f92-b806-cd8a44ddf297/20260723-p0-no-model-v2/report.json`
报告 SHA-256：`9315a70395bb33e3c619f10427115443079d2ba56d1a8b74fcd32ba41ac0f139`

## 基线复现

诊断脚本从 run artifact 重新加载 final Scene、reference 和 metric background，重新 materialize GLSL 并真实渲染。记录值与复算值逐项一致，复合 loss 均为：

```text
0.04835044430924994
```

该检查排除了“后续实验使用了不同评分入口或不同基线图像”的混淆。

## Geometry 语义实验

### 掩码阈值与软化

| 方案 | geometry loss |
|---|---:|
| hard threshold `0.03` | `0.125434` |
| hard threshold `0.05`（生产值） | `0.111565` |
| hard threshold `0.07` | `0.108575` |
| hard threshold `0.10` | `0.120445` |
| soft threshold `0.03–0.10` | `0.113890` |

单纯移动 hard threshold 或将其软化没有消除约 `0.11` 的 geometry 残差。参考前景中有 `14.98%` 落在 final circle 之外；按整图计，false negative 为 `3.82%`，false positive 为 `3.41%`。这说明残差不只是一个阈值常量的问题，参考图中的软投影、亮边与主体轮廓被当前“距背景颜色”掩码混在一起。

### 理想圆与实际模板受限搜索

- 理想二值圆在 1053 个中心/半径组合中的最优 geometry loss 为 `0.094569`，仍不能解释全部参考前景掩码。
- 实际模板执行 64 个逻辑 draw，仅搜索 base 与现有 shadow 的确定性候选；18 步被接受。

| 指标 | final Scene | geometry 优先搜索后 | 变化 |
|---|---:|---:|---:|
| geometry mask loss | `0.111565` | `0.089958` | 改善 |
| total loss | `0.048350` | `0.046294` | 改善 |
| foreground MAE | `0.047052` | `0.051803` | 恶化 |
| global MAE | `0.034252` | `0.035905` | 恶化 |

搜索后的图仍是平滑粉色主体与软影，没有恢复参考图的局部镜面条带。这里不能推导“该 target 在全局不可达”，因为搜索是局部且有界的；但可以确认当前 composite objective 会奖励一种人工视觉上没有修复关键结构、且像素 MAE 反而更差的变化。

## Maturity 预算实验

两个 fixture 都固定为左上 `gaussian_lobe`，只改变初始参数。每种策略均从同一个 raw Scene 开始：

| fixture | raw loss | fixed 12 | interleaved 12 | fixed 32 | interleaved 32 |
|---|---:|---:|---:|---:|---:|
| underfit | `0.047991` | `0.047924` | `0.047748` | **`0.047690`** | `0.047696` |
| overfit | `0.054620` | `0.049148` | `0.049389` | `0.048102` | **`0.048069`** |

锚点 loss 为 `0.048350`。underfit raw 已优于锚点，四种 maturity 都继续改善；overfit raw 明显更差，两个 12 draw 策略成熟后仍会被严格拒绝，两个 32 draw 策略则刚好进入可接受区。

可得：

- 双向交替在 underfit/12 draw 上更好，但在 overfit/12 draw 上更差，不能视为稳定替代方案。
- 32 draw 在两个 fixture 上均超过对应 12 draw，且 current/interleaved 差距很小；扩大可到达的参数轮次比改方向顺序更关键。
- 两个 32 draw 输出仍没有形成参考图的弧形白色高光。loss 改善不等于视觉结构正确，再次说明搜索预算与 scorer 语义必须分开验证。

## 实施建议

下一增量按以下顺序执行，且每次只改变一个因素：

1. 用固定 7 例执行 geometry 分量消融，分别记录主体内、主体外投影、边缘和人工偏好；先验证 objective 与视觉方向是否一致。
2. 终态私有 artifact 保存可重放 typed Patch、候选 Scene、raw/matured 指标及必要版本；公开层继续只暴露 hash 和脱敏摘要。
3. 在 scorer 语义稳定后，对相同真实 Patch 重放 12/32 draw，比较接受召回、误接受、draw 与最终人工偏好，再决定生产预算。
4. 最后才单独调整 Prompt，并用冻结 D058/D059 manifest 和固定 7 例做真实模型 benchmark 与匿名盲评。

本报告不授权降低目标、把独立实验改写成冻结 benchmark、标记 F09 passing，或仅凭单例 loss 下降进入灰度。
