from io import BytesIO
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from PIL import Image, ImageDraw

from agent.app.contracts.llm import LLMResponse
from agent.app.contracts.png_to_shader_min import apply_min_author_patch
from agent.app.graphs.png_to_shader_min_graph import build_png_to_shader_min_graph
from agent.app.graphs.png_to_shader_min_routing import (
    route_after_base,
    route_after_feature,
    route_after_render,
)
from agent.app.nodes.png_to_shader_min import MinRendererRegistry, make_min_nodes
from agent.app.parsers.png_to_shader_min import (
    MinAuthorParseError,
    parse_min_author_patch,
)
from shaderforge.generation import bake_min_uniforms, materialize_min_shader
from shaderforge.perception import perceive_min_target
from shaderforge.scene import AddFeaturePatch, Feature, MinScene, apply_scene_patch
from shaderforge.store import LocalArtifactStore
from shaderforge.validation import validate_shader


def _pink_orb_png() -> bytes:
    image = Image.new("RGB", (96, 96), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((14, 14, 82, 82), fill=(245, 80, 130))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeRenderer:
    instances: list["_FakeRenderer"] = []

    def __init__(self) -> None:
        self.closed = False
        self.prepare_calls = 0
        self.prepared = _FakePrepared()
        self.instances.append(self)

    async def prepare(self, _source, width, height, _uniform_schema):
        self.prepare_calls += 1
        self.prepared.width = width
        self.prepared.height = height
        return self.prepared

    async def close(self) -> None:
        self.closed = True


class _FakePrepared:
    def __init__(self) -> None:
        self.closed = False
        self.width = 0
        self.height = 0
        self.prepare_duration_ms = 3.5
        self.render_durations_ms: tuple[float, ...] = ()

    @property
    def render_count(self) -> int:
        return len(self.render_durations_ms)

    async def render_uniforms(self, _values, *, capture_png=False):
        self.render_durations_ms = (*self.render_durations_ms, 1.25)
        rgb = Image.new("RGB", (self.width, self.height), "white").tobytes()
        image_bytes = None
        if capture_png:
            image = Image.frombytes("RGB", (self.width, self.height), rgb)
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            image_bytes = buffer.getvalue()
        return SimpleNamespace(
            success=True,
            rgb_bytes=rgb,
            image_bytes=image_bytes,
            draw_error=None,
        )

    async def close(self) -> None:
        self.closed = True


class _FakeGateway:
    def __init__(self, *responses: str | Exception) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def ainvoke(self, _messages, _options):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return LLMResponse(
            message=AIMessage(content=response),
            text=response,
            reasoning_content=None,
            model_ref="fake:min-author",
            requested_model_ref="fake:min-author",
            latency_ms=1,
        )


def _author_state(*, llm_budget: int = 2) -> dict[str, object]:
    image = _pink_orb_png()
    perception = perceive_min_target(image)
    scene = perception.fallback_scene.model_dump(mode="json")
    return {
        "project_id": "author-project",
        "run_id": "author-run",
        "image": image,
        "content_type": "image/png",
        "instruction": "保留粉色主体",
        "perception": perception.summary,
        "target_rgb": perception.target_rgb,
        "scene": scene,
        "llm_budget": llm_budget,
        "llm_call_count": 0,
        "refine_count": 0,
        "refine_budget": 3,
        "trace": (),
        "current_best": {
            "scene": scene,
            "mae": 0.2,
            "glsl": "best-glsl",
            "render": image,
        },
    }


def test_scene_template_and_patch_are_strict_and_valid_webgl1() -> None:
    perception = perceive_min_target(_pink_orb_png())
    scene = perception.fallback_scene
    patched = apply_scene_patch(
        scene,
        AddFeaturePatch(
            op="add_feature",
            feature=Feature(id="edge", type="edge_line", intensity=0.1),
        ),
    )

    assert isinstance(MinScene.model_validate(patched.model_dump()), MinScene)
    materialized = materialize_min_shader(patched)
    source = bake_min_uniforms(materialized)
    assert validate_shader(source).valid
    assert "texture2D(" not in source
    assert "iResolution.xy" in materialized.shadertoy_source
    assert "uniform vec3 u_bg" not in materialized.shadertoy_source


def test_min_author_patch_parser_requires_one_whitelisted_typed_patch() -> None:
    patch = parse_min_author_patch(
        '{"operation":"replace","path":"/object/color_field/model","value":"solid"}'
    )
    scene = perceive_min_target(_pink_orb_png()).fallback_scene

    assert apply_min_author_patch(scene, patch).object.color_field.model == "solid"
    with pytest.raises(MinAuthorParseError):
        parse_min_author_patch(
            '[{"operation":"replace","path":"/object/color_field/model","value":"solid"}]'
        )
    with pytest.raises(MinAuthorParseError):
        parse_min_author_patch(
            '{"operation":"replace","path":"/canvas/width","value":32}'
        )
    with pytest.raises(MinAuthorParseError):
        parse_min_author_patch(
            '{"operation":"replace","operation":"add","path":"/object/features","value":NaN}'
        )


@pytest.mark.anyio
async def test_min_author_initial_accepts_complete_strict_scene(tmp_path) -> None:
    state = _author_state()
    data = dict(state["scene"])  # type: ignore[arg-type]
    data["object"] = dict(data["object"])
    data["object"]["color_field"] = dict(data["object"]["color_field"])
    data["object"]["color_field"]["model"] = "solid"
    scene = MinScene.model_validate(data)
    gateway = _FakeGateway(scene.model_dump_json())
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_FakeRenderer),  # type: ignore[arg-type]
        gateway,
    )

    update = await nodes["author_initial"](state)

    assert update["scene"]["object"]["color_field"]["model"] == "solid"
    assert update["llm_call_count"] == 1
    assert update["author_error"] is None
    assert gateway.calls == 1


@pytest.mark.anyio
async def test_min_author_initial_failure_falls_back_to_perception(tmp_path) -> None:
    state = _author_state()
    gateway = _FakeGateway("not-json", RuntimeError("provider down"))
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_FakeRenderer),  # type: ignore[arg-type]
        gateway,
    )

    update = await nodes["author_initial"](state)

    assert update["scene"] == state["scene"]
    assert update["llm_call_count"] == 2
    assert str(update["author_error"]).startswith("llm_repair_failed:")
    assert gateway.calls == 2


@pytest.mark.anyio
async def test_min_author_refine_rejects_illegal_patch_and_keeps_best(tmp_path) -> None:
    state = _author_state()
    illegal = '{"operation":"replace","path":"/canvas/width","value":32}'
    gateway = _FakeGateway(illegal, illegal)
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_FakeRenderer),  # type: ignore[arg-type]
        gateway,
    )

    update = await nodes["author_refine"](state)

    assert update["scene"] == state["current_best"]["scene"]  # type: ignore[index]
    assert update["llm_call_count"] == 2
    assert update["refine_count"] == 1
    assert update["author_error"] == "invalid_min_author_patch_json"


@pytest.mark.anyio
async def test_refine_candidate_cannot_overwrite_better_current_best(tmp_path) -> None:
    state = _author_state(llm_budget=1)
    state["current_best"] = {
        **state["current_best"],  # type: ignore[dict-item]
        "mae": 0.0,
    }
    gateway = _FakeGateway(
        '{"operation":"replace","path":"/object/color_field/model","value":"solid"}'
    )
    registry = MinRendererRegistry(_FakeRenderer)  # type: ignore[arg-type]
    nodes = make_min_nodes(LocalArtifactStore(tmp_path), registry, gateway)
    refined = await nodes["author_refine"](state)
    render_state = {
        **state,
        **refined,
        "render_count": 0,
        "render_budget": 1,
    }

    update = await nodes["render_and_evaluate"](render_state)

    assert update["current_best"] == state["current_best"]
    assert update["current_best_mae"] == 0.0
    assert update["scene"] == state["current_best"]["scene"]  # type: ignore[index]


@pytest.mark.anyio
async def test_min_author_total_calls_including_repairs_never_exceed_six(
    tmp_path,
) -> None:
    state = _author_state(llm_budget=99)
    gateway = _FakeGateway(*("invalid" for _ in range(6)))
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_FakeRenderer),  # type: ignore[arg-type]
        gateway,
    )

    for node_name in ("author_initial", "author_refine", "author_refine"):
        state.update(await nodes[node_name](state))
    no_budget_update = await nodes["author_refine"](state)

    assert state["llm_call_count"] == 6
    assert no_budget_update.get("llm_call_count", 6) == 6
    assert gateway.calls == 6


@pytest.mark.anyio
async def test_min_author_zero_budget_never_calls_gateway(tmp_path) -> None:
    state = _author_state(llm_budget=0)
    gateway = _FakeGateway(RuntimeError("must not be called"))
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_FakeRenderer),  # type: ignore[arg-type]
        gateway,
    )

    update = await nodes["author_initial"](state)

    assert update["scene"] == state["scene"]
    assert gateway.calls == 0


def test_min_routing_rejects_unknown_action() -> None:
    assert route_after_render({"next_action": "optimize_base"}) == "optimize_base"
    assert route_after_base({"next_action": "author_refine"}) == "author_refine"
    assert route_after_feature({"next_action": "finalize"}) == "finalize"


@pytest.mark.anyio
async def test_min_graph_writes_trace_and_final_artifacts(tmp_path) -> None:
    artifacts = LocalArtifactStore(tmp_path)
    registry = MinRendererRegistry(_FakeRenderer)  # type: ignore[arg-type]
    graph = build_png_to_shader_min_graph(
        artifact_store=artifacts,
        renderer_registry=registry,
    )

    result = await graph.ainvoke(
        {
            "project_id": "project-1",
            "run_id": "run-1",
            "image": _pink_orb_png(),
            "content_type": "image/png",
            "render_budget": 1,
            "llm_budget": 0,
            "refine_budget": 0,
            "target_mae": 1.0,
        }
    )

    assert result["status"] == "completed"
    assert result["render_count"] == 1
    assert result["final_result"]["renderer_path"] == "prepared_uniforms_v1"
    assert result["final_result"]["uniform_render_count"] == 1
    assert result["final_result"]["target_reached"] is True
    assert [item["phase"] for item in result["trace"]][-1] == "finalize"
    final_trace = result["trace"][-1]
    assert {
        key: final_trace[key]
        for key in (
            "renderer_path",
            "target_mae",
            "target_reached",
            "prepare_duration_ms",
            "uniform_render_count",
            "uniform_render_p95_ms",
        )
    } == {
        "renderer_path": "prepared_uniforms_v1",
        "target_mae": 1.0,
        "target_reached": True,
        "prepare_duration_ms": 3.5,
        "uniform_render_count": 1,
        "uniform_render_p95_ms": 1.25,
    }
    run = artifacts.resolve_run("run-1")
    assert run.read_bytes("final/render.png")
    assert b'"schema_version":"png_to_shader_min_manifest_v1"' in run.read_bytes(
        "final/manifest.json"
    )
    metrics = run.read_bytes("final/metrics.json")
    assert b'"renderer_path":"prepared_uniforms_v1"' in metrics
    assert b'"uniform_render_p95_ms":1.25' in metrics
    manifest = run.read_bytes("final/manifest.json")
    assert b'"renderer_path":"prepared_uniforms_v1"' in manifest
    assert b'"target_reached":true' in manifest


@pytest.mark.anyio
async def test_min_graph_builder_injects_fake_gateway_for_model_author(
    tmp_path,
) -> None:
    image = _pink_orb_png()
    data = perceive_min_target(image).fallback_scene.model_dump(mode="json")
    data["object"]["color_field"]["model"] = "solid"
    gateway = _FakeGateway(MinScene.model_validate(data).model_dump_json())
    graph = build_png_to_shader_min_graph(
        artifact_store=LocalArtifactStore(tmp_path),
        renderer_registry=MinRendererRegistry(_FakeRenderer),  # type: ignore[arg-type]
        gateway=gateway,
    )

    result = await graph.ainvoke(
        {
            "project_id": "project-model",
            "run_id": "run-model",
            "image": image,
            "content_type": "image/png",
            "render_budget": 1,
            "llm_budget": 1,
            "refine_budget": 0,
            "target_mae": 1.0,
        }
    )

    assert gateway.calls == 1
    assert result["llm_call_count"] == 1
    assert result["final_result"]["scene"]["object"]["color_field"]["model"] == "solid"


@pytest.mark.anyio
async def test_min_graph_prepares_once_and_reuses_program_for_candidates(
    tmp_path,
) -> None:
    _FakeRenderer.instances.clear()
    artifacts = LocalArtifactStore(tmp_path)
    graph = build_png_to_shader_min_graph(
        artifact_store=artifacts,
        renderer_registry=MinRendererRegistry(_FakeRenderer),  # type: ignore[arg-type]
    )

    result = await graph.ainvoke(
        {
            "project_id": "project-hot",
            "run_id": "run-hot",
            "image": _pink_orb_png(),
            "content_type": "image/png",
            "render_budget": 3,
            "llm_budget": 0,
            "refine_budget": 0,
            "target_mae": 0.0,
        }
    )

    assert len(_FakeRenderer.instances) == 1
    renderer = _FakeRenderer.instances[0]
    assert renderer.prepare_calls == 1
    assert renderer.prepared.render_count == 3
    assert renderer.prepared.closed is True
    assert renderer.closed is True
    assert result["final_result"]["uniform_render_count"] == 3
    assert result["final_result"]["target_reached"] is False
    base_trace = next(item for item in result["trace"] if item["phase"] == "optimize_base")
    assert base_trace["candidates_evaluated"] == 2
