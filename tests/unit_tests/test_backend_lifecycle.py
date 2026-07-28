from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI

import backend.app.database.agent_memory as agent_memory
import backend.app.database.session as database_session
import backend.app.main as backend_main
from backend.app.core.settings import BackendSettings
from shaderforge.config import RUNTIME_TIMEOUTS


def test_backend_settings_freeze_boot_environment(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/shadergen")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv(
        "SHADERGEN_CORS_ORIGINS",
        "https://studio.example,https://review.example",
    )
    settings = BackendSettings.from_env(load_environment=False)
    assert settings.database_url == "postgresql://example/shadergen"
    assert settings.log_level == "DEBUG"
    assert settings.cors_origins == (
        "https://studio.example",
        "https://review.example",
    )


def test_backend_settings_reject_wildcard_cors(monkeypatch) -> None:
    monkeypatch.setenv("SHADERGEN_CORS_ORIGINS", "*")
    with pytest.raises(ValueError, match="不允许使用"):
        BackendSettings.from_env(load_environment=False)


def test_backend_settings_use_unified_timeout_yaml() -> None:
    settings = BackendSettings.from_env(load_environment=False)

    assert (
        settings.engine_rollout_attempt_timeout_seconds
        == RUNTIME_TIMEOUTS.engine.attempt_seconds
    )
    assert (
        settings.engine_rollout_close_timeout_seconds
        == RUNTIME_TIMEOUTS.engine.close_seconds
    )
    assert (
        settings.production_shadow_attempt_timeout_seconds
        == RUNTIME_TIMEOUTS.production_shadow.attempt_seconds
    )
    assert (
        settings.production_shadow_close_timeout_seconds
        == RUNTIME_TIMEOUTS.production_shadow.close_seconds
    )
    assert (
        settings.production_shadow_resource_close_timeout_seconds
        == RUNTIME_TIMEOUTS.production_shadow.resource_close_seconds
    )


@pytest.mark.anyio
async def test_database_pool_closes_when_schema_initialization_fails(
    monkeypatch,
) -> None:
    events: list[str] = []

    class FakePool:
        async def close(self) -> None:
            events.append("close_database")

    async def create_pool(*args, **kwargs):
        return FakePool()

    async def initialize_schema(pool) -> None:
        raise RuntimeError("schema failed")

    monkeypatch.setattr(database_session.asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(
        database_session, "initialize_database_schema", initialize_schema
    )
    app = FastAPI()
    with pytest.raises(RuntimeError, match="schema failed"):
        await database_session.open_database_pool(
            app,
            "postgresql://example/shadergen",
        )
    assert events == ["close_database"]
    assert getattr(app.state, "db_pool", None) is None


@pytest.mark.anyio
async def test_dormant_agent_memory_pool_closes_when_startup_is_cancelled(
    monkeypatch,
) -> None:
    events: list[str] = []

    class FakePool:
        async def open(self, *, wait: bool) -> None:
            events.append("open_memory_pool")

        async def close(self) -> None:
            events.append("close_memory_pool")

    async def verify_schema(saver, store) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(agent_memory, "_pool", lambda database_url: FakePool())
    monkeypatch.setattr(agent_memory, "AsyncPostgresSaver", lambda pool: object())
    monkeypatch.setattr(agent_memory, "AsyncPostgresStore", lambda pool: object())
    monkeypatch.setattr(agent_memory, "_verify_schema", verify_schema)
    app = FastAPI()
    with pytest.raises(asyncio.CancelledError):
        await agent_memory.open_agent_memory(app, "postgresql://example/shadergen")
    assert events == ["open_memory_pool", "close_memory_pool"]


@pytest.mark.anyio
async def test_lifespan_injects_and_closes_only_scene_mvp_service(
    monkeypatch,
) -> None:
    events: list[str] = []
    min_service = object()

    async def open_database(app, database_url) -> None:
        events.append("open_database")
        app.state.db_pool = object()

    async def close_database(app) -> None:
        events.append("close_database")
        app.state.db_pool = None

    async def close_min(service) -> None:
        assert service is min_service
        events.append("close_min")

    monkeypatch.setattr(backend_main, "open_database_pool", open_database)
    monkeypatch.setattr(backend_main, "close_database_pool", close_database)
    monkeypatch.setattr(
        backend_main,
        "get_default_png_to_shader_min_service",
        lambda: min_service,
    )
    monkeypatch.setattr(backend_main, "close_png_to_shader_min_service", close_min)
    app = FastAPI()
    async with backend_main.build_lifespan(BackendSettings())(app):
        assert app.state.png_to_shader_min_service is min_service
        assert not hasattr(app.state, "png_to_shader_v1_service")
    assert events == ["open_database", "close_min", "close_database"]
    assert app.state.png_to_shader_min_service is None
    assert app.state.project_locks is None
