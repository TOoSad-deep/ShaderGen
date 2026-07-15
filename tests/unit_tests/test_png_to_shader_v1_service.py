from __future__ import annotations

import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from agent.app.services.png_to_shader_v1 import (
    NoValidatedShaderError,
    PngToShaderV1Service,
    PublicArtifactNotFoundError,
    generate_png_to_shader_v1,
)
from shaderforge.store import LocalArtifactStore


class FakeGraph:
    def __init__(self, output: dict) -> None:
        self.output = output
        self.calls = []

    async def ainvoke(self, state, config):
        self.calls.append((state, config))
        return self.output


class FailingGraph:
    async def ainvoke(self, state, config):
        raise RuntimeError("graph invariant failed")


class FakeRendererRegistry:
    def __init__(self, *, fail_on_close: bool = False) -> None:
        self.fail_on_close = fail_on_close
        self.closed_keys: list[tuple[str, str]] = []

    async def close(self, key: tuple[str, str]) -> None:
        self.closed_keys.append(key)
        if self.fail_on_close:
            raise RuntimeError("private cleanup detail")


class RecordingCheckpointer:
    def __init__(self) -> None:
        self.deleted_threads: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted_threads.append(thread_id)


def make_service(
    tmp_path: Path, output: dict
) -> tuple[PngToShaderV1Service, FakeGraph]:
    graph = FakeGraph(output)
    service = PngToShaderV1Service(
        graph,
        InMemorySaver(),
        InMemoryStore(),
        LocalArtifactStore(tmp_path / "artifacts"),
        "ephemeral",
    )
    return service, graph


@pytest.mark.anyio
async def test_service_closes_run_renderer_after_unexpected_graph_error(
    tmp_path: Path,
) -> None:
    registry = FakeRendererRegistry()
    service = PngToShaderV1Service(
        FailingGraph(),
        InMemorySaver(),
        InMemoryStore(),
        LocalArtifactStore(tmp_path / "artifacts"),
        "ephemeral",
        registry,
    )

    with pytest.raises(RuntimeError, match="graph invariant failed"):
        await service.invoke(
            "project-1",
            {"project_id": "project-1", "run_id": "run-1"},
        )

    assert registry.closed_keys == [("project-1", "run-1")]


@pytest.mark.anyio
async def test_service_rejects_project_id_mismatch_before_graph_invocation(
    tmp_path: Path,
) -> None:
    registry = FakeRendererRegistry()
    graph = FakeGraph({})
    service = PngToShaderV1Service(
        graph,
        InMemorySaver(),
        InMemoryStore(),
        LocalArtifactStore(tmp_path / "artifacts"),
        "ephemeral",
        registry,
    )

    with pytest.raises(ValueError, match="project_id"):
        await service.invoke(
            "project-1",
            {"project_id": "project-2", "run_id": "run-1"},
        )

    assert graph.calls == []
    assert registry.closed_keys == []


@pytest.mark.anyio
async def test_service_cleanup_failure_does_not_mask_graph_result(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = FakeRendererRegistry(fail_on_close=True)
    graph = FakeGraph({"final_result": {"success": True}})
    service = PngToShaderV1Service(
        graph,
        InMemorySaver(),
        InMemoryStore(),
        LocalArtifactStore(tmp_path / "artifacts"),
        "ephemeral",
        registry,
    )

    result = await service.invoke(
        "project-1",
        {"project_id": "project-1", "run_id": "run-1"},
    )

    assert result == {"final_result": {"success": True}}
    assert registry.closed_keys == [("project-1", "run-1")]
    assert "error_type=RuntimeError" in caplog.text
    assert "private cleanup detail" not in caplog.text


@pytest.mark.anyio
async def test_service_maps_success_and_uses_isolated_checkpoint_thread(
    tmp_path: Path,
) -> None:
    score = {
        "metric_version": "basic_oracle_v1",
        "total_loss": 0.2,
        "global_rmse": 0.1,
    }
    output = {
        "memory_status": "durable",
        "final_result": {
            "success": True,
            "candidate_id": "candidate-0002",
            "glsl": "precision mediump float; void main(){gl_FragColor=vec4(1.0);}",
            "score_breakdown": score,
            "stop_reason": "stagnation",
            "visual_refinement_count": 2,
            "render_width": 505,
            "render_height": 527,
        },
        "visual_review": {
            "candidate_id": "candidate-0002",
            "overall_assessment": "轮廓已接近。",
            "recommended_changes": [],
        },
        "model_calls": ({"model_ref": "fake"},),
        "events": ({"stage": "selection", "event_type": "current_best_updated"},),
        "logs": (),
    }
    service, graph = make_service(tmp_path, output)

    result = await generate_png_to_shader_v1(
        b"png",
        "image/png",
        project_id="project-1",
        run_id="run-1",
        quality_preset="high",
        instruction="保留白底",
        service=service,
    )

    assert result.best_candidate_id == "candidate-0002"
    assert result.iterations == 2
    assert (result.render_width, result.render_height) == (505, 527)
    assert result.score == score
    assert result.unscored_fallback is False
    assert result.review is not None
    assert result.review["candidate_id"] == "candidate-0002"
    state, config = graph.calls[0]
    assert state["quality_preset"] == "high"
    assert state["instruction"] == "保留白底"
    assert config["configurable"]["thread_id"] == "png-to-shader-v1:project-1"


@pytest.mark.anyio
async def test_clear_memory_removes_v1_and_historical_checkpoint_threads(
    tmp_path: Path,
) -> None:
    checkpointer = RecordingCheckpointer()
    service = PngToShaderV1Service(
        FakeGraph({}),
        checkpointer,
        InMemoryStore(),
        LocalArtifactStore(tmp_path / "artifacts"),
        "ephemeral",
    )
    project_id = "11111111-1111-4111-8111-111111111111"

    result = await service.clear_memory(project_id)

    assert checkpointer.deleted_threads == [
        f"png-to-shader-v1:{project_id}",
        project_id,
    ]
    assert result.deleted_memories == 0


@pytest.mark.anyio
async def test_service_does_not_attach_stale_review_to_different_final_candidate(
    tmp_path: Path,
) -> None:
    output = {
        "memory_status": "ephemeral",
        "final_result": {
            "success": True,
            "candidate_id": "candidate-0003",
            "glsl": "precision mediump float; void main(){gl_FragColor=vec4(1.0);}",
            "score_breakdown": None,
            "unscored_fallback": True,
            "stop_reason": "completed_with_best_effort",
            "visual_refinement_count": 1,
            "render_width": 64,
            "render_height": 64,
        },
        "visual_review": {
            "candidate_id": "candidate-0002",
            "overall_assessment": "针对旧候选的评审",
        },
    }
    service, _graph = make_service(tmp_path, output)

    result = await generate_png_to_shader_v1(
        b"png",
        "image/png",
        project_id="project-1",
        run_id="run-stale-review",
        quality_preset="balanced",
        instruction="",
        service=service,
    )

    assert result.best_candidate_id == "candidate-0003"
    assert result.score is None
    assert result.unscored_fallback is True
    assert result.review is None


@pytest.mark.anyio
async def test_service_normalizes_legacy_pair_list_score_maps(tmp_path: Path) -> None:
    output = {
        "memory_status": "ephemeral",
        "final_result": {
            "success": True,
            "candidate_id": "candidate-0001",
            "glsl": "precision mediump float; void main(){gl_FragColor=vec4(1.0);}",
            "score_breakdown": {
                "metric_version": "basic_oracle_v1",
                "total_loss": 0.1,
                "roi_losses": [["subject", 0.2]],
                "protected_region_losses": [["center", 0.08]],
                "effective_weights": [["global_rmse", 0.35]],
            },
            "stop_reason": "quality_threshold_met",
            "visual_refinement_count": 0,
            "render_width": 32,
            "render_height": 24,
        },
    }
    service, _graph = make_service(tmp_path, output)

    result = await generate_png_to_shader_v1(
        b"png",
        "image/png",
        project_id="project-1",
        run_id="run-1",
        quality_preset="balanced",
        instruction="",
        service=service,
    )

    assert result.score["roi_losses"] == {"subject": 0.2}
    assert result.score["protected_region_losses"] == {"center": 0.08}
    assert result.score["effective_weights"] == {"global_rmse": 0.35}


@pytest.mark.anyio
async def test_service_reports_terminal_run_without_validated_candidate(
    tmp_path: Path,
) -> None:
    output = {
        "final_result": {
            "success": False,
            "stop_reason": "compile_repair_exhausted",
            "elapsed_seconds": 12.3456,
            "candidate_count": 1,
            "model_call_count": 3,
            "compile_repair_count": 1,
            "visual_refinement_count": 0,
        },
        "events": (
            {
                "stage": "render",
                "event_type": "compile_failed",
                "payload": {
                    "failure_stage": "static_validation",
                    "error_type": "WebGLCompileError",
                    "violation_codes": ["reversed_smoothstep_edges"],
                    "violations": [
                        {
                            "code": "reversed_smoothstep_edges",
                            "severity": "error",
                            "line": 31,
                        }
                    ],
                },
            },
        ),
        "model_calls": (
            {
                "model_ref": "fake",
                "role": "shader_author",
                "parse_status": "valid",
                "latency_ms": 120,
                "error_codes": [],
            },
        ),
        "logs": (),
    }
    service, _graph = make_service(tmp_path, output)

    with pytest.raises(NoValidatedShaderError) as raised:
        await generate_png_to_shader_v1(
            b"png",
            "image/png",
            project_id="project-1",
            run_id="run-1",
            quality_preset="fast",
            instruction="",
            service=service,
        )

    assert raised.value.stop_reason == "compile_repair_exhausted"
    assert raised.value.events[0]["event_type"] == "compile_failed"
    assert raised.value.diagnostics == {
        "stop_reason": "compile_repair_exhausted",
        "elapsed_seconds": 12.346,
        "candidate_count": 1,
        "model_call_count": 3,
        "recorded_model_calls": 1,
        "model_latency_ms": 120,
        "compile_repair_count": 1,
        "visual_refinement_count": 0,
        "failure_stage": "static_validation",
        "failure_event": "compile_failed",
        "failure_error_type": "WebGLCompileError",
        "cleanup_failure_error_type": None,
        "last_pipeline_stage": "render",
        "last_pipeline_event": "compile_failed",
        "last_model_role": "shader_author",
        "last_model_parse_status": "valid",
        "structured_output_error_codes": [],
        "shader_validation_violation_codes": ["reversed_smoothstep_edges"],
        "shader_validation_violations": [
            {
                "code": "reversed_smoothstep_edges",
                "severity": "error",
                "line": 31,
            }
        ],
        "shader_failure_stage": "static_validation",
        "validation_error_codes": ["reversed_smoothstep_edges"],
        "validation_error_codes_deprecated": True,
    }


@pytest.mark.anyio
async def test_service_separates_parser_and_shader_failures_and_keeps_timeout_context(
    tmp_path: Path,
) -> None:
    output = {
        "final_result": {
            "success": False,
            "stop_reason": "wall_time_exhausted",
            "elapsed_seconds": 300.0,
            "candidate_count": 1,
            "model_call_count": 4,
            "compile_repair_count": 1,
            "visual_refinement_count": 0,
        },
        "events": (
            {
                "stage": "render",
                "event_type": "compile_failed",
                "payload": {
                    "failure_stage": "static_validation",
                    "violation_codes": ["reversed_smoothstep_edges"],
                    "violations": [
                        {
                            "code": "reversed_smoothstep_edges",
                            "severity": "error",
                            "line": 44,
                        }
                    ],
                },
            },
            {
                "stage": "author_compile_repair",
                "event_type": "model_failed",
                "payload": {
                    "error_type": "TimeoutError",
                    "timeout_source": "wall_deadline_reserve",
                    "timeout_seconds": 60.0,
                    "stage_elapsed_seconds": 60.01,
                    "remaining_wall_seconds": 30.0,
                    "reserved_wall_seconds": 30.0,
                    "attempt_count_incomplete": True,
                },
            },
            {
                "stage": "finalize",
                "event_type": "renderer_close_failed",
                "payload": {"error_type": "CleanupTimeoutError"},
            },
        ),
        "model_calls": (
            {
                "model_ref": "fake",
                "role": "shader_author",
                "parse_status": "invalid",
                "latency_ms": 120,
                "error_codes": ["binding_mismatch"],
            },
        ),
        "logs": (),
    }
    service, _graph = make_service(tmp_path, output)

    with pytest.raises(NoValidatedShaderError) as raised:
        await generate_png_to_shader_v1(
            b"png",
            "image/png",
            project_id="project-1",
            run_id="run-timeout",
            quality_preset="balanced",
            instruction="",
            service=service,
        )

    diagnostics = raised.value.diagnostics
    assert diagnostics["failure_stage"] == "author_compile_repair"
    assert diagnostics["failure_error_type"] == "TimeoutError"
    assert diagnostics["cleanup_failure_error_type"] == "CleanupTimeoutError"
    assert diagnostics["shader_failure_stage"] == "static_validation"
    assert diagnostics["structured_output_error_codes"] == ["binding_mismatch"]
    assert diagnostics["shader_validation_violation_codes"] == [
        "reversed_smoothstep_edges"
    ]
    assert diagnostics["validation_error_codes"] == [
        "binding_mismatch",
        "reversed_smoothstep_edges",
    ]
    assert diagnostics["timeout_source"] == "wall_deadline_reserve"
    assert diagnostics["remaining_wall_seconds"] == 30.0
    assert diagnostics["attempt_count_incomplete"] is True


def test_service_reads_only_public_artifact_whitelist(tmp_path: Path) -> None:
    service, _graph = make_service(tmp_path, {})
    run = service.artifact_store.register_run("project-1", "run-1")
    run.write_bytes("final/render.png", b"png", content_type="image/png")
    run.write_json("final/metrics.json", {"total_loss": 0.2})
    run.write_json("final/manifest.json", {"run_id": "run-1"})
    run.write_text("candidates/candidate-0001/shader.frag", "secret")

    render = service.read_public_artifact("run-1", "final-render")
    metrics = service.read_public_artifact("run-1", "metrics")

    assert render.data == b"png"
    assert render.content_type == "image/png"
    assert json.loads(metrics.data) == {"total_loss": 0.2}
    with pytest.raises(PublicArtifactNotFoundError, match="白名单"):
        service.read_public_artifact(
            "run-1", "../candidates/candidate-0001/shader.frag"
        )
