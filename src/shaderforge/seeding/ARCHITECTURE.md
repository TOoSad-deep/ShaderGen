# ShaderForge Seeding 架构

`seeding/` 负责把冻结 `IntentIR` 映射为可重放的 `SeedPlanV1`，再无模型地展开为 `TypedEffectGenome`。

- Matcher 固定生成三个角色：最低复杂度、语义层增强和备选结构解释；所有计划绑定 Intent、TargetHypothesis、required layers、有限 override、evidence 和随机种子。
- Expander 只使用版本化模板和 typed node/binding 契约，不接受任意参数 path 或 GLSL 片段；SDF→mask 必须显式使用冻结 analytic AA conversion。
- Complex branch 只消费 `InstanceIntentV2` 的逐 ownership mask 实测 center/axes/orientation/topology：每实例生成独立 SDF，只有该实例自身为 ring/hollow 才生成 outer-minus-inner，open 再生成 cutter difference。语义 segmented radial-ring 现在可通过 `ObjectIntent.radial_segment_evidence_ref` 到达版本化 raw segment 几何；当前 Expander 仍可用 ownership bbox fallback 生成可运行候选，但该近似不获得 segment topology 真值，必须由 actual rendered gate 检出，后续 segment primitive/Resolver 接入直接消费该 typed ref。V2.4 production 冻结 instance masks 为互斥 partition，多实例要求 Intent relations 精确覆盖全部 instance pair，且只允许可由当前 visible-delta diagnostics 证明的 `disjoint/touches` 并归约为 union；`overlap/contains/subtracts` 显式 unsupported。缺 pair、unsupported relation 或未消费 relation 均 fail closed。
- `effect_genome_expander_v2` 的 production-admission capability 只记录 typed Expander 与 evaluator 已共同证明的交集：solid、单实例、零孔，以及除 `background` 外的九项 required-layer taxonomy。虽然 Expander 能为 `background` 生成节点，但其 `gaussian_color_lobe` 尚不满足 evaluator 的 background typed receipt，所以不得据此扩张 capability；ring/hollow/open、多实例和有孔结构同样保持 unsupported。
- Diversity gate 要求三个 `semantic_genome_hash` 全部不同，且 template、topology 或 enabled-layer 结构签名至少有两种；无法满足时保留 `diversity_exception` 并判定失败，不能靠 record id、provenance 或随机数伪造差异。
- 本包不调用模型，不读取 release-held-out，也不负责编译、渲染、评价或生产 Selector 准入。
