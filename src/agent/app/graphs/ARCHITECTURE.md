# LayerPlan Direct graph

`layerplan_glsl_direct.py` is the private product graph. The service runner
injects the Gateway, Renderer, receipt issuer, clock and attempt-local prepared
program cache through `DirectGraphContext`; process handles never enter graph
state. The compiled graph is private: callers must use
`run_layerplan_glsl_direct_graph`, which disables LangSmith tracing and closes
prepared programs in `finally`.

```text
START
  → prepare_reference
  → author_layer_plan
      ├─ failed → release_resources → finalize_attempt → END
      └─ ready → author_initial
                    ├─ failed → release_resources
                    └─ ready → compile_candidate
                                  → validate_candidate
                                  → prepare_program
                                  → render_program
                                  → verify_receipt
                                  → attest_candidate
                                  → evaluate_candidate
                                  → select_candidate
                                  → decide_uniform_optimization
                                      ├─ target/hard block → release_resources
                                      ├─ source eligible
                                      │    → propose_uniform_candidate
                                      │    → apply_uniform_candidate
                                      │    → compile/validate/prepare/draw
                                      │    → receipt/attest/evaluate/select
                                      │    → record_uniform_outcome
                                      │    → decide_uniform_optimization
                                      └─ local optimum/already searched/budget
                                           → decide_refinement
                                               ├─ target/budget/patience/duplicate
                                               │    → release_resources
                                               └─ refine → author_refinement
                                                             ├─ failed
                                                             │    → decide_refinement
                                                             └─ patch
                                                                  → apply_refinement
                                                                  → compile_candidate
```

Initial candidate failures terminate the attempt. Refine failures preserve the
incumbent and return to `decide_refinement`; exhausted compile/draw/LLM budget
or an unavailable renderer terminates the loop immediately. `select_candidate`
is the only node allowed to replace `current_best`; after Initial it requires
strict dominance across target-relative MAE/loss excesses. The per-run
`DirectOptimizationPolicy` stops when both targets are reached and treats an
accepted candidate as material when either `min_delta_mae` or `min_delta_loss`
is met. It permits one feedback-aware recovery by default and prevents an
identical/no-op Patch from reaching compile or draw.

`tunable_manifest` search is deterministic and bounded. One source opens at most
one coordinate-pattern session; each move changes one manifest component on a
Decimal lattice. Prepared WebGL programs may be reused, but every binding still
passes validation, a real draw, a fresh receipt/attestation, evaluation and the
same strict `select_candidate` boundary. A newly accepted structural source may
open a fresh session while the attempt-wide tuning and draw budgets remain.
Candidate-local apply/compile/validation/proof failures consume one bounded
probe and continue to the paired direction or next component; renderer and
global budget failures remain terminal. A failure-bearing pass that cannot
produce material progress ends as `candidate_failures_exhausted`, not as a
false `local_optimum`.

Initial/Refine Author may also return an optional `optimization_focus` sidecar.
The adapter strips it before trusted Layered/Patch assembly, so it never enters
Shader semantics, hashes or provenance. After the authored candidate has passed
the real compile/proof path, `evaluate_candidate` validates the focus against
that exact Layered/Program pair. A valid focus freezes every component outside
its target-layer whitelist; an invalid focus is dropped and uniform selection
falls back to the existing deterministic LayerPlan/residual heuristic. Uniform
derivations inherit the accepted incumbent focus, while rejected Refine
candidates cannot replace it.

Focused ROI metrics reuse the candidate's existing beauty render and therefore
add no draw. The ROI comes from the target LayerPlan region, its intersection
with the worst residual tile, or the full canvas, all in bottom-left WebGL UV.
Local MAE, geometry, edge and outside-ROI facts are available to later Refine,
and uniform-derived candidates reuse the incumbent's resolved ROI so comparisons
within one numeric session stay spatially stable. `select_candidate` still uses
only the global target-relative MAE/loss boundary.

Before a Refine call, `author_refinement` may reuse the incumbent's prepared
program for up to two budgeted diagnostic draws. The trusted compiler packs the
independent alpha union for subject/highlight/detail and
shadow/glow/background into fixed RGB channels; the evaluation domain splits
them into grayscale PNG masks. Only roles present in the canonical LayerPlan
are sent to Refine. These masks are private prompt inputs whose role and content
hash are bound into the Author input identity; a missing or failed diagnostic
degrades to no mask and never blocks refinement or changes candidate selection.

`release_resources` makes normal cleanup observable. The invocation wrapper also
uses `finally` so unexpected node exceptions still close prepared programs.

`layerplan_glsl_direct_studio.py` is the only graph registered by
`langgraph.json`. It is a one-node JSON adapter:

```text
START → run_owned_attempt → END
```

It accepts strict base64 bounded to 8 MiB, owns and closes the default runner,
and returns only `DirectAttemptResult.to_safe_summary()`. Studio startup applies
the fail-closed tracing policy from `observability/langgraph_privacy.py`; the
root `.env` remains available for model/provider configuration.
