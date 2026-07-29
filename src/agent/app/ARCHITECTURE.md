# Agent application

- `contracts/`: graph-independent LayerPlan, Layered author and direct-attempt contracts.
- `states/`: private attempt state and runtime dependency context.
- `nodes/layered_direct/`: bounded model calls plus one-step workflow nodes.
- `graphs/`: current LayerPlan Direct nodes, edges, routes and refine loop.
- `services/layerplan_glsl_direct.py`: stable compatibility facade and thin graph runner.
- `llms/`, `messages/`, `prompts/`, `observability/`: shared model support.
