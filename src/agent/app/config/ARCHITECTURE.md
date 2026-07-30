# Agent configuration

Model selection is read from environment-backed model configuration. Runtime
timeouts are owned by `shaderforge.config`.

`direct_quality_presets.yaml` is the editable source for all four Direct quality
presets. Each preset owns its optimization targets, convergence controls, and
attempt-wide LLM/compile/draw/Refine/uniform-search budgets. The Agent loads and
strictly validates the file at process startup; unknown fields, missing presets,
invalid scalar types, and budget combinations that cannot cover their declared
work fail closed. Restart the Backend and any standalone Agent/LangGraph process
after editing the YAML.
Draw capacity beyond the declared structural-candidate and uniform-search
minimum is the bounded role-alpha diagnostic budget. Refine uses at most two of
those surplus draws per round and skips mask generation when no surplus remains.
