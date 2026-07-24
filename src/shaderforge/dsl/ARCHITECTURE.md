# shaderforge.dsl 架构

最小 Shader DSL V1 的领域契约与确定性 specialized WebGL1 Compiler，按
`docs/superpowers/specs/2026-07-23-minimal-shader-dsl-evolution-design.md` 落地。
当前默认 `scene_mvp` 直接以 ShaderDocument 作为产品真相源；Model Author、
typed layer patch、node-id 参数优化、CandidateSnapshot、Compiler、program cache
和 final Artifact 已贯通。MinScene 适配只用于感知 fallback 与 legacy 审计。

- `document.py`：严格 ShaderDocument 契约。有序 1..8 个 Layer（数组顺序固定为后到前）；
  层内 ShapeExpr 为 circle/ellipse/rounded_box/segment 与二元 union/subtract/intersect
  组成的树，可选 translate/正 scale/cos-sin rotation transform；预算为每层最多 4 个
  primitive、CSG 深度最多 2、全文最多 32 个 primitive；Fill 为 solid/linear/radial
  （Canvas 坐标）；Effect 为 rim/shadow/glow（每种每层最多一个，规范化为
  shadow→glow→rim 固定顺序）；layer opacity 与 opaque Canvas；NaN/Infinity、非正
  scale/radius、零长 segment、越界 corner_radius、非单位 rotation、重复 id 一律拒绝。
  正几何量和方向跨度下限固定为 `0.01`，Canvas 最长边不超过 `1024`，避免 WebGL1
  `mediump` 下平方项下溢为零。
- `canonical.py`：确定性 canonical JSON（map key 规范排序、layers 数组保持原样）、
  document_sha256（绑定结构、层序与全部参数）、topology_sha256（绑定 schema、
  节点/材质/effect 类型、连接与层序）、稳定参数清单（`node:<id>.<field>`、`layer:<id>.<field>`、
  `canvas.background.<channel>`，按路径排序）及其哈希。
- `compiler.py`：确定性编译链（严格解析 → typed IR → resource plan → specialized
  WebGL1 GLSL → 静态验证）。按实际 Layer/CSG 结构静态展开，不实现任意节点解释器；
  sRGB 编码域内以 premultiplied source-over 合成，最终输出 Alpha 固定 1；边缘 AA
  从 `u_resolution` 按像素宽度推导；非均匀缩放距离校正固定为乘以 min(scale.x,
  scale.y)，由 compiler 版本冻结。非 active block 参数烘焙为源码常量，active block
  连续参数打包为 packed vec4 uniform，自定义 fragment uniform 不超过 14 个 vec4
  （加 `u_resolution` 不超过 15）；产物包含 fragment source、uniform values/schema、
  资源摘要与全部版本化哈希。

- `migration.py`：旧 MinScene 可证明子集到 ShaderDocument 的确定性迁移映射（`adapt_min_scene_to_shader_graph`）；`polar_arc`、`edge_line`、`gaussian_lobe` 遇到即 fail closed。感知层与 legacy shadow runner 共用此映射。

单元测试在 `tests/unit_tests/test_dsl_renderer.py`。运行 `make test` 可验证本包。
