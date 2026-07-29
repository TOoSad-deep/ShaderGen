# Architecture

## Current request path

```text
POST /api/shader/generate
  → project lock + optional process ledger
  → direct-only parent coordinator
  → up to 3 fresh isolated attempts
      → LayerPlan Direct LangGraph
          → prepare reference
          → visual LayerPlan author
          → Layered Initial author
          → deterministic compile
          → static validation
          → WebGL1 prepare/link → draw
          → receipt + attestation
          → evaluate → select incumbent
          → optional single-layer Refine loop
          → release resources → finalize
  → atomic parent Artifact publication
  → API response
```

## Ownership

- `src/agent/app/graphs/`: attempt topology, routing and bounded loop.
- `src/agent/app/states/`: private graph state and runtime dependency context.
- `src/agent/app/nodes/layered_direct/`: model calls and one-step graph nodes.
- `src/agent/app/contracts/layerplan_glsl_direct.py`: graph-independent attempt contracts.
- `src/agent/app/services/layerplan_glsl_direct.py`: compatibility graph facade.
- `backend/app/services/engine_rollout_graph.py`: parent retry/publication graph.
- `src/shaderforge/layered_spec/`: Layered models, patching and compiler.
- `src/shaderforge/program_spec/`: canonical execution IR, hashes and attestations.
- `src/shaderforge/validation/`: static WebGL1 safety checks.
- `src/shaderforge/rendering/`: real WebGL1 preparation and drawing.
- `backend/app/services/engine_rollout*.py`: three-attempt coordination and
  parent publication.

## Artifact boundary

Child attempts write detailed LayerPlan, Layered spec, ProgramSpec, diagnostics
and ordered high-level Initial/Refine renders to the private attempt root.
Individual parameter-search trial renders never enter that history. When the
parameter-tuning inner loop is introduced, a separate boundary contract may
retain only its pre-tuning initial and final-best snapshots. Only the selected
attempt is promoted to the public parent root, which exposes `render.png`,
`metrics.json` and `manifest.json`.

## Deliberately absent

There is no alternate engine, compatibility fallback, legacy
`png_to_shader_min` graph, shadow experiment, promotion policy or visual node
laboratory in the product runtime.
