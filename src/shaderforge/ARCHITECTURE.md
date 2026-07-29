# ShaderForge

ShaderForge is the deterministic domain layer:

```text
LayerPlan + LayeredShaderSpec
  → bounded tunable-manifest uniform derivation (optional)
  → Layered compiler
  → ShaderProgramSpec
  → safety validation
  → WebGL1 renderer
  → receipt + attestation
```

Store and evaluation packages support isolated attempts and quality selection.
