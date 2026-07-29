# Architecture

## Current request path

```text
POST /api/shader/generate
  → project lock + optional process ledger
  → direct-only parent coordinator
  → up to 3 fresh isolated attempts
      → visual LayerPlan author
      → Layered Initial author
      → deterministic compile
      → static validation
      → WebGL1 prepare/link/draw
      → receipt + attestation
      → optional single-layer Refine
  → atomic parent Artifact publication
  → API response
```

## Ownership

- `src/agent/app/nodes/layered_direct/`: model calls and structured repair.
- `src/agent/app/services/layerplan_glsl_direct.py`: one isolated attempt.
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

There is no alternate engine, compatibility fallback, graph runtime, shadow
experiment, promotion policy or visual node laboratory in the product runtime.
