# Backend

FastAPI exposes health, Direct generation, progress and three public Artifact
reads. Application lifespan creates one direct-only runtime with disjoint public
parent and private child stores. The parent coordinator invokes an embedded
LangGraph and performs at most three fresh attempts without reusing an
attempt-local Renderer or cache.

```text
START
  → initialize_parent
  → execute_attempt
  → record_attempt_outcome
      ├─ retry → prepare_retry → execute_attempt
      ├─ success → publish_parent → finalize_parent → END
      └─ exhausted → finalize_parent → ParentRunFailure
```

`app/services/engine_rollout.py` is the stable facade,
`engine_rollout_state.py` owns the parent contracts/state/runtime context, and
`engine_rollout_graph.py` owns nodes, routes, retry loop, publication and
finalization. Attempt timeout, executor cleanup and parent Artifact publication
retain their existing contracts. The compiled parent graph is private; its
wrapper disables tracing because state contains the uploaded image and request.

```bash
make dev-backend
```

## Diagnostics

Backend, Agent and ShaderForge terminal logs share the correlation fields
`request_id`, `run_id`, `project_id`, `attempt_id` and `stage`. A caller may send
an ASCII `X-Request-ID` containing letters, digits, `.`, `_` or `-`; otherwise
the backend generates a UUID. Every HTTP response echoes the resolved
`X-Request-ID`.

Handled 4xx responses emit `event=request.rejected` at warning level and handled
5xx responses emit `event=request.failed` at error level. Pipeline boundaries
add stable `error_code`, `error_type`, retryability and suppressed-cleanup
fields. Unexpected exceptions retain a sanitized type chain and repository
stack locations, but logs must not include exception messages, API keys,
uploaded images, prompts, GLSL, model output, reasoning or raw provider
responses. `make dev-backend` disables Uvicorn's duplicate access line because
the correlated request middleware already records method, path, status and
duration.
