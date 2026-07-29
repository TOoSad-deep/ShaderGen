# Backend

FastAPI exposes health, Direct generation, progress and three public Artifact
reads. Application lifespan creates one direct-only runtime with disjoint public
parent and private child stores. The coordinator performs at most three fresh
attempts and never reuses an attempt-local Renderer or cache.

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
