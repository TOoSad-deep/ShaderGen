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
                                  → decide_refinement
                                      ├─ done/hard budget block → release_resources
                                      └─ refine → author_refinement
                                                    ├─ failed → decide_refinement
                                                    └─ patch → apply_refinement
                                                               → compile_candidate
```

Initial candidate failures terminate the attempt. Refine failures preserve the
incumbent and return to `decide_refinement`; exhausted compile/draw/LLM budget
or an unavailable renderer terminates the loop immediately. `select_candidate`
is the only node allowed to replace `current_best`, and only for strictly lower
loss.

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
