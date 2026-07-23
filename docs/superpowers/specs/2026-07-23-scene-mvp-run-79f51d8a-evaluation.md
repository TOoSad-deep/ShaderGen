# scene_mvp run `79f51d8a` 联合评估

日期：2026-07-23
完整 run id：`79f51d8a-1aaa-4f92-b806-cd8a44ddf297`

本报告由 Codex 与 Kimi 两路只读审查交叉形成。审查读取本地 run 产物、公开 Artifact/Progress API、PostgreSQL 账本、后端日志和当前实现；Kimi 另使用本地 Playwright WebGL1 Renderer 与真实评估器完成约 35 次无模型反事实 draw。审查未修改算法代码、未调用真实模型。

## 结论

该 run 的技术流程成功，配置身份、Graph 预算、Patch 去重、局部成熟和 `current_best` 安全边界均按设计工作；质量门禁明确失败。最终 `global_mae=0.034252` 达到实验 MAE 目标 `0.04`，但 `total_loss=0.048350` 是目标 `0.02` 的 2.42 倍，`target_reached=false`。数据库 `succeeded` 和 HTTP 200 只表示流程完成，不能解释为质量通过。

该 run 正确标记为 `independent_experiment`，不是 D058/D059 冻结 benchmark，不能进入冻结 gate、不能支持 F09 `passing` 或灰度发布。配置漂移不是视觉失败根因；恢复更宽松目标也不会补回缺失的左上高光、左侧深红层次、薄亮边和球外粉色柔和投影。

当前最可信的联合判断是：

1. 左上高光缺失主要由模型提议偏离有效参数区间，以及 11 次局部成熟的固定顺序/单步搜索未进入狭窄改善盆地造成；不是 `gaussian_lobe` 完全无法表达。
2. `geometry_mask_loss` 把参考图球外柔和投影、浅边缘与主体轮廓共同放进硬阈值前景掩码，和当前对称背景 shadow 的表达能力存在错位，很可能形成显著 loss 地板；“目标结构性不可达”仍是高置信假设，不是单例已证明的普遍事实。
3. 运行是 LLM-bound 而不是 render-bound：模型预算耗尽时仍有 501 次 render 余量，当前预算与搜索策略没有充分利用 high 档 draw。

## 身份、配置与可比性

- `run_classification=independent_experiment`
- `experiment_id=scene-mvp-agent-optimization-20260723`
- `report_schema_version=scene_mvp_run_report_v1`
- `config_fingerprint=b6c03fcaae77fcaa74bde1988ab345941d9e2557c1d422d203701b9c3b88af06`
- `quality_preset=high`
- 实际预算：render/LLM/Refine=`640/9/9`
- 实际目标：MAE/loss=`0.04/0.02`
- Graph 注入 recursion limit=`69`

Progress、manifest、metrics 和 PostgreSQL `agent_runs.result` 中的身份与配置一致；当前严格配置加载器可复算出相同指纹。该指纹只覆盖规范化 YAML，不覆盖源码、Prompt、metric 实现、工作树状态或实际模型身份，因此只能证明配置同一性，不能单独证明完整执行环境可复现。

## 运行过程

PostgreSQL 记录运行约 92.72 秒；Graph 在约 92.63 秒完成 61 个节点更新，低于 recursion limit 69。未发现 Renderer、解析、账本、HTTP 或 Graph 异常。

| 阶段 | best loss | 说明 |
|---|---:|---|
| Initial Author | 0.061547 | MAE 0.048484 |
| Base optimize | 0.058128 | 接受 `object.color_field.scale` |
| Rim optimize | 0.058113 | 收益很小 |
| Shadow optimize | 0.052912 | 初始确定性搜索的主要收益 |
| Refine 4 | 0.050507 | 接受右下 `gaussian_lobe` |
| Refine 5 | 0.049300 | 替换并成熟右下高光 |
| Refine 6 | 0.048350 | 再次替换并成熟右下高光 |
| Final | 0.048350 | 后两轮没有进一步改善 |

最终相对 Initial 的 loss 改善 21.44%，MAE 改善 29.35%。共执行 9/9 次模型调用：1 次 Initial、8 次 Refine；报告 token 合计 38,189，Author latency 合计 56.31 秒，约占 Graph 时间 60.8%。Refine 实际上限受全局 LLM 预算约束为 8 次。

Render 使用 139/640，利用率 21.72%。组成可复算为：初帧 1 + base 30 + rim 12 + shadow 12 + 7 个非重复 Patch 各 12 draw；另有 1 个重复 Patch 被正确拦截为 0 draw。

## Patch 与局部成熟

8 个 Refine Patch 中：

- 3 个接受；
- 4 个因 `no_strict_loss_improvement` 拒绝；
- 1 个近期重复 Patch 在模型调用后被拦截，未分配 draw；
- 7 个非重复候选的 matured loss 都优于各自 raw loss，证明局部成熟机制本身有效；
- 所有接受都集中在右下高光，左上高光相关候选成熟后仍明显差于锚点。

当前 feature 局部成熟有 8 个数值字段，候选顺序先遍历所有字段的 decrease，再遍历 increase。总预算为 1 次 raw + 11 次局部 draw，因此 increase 只覆盖 `center[0]`、`center[1]`、`axes[0]`；`axes[1]`、三个 color 通道和 intensity 的 increase 永远不会在本轮被评估。每次又只移动一个固定步长，难以从偏大、偏亮或长短轴错误的 raw Patch 进入狭窄改善区。

Kimi 的无模型反事实实验给出关键区分：

- 在左上放置较小的 `gaussian_lobe`，`axes=(0.20,0.08)`、`intensity=0.8`，可把 loss 从 0.048350 降至 0.047835，严格改善 0.000515，说明高光表达能力存在。
- 同一 lobe 稍大或稍亮就会明显变差；例如 `axes=(0.35,0.15)`、`intensity=1.2` 时 loss 增加约 0.0057，说明改善盆地很窄。
- 六个针对球外投影的 shadow 变体全部使总 loss 与 geometry 变差，支持“当前对称 shadow 与参考柔和投影/硬掩码错位”的假设，但该小样本不能证明所有可行参数均失败。

## 最终质量分解

| 指标 | 值 | 加权贡献 | loss 占比 |
|---|---:|---:|---:|
| `geometry_mask_loss` | 0.111565 | 0.016735 | 34.61% |
| `foreground_mae` | 0.047052 | 0.011763 | 24.33% |
| `worst_tile_mae` | 0.072305 | 0.010846 | 22.43% |
| `global_mae` | 0.034252 | 0.006850 | 14.17% |
| `background_mae` | 0.008660 | 0.001299 | 2.69% |
| `edge_loss` | 0.008576 | 0.000858 | 1.77% |

前三项合计贡献约 81.4%，说明失败集中在结构掩码、主体内部和最差局部区域，不是背景或 Renderer 噪声。

按 `rendered-reference` 约定复算的 4×4 空间残差：

- row 1 / column 0：MAE `0.074620`，亮度偏差 `+0.082938`，RGB 偏差约 `[+0.0092,+0.1037,+0.0943]`；
- row 0 / column 2：MAE `0.069989`，亮度偏差 `+0.071276`，RGB 偏差约 `[+0.0002,+0.0916,+0.0790]`。

两个最差区域都过亮且绿/蓝通道过高，和最终画面左侧/上部过浅、缺少深红层次和精确镜面结构一致。

视觉问题按优先级排序：

1. 左上窄镜面高光缺失；
2. 球外底部偏右的粉色柔和投影/浅边缘没有正确表达；
3. 左侧深红层次与完整薄亮边不足；
4. 右下高光仍过大、过软；
5. 中部色场略偏饱和，局部渐变形态仍不一致。

## 证据链缺口

- run 目录没有独立 `run-config.json`；当前只能通过指纹与工作树 YAML 间接核对实际配置。
- manifest/账本没有记录 requested/actual model ref、model identity source、Initial/Refine Prompt 版本、源码 revision 或工作树 digest。
- 公开 Patch 证据只有操作、feature 摘要、指纹和指标 delta；不含可受控重放的私有 typed Patch、raw/matured scene、每次成熟接受的参数路径/方向或候选残差。
- 最终 metrics 没有显式记录 `metric_background` 和 perception 版本。评分实际使用感知背景 `[0.996078,0.996078,0.996078]`，final scene 背景为 `[0.996078,1.0,0.996078]`；若只用公开 final scene 背景重算，会产生约 `0.000140` 的 loss 偏差。
- durable `agent_events` 保存 41 个 trace 阶段事件，但 61 步完整路由时间线仍依赖单进程 Progress 内存。
- Progress 最终 snapshot 的 counters 只保留 `render_count=139`，没有累计 LLM/Refine counter；历史事件和 metrics 仍可恢复 9/8。
- 该独立单例尚未进入 `docs/evidence/registry.json`，也不应冒充冻结 benchmark 的 durable 发布证据。

## 建议的实验顺序

### P0：先判定指标地板与搜索误拒

1. 固定当前 scene、metric 和参考图，做 primitive/背景 feature 的无模型较密网格或受控优化，计算该样本的最小可行 geometry 与 total loss；不要直接根据六个 shadow 变体调整 target。
2. 对 geometry 做独立消融：硬阈值 IoU、软掩码、主体轮廓与外部 glow/shadow 分离。只改一项，比较复算指标与人工判断是否同向。
3. 在受控私有 Artifact 中保存本 run 被拒高光 Patch，分别用现有 12 draw、每参数双向交替和 32 draw 重放，测量误拒率、最终 loss 与 draw 开销。

### P1：充分利用 render 预算

1. 改为每参数双向交替或残差引导的参数排序，保证短轴与 intensity 两个方向不会长期饿死。
2. 对局部高光采用多轮 coordinate descent 或 successive halving；先用本 run 的固定 Patch fixture 验证，不同时修改 Prompt、scorer 和搜索器。
3. 对 12/24/32 draw 做 A/B；若 32 draw 能稳定救回左上高光，优先调整 render 分配，而不是增加 LLM 调用。

### P1：补齐可复验证据

1. 终态记录实际模型身份、Prompt 版本、源码 revision/worktree digest、metric background、perception/metric/template 版本。
2. 为私有审计增加 typed Patch 与成熟参数轨迹；公开报告仍只暴露安全摘要与 hash。
3. durable ledger 保存路由摘要和累计 counters，Progress snapshot 采用累计合并语义。

### 最终门禁

完成单因素验证后，再按 D058/D059 冻结配置运行固定 7 例真实模型 benchmark，并制作匿名人工盲评。不得用本单例的 MAE 达标、loss 下降或局部反事实改善替代冻结 gate。

## 证据位置

- Run 根目录：`output/png-to-shader/a7611e43-8bb8-4b6a-ae91-4fbebb2b0e59/79f51d8a-1aaa-4f92-b806-cd8a44ddf297/`
- 参考图：`input/reference.png`
- 最终渲染：`final/render.png`
- 指标：`final/metrics.json`
- Manifest：`final/manifest.json`
- 导出 Shader：`final/webgl1.glsl`、`final/shadertoy.glsl`
