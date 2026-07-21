# ShaderForge Genome 架构

`genome/` 保存可确定性展开、验证、编译和搜索的 Effect Genome 领域表示。

## V2.0 已冻结范围

- `models.py` 冻结 `genome_v0`、`effect_node_registry_v0`、typed SDF/mask/color ports、mask/SDF/抗锯齿语义和完整 `ParameterSpec` 布局。
- `ParameterSpec.value/min/max` 只能是与 dtype 精确匹配的 bool、int、有限 float 或固定长度 tuple；vector 进入模型后深度不可变，并逐元素校验 `min <= value <= max`。四类 Genome hash 与 provenance 的目标假设 hash 都使用 64 位小写 SHA-256 类型。
- Genome 是闭合的单输出 DAG：每个已声明 input port 恰好一条入边，`output_node_id` 必须无出边，且所有 node 都必须可达该输出；悬空节点、缺失输入和输出后继均 fail closed。
- `canonical.py` 冻结 `genome_hash_v1`：稳定拓扑序和 canonical node id 不依赖 record node id；topology hash 排除参数值，layout hash 排除 value，semantic hash 绑定 contract/topology/layout/value，record hash 再纳入 genome id 与 provenance。
- canonical JSON 使用 UTF-8/NFC、稳定 key/集合排序、binary64 小写 hex、`-0` 归零，并拒绝 NaN/Infinity。

## V2.2 typed Effect Genome

- `typed_nodes.py` 在兼容 V2.0 `EffectGenome`/`EffectNode` 的前提下增加 `TypedEffectGenome`：16 个 node kind 均为以 `kind` 判别的 sealed Pydantic 类型，调用方不能通过 generic payload 越过 kind 契约。
- 每个 kind 冻结 exact binding name，并校验所引用 `ParameterSpec` 的 dtype、unit、coordinate space 与 color space；Genome 中不允许保留未绑定参数。几何和空间宽度统一使用冻结 Intent 契约的 `shader_uv_bottom_left + normalized`，角度使用 radians，方向使用同一 shader-UV 空间的 unit vector，颜色使用 `linear_rgb + rgba`，强度/透明度使用 ratio。
- typed edge 对 SDF→mask 强制携带 `analytic_fixed_width_v1`，同类型 edge 禁止携带该转换。mask coverage 继续使用 0 outside/1 inside；union/intersection/difference 分别冻结为 max、min、`left * (1 - right)`，颜色组合冻结为 premultiplied source-over。
- `TypedEffectGenome` 复用 V2.0 的全 input 单入边、唯一输出汇点、DAG 与全节点可达校验；typed payload 与 SDF→mask conversion 进入 topology projection，因此任何编译语义变化都会命中 topology/semantic hash。参数布局、值和 provenance 仍分别遵守既有四类 hash 边界。

SeedPlan、Expander、typed AST、GLSL emitter 与真实 WebGL 编译测试由各自 V2.2 子包负责，不在 `genome/` 内交叉实现。
