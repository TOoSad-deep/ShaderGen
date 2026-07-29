# Agent contracts

`layer_plan.py` validates visual-analysis JSON and delegates canonical assembly
to ShaderForge. `layered_direct_glsl.py` does the same for Layered Initial and
single-layer Patch output. Trusted identity and hashes are never model fields.

`layerplan_glsl_direct.py` owns graph-independent budgets, renderer protocols,
immutable result models, mutable ledgers and redaction helpers. Services,
states and nodes depend on this contract module; contracts never import graph
or service code.
