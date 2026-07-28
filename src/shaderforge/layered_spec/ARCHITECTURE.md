# ShaderForge Layered Spec 架构

`layered_spec/` 是默认 direct GLSL 的 Layer 级作者表示。它只依赖
`shaderforge.program_spec`，不调用模型、Renderer、Agent 或 Backend。

- `LayeredShaderSpecV1` 与 canonical `LayerPlanV1` 的 layer ID、role、
  z-index 和顺序逐项一致；可信层注入 Plan hash 与 `AuthorIdentity`。
- 模型只提供每层 `glsl_body`、uniform bindings 和 tunable manifest；
  layer/spec hash 均由可信层以 canonical JSON 重算。
- `LayerPatchV1` 只支持以 base/spec hash 为并发保护的整层替换，不支持
  增删、重排、参数 Patch、helper、blend variant 或 multipass。
- Compiler 把每层正文包装为固定 `vec4 function(vec2 uv)`，按 z-index
  稳定执行 premultiplied source-over，最后与白底合成并确定性生成现有
  `ShaderProgramSpecV1`；常量 edge 倒置的 `smoothstep` 使用既有确定性等价
  修复，其余非法源码继续拒绝。安全校验、真实执行和 attestation 仍由既有
  链路负责。
