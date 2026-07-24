"""scene_mvp 运行进度读取：registry 语义、路由契约与 POST 端到端进度流."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from agent.app.nodes.png_to_shader_min import MinRendererRegistry
from agent.app.services.png_to_shader_min import PngToShaderMinService
from backend.app.main import app
from backend.app.services.run_progress import (
    RUN_PROGRESS_TTL_SECONDS,
    RunProgressRegistry,
)
from shaderforge.store import LocalArtifactStore

CLIENT_RUN_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
FAKE_PNG = b"\x89PNG\r\n\x1a\nfake-progress-frame"


class _FakePrepared:
    def __init__(self) -> None:
        self.width = 0
        self.height = 0

    async def render_uniforms(self, _values, *, capture_png=False):
        rgb = Image.new("RGB", (self.width or 1, self.height or 1), "white").tobytes()
        image_bytes = None
        if capture_png:
            buffer = BytesIO()
            Image.frombytes("RGB", (self.width or 1, self.height or 1), rgb).save(
                buffer, format="PNG"
            )
            image_bytes = buffer.getvalue()
        return SimpleNamespace(
            success=True,
            rgb_bytes=rgb,
            image_bytes=image_bytes,
            draw_error=None,
        )

    async def close(self) -> None:
        return None


class _FakeRenderer:
    def __init__(self) -> None:
        self.prepared = _FakePrepared()

    async def prepare(self, _source, width, height, _uniform_schema):
        self.prepared.width = width
        self.prepared.height = height
        return self.prepared

    async def close(self) -> None:
        return None


_TRACE_1 = ({"phase": "initialize", "status": "completed", "message": "已登记运行。"},)
_TRACE_2 = (
    *_TRACE_1,
    {
        "phase": "render_and_evaluate",
        "status": "completed",
        "message": "accepted，候选 loss=0.100000，best loss=0.100000",
    },
)
_TRACE_3 = (
    *_TRACE_2,
    {"phase": "finalize", "status": "completed", "message": "已固化 final。"},
)


def _final_result(project_id: str, run_id: str) -> dict:
    return {
        "project_id": project_id,
        "run_id": run_id,
        "glsl": "void main(){}",
        "render_width": 8,
        "render_height": 8,
        "status": "completed",
        "stop_reason": "target_loss_reached",
        "template_version": "png_to_shader_min_template_v3",
        "quality_preset": "fast",
        "current_best_mae": 0.1,
        "current_best_loss": 0.1,
        "metric_breakdown": {"metric_version": "min_scene_composite_v3"},
        "render_count": 1,
        "render_budget": 48,
        "llm_call_count": 0,
        "llm_budget": 2,
        "refine_budget": 1,
        "run_classification": "independent_experiment",
        "experiment_id": "scene-mvp-agent-optimization-20260723",
        "config_fingerprint": "a" * 64,
        "report_schema_version": "scene_mvp_run_report_v1",
        "patch_candidate_draw_budget": 12,
        "patch_evidence": (),
        "renderer_path": "prepared_uniforms_v1",
        "target_mae": 0.08,
        "target_loss": 0.08,
        "target_reached": True,
        "prepare_duration_ms": 1.0,
        "uniform_render_count": 1,
        "uniform_render_p95_ms": 1.0,
        "scene": {"background": "white"},
        "shader_graph_shadow": {
            "status": "rendered",
            "renderer_path": "compiled_graph_program_cache_v1",
            "dsl_schema_version": "shader_graph_v1",
            "compiler_version": "shader_dsl_compiler_v1",
            "document_sha256": "a" * 64,
            "topology_sha256": "b" * 64,
            "layer_count": 1,
            "primitive_count": 1,
            "compile_count": 1,
            "cache_hit_count": 0,
            "cache_size": 0,
            "render_duration_ms": 1.0,
            "unsupported_features": [],
            "error_code": None,
            "resource_summary": {"layer_count": 1},
            "shader_graph": {
                "schema_version": "shader_graph_v1",
                "layers": [{"id": "legacy_body"}],
            },
        },
        "trace": _TRACE_3,
    }


class _ProgressMinGraph:
    """按 updates 流式产出三个节点更新的最小 fake graph."""

    async def astream(self, state, _config, *, stream_mode="updates"):
        yield {
            "initialize_run": {
                "phase": "initialize",
                "trace": _TRACE_1,
            }
        }
        yield {
            "render_and_evaluate": {
                "phase": "render",
                "render_count": 1,
                "current_best_mae": 0.1,
                "current_best_loss": 0.1,
                "current_render": FAKE_PNG,
                "trace": _TRACE_2,
            }
        }
        yield {
            "finalize": {
                "status": "completed",
                "stop_reason": "target_loss_reached",
                "trace": _TRACE_3,
                "final_result": _final_result(state["project_id"], state["run_id"]),
            }
        }


def test_registry_begin_publish_read_finish_cycle() -> None:
    registry = RunProgressRegistry()
    registry.begin(
        "run-1",
        project_id="project-1",
        generation_mode="scene_mvp",
        quality_preset="fast",
    )
    with pytest.raises(ValueError, match="进行中"):
        registry.begin(
            "run-1",
            project_id="project-1",
            generation_mode="scene_mvp",
            quality_preset="fast",
        )

    registry.publish("run-1", {"node": "initialize_run", "status": "completed"})
    registry.publish(
        "run-1",
        {
            "node": "render_and_evaluate",
            "status": "completed",
            "counters": {"render_count": 2},
            "best": {"mae": 0.1, "loss": 0.1},
        },
    )
    registry.publish_render("run-1", FAKE_PNG)

    first = registry.read("run-1", after=0)
    assert first["status"] == "running"
    assert first["latest_seq"] == 2
    assert [event["seq"] for event in first["events"]] == [1, 2]
    assert first["snapshot"]["counters"] == {"render_count": 2}
    assert first["snapshot"]["render_seq"] == 1

    delta = registry.read("run-1", after=1)
    assert [event["node"] for event in delta["events"]] == ["render_and_evaluate"]

    assert registry.diagnostic_snapshot("run-1") == {
        "latest_seq": 2,
        "current_node": "render_and_evaluate",
        "counters": {"render_count": 2},
        "best": {"mae": 0.1, "loss": 0.1},
        "budgets": {},
    }
    assert registry.diagnostic_snapshot("unknown-run") == {}

    png, render_seq = registry.read_render("run-1")
    assert png == FAKE_PNG
    assert render_seq == 1

    registry.finish("run-1", "succeeded", "target_loss_reached")
    finished = registry.read("run-1", after=0)
    assert finished["status"] == "succeeded"

    # 已结束的 run_id 允许复用；进行中的冲突已被拒绝。
    registry.begin(
        "run-1",
        project_id="project-1",
        generation_mode="scene_mvp",
        quality_preset="fast",
    )
    assert registry.read("run-1")["status"] == "running"


def test_registry_sweeps_stale_entries() -> None:
    registry = RunProgressRegistry()
    registry.begin(
        "run-stale",
        project_id="project-1",
        generation_mode="scene_mvp",
        quality_preset="fast",
    )
    registry._runs["run-stale"].last_touch -= RUN_PROGRESS_TTL_SECONDS + 1
    assert registry.read("run-stale")["status"] == "pending"
    assert registry.read("run-stale")["events"] == []


def test_progress_routes_return_pending_and_404_for_unknown_run() -> None:
    previous = getattr(app.state, "run_progress", None)
    app.state.run_progress = RunProgressRegistry()
    try:
        client = TestClient(app)
        run_id = uuid4()
        progress = client.get(f"/api/shader/runs/{run_id}/progress")
        render = client.get(f"/api/shader/runs/{run_id}/progress/render")
    finally:
        app.state.run_progress = previous

    assert progress.status_code == 200
    payload = progress.json()
    assert payload["run_id"] == str(run_id)
    assert payload["status"] == "pending"
    assert payload["events"] == []
    assert payload["latest_seq"] == 0
    assert render.status_code == 404


def test_scene_mvp_post_with_client_run_id_publishes_progress(tmp_path: Path) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    registry = MinRendererRegistry(_FakeRenderer)  # type: ignore[arg-type]
    min_service = PngToShaderMinService(
        _ProgressMinGraph(),
        artifacts,
        registry,
        llm_budget=2,
        refine_budget=1,
    )
    previous_service = getattr(app.state, "png_to_shader_min_service", None)
    previous_progress = getattr(app.state, "run_progress", None)
    app.state.png_to_shader_min_service = min_service
    app.state.run_progress = RunProgressRegistry()
    try:
        client = TestClient(app)
        generated = client.post(
            "/api/shader/generate",
            files={"file": ("target.png", b"target", "image/png")},
            data={
                "quality_preset": "fast",
                "run_id": CLIENT_RUN_ID,
            },
        )
        progress = client.get(f"/api/shader/runs/{CLIENT_RUN_ID}/progress")
        delta = client.get(f"/api/shader/runs/{CLIENT_RUN_ID}/progress?after=2")
        render = client.get(f"/api/shader/runs/{CLIENT_RUN_ID}/progress/render")
    finally:
        app.state.png_to_shader_min_service = previous_service
        app.state.run_progress = previous_progress

    assert generated.status_code == 200
    assert generated.json()["run_id"] == CLIENT_RUN_ID
    assert generated.json()["generation_mode"] == "scene_mvp"
    graph_shadow = generated.json()["min_pipeline"]["shader_graph_shadow"]
    assert graph_shadow["status"] == "rendered"
    assert graph_shadow["shader_graph"]["layers"][0]["id"] == "legacy_body"

    payload = progress.json()
    assert payload["status"] == "succeeded"
    assert payload["latest_seq"] == 3
    assert [event["node"] for event in payload["events"]] == [
        "initialize_run",
        "render_and_evaluate",
        "finalize",
    ]
    render_event = payload["events"][1]
    assert render_event["counters"]["render_count"] == 1
    assert render_event["best"]["mae"] == 0.1
    # 白名单：事件不得携带大对象字段。
    for event in payload["events"]:
        assert "current_render" not in event
        assert "scene" not in event
        assert "final_result" not in event
    assert payload["snapshot"]["render_seq"] == 1
    assert payload["snapshot"]["counters"]["render_count"] == 1

    delta_payload = delta.json()
    assert [event["node"] for event in delta_payload["events"]] == ["finalize"]

    assert render.status_code == 200
    assert render.content == FAKE_PNG
