# ShaderForge Compiler 架构

`compiler/` 把已严格提升为 `TypedEffectGenome` 的图确定性编译为 WebGL1 / GLSL ES 1.00。

- `compile_effect_genome()` 固定执行 typed promotion、稳定拓扑排序、canonical node/parameter 命名、typed AST、safe stdlib、源码映射、参数表和静态校验。
- Compiler 覆盖 `effect_node_registry_v0` 的全部 NodeKind；registry 与 emitter 不闭合、输入 Genome 非法或自产 GLSL 未通过 canonical V1 Validator 时统一抛出 `CompilerDefectError`，不得交给模型修改源码。
- `CompilationProduct` 是渲染前的纯内存结果；`materialize_compilation()` 只负责把 GLSL、AST、node line map 和 parameter table 写入内容寻址 Catalog，并返回 `CompilationBundle`。
- `compile_diagnostic_passes()` 从同一 typed Genome AST 生成 breaking 的 `diagnostic_compilation_product_v3`，且每份自产 diagnostic GLSL 都先通过 canonical V1 Validator。`stable_instance_ordinal_first_match_v1` policy 先用“禁用全部 instance roots”的 final-output delta 形成 subject 可见域，再按稳定 instance ordinal 把每个 raw topology 像素唯一分给首个命中实例；subject 与 owner pass 使用同一 byte `8` 二值阈值，因此 overlap 不会在逐实例 union 中丢失，owner masks 也保持互斥。该规则不读取 case/Manifest/target mask，不放宽 union IoU。每个已启用 semantic layer 的最长边 64 `layer_visible_delta` 仍比较正常 final output 与只禁用目标 layer 的输出。每个 v3 pass/bundle/source 都绑定 policy、canonical node/kind/output type、GLSL bytes/SHA；旧 v2 payload 不得升级加载。
- 同一 semantic Genome 的源码 bytes、GLSL SHA-256、AST、SourceMap 和 parameter table 必须稳定；record node id、集合输入顺序和 provenance 不得影响编译结果。

Compiler 不负责模板匹配、参数搜索、视觉评分、Renderer 生命周期或 production admission。
