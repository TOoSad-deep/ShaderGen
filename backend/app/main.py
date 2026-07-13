"""FastAPI 后端入口."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.router import api_router
from backend.app.core.logging import configure_logging
from backend.app.database.agent_memory import close_agent_memory, open_agent_memory
from backend.app.database.session import close_database_pool, open_database_pool
from backend.app.middleware.request_logging import build_request_logging_middleware
from backend.app.services.shader import ProjectLockRegistry

configure_logging()
logger = logging.getLogger("backend.app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """记录后端生命周期."""
    logger.info("backend.startup")
    app.state.project_locks = ProjectLockRegistry()
    await open_database_pool(app)
    try:
        await open_agent_memory(app)
        yield
    finally:
        await close_agent_memory(app)
        await close_database_pool(app)
        app.state.project_locks = None
        logger.info("backend.shutdown")


app = FastAPI(title="ShaderGen API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(build_request_logging_middleware(logger))
app.include_router(api_router)
