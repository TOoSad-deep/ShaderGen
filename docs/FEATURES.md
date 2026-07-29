# Features

## F09 — Layered Direct GLSL (`active`)

Current product scope:

- reference PNG visual analysis into canonical `LayerPlanV1`;
- explicit LangGraph nodes for every current attempt stage and refine routing;
- model-authored `LayeredShaderSpecV1`;
- deterministic compilation into `ShaderProgramSpecV1`;
- real WebGL1 compile/link/draw acceptance;
- metric-based incumbent selection, bounded single-layer refinement, and
  deterministic `tunable_manifest`-driven uniform-only search;
- three fresh attempts per parent run;
- private child evidence and atomic selected-parent Artifact publication;
- progress, failure and attempt summaries in Backend and Frontend.

Acceptance is based on the current Direct chain only. Historical engines and
experimentation facilities are not compatibility targets.
