# Agent services

`layerplan_glsl_direct.py` owns one isolated Layered Direct attempt.
`engine_rollout_artifacts.py` owns private child reads and atomic public parent
publication.

A successful Direct attempt retains every successfully rendered high-level
Initial/Refine author candidate under `private/renders/` and indexes the ordered
history in `private/manifest.json`. The selected best remains available at
`private/render.png`. Individual parameter-search trial renders never enter this
history. When parameter tuning is introduced, it must use a separate boundary
contract that may retain only its pre-tuning initial and final-best snapshots.
Failed attempts write `private/failure-summary.json` instead of a completed
private manifest.

Private process renders never cross the Artifact boundary. The selected parent
still publishes exactly `render.png`, `metrics.json`, and `manifest.json`.
