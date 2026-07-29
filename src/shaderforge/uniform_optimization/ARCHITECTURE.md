# Uniform optimization

This package is deterministic domain logic for bounded, manifest-only uniform
search. It never calls an LLM, Renderer, graph, Artifact store, or objective
function.

`flatten_tunable_components` verifies a Layered/Program pair, exposes only
manifest-addressable scalar components, and uses Decimal base-anchored lattice
values. `search.py` owns the `uniform_coordinate_v2` deterministic active-set
permutation and coordinate-pattern session. Candidate-local failures consume a
bounded probe and advance the session without inflating real draw/evaluation
counts; hard renderer and attempt-budget failures remain graph-level terminal
conditions.

`patching.py` validates one trusted patch, changes exactly one component, rebuilds
the affected Layer/Layered/Program hashes, clears any old attestation, preserves
the original model `AuthorIdentity`, and binds
`UniformOptimizationProvenanceV1`. Model parsers never accept Patch or
provenance fields. Graph nodes remain responsible for real draw/evaluation and
target-relative MAE/loss selection. `UniformOptimizationSummaryV2` publishes
both MAE and loss before/after values and deltas for the current source-scoped
search session, so structural Refine gains are not attributed to uniform tuning.
Its private trace hash covers the same session slice; attempt-wide private trace
items remain available separately.
