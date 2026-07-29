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
