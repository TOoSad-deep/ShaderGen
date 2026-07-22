from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from agent.app.services.png_to_shader_v1 import (
    NoValidatedShaderError,
    PngToShaderV1Result,
    PublicArtifact,
    PublicArtifactNotFoundError,
)
from backend.app.api.routes import shader as shader_route
from backend.app.main import app
from backend.app.services import shader_generation as shader_generation_service


def score() -> dict:
    return {
        "metric_version": "basic_oracle_v1",
        "total_loss": 0.104,
        "global_rmse": 0.087,
        "global_mae": 0.071,
        "edge_loss": 0.132,
        "geometry_loss": 0.08,
        "representative_pixel_loss": 0.09,
        "roi_losses": {"highlight": 0.11},
        "protected_region_losses": {"subject": 0.07},
        "effective_weights": {"global_rmse": 0.35},
        "diagnostics": [],
    }


def test_generate_procedural_v1_contract(monkeypatch) -> None:
    async def fake_generate(image: bytes, content_type: str, **kwargs):
        assert image == b"image"
        assert content_type == "image/png"
        assert kwargs["quality_preset"] == "high"
        assert kwargs["instruction"] == "保留左上高光"
        return PngToShaderV1Result(
            project_id=kwargs["project_id"],
            run_id=kwargs["run_id"],
            glsl="precision mediump float; void main(){gl_FragColor=vec4(1.0);}",
            memory_status="ephemeral",
            quality_preset="high",
            iterations=2,
            stop_reason="stagnation",
            best_candidate_id="candidate-0002",
            render_width=505,
            render_height=527,
            score=score(),
            unscored_fallback=False,
            review={
                "overall_assessment": "高光已接近。",
                "recommended_changes": [
                    {
                        "target": "rim",
                        "direction": "略微收窄",
                        "reason": "右侧边缘偏厚",
                    }
                ],
            },
            glsl_model_name="fake-author",
            vision_model_name="fake-vision",
            events=(
                {
                    "stage": "selection",
                    "event_type": "current_best_updated",
                    "payload": {"candidate_id": "candidate-0002"},
                },
            ),
        )

    monkeypatch.setattr(
        shader_generation_service,
        "generate_procedural_shader_from_image",
        fake_generate,
    )
    monkeypatch.setattr(
        shader_generation_service,
        "get_png_to_shader_v1_models",
        lambda: ("fake-author", "fake-vision"),
    )

    response = TestClient(app).post(
        "/api/shader/generate",
        files={"file": ("target.png", b"image", "image/png")},
        data={
            "generation_mode": "procedural_v1",
            "quality_preset": "high",
            "instruction": "保留左上高光",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    UUID(payload["project_id"])
    UUID(payload["run_id"])
    assert payload["generation_mode"] == "procedural_v1"
    assert payload["quality_preset"] == "high"
    assert payload["iterations"] == 2
    assert payload["stop_reason"] == "stagnation"
    assert payload["best_candidate_id"] == "candidate-0002"
    assert payload["unscored_fallback"] is False
    assert (payload["render_width"], payload["render_height"]) == (505, 527)
    assert payload["score"]["total_loss"] == 0.104
    assert payload["final_render_url"].endswith("/artifacts/final-render")
    assert payload["review"] == {
        "evaluation": "高光已接近。",
        "suggestions": ["rim：略微收窄（右侧边缘偏厚）"],
    }


def test_generate_scene_mvp_contract_and_ledger(monkeypatch) -> None:
    calls: dict[str, object] = {}
    project_id = uuid4()

    async def fake_start(*args, **kwargs):
        calls["start"] = kwargs

    async def fake_success(*args, **kwargs):
        calls["success"] = kwargs

    async def fake_generate(image: bytes, content_type: str, **kwargs):
        assert image == b"image"
        assert content_type == "image/png"
        assert kwargs["project_id"] == str(project_id)
        assert UUID(kwargs["run_id"])
        assert kwargs["quality_preset"] == "high"
        assert kwargs["instruction"] == "保留右侧轮廓"
        assert kwargs["service"] is app.state.png_to_shader_min_service
        return SimpleNamespace(
            project_id=kwargs["project_id"],
            run_id=kwargs["run_id"],
            glsl="precision mediump float; void main(){gl_FragColor=vec4(1.0);}",
            render_width=64,
            render_height=48,
            status="succeeded",
            stop_reason="completed",
            template_version="png_to_shader_min_template_v2",
            quality_preset="high",
            current_best_mae=0.03125,
            current_best_loss=0.04,
            metric_breakdown={
                "metric_version": "min_scene_composite_v2",
                "total_loss": 0.04,
                "global_mae": 0.03125,
                "foreground_mae": 0.05,
                "highlight_mae": 0.06,
                "shadow_mae": 0.04,
            },
            render_count=3,
            render_budget=160,
            llm_call_count=1,
            llm_budget=6,
            refine_budget=3,
            renderer_path="prepared_uniforms_v1",
            target_mae=0.08,
            target_loss=0.08,
            target_reached=True,
            prepare_duration_ms=12.5,
            uniform_render_count=3,
            uniform_render_p95_ms=4.25,
            scene={
                "schema_version": "png_to_shader_min_scene_v2",
                "canvas": {
                    "width": 64,
                    "height": 48,
                    "background": [0.0, 0.0, 0.0],
                },
                "object": {},
            },
            trace=(
                {
                    "phase": "bootstrap",
                    "status": "completed",
                    "duration_ms": 2.5,
                },
                {
                    "phase": "evaluate",
                    "status": "completed",
                    "duration_ms": 4.0,
                },
            ),
        )

    monkeypatch.setattr(
        shader_generation_service, "start_shader_generation_run", fake_start
    )
    monkeypatch.setattr(
        shader_generation_service, "record_shader_generation_success", fake_success
    )
    monkeypatch.setattr(
        shader_generation_service,
        "generate_scene_shader_from_image",
        fake_generate,
    )
    previous_pool = getattr(app.state, "db_pool", None)
    previous_service = getattr(app.state, "png_to_shader_min_service", None)
    app.state.db_pool = object()
    app.state.png_to_shader_min_service = object()
    try:
        response = TestClient(app).post(
            "/api/shader/generate",
            files={"file": ("target.png", b"image", "image/png")},
            data={
                "project_id": str(project_id),
                "generation_mode": "scene_mvp",
                "quality_preset": "high",
                "instruction": " 保留右侧轮廓 ",
            },
        )
    finally:
        app.state.db_pool = previous_pool
        app.state.png_to_shader_min_service = previous_service

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_id"] == str(project_id)
    assert UUID(payload["run_id"])
    assert payload["generation_mode"] == "scene_mvp"
    assert payload["quality_preset"] == "high"
    assert payload["render_width"] == 64
    assert payload["render_height"] == 48
    assert payload["stop_reason"] == "completed"
    assert payload["score"] is None
    assert payload["min_pipeline"] == {
        "mae": 0.03125,
        "objective_loss": 0.04,
        "metric_breakdown": {
            "metric_version": "min_scene_composite_v2",
            "total_loss": 0.04,
            "global_mae": 0.03125,
            "foreground_mae": 0.05,
            "highlight_mae": 0.06,
            "shadow_mae": 0.04,
        },
        "template_version": "png_to_shader_min_template_v2",
        "render_count": 3,
        "render_budget": 160,
        "llm_call_count": 1,
        "llm_budget": 6,
        "refine_budget": 3,
        "renderer_path": "prepared_uniforms_v1",
        "target_mae": 0.08,
        "target_loss": 0.08,
        "target_reached": True,
        "prepare_duration_ms": 12.5,
        "uniform_render_count": 3,
        "uniform_render_p95_ms": 4.25,
        "scene": {
            "schema_version": "png_to_shader_min_scene_v2",
            "canvas": {
                "width": 64,
                "height": 48,
                "background": [0.0, 0.0, 0.0],
            },
            "object": {},
        },
        "trace": [
            {
                "phase": "bootstrap",
                "status": "completed",
                "duration_ms": 2.5,
            },
            {
                "phase": "evaluate",
                "status": "completed",
                "duration_ms": 4.0,
            },
        ],
    }
    assert payload["final_render_url"].endswith("/artifacts/final-render")
    assert payload["metrics_url"].endswith("/artifacts/metrics")
    assert payload["manifest_url"].endswith("/artifacts/manifest")
    start = calls["start"]
    assert isinstance(start, dict)
    assert start["project_id"] == project_id
    assert start["generation_mode"] == "scene_mvp"
    assert start["quality_preset"] == "high"
    success = calls["success"]
    assert isinstance(success, dict)
    assert success["result_summary"]["current_best_mae"] == 0.03125
    assert success["result_summary"]["current_best_loss"] == 0.04
    assert success["result_summary"]["template_version"] == (
        "png_to_shader_min_template_v2"
    )
    assert success["result_summary"]["quality_preset"] == "high"
    assert success["result_summary"]["render_budget"] == 160
    assert success["result_summary"]["renderer_path"] == "prepared_uniforms_v1"
    assert success["result_summary"]["target_reached"] is True
    assert success["record_default_model_call"] is False
    assert [event["stage"] for event in success["events"]] == [
        "bootstrap",
        "evaluate",
    ]


def test_artifact_endpoint_falls_back_to_scene_mvp_service() -> None:
    run_id = uuid4()

    class ProceduralService:
        def read_public_artifact(self, requested_run_id, artifact_name):
            raise PublicArtifactNotFoundError("missing")

    class MinService:
        def read_public_artifact(self, requested_run_id, artifact_name):
            assert requested_run_id == str(run_id)
            assert artifact_name == "metrics"
            return PublicArtifact(
                data=b'{"current_best_mae":0.03125}',
                content_type="application/json; charset=utf-8",
                filename="metrics.json",
            )

    previous_procedural = getattr(app.state, "png_to_shader_v1_service", None)
    previous_min = getattr(app.state, "png_to_shader_min_service", None)
    app.state.png_to_shader_v1_service = ProceduralService()
    app.state.png_to_shader_min_service = MinService()
    try:
        response = TestClient(app).get(f"/api/shader/runs/{run_id}/artifacts/metrics")
    finally:
        app.state.png_to_shader_v1_service = previous_procedural
        app.state.png_to_shader_min_service = previous_min

    assert response.status_code == 200
    assert response.json() == {"current_best_mae": 0.03125}
    assert response.headers["content-disposition"] == (
        'inline; filename="metrics.json"'
    )


def test_generate_unscored_fallback_returns_shader_and_records_truthful_summary(
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    async def fake_start(*args, **kwargs):
        return None

    async def fake_success(*args, **kwargs):
        calls["success"] = kwargs

    async def fake_generate(*args, **kwargs):
        return PngToShaderV1Result(
            project_id=kwargs["project_id"],
            run_id=kwargs["run_id"],
            glsl="precision mediump float; void main(){gl_FragColor=vec4(1.0);}",
            memory_status="ephemeral",
            quality_preset="balanced",
            iterations=0,
            stop_reason="completed_with_best_effort",
            best_candidate_id="candidate-0001",
            render_width=32,
            render_height=24,
            score=None,
            unscored_fallback=True,
            review=None,
            glsl_model_name="fake-author",
            vision_model_name="fake-vision",
            events=(
                {
                    "stage": "evaluate",
                    "event_type": "evaluation_failed",
                    "payload": {"error_type": "EvaluatorUnavailableError"},
                },
            ),
        )

    monkeypatch.setattr(
        shader_generation_service, "start_shader_generation_run", fake_start
    )
    monkeypatch.setattr(
        shader_generation_service, "record_shader_generation_success", fake_success
    )
    monkeypatch.setattr(
        shader_generation_service,
        "generate_procedural_shader_from_image",
        fake_generate,
    )
    app.state.db_pool = object()
    try:
        response = TestClient(app).post(
            "/api/shader/generate",
            files={"file": ("target.png", b"image", "image/png")},
            data={"generation_mode": "procedural_v1"},
        )
    finally:
        del app.state.db_pool

    assert response.status_code == 200
    payload = response.json()
    assert "gl_FragColor" in payload["glsl"]
    assert payload["final_render_url"].endswith("/artifacts/final-render")
    assert payload["score"] is None
    assert payload["metrics_url"] is None
    assert payload["unscored_fallback"] is True
    success = calls["success"]
    assert isinstance(success, dict)
    summary = success["result_summary"]
    assert isinstance(summary, dict)
    assert summary["score"] is None
    assert summary["metrics_available"] is False
    assert summary["metrics_url"] is None
    assert summary["unscored_fallback"] is True


def test_response_contract_failure_is_recorded_as_failed_before_success(
    monkeypatch,
    caplog,
) -> None:
    calls: dict[str, object] = {}

    async def fake_start(*args, **kwargs):
        calls["started"] = kwargs["run_id"]

    async def fake_failure(*args, **kwargs):
        calls["failure"] = kwargs

    async def fake_success(*args, **kwargs):
        calls["success"] = kwargs

    async def fake_generate(*args, **kwargs):
        invalid_score = score()
        invalid_score["roi_losses"] = [["highlight", 0.11]]
        return PngToShaderV1Result(
            project_id=kwargs["project_id"],
            run_id=kwargs["run_id"],
            glsl="precision mediump float; void main(){gl_FragColor=vec4(1.0);}",
            memory_status="ephemeral",
            quality_preset="balanced",
            iterations=0,
            stop_reason="quality_threshold_met",
            best_candidate_id="candidate-0001",
            render_width=32,
            render_height=24,
            score=invalid_score,
            unscored_fallback=False,
            review=None,
            glsl_model_name="fake-author",
            vision_model_name="fake-vision",
        )

    monkeypatch.setattr(
        shader_generation_service, "start_shader_generation_run", fake_start
    )
    monkeypatch.setattr(
        shader_generation_service, "record_shader_generation_failure", fake_failure
    )
    monkeypatch.setattr(
        shader_generation_service, "record_shader_generation_success", fake_success
    )
    monkeypatch.setattr(
        shader_generation_service,
        "generate_procedural_shader_from_image",
        fake_generate,
    )
    monkeypatch.setattr(
        shader_generation_service,
        "get_png_to_shader_v1_models",
        lambda: ("fake-author", "fake-vision"),
    )
    app.state.db_pool = object()
    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/shader/generate",
            files={"file": ("target.png", b"image", "image/png")},
            data={"generation_mode": "procedural_v1"},
        )
    finally:
        del app.state.db_pool

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["message"] == "生成已完成，但结果格式校验失败。"
    assert detail["code"] == "response_contract_failed"
    assert detail["stage"] == "backend_response"
    assert detail["retryable"] is False
    assert UUID(detail["run_id"])
    assert "success" not in calls
    failure = calls["failure"]
    assert isinstance(failure, dict)
    assert failure["diagnostics"]["failure_stage"] == "backend_response"
    assert failure["diagnostics"]["failure_event"] == "response_contract_failed"
    assert "shader.generate.response_contract_failed" in caplog.text


def test_generate_defaults_to_procedural_v1(monkeypatch) -> None:
    async def fake_generate(*args, **kwargs):
        assert kwargs["quality_preset"] == "balanced"
        return PngToShaderV1Result(
            project_id=kwargs["project_id"],
            run_id=kwargs["run_id"],
            glsl="precision mediump float; void main(){gl_FragColor=vec4(1.0);}",
            memory_status="ephemeral",
            quality_preset="balanced",
            iterations=0,
            stop_reason="quality_threshold_met",
            best_candidate_id="candidate-0001",
            render_width=32,
            render_height=24,
            score=score(),
            unscored_fallback=False,
            review=None,
            glsl_model_name="fake-author",
            vision_model_name="fake-vision",
        )

    monkeypatch.setattr(
        shader_generation_service,
        "generate_procedural_shader_from_image",
        fake_generate,
    )

    response = TestClient(app).post(
        "/api/shader/generate",
        files={"file": ("target.png", b"image", "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["generation_mode"] == "procedural_v1"
    assert response.json()["final_render_url"].endswith("/artifacts/final-render")


def test_generate_rejects_removed_legacy_mode() -> None:
    response = TestClient(app).post(
        "/api/shader/generate",
        files={"file": ("target.png", b"image", "image/png")},
        data={"generation_mode": "legacy"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "client_validation"


def test_removed_review_endpoint_returns_not_found() -> None:
    response = TestClient(app).post("/api/shader/review")

    assert response.status_code == 404


def test_generate_procedural_failure_is_safe_and_understandable(
    monkeypatch,
    caplog,
) -> None:
    async def fake_generate(*args, **kwargs):
        raise NoValidatedShaderError(
            {
                "final_result": {
                    "success": False,
                    "stop_reason": "compile_repair_exhausted",
                    "elapsed_seconds": 42.0,
                    "candidate_count": 1,
                    "model_call_count": 2,
                },
                "model_calls": (
                    {
                        "reasoning_content": "不得进入响应",
                        "role": "shader_author",
                        "parse_status": "valid",
                        "latency_ms": 10,
                    },
                ),
                "events": (
                    {
                        "stage": "render",
                        "event_type": "compile_failed",
                        "payload": {"error_type": "WebGLCompileError"},
                    },
                ),
                "logs": (),
            }
        )

    monkeypatch.setattr(
        shader_generation_service,
        "generate_procedural_shader_from_image",
        fake_generate,
    )

    response = TestClient(app).post(
        "/api/shader/generate",
        files={"file": ("target.png", b"image", "image/png")},
        data={"generation_mode": "procedural_v1"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail == {
        "message": "Shader 编译修复次数已耗尽，未生成可运行结果。",
        "code": "shader_validation_failed",
        "run_id": detail["run_id"],
        "stage": "render",
        "retryable": False,
        "stop_reason": "compile_repair_exhausted",
    }
    assert UUID(detail["run_id"])
    assert "不得进入响应" not in response.text
    assert "shader.generate.no_validated_result" in caplog.text
    assert "failure_stage=render" in caplog.text
    assert "failure_error_type=WebGLCompileError" in caplog.text
    assert "duration_ms=" in caplog.text
    assert "不得进入响应" not in caplog.text


@pytest.mark.parametrize(
    (
        "stop_reason",
        "stage",
        "event_type",
        "error_type",
        "status",
        "code",
        "retryable",
    ),
    (
        (
            "wall_time_exhausted",
            "author_compile_repair",
            "model_failed",
            "TimeoutError",
            504,
            "generation_timeout",
            True,
        ),
        (
            "completed_with_best_effort",
            "shader_author",
            "model_failed",
            "ModelTimeoutError",
            504,
            "model_timeout",
            True,
        ),
        (
            "renderer_unavailable",
            "render",
            "renderer_failed",
            "RendererUnavailableError",
            503,
            "renderer_unavailable",
            True,
        ),
        (
            "completed_with_best_effort",
            "shader_author",
            "model_failed",
            "LLMInvocationError",
            503,
            "model_unavailable",
            True,
        ),
        (
            "completed_with_best_effort",
            "shader_author",
            "model_failed",
            "LLMConfigurationError",
            500,
            "model_configuration_error",
            False,
        ),
        (
            "completed_with_best_effort",
            "shader_author",
            "model_failed",
            "LLMResponseError",
            502,
            "model_response_invalid",
            True,
        ),
    ),
)
def test_generate_procedural_failure_maps_typed_server_errors(
    monkeypatch,
    stop_reason: str,
    stage: str,
    event_type: str,
    error_type: str,
    status: int,
    code: str,
    retryable: bool,
) -> None:
    async def fake_generate(*args, **kwargs):
        raise NoValidatedShaderError(
            {
                "final_result": {
                    "success": False,
                    "stop_reason": stop_reason,
                    "elapsed_seconds": 12.5,
                    "candidate_count": 1,
                    "model_call_count": 1,
                },
                "model_calls": (
                    {
                        "role": "shader_author",
                        "parse_status": "invalid",
                        "latency_ms": 12500,
                        "provider_raw_response": "PRIVATE_PROVIDER_RESPONSE",
                    },
                ),
                "events": (
                    {
                        "stage": stage,
                        "event_type": event_type,
                        "payload": {"error_type": error_type},
                    },
                ),
                "logs": (),
            }
        )

    monkeypatch.setattr(
        shader_generation_service,
        "generate_procedural_shader_from_image",
        fake_generate,
    )

    response = TestClient(app).post(
        "/api/shader/generate",
        files={"file": ("target.png", b"image", "image/png")},
        data={"generation_mode": "procedural_v1"},
    )

    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert detail["stage"] == stage
    assert detail["retryable"] is retryable
    assert detail["stop_reason"] == stop_reason
    assert UUID(detail["run_id"])
    assert "PRIVATE_PROVIDER_RESPONSE" not in response.text


def test_generate_client_validation_uses_typed_safe_error() -> None:
    response = TestClient(app).post(
        "/api/shader/generate",
        files={"file": ("target.txt", b"PRIVATE_IMAGE_BYTES", "text/plain")},
        data={"generation_mode": "procedural_v1"},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "client_validation"
    assert detail["stage"] == "request_validation"
    assert detail["retryable"] is False
    assert detail["stop_reason"] == "client_validation"
    assert UUID(detail["run_id"])
    assert "PRIVATE_IMAGE_BYTES" not in response.text


def test_generate_fastapi_validation_uses_typed_safe_error() -> None:
    response = TestClient(app).post(
        "/api/shader/generate",
        data={
            "generation_mode": "unsupported-mode",
            "instruction": "PRIVATE_USER_TEXT",
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "client_validation"
    assert detail["stage"] == "request_validation"
    assert detail["retryable"] is False
    assert UUID(detail["run_id"])
    assert "PRIVATE_USER_TEXT" not in response.text


def test_generation_run_start_failure_maps_to_typed_persistence_error(
    monkeypatch,
    caplog,
) -> None:
    async def fake_start(*args, **kwargs):
        raise RuntimeError("PRIVATE_DATABASE_DETAIL")

    async def fail_generate(*args, **kwargs):
        raise AssertionError("run 总账失败后不应调用生成服务")

    monkeypatch.setattr(
        shader_generation_service, "start_shader_generation_run", fake_start
    )
    monkeypatch.setattr(
        shader_generation_service,
        "generate_procedural_shader_from_image",
        fail_generate,
    )
    app.state.db_pool = object()
    try:
        response = TestClient(app).post(
            "/api/shader/generate",
            files={"file": ("target.png", b"image", "image/png")},
            data={"generation_mode": "procedural_v1"},
        )
    finally:
        del app.state.db_pool

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "persistence_unavailable"
    assert detail["stage"] == "persistence"
    assert detail["retryable"] is True
    assert detail["stop_reason"] == "persistence_unavailable"
    assert UUID(detail["run_id"])
    assert "persistence_stage=create_generation_run" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "PRIVATE_DATABASE_DETAIL" not in caplog.text


def test_unexpected_procedural_error_maps_to_internal_pipeline_error(
    monkeypatch,
    caplog,
) -> None:
    async def broken_pipeline(*args, **kwargs):
        raise AssertionError("PRIVATE_INTERNAL_DETAIL")

    monkeypatch.setattr(
        shader_generation_service,
        "generate_procedural_shader_from_image",
        broken_pipeline,
    )

    response = TestClient(app).post(
        "/api/shader/generate",
        files={"file": ("target.png", b"image", "image/png")},
        data={"generation_mode": "procedural_v1"},
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "internal_pipeline_error"
    assert detail["stage"] == "pipeline"
    assert detail["retryable"] is False
    assert detail["stop_reason"] == "internal_pipeline_error"
    assert UUID(detail["run_id"])
    assert "error_type=AssertionError" in caplog.text
    assert "PRIVATE_INTERNAL_DETAIL" not in response.text
    assert "PRIVATE_INTERNAL_DETAIL" not in caplog.text


def test_empty_procedural_result_is_recorded_and_uses_typed_internal_error(
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    async def fake_start(*args, **kwargs):
        calls["start"] = kwargs

    async def fake_failure(*args, **kwargs):
        calls["failure"] = kwargs

    async def empty_pipeline(*args, **kwargs):
        return None

    monkeypatch.setattr(
        shader_generation_service,
        "start_shader_generation_run",
        fake_start,
    )
    monkeypatch.setattr(
        shader_generation_service,
        "record_shader_generation_failure",
        fake_failure,
    )
    monkeypatch.setattr(
        shader_generation_service,
        "generate_procedural_shader_from_image",
        empty_pipeline,
    )
    app.state.db_pool = object()
    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/shader/generate",
            files={"file": ("target.png", b"image", "image/png")},
            data={"generation_mode": "procedural_v1"},
        )
    finally:
        del app.state.db_pool

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "internal_pipeline_error"
    assert detail["stage"] == "pipeline"
    assert detail["retryable"] is False
    failure = calls["failure"]
    assert isinstance(failure, dict)
    assert failure["stop_reason"] == "internal_pipeline_error"
    diagnostics = failure["diagnostics"]
    assert isinstance(diagnostics, dict)
    assert diagnostics["failure_error_type"] == "RuntimeError"


def test_failure_persistence_error_does_not_mask_generation_timeout(
    monkeypatch,
    caplog,
) -> None:
    async def fake_start(*args, **kwargs):
        return None

    async def fake_record_failure(*args, **kwargs):
        raise RuntimeError("PRIVATE_DATABASE_DETAIL")

    async def fake_generate(*args, **kwargs):
        raise NoValidatedShaderError(
            {
                "final_result": {
                    "success": False,
                    "stop_reason": "wall_time_exhausted",
                    "elapsed_seconds": 300.0,
                },
                "events": (
                    {
                        "stage": "author_compile_repair",
                        "event_type": "model_failed",
                        "payload": {"error_type": "TimeoutError"},
                    },
                ),
            }
        )

    monkeypatch.setattr(
        shader_generation_service, "start_shader_generation_run", fake_start
    )
    monkeypatch.setattr(
        shader_generation_service,
        "record_shader_generation_failure",
        fake_record_failure,
    )
    monkeypatch.setattr(
        shader_generation_service,
        "generate_procedural_shader_from_image",
        fake_generate,
    )
    app.state.db_pool = object()
    try:
        response = TestClient(app).post(
            "/api/shader/generate",
            files={"file": ("target.png", b"image", "image/png")},
            data={"generation_mode": "procedural_v1"},
        )
    finally:
        del app.state.db_pool

    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "generation_timeout"
    assert "shader.generate.failure_persistence_failed" in caplog.text
    assert "persistence_stage=outcome_transaction" in caplog.text
    assert "PRIVATE_DATABASE_DETAIL" not in caplog.text


def test_procedural_success_persistence_error_does_not_mask_shader(
    monkeypatch,
    caplog,
) -> None:
    async def fake_start(*args, **kwargs):
        return None

    async def fake_record_success(*args, **kwargs):
        raise RuntimeError("PRIVATE_DATABASE_DETAIL")

    async def fake_generate(*args, **kwargs):
        return PngToShaderV1Result(
            project_id=kwargs["project_id"],
            run_id=kwargs["run_id"],
            glsl="precision mediump float; void main(){gl_FragColor=vec4(1.0);}",
            memory_status="ephemeral",
            quality_preset="balanced",
            iterations=0,
            stop_reason="quality_threshold_met",
            best_candidate_id="candidate-0001",
            render_width=32,
            render_height=24,
            score=score(),
            unscored_fallback=False,
            review=None,
            glsl_model_name="fake-author",
            vision_model_name="fake-vision",
        )

    monkeypatch.setattr(
        shader_generation_service, "start_shader_generation_run", fake_start
    )
    monkeypatch.setattr(
        shader_generation_service,
        "record_shader_generation_success",
        fake_record_success,
    )
    monkeypatch.setattr(
        shader_generation_service,
        "generate_procedural_shader_from_image",
        fake_generate,
    )
    app.state.db_pool = object()
    try:
        response = TestClient(app).post(
            "/api/shader/generate",
            files={"file": ("target.png", b"image", "image/png")},
            data={"generation_mode": "procedural_v1"},
        )
    finally:
        del app.state.db_pool

    assert response.status_code == 200
    assert "gl_FragColor" in response.json()["glsl"]
    assert "shader.generate.success_persistence_failed" in caplog.text
    assert "persistence_stage=outcome_commit" in caplog.text
    assert "PRIVATE_DATABASE_DETAIL" not in caplog.text


def test_artifact_endpoint_uses_fixed_whitelist(monkeypatch) -> None:
    run_id = uuid4()

    def fake_read(requested_run_id: str, artifact_name: str, **kwargs):
        assert requested_run_id == str(run_id)
        if artifact_name != "final-render":
            raise PublicArtifactNotFoundError("not public")
        return PublicArtifact(b"png", "image/png", "final-render.png")

    monkeypatch.setattr(shader_route, "read_shader_run_artifact", fake_read)
    client = TestClient(app)

    response = client.get(f"/api/shader/runs/{run_id}/artifacts/final-render")
    unknown = client.get(f"/api/shader/runs/{run_id}/artifacts/shader-source")

    assert response.status_code == 200
    assert response.content == b"png"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert unknown.status_code == 404
