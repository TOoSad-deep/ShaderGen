# Agent architecture

```text
LLM Gateway
  → LayerPlan author
  → Layered Initial author
  → optional single-layer Refine author
  → Direct attempt service
```

Prompts live only in `app/prompts`; deterministic domain behavior lives in
ShaderForge.
