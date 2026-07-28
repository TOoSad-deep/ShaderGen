"""Backend 对 engine rollout runtime 的真实注入与兼容接线回归."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from starlette.requests import Request

import backend.app.main as backend_main
from agent.app.services.png_to_shader_min import MinPublicArtifact
from backend.app.api.routes.shader import get_shader_run_artifact
from backend.app.core.settings import BackendSettings
from backend.app.services.engine_rollout import (
    AttemptRef,
    EngineResponseContractFailure,
    ParentRunFailure,
)
from backend.app.services.run_progress import RunProgressRegistry
from backend.app.services.shader import (
    ProjectLockRegistry,
    read_shader_run_artifact,
)
from backend.app.services.shader_generation import (
    ShaderGenerationCommand,
    ShaderGenerationDependencies,
    ShaderGenerationUseCaseError,
    execute_shader_generation,
)


class _Runtime:
    def __init__(self) -> None:
        self.closed = False
        self.generate_calls = 0

    async def generate(self, *_args: Any, **kwargs: Any) -> Any:
        self.generate_calls += 1
        project_id = kwargs["project_id"]
        run_id = kwargs["run_id"]
        attempt_id = str(uuid4())
        return SimpleNamespace(
            project_id=project_id,
            run_id=run_id,
            glsl="direct-glsl",
            renderer_path="direct_program_spec_v1",
            scene=None,
            trace=(
                {
                    "phase": "direct_glsl",
                    "status": "completed",
                },
            ),
            shader_graph_shadow=None,
            status="completed",
            stop_reason="direct_attempt_completed",
            render_width=32,
            render_height=32,
            current_best_mae=0.1,
            current_best_loss=0.2,
            metric_breakdown={"metric_version": "min_scene_composite_v3"},
            template_version="direct-v1",
            render_count=1,
            render_budget=2,
            llm_call_count=1,
            llm_budget=2,
            refine_budget=1,
            run_classification="independent_experiment",
            experiment_id="rollout-test",
            config_fingerprint="a" * 64,
            report_schema_version="direct_glsl_attempt_result_v1",
            patch_candidate_draw_budget=1,
            patch_evidence=(),
            target_mae=0.2,
            target_loss=0.3,
            target_reached=True,
            prepare_duration_ms=1.0,
            uniform_render_count=0,
            uniform_render_p95_ms=0.0,
            quality_preset=kwargs["quality_preset"],
            engine="direct_glsl_layerplan_v1",
            representation="shader_program_spec_v1",
            engine_run={
                "policy_id": "canary-test",
                "policy_sha256": "b" * 64,
                "configured_stage": "canary",
                "stage": "canary",
                "bucket": 0,
                "selected_attempt_id": attempt_id,
                "attempt_refs": [
                    {
                        "attempt_id": attempt_id,
                        "engine": "direct_glsl_layerplan_v1",
                        "representation": "shader_program_spec_v1",
                        "status": "succeeded",
                        "failure_code": None,
                    }
                ],
                "fallback_from": None,
                "fallback_reason": None,
                "promotion_authorization_sha256": "c" * 64,
            },
        )

    async def read_public_artifact(
        self,
        _run_id: str,
        artifact_name: str,
    ) -> MinPublicArtifact:
        return MinPublicArtifact(
            data=artifact_name.encode(),
            content_type="application/octet-stream",
            filename=artifact_name,
        )

    async def close(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_usecase_selects_rollout_runtime_and_exposes_discriminator() -> None:
    runtime = _Runtime()

    class _ForbiddenOldService:
        async def generate(self, *_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("rollout 已装配时不得调用公开旧 service。")

    class _ShadowSpy:
        calls = 0

        def submit(self, **_kwargs: Any) -> dict[str, Any]:
            self.calls += 1
            return {"status": "accepted"}

    shadow = _ShadowSpy()
    project_id = uuid4()
    run_id = uuid4()
    response = await execute_shader_generation(
        ShaderGenerationCommand(
            image=b"image",
            filename="target.png",
            content_type="image/png",
            project_id=project_id,
            run_id=run_id,
            quality_preset="fast",
            instruction="",
            started_at=time.perf_counter(),
        ),
        ShaderGenerationDependencies(
            pool=None,
            min_service=_ForbiddenOldService(),
            engine_rollout_service=runtime,
            locks=ProjectLockRegistry(),
            production_shadow=shadow,
        ),
    )

    assert runtime.generate_calls == 1
    assert shadow.calls == 0
    assert response.engine == "direct_glsl_layerplan_v1"
    assert response.representation == "shader_program_spec_v1"
    assert response.engine_run is not None
    assert response.engine_run.stage == "canary"
    assert response.min_pipeline.renderer_path == "direct_program_spec_v1"


@pytest.mark.anyio
async def test_usecase_preserves_safe_parent_failure_and_attempt_summary() -> None:
    direct_attempt = str(uuid4())
    retry_attempt = str(uuid4())

    class _FailedRuntime:
        async def generate(self, *_args: Any, **_kwargs: Any) -> Any:
            raise ParentRunFailure(
                "direct_attempts_failed",
                attempt_refs=(
                    AttemptRef(
                        attempt_id=direct_attempt,
                        engine="direct_glsl_layerplan_v1",
                        representation="shader_program_spec_v1",
                        status="failed",
                        failure_code="direct_attempt_failed",
                    ),
                    AttemptRef(
                        attempt_id=retry_attempt,
                        engine="direct_glsl_layerplan_v1",
                        representation="shader_program_spec_v1",
                        status="failed",
                        failure_code="llm_transient_failure",
                    ),
                ),
            )

    progress = RunProgressRegistry()
    run_id = uuid4()
    with pytest.raises(ShaderGenerationUseCaseError) as raised:
        await execute_shader_generation(
            ShaderGenerationCommand(
                image=b"private-image",
                filename="target.png",
                content_type="image/png",
                project_id=uuid4(),
                run_id=run_id,
                quality_preset="fast",
                instruction="private-instruction",
                started_at=time.perf_counter(),
            ),
            ShaderGenerationDependencies(
                pool=None,
                min_service=None,
                engine_rollout_service=_FailedRuntime(),
                locks=ProjectLockRegistry(),
                progress=progress,
            ),
        )

    error = raised.value
    assert error.status_code == 502
    assert error.code == "direct_attempts_failed"
    assert error.stage == "engine_rollout"
    assert error.stop_reason == "direct_attempts_failed"
    assert "internal_pipeline_error" not in str(error)
    snapshot = progress.read(str(run_id))
    assert snapshot["status"] == "failed"
    failure_event = next(
        event for event in snapshot["events"] if event.get("phase") == "engine_failed"
    )
    assert failure_event["failure_code"] == "direct_attempts_failed"
    assert failure_event["attempt_refs"] == [
        {
            "attempt_id": direct_attempt,
            "engine": "direct_glsl_layerplan_v1",
            "representation": "shader_program_spec_v1",
            "status": "failed",
            "failure_code": "direct_attempt_failed",
        },
        {
            "attempt_id": retry_attempt,
            "engine": "direct_glsl_layerplan_v1",
            "representation": "shader_program_spec_v1",
            "status": "failed",
            "failure_code": "llm_transient_failure",
        },
    ]
    assert "private-instruction" not in str(failure_event)


@pytest.mark.anyio
async def test_business_exception_name_does_not_impersonate_timeout() -> None:
    class BusinessTimeoutStateError(RuntimeError):
        pass

    class _FailedRuntime:
        async def generate(self, *_args: Any, **_kwargs: Any) -> Any:
            raise BusinessTimeoutStateError("private detail")

    with pytest.raises(ShaderGenerationUseCaseError) as raised:
        await execute_shader_generation(
            ShaderGenerationCommand(
                image=b"private-image",
                filename="target.png",
                content_type="image/png",
                project_id=uuid4(),
                run_id=uuid4(),
                quality_preset="fast",
                instruction="private-instruction",
                started_at=time.perf_counter(),
            ),
            ShaderGenerationDependencies(
                pool=None,
                min_service=None,
                engine_rollout_service=_FailedRuntime(),
                locks=ProjectLockRegistry(),
            ),
        )

    error = raised.value
    assert error.status_code == 500
    assert error.code == "internal_pipeline_error"
    assert error.stage == "pipeline"
    assert error.retryable is False


@pytest.mark.anyio
async def test_engine_response_contract_failure_keeps_safe_field_stage() -> None:
    class _InvalidRuntime:
        async def generate(self, *_args: Any, **_kwargs: Any) -> Any:
            raise EngineResponseContractFailure("min_pipeline.render_count")

    with pytest.raises(ShaderGenerationUseCaseError) as raised:
        await execute_shader_generation(
            ShaderGenerationCommand(
                image=b"private-image",
                filename="target.png",
                content_type="image/png",
                project_id=uuid4(),
                run_id=uuid4(),
                quality_preset="fast",
                instruction="private-instruction",
                started_at=time.perf_counter(),
            ),
            ShaderGenerationDependencies(
                pool=None,
                min_service=None,
                engine_rollout_service=_InvalidRuntime(),
                locks=ProjectLockRegistry(),
            ),
        )

    error = raised.value
    assert error.status_code == 500
    assert error.code == "response_contract_failed"
    assert error.stage == "response_contract"
    assert error.stop_reason == "engine_response_contract_failed"


@pytest.mark.anyio
async def test_artifact_reader_supports_async_rollout_reader() -> None:
    artifact = await read_shader_run_artifact(
        str(uuid4()),
        "manifest",
        service=_Runtime(),
    )
    assert artifact.data == b"manifest"


@pytest.mark.anyio
async def test_canary_reader_falls_back_to_public_v1_history_only() -> None:
    class _MissingRollout:
        async def read_public_artifact(
            self,
            _run_id: str,
            _artifact_name: str,
        ) -> MinPublicArtifact:
            raise FileNotFoundError("不是 v2 parent")

    class _LegacyPublic:
        def read_public_artifact(
            self,
            _run_id: str,
            artifact_name: str,
        ) -> MinPublicArtifact:
            return MinPublicArtifact(
                data=f"legacy-{artifact_name}".encode(),
                content_type="application/json",
                filename=f"{artifact_name}.json",
            )

    app = FastAPI()
    app.state.engine_rollout_service = _MissingRollout()
    app.state.png_to_shader_min_service = _LegacyPublic()
    app.state.project_locks = ProjectLockRegistry()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1),
            "scheme": "http",
            "app": app,
        }
    )

    response = await get_shader_run_artifact(request, uuid4(), "manifest")
    assert response.body == b"legacy-manifest"


@pytest.mark.anyio
async def test_lifespan_injects_and_closes_rollout_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime()
    old_service = object()

    async def open_database(app: FastAPI, _url: str | None) -> None:
        app.state.db_pool = None

    async def close_database(app: FastAPI) -> None:
        app.state.db_pool = None

    monkeypatch.setattr(backend_main, "open_database_pool", open_database)
    monkeypatch.setattr(backend_main, "close_database_pool", close_database)
    monkeypatch.setattr(
        backend_main,
        "get_default_png_to_shader_min_service",
        lambda: old_service,
    )
    monkeypatch.setattr(
        backend_main,
        "build_engine_rollout_runtime",
        lambda **_kwargs: runtime,
    )
    app = FastAPI()
    async with backend_main.build_lifespan(BackendSettings())(app):
        assert app.state.engine_rollout_service is runtime
    assert runtime.closed


def test_settings_reads_private_rollout_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private-attempts"
    monkeypatch.delenv("SHADERGEN_ENGINE_POLICY_PATH", raising=False)
    monkeypatch.delenv("SHADERGEN_DIRECT_GLSL_KILL_SWITCH", raising=False)
    monkeypatch.setenv(
        "SHADERGEN_ENGINE_ROLLOUT_PRIVATE_ARTIFACT_ROOT",
        str(private_root),
    )
    settings = BackendSettings.from_env(load_environment=False)
    assert settings.engine_rollout_private_artifact_root == private_root


@pytest.mark.parametrize(
    ("rollout_relative", "shadow_relative"),
    [
        ("shared", "shared"),
        ("shadow/rollout", "shadow"),
        ("rollout", "rollout/shadow"),
    ],
)
def test_settings_rejects_overlapping_private_artifact_roots(
    tmp_path: Path,
    rollout_relative: str,
    shadow_relative: str,
) -> None:
    with pytest.raises(ValueError, match="彼此隔离、互不嵌套"):
        BackendSettings(
            engine_rollout_private_artifact_root=tmp_path / rollout_relative,
            production_shadow_artifact_root=tmp_path / shadow_relative,
        )


@pytest.mark.anyio
async def test_lifespan_rejects_rollout_root_overlapping_actual_public_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    public_root = tmp_path / "public"
    settings = BackendSettings(
        engine_rollout_private_artifact_root=public_root / "private-rollout",
        production_shadow_artifact_root=tmp_path / "private-shadow",
    )
    public_service = SimpleNamespace(artifacts=SimpleNamespace(base_root=public_root))

    async def open_database(app: FastAPI, _url: str | None) -> None:
        app.state.db_pool = None

    async def close_database(app: FastAPI) -> None:
        app.state.db_pool = None

    monkeypatch.setattr(backend_main, "open_database_pool", open_database)
    monkeypatch.setattr(backend_main, "close_database_pool", close_database)
    monkeypatch.setattr(
        backend_main,
        "get_default_png_to_shader_min_service",
        lambda: public_service,
    )
    app = FastAPI()
    with pytest.raises(ValueError, match="public_artifact_root"):
        async with backend_main.build_lifespan(settings)(app):
            raise AssertionError("危险根配置不得进入应用运行阶段。")
