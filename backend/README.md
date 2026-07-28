# Backend

FastAPI exposes health, Direct generation, progress and three public Artifact
reads. Application lifespan creates one direct-only runtime with disjoint public
parent and private child stores. The coordinator performs at most three fresh
attempts and never reuses an attempt-local Renderer or cache.

```bash
make dev-backend
```
