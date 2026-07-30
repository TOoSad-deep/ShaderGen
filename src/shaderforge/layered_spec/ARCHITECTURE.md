# ShaderForge Layered Spec 架构

`layered_spec/` 是默认 direct GLSL 的 Layer 级作者表示。它只依赖
`shaderforge.program_spec`，不调用模型、Renderer、Agent 或 Backend。

- `blend_modes.py` 独立拥有八种 mode 白名单、默认值和可信 GLSL 合成公式；
  `models.py` 只维护 Layer/Spec/Patch 数据结构，`compiler.py` 只负责编排
  Layer 函数、合成顺序和 ProgramSpec 生成。
- `LayeredShaderSpecV1` 与 canonical `LayerPlanV1` 的 layer ID、role、
  z-index 和顺序逐项一致；可信层注入 Plan hash 与 `AuthorIdentity`。
- 模型只提供每层 `blend_mode`、`glsl_body`、uniform bindings 和 tunable
  manifest；layer/spec hash 均由可信层以 canonical JSON 重算。
- `LayerPatchV1` 只支持以 base/spec hash 为并发保护的整层替换，不支持
  增删、重排、helper 或 multipass；replacement 可以在固定白名单内修改目标
  Layer 的 blend mode；trusted uniform-only
  Patch 位于 `shaderforge.uniform_optimization`，不进入模型 schema。
- Compiler 把每层正文包装为固定 `vec4 function(vec2 uv)`，按 z-index
  稳定执行 premultiplied-alpha 合成。支持 `source_over`、`add`、`screen`、
  `multiply`、`overlay`、`soft_light`、`lighten`、`darken` 八种固定公式；
  默认 `source_over` 保持既有行为。最后与白底合成并确定性生成现有
  `ShaderProgramSpecV1`；常量 edge 倒置的 `smoothstep` 使用既有确定性等价
  修复，其余非法源码继续拒绝。安全校验、真实执行和 attestation 仍由既有
  链路负责；当前产品不以 uniform 数量或总分量预拒绝 Layered 候选，而由
  当前真实 Renderer 的 prepare/link/draw 判定容量。
- Compiler 还会注入不属于 `ShaderProgramSpecV1` binding 的内部
  `u_sg_role_mask_mode`：默认 `0` 严格走 beauty 合成；`1` 在 RGB 分别输出
  subject/highlight/detail 的逐像素 alpha union，`2` 依次输出
  shadow/glow/background，均仅供可信诊断渲染使用。模型 Layer 正文与 bindings
  都不得引用该标识符。
- uniform-only 派生保留原模型 `AuthorIdentity`，以独立
  `UniformOptimizationProvenanceV1` 绑定父 Layered/Program、算法配置和
  move；provenance 非空时进入 Layered hash，未优化旧 Spec 的 hash 语义不变。
