# Agent architecture

```text
Direct attempt service
  → LayerPlan Direct LangGraph
      → prepare reference
      → LayerPlan author
      → Layered Initial author
      → compile → validate → WebGL prepare → draw
      → verify receipt/attestation → evaluate → select incumbent
      → optional LayerPatch Refine loop
      → release resources → finalize
```

Prompts live only in `app/prompts`; deterministic domain behavior lives in
ShaderForge.
