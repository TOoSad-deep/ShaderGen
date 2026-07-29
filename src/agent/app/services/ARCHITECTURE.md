# Agent services

`layerplan_glsl_direct.py` is the stable Backend facade. It re-exports the
configuration/result contracts, constructs `DirectGraphContext`, invokes the
guarded private graph entry, and returns `DirectAttemptResult`. Graph topology,
state and workflow helpers do not live in the service layer.

The caller passes only `quality_preset`; the service resolves the Agent-owned
per-run `DirectOptimizationPolicy`. Static config owns the attempt-wide
compile/draw/LLM/Refine and uniform-search budgets.

`engine_rollout_artifacts.py` owns private child reads and atomic public parent
publication.

A successful Direct attempt retains every successfully rendered high-level
Initial/Refine author candidate under `private/renders/` and indexes the ordered
history in `private/manifest.json`. Individual parameter-search trial renders,
including a parameter-tuned final best, never enter this history. The selected
best always remains available at `private/render.png`, and
`render_retention.final_best_sequence` may therefore point to a candidate that
is intentionally absent from `render_retention.renders`. Failed attempts write
`private/failure-summary.json` instead of a completed private manifest.

Private process renders never cross the Artifact boundary. The selected parent
still publishes exactly `render.png`, `metrics.json`, and `manifest.json`.
