# LayerPlan shadow suite `43a0748fa395` 真实模型实验分析

## 结论

本轮自动门禁为 **`not_supported`**，生产晋升决策为
**`no_go_automatic_gate_failed`**。不得据此替换 D070 的
ShaderDocument/specialized Compiler 生产路径，也不进入人工晋升盲评。

LayerPlan 臂呈现值得继续验证的信号，但证据不够稳定：

- 8 个 run 中 Arm A 成功 `5/8`，Arm B 成功 `7/8`；
- 5 个可配对 run 中 Arm B 的 final loss 为 `4` 胜 `1` 负；
- AB 与 BA 可比较子集的 `B-A` loss 中位数均为负；
- 但只有 `rimmed_disk` 两轮都可比较，`3/4` 样本按预声明规则为
  inconclusive，超过 `0.25` 上限；
- 样本级超过 `0.005` 改善阈值的比例仅 `1/4=0.25`，低于要求的 `0.75`。

因此，当前结果只能表明“LayerPlan 可能改善部分 direct GLSL 候选与生成成功率”，
不能证明它在冻结样本上稳定优于无 LayerPlan 对照，更不能证明唯一因果关系。

## 冻结身份

- 协议提交：`f3ad2e9`（随后 `4172d69` 只记录首次配置失败事实）
- manifest：`benchmarks/layerplan_glsl_shadow/manifest_v1.yaml`
- manifest SHA-256：
  `419e29e49b806bb3bae6a67a1dbd20c78a58faa2bf19d30e0a63b3ac0ac1a16a`
- gate：`benchmarks/layerplan_glsl_shadow/gate_v1.yaml`
- 样本：`solid_circle`、`ellipse_gradient`、`rimmed_disk`、`pink_gel`
- 顺序：round 1=`AB`，round 2=`BA`
- 模型：`kimi:k3-256k`；Kimi family 实际 temperature=`1`
- metric：`min_scene_composite_v3`
- suite id：`shadow-suite-43a0748fa395`
- suite report SHA-256：
  `43a0748fa39525b0c44106b2ffc323557e29fc1cb553300cb60408af39ee1075`
- durability：`local_private_not_registered`
- 本地私有路径：
  `/private/tmp/shadergen-layerplan-shadow-f3ad2e9-live2/shadow-suite-43a0748fa395`

该路径不属于 durable evidence。报告及其 8 个单 run 已通过递归 verifier，
但本机 `/private/tmp` 产物不能支撑跨环境复验或生产晋升。

## 自动门禁结果

| 项目 | 结果 | 门槛 |
|---|---:|---:|
| improved sample ratio | `0.25`（1/4） | `>=0.75` |
| inconclusive sample ratio | `0.75`（3/4） | `<=0.25` |
| AB median `B-A` | `-0.007551` | `<0` |
| BA median `B-A` | `-0.035707` | `<0` |
| order direction | 一致 | 一致 |
| automatic gate | `not_supported` | 全部条件通过 |

## 分样本结果

`B-A` 为负表示 LayerPlan 臂 loss 更低。

| 样本 | AB `B-A` | BA `B-A` | 样本状态 | 超过 `0.005` 改善 |
|---|---:|---:|---|---|
| solid_circle | `-0.007551` | 不可配对（A 无候选） | inconclusive | 否 |
| ellipse_gradient | `+0.011101` | 不可配对（A 无候选） | inconclusive | 否 |
| rimmed_disk | `-0.061700` | `-0.002770` | comparable | 是（median `-0.032235`） |
| pink_gel | 不可配对（A/B 无候选） | `-0.068644` | inconclusive | 否 |

## 失败根因

所有不可配对 run 都收敛到预声明的 `author_output_invalid`，具体安全错误为
`glsl_renderer_contract_violation`：

- `solid_circle/BA`：Arm A Initial 无有效输出；
- `ellipse_gradient/BA`：Arm A Initial 无有效输出；
- `pink_gel/AB`：Arm A 与 Arm B Initial 均无有效输出。

同一错误也出现在若干成功 run 的 Refine 候选中，但 incumbent 被安全保留。
没有发现 compile/link/draw、metric、receipt、证据 hash 或 Renderer
不可用导致的 suite 失败。

这说明下一瓶颈是 direct GLSL Author/结构修复对
`webgl1_static_no_texture_v1` 的遵循稳定性，而不是放宽 Validator。
后续若修改 Prompt、repair 或模型输出契约，必须升级版本并重新冻结实验身份，
不得把新实现结果追加进本 v1 suite。

## 资源摘要

| 范围 | direct/plan LLM calls | tokens | compile | draw | accepted candidates |
|---|---:|---:|---:|---:|---:|
| Arm A | 28 | 95,936 | 11 | 12 | 11 |
| Arm B direct Author | 36 | 145,836 | 13 | 18 | 16 |
| LayerPlan 独立预算 | 8 | 10,707 | — | — | — |

两臂拥有相同上限，但实际调用量会随结构修复和候选有效性变化；这属于被测路径的
结果，不得把实际 token 数相等当作有效性前提。LayerPlan 的 8 次调用继续由独立
ledger 记账，不占 Arm B direct Author 预算。

## 下一步

1. 保持生产路径和当前 Validator 不变。
2. 为 direct GLSL Initial/Refine/repair 增加新的版本化契约遵循改进，重点消除
   texture/sampler/保留 uniform/预处理等 Renderer contract 违规。
3. 用固定 fake 与真实连通性 canary 验证新 Prompt/repair，不用放宽 Schema
   或静默修补 GLSL 掩盖模型错误。
4. Prompt/repair/实现变更后建立新的 manifest/gate 版本与实现身份绑定，再运行
   新 suite；不得覆盖 `43a0748fa395`。
5. 只有新自动 gate 通过后才生成独立人工盲评包；durable 证据仍是晋升前置条件。
