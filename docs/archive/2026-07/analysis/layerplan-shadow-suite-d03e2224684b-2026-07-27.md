# LayerPlan shadow suite `d03e2224684b` 真实模型实验分析

> 归档状态：历史实验分析，`no_go_pending_durable` 不是当前阻塞条件或待办。

## 结论

本轮冻结的 v2 自动门禁为 **`supported`**；D096 后续人工门禁也为
**`supported`**，但生产晋升决策仍是 **`no_go_pending_durable`**。

这表示 D092 的 direct GLSL v2 契约稳定性改造已经把实验从“自动门禁失败”
推进到“允许进入独立人工盲评与 durable evidence 阶段”，并不表示可以立即
替换 D070 的 ShaderDocument/specialized Compiler 生产路径：

- 4 个样本中 3 个达到样本级 `0.005` 改善边界，比例为 `0.75`；
- 1/4 样本因一轮不可配对而 inconclusive，比例为 `0.25`；
- AB 与 BA 的 `B-A` loss 中位数均为负，方向一致；
- 自动门禁的改善率和 inconclusive 比例都恰好位于冻结阈值边界，不应扩大解释；
- 人工 Arm B preference 为 `5/8=0.625`，超过冻结 `0.5` 门槛；
- 完整 promotion bundle 仍是本地私有证据，不能支撑生产晋升。

## 冻结身份

- 协议提交：`3c6f8ab`
- manifest：`benchmarks/layerplan_glsl_shadow/manifest_v2.yaml`
- manifest SHA-256：
  `ac4eb80838c1b49ac948a4892ce0ce9a133534062d67e79cfc404307d5d0b394`
- gate：`benchmarks/layerplan_glsl_shadow/gate_v2.yaml`
- gate SHA-256：
  `1f1a4b3786cfef12e7e5c5c738a6f69877da3babe8295af59446d4b12079fd8b`
- implementation identity SHA-256：
  `76897856088dd9adebd99d87c8585a00b68c524ea5438774d9a85a1e8fdfb9a4`
- config fingerprint：AB=
  `22a4fad1ba6242cda3fa2a0d9c82e55cc9e8e2b338fc171ad1db71b52d1bb86f`，
  BA=`92ffe28f5d2588b7cc2f0975ca59cb38be74542c852a076615d43d3f80666eb7`
- 模型：`kimi:k3-256k`；Kimi family 实际 temperature=`1`，
  ShaderGen 配置 `reasoning_effort=low`
- metric：`min_scene_composite_v3`
- suite id：`shadow-suite-d03e2224684b`
- suite report SHA-256：
  `d03e2224684b134f59aaf0dd850cba97719ffc7b08e2ce90e2896b5c183e197d`
- durability：`local_private_not_registered`
- 本地私有路径：
  `/private/tmp/shadergen-layerplan-shadow-3c6f8ab-live/shadow-suite-d03e2224684b`

该 suite 及其引用的 8 个单 run 已通过递归 verifier。`/private/tmp` 不具备
不可变保留或跨环境可复验能力，因此本轮自动结论尚不是 durable 发布证据。

## 自动门禁结果

`B-A` 为负表示只增加 advisory LayerPlan 的 Arm B loss 更低。

| 项目 | 结果 | 门槛 |
|---|---:|---:|
| improved sample ratio | `0.75`（3/4） | `>=0.75` |
| inconclusive sample ratio | `0.25`（1/4） | `<=0.25` |
| AB median `B-A` | `-0.041426` | `<0` |
| BA median `B-A` | `-0.036113` | `<0` |
| order direction | 一致 | 一致 |
| automatic gate | `supported` | 全部条件通过 |
| promotion decision | `no_go_pending_durable` | 人工已通过，durable 仍未完成 |

## D096 人工盲评与 promotion bundle

- 盲评 package：`shadow-review-v1-d03e2224684b`
- package manifest SHA-256：
  `fcc0f8dcdd8a111b4756e1419e505b98b3b94e0bbe5be972335ea5d6f581ea39`
- 可评项：7；不可评项：1（`pink_gel/BA`）
- Arm B 偏好：5；Arm A 偏好：1；平局：1
- 冻结分母：8；Arm B preference=`0.625`
- human gate：`supported`
- promotion bundle：`promotion-evidence-f42aefb52724`
- bundle manifest SHA-256：
  `f42aefb5272421987926b03598172767ddec1629fcc6ba95ea2175ee009576a6`
- bundle 规模：约 1.7 MB、210 个文件
- durability：`local_private_not_registered`

人工下载 JSON 的 reviewer 代号为空。评价前只在私有规范化副本中补入
`human-reviewer-1`，7 个 choice 未改变；canonical evaluator 只公开 reviewer
alias hash。完整 bundle 已通过离线递归 verifier，但位于 `/private/tmp`，仍需用户
授权目标介质后才能迁入不可变存储并登记 registry。

## 分样本结果

| 样本 | AB `B-A` | BA `B-A` | 样本中位数 | 状态 | 超过 `0.005` 改善 |
|---|---:|---:|---:|---|---|
| solid_circle | `-0.067217` | `-0.044328` | `-0.055772` | comparable | 是 |
| ellipse_gradient | `-0.060122` | `+0.013021` | `-0.023550` | comparable | 是 |
| rimmed_disk | `-0.000775` | `-0.036113` | `-0.018444` | comparable | 是 |
| pink_gel | `-0.022730` | B 无有效候选 | — | inconclusive | 否 |

`ellipse_gradient` 存在一个 BA 次序下 Arm B 反而更差的 run；`rimmed_disk`
的 AB run 改善小于 margin。样本聚合和顺序门禁最终通过，但这些波动说明结果
不能被解释为“LayerPlan 对每次生成都稳定更优”。

## 契约稳定性与资源摘要

相比 v1 的 Arm A `5/8`、Arm B `7/8`，v2 达到 Arm A `8/8`、
Arm B `7/8`。唯一不可比较项是 `pink_gel/BA` 的 Arm B Initial：
一次 direct 调用后仍以 `glsl_renderer_contract_violation` 收敛为
`author_output_invalid`；没有候选越过静态安全校验、真实 compile/draw、
receipt 或 strict loss 选择边界。

| 范围 | LLM calls | tokens | compile | draw | accepted candidates | repair |
|---|---:|---:|---:|---:|---:|---:|
| Arm A | 33 | 106,351 | 18 | 18 | 16 | 3 |
| Arm B direct Author | 32 | 122,469 | 17 | 18 | 13 | 6 |
| LayerPlan 独立预算 | 8 | 10,112 | — | — | — | — |

实际调用量会随 repair、候选合法性和 Refine 结果变化；冻结的是两臂上限与
执行契约，不要求实际 token 或调用数相等。LayerPlan 的调用继续由独立 ledger
记账，不占 Arm B direct Author 预算。

## 下一步与生产边界

1. 把可跨环境复验的内容寻址证据迁入符合
   `docs/evidence/README.md` 的 durable 介质，并在 registry 登记；本地私有路径
   或普通短期 CI Artifact 只能保持 no-go。
2. 完成迁移后由新 ADR 绑定 registry entry、不可变 URI/hash、当前 direct
   implementation identity 与允许的 canary 上限。
3. 生产接入按 D095 在每个 run 启动前冻结 engine；先以完全隔离的 production
   shadow 接入，旧 `shader_graph_v1` 继续提供权威结果。
4. 只有自动、人工、durable 三项均通过后才允许内部稳定分桶 canary；必须保留
   server-side kill switch、显式 fallback attempt 与 artifact/预算/cache 隔离。
5. `LayerPlanV1` 永久 advisory，不能进入候选接受谓词；direct 路径的执行真相
   是 `ShaderProgramSpecV1`，不得把现有可执行 ShaderDocument 重新解释为分层文档。
