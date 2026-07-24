from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from langchain_core.messages import AIMessage
from PIL import Image, ImageDraw

from agent.app.config.png_to_shader_min import MIN_PIPELINE_CONFIG
from agent.app.contracts.llm import LLMResponse
from agent.app.contracts.png_to_shader_min import (
    apply_min_author_patch,
    summarize_min_author_patch,
)
from agent.app.graphs.png_to_shader_min_graph import build_png_to_shader_min_graph
from agent.app.graphs.png_to_shader_min_routing import (
    route_after_base,
    route_after_feature,
    route_after_render,
)
from agent.app.nodes.png_to_shader_min import MinRendererRegistry, make_min_nodes
from agent.app.nodes.png_to_shader_min.model_author import (
    MAX_MIN_LLM_CALLS,
    MIN_AUTHOR_REFINE_PROMPT,
)
from agent.app.nodes.png_to_shader_min.shader_graph_shadow import (
    ShaderGraphShadowResult,
)
from agent.app.parsers.png_to_shader_min import (
    MinAuthorParseError,
    parse_min_author_patch,
)
from agent.app.services.png_to_shader_min import PngToShaderMinService
from shaderforge.evaluation import evaluate_min_scene
from shaderforge.generation import (
    MAX_MIN_FEATURES,
    MIN_TEMPLATE_FRAGMENT_UNIFORM_VECTORS,
    MIN_TEMPLATE_VERSION,
    WEBGL1_MIN_FRAGMENT_UNIFORM_VECTORS,
    bake_min_uniforms,
    materialize_min_shader,
)
from shaderforge.perception import perceive_min_target
from shaderforge.scene import (
    AddFeaturePatch,
    Feature,
    MinScene,
    Primitive,
    ReplaceFeaturePatch,
    SolidColorField,
    apply_scene_patch,
)
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


class _UniformValuePrepared(_FakePrepared):
    async def render_uniforms(self, values, *, capture_png=False):
        self.render_durations_ms = (*self.render_durations_ms, 1.25)
        channel = round(float(values["u_scene_color_a_param_x"][0]) * 255)
        rgb = bytes((channel, channel, channel)) * self.width * self.height
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


class _UniformValueRenderer(_FakeRenderer):
    def __init__(self) -> None:
        super().__init__()
        self.prepared = _UniformValuePrepared()


class _PatchMaturityPrepared(_FakePrepared):
    async def render_uniforms(self, values, *, capture_png=False):
        self.render_durations_ms = (*self.render_durations_ms, 1.25)
        feature_kind = float(values["u_feature_kinds"][0])
        value = (
            float(values["u_feature_0_color_power"][3]) if feature_kind > 0.0 else 0.25
        )
        channel = round(value * 255)
        rgb = bytes((channel, channel, channel)) * self.width * self.height
        return SimpleNamespace(
            success=True,
            rgb_bytes=rgb,
            image_bytes=None,
            draw_error=None,
        )


class _PatchMaturityRenderer(_FakeRenderer):
    def __init__(self) -> None:
        super().__init__()
        self.prepared = _PatchMaturityPrepared()


class _FailingPrepared(_FakePrepared):
    async def render_uniforms(self, _values, *, capture_png=False):
        self.render_durations_ms = (*self.render_durations_ms, 1.25)
        return SimpleNamespace(
            success=False,
            rgb_bytes=None,
            image_bytes=None,
            draw_error="synthetic_draw_failure",
        )


class _FailingRenderer(_FakeRenderer):
    def __init__(self) -> None:
        super().__init__()
        self.prepared = _FailingPrepared()


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


class _BudgetGraph:
    def __init__(self) -> None:
        self.inputs: list[dict[str, object]] = []
        self.configs: list[dict[str, object]] = []

    async def astream(self, state, config, *, stream_mode="updates"):
        self.inputs.append(state)
        self.configs.append(config)
        yield {
            "finalize": {
                "final_result": {
                    "project_id": state["project_id"],
                    "run_id": state["run_id"],
                    "glsl": "void main(){}",
                    "render_width": 1,
                    "render_height": 1,
                    "status": "completed",
                    "stop_reason": "bounded_mvp_complete",
                    "template_version": "png_to_shader_min_template_v3",
                    "quality_preset": state["quality_preset"],
                    "current_best_mae": 0.1,
                    "current_best_loss": 0.11,
                    "metric_breakdown": {"metric_version": "min_scene_composite_v3"},
                    "render_count": 1,
                    "render_budget": state["render_budget"],
                    "llm_call_count": 0,
                    "llm_budget": state["llm_budget"],
                    "refine_budget": state["refine_budget"],
                    "run_classification": state["run_classification"],
                    "experiment_id": state["experiment_id"],
                    "config_fingerprint": state["config_fingerprint"],
                    "report_schema_version": state["report_schema_version"],
                    "patch_candidate_draw_budget": 12,
                    "patch_evidence": (),
                    "renderer_path": "prepared_uniforms_v1",
                    "target_mae": state["target_mae"],
                    "target_loss": state["target_loss"],
                    "target_reached": False,
                    "prepare_duration_ms": 1.0,
                    "uniform_render_count": 1,
                    "uniform_render_p95_ms": 1.0,
                    "scene": {},
                    "trace": (),
                }
            }
        }


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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("quality_preset", "render_budget", "llm_budget", "refine_budget"),
    (
        ("fast", 48, 2, 1),
        ("balanced", 96, 4, 2),
        ("high", 640, 9, 9),
        ("manual", 1000, 32, 30),
    ),
)
async def test_scene_mvp_quality_preset_selects_bounded_budgets(
    tmp_path,
    quality_preset,
    render_budget,
    llm_budget,
    refine_budget,
) -> None:
    graph = _BudgetGraph()
    registry = MinRendererRegistry(_FakeRenderer)  # type: ignore[arg-type]
    service = PngToShaderMinService(
        graph,
        LocalArtifactStore(tmp_path),
        registry,
        llm_budget=MIN_PIPELINE_CONFIG.max_llm_budget,
        refine_budget=MIN_PIPELINE_CONFIG.max_refine_budget,
    )

    result = await service.generate(
        b"image",
        "image/png",
        project_id="budget-project",
        run_id=f"budget-{quality_preset}",
        quality_preset=quality_preset,
    )

    assert graph.inputs[-1]["render_budget"] == render_budget
    assert graph.inputs[-1]["llm_budget"] == llm_budget
    assert graph.inputs[-1]["refine_budget"] == refine_budget
    assert graph.inputs[-1]["target_loss"] == 0.02
    assert graph.configs[-1]["recursion_limit"] == (
        MIN_PIPELINE_CONFIG.quality_presets[quality_preset].recursion_limit
    )
    assert result.quality_preset == quality_preset
    assert result.render_budget == render_budget


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
    assert patched.schema_version == "png_to_shader_min_scene_v3"
    materialized = materialize_min_shader(patched)
    source = bake_min_uniforms(materialized)
    assert validate_shader(source).valid
    assert "texture2D(" not in source
    assert "iResolution.xy" in materialized.shadertoy_source
    assert "uniform vec4 u_scene_bg_scale" not in materialized.shadertoy_source
    assert materialized.template_version == "png_to_shader_min_template_v3"
    assert MIN_TEMPLATE_VERSION == "png_to_shader_min_template_v3"


def test_scene_template_consumes_multiple_feature_slots_and_all_numeric_fields() -> (
    None
):
    scene = perceive_min_target(_pink_orb_png()).fallback_scene.model_copy(
        update={
            "object": perceive_min_target(
                _pink_orb_png()
            ).fallback_scene.object.model_copy(
                update={
                    "features": (
                        Feature(
                            id="top",
                            type="polar_arc",
                            center=(-0.3, 0.6),
                            axes=(0.8, 0.15),
                            color=(1.0, 1.0, 1.0),
                            intensity=0.8,
                        ),
                        Feature(
                            id="bottom",
                            type="edge_line",
                            center=(0.4, -0.6),
                            axes=(0.6, 0.1),
                            color=(1.0, 0.9, 0.9),
                            intensity=0.6,
                        ),
                    )
                }
            )
        }
    )

    materialized = materialize_min_shader(scene)

    assert materialized.uniform_values["u_feature_0_shape"] == (-0.3, 0.6, 0.8, 0.15)
    assert materialized.uniform_values["u_feature_0_color_power"] == (
        1.0,
        1.0,
        1.0,
        0.8,
    )
    assert materialized.uniform_values["u_feature_1_shape"] == (0.4, -0.6, 0.6, 0.1)
    assert materialized.uniform_values["u_feature_kinds"] == (3.0, 4.0, 0.0, 0.0)
    assert "u_feature_0_shape" in materialized.webgl1_source
    assert "u_feature_1_color_power" in materialized.webgl1_source
    assert (
        materialized.webgl1_source.count("applyFeatureBody(body")
        == MAX_MIN_FEATURES * 3
    )
    first_lobe = materialized.webgl1_source.index(
        "applyFeatureBody(body, p, objectDistance, 1.0"
    )
    first_rim = materialized.webgl1_source.index(
        "applyFeatureBody(body, p, objectDistance, 2.0"
    )
    first_detail = materialized.webgl1_source.index(
        "applyFeatureBody(body, p, objectDistance, 3.0"
    )
    assert first_lobe < first_rim < first_detail
    assert (
        materialized.webgl1_source.count("applyFeatureBackground(background")
        == MAX_MIN_FEATURES
    )
    active_fragment_uniform_vectors = 1 + len(materialized.uniform_schema)
    assert (
        active_fragment_uniform_vectors == MIN_TEMPLATE_FRAGMENT_UNIFORM_VECTORS == 15
    )
    assert active_fragment_uniform_vectors <= WEBGL1_MIN_FRAGMENT_UNIFORM_VECTORS
    assert "float rimWeight" in materialized.webgl1_source
    assert "float arcWeight" in materialized.webgl1_source
    assert "float lineWeight" in materialized.webgl1_source
    assert validate_shader(bake_min_uniforms(materialized)).valid


def test_scene_rejects_more_features_than_fixed_prepared_slots() -> None:
    scene = perceive_min_target(_pink_orb_png()).fallback_scene
    data = scene.model_dump(mode="python")
    data["object"]["features"] = [
        Feature(id=f"feature_{index}", type="rim").model_dump(mode="python")
        for index in range(MAX_MIN_FEATURES + 1)
    ]

    with pytest.raises(ValueError, match="4 items"):
        MinScene.model_validate(data)


def test_circle_requires_equal_axes_and_color_fields_are_strict_unions() -> None:
    with pytest.raises(ValueError, match="circle axes"):
        Primitive(type="circle", center=(0.0, 0.0), axes=(0.8, 0.7))
    assert Primitive(type="circle", center=(0.0, 0.0), axes=(0.8, 0.8)).axes == (
        0.8,
        0.8,
    )
    scene = perceive_min_target(_pink_orb_png()).fallback_scene.model_dump(
        mode="python"
    )
    scene["object"]["color_field"] = {
        "model": "solid",
        "color": (0.8, 0.4, 0.5),
        "scale": 1.0,
    }
    with pytest.raises(ValueError, match="Extra inputs"):
        MinScene.model_validate(scene)


def test_replace_feature_is_atomic_and_preserves_stable_id_at_full_capacity() -> None:
    scene = perceive_min_target(_pink_orb_png()).fallback_scene
    features = tuple(
        Feature(id=f"slot_{index}", type="rim", intensity=0.1 + index * 0.1)
        for index in range(MAX_MIN_FEATURES)
    )
    full = MinScene.model_validate(
        {
            **scene.model_dump(mode="python"),
            "object": {
                **scene.object.model_dump(mode="python"),
                "features": features,
            },
        }
    )
    replacement = Feature(
        id="slot_2",
        type="gaussian_lobe",
        center=(0.2, 0.3),
        axes=(0.4, 0.2),
        color=(1.0, 0.8, 0.9),
        intensity=0.7,
    )

    patched = apply_scene_patch(
        full,
        ReplaceFeaturePatch(
            op="replace_feature", feature_id="slot_2", feature=replacement
        ),
    )

    assert len(patched.object.features) == MAX_MIN_FEATURES
    assert patched.object.features[2] == replacement
    with pytest.raises(ValueError, match="不存在"):
        apply_scene_patch(
            full,
            ReplaceFeaturePatch(
                op="replace_feature",
                feature_id="missing",
                feature=replacement.model_copy(update={"id": "missing"}),
            ),
        )
    with pytest.raises(ValueError, match="稳定 feature id"):
        ReplaceFeaturePatch(
            op="replace_feature",
            feature_id="slot_1",
            feature=replacement,
        )


def test_min_author_patch_parser_requires_one_whitelisted_typed_patch() -> None:
    patch = parse_min_author_patch(
        '{"operation":"replace","path":"/object/color_field","value":{"model":"solid","color":[0.9,0.4,0.5]}}'
    )
    scene = perceive_min_target(_pink_orb_png()).fallback_scene

    assert apply_min_author_patch(scene, patch).object.color_field.model == "solid"
    feature_patch = parse_min_author_patch(
        '{"operation":"replace","path":"/object/features","value":'
        '{"feature_id":"rim","feature":{"id":"rim","type":"gaussian_lobe",'
        '"center":[0,0],"axes":[0.4,0.3],"color":[1,0.8,0.9],"intensity":0.5}}}'
    )
    assert apply_min_author_patch(scene, feature_patch).object.features[0].type == (
        "gaussian_lobe"
    )
    with pytest.raises(MinAuthorParseError):
        parse_min_author_patch(
            '[{"operation":"replace","path":"/object/color_field","value":{"model":"solid","color":[1,1,1]}}]'
        )
    with pytest.raises(MinAuthorParseError):
        parse_min_author_patch(
            '{"operation":"replace","path":"/canvas/width","value":32}'
        )
    with pytest.raises(MinAuthorParseError):
        parse_min_author_patch(
            '{"operation":"replace","operation":"add","path":"/object/features","value":NaN}'
        )


def test_min_refine_prompt_explains_residual_and_rejection_evidence() -> None:
    assert MIN_AUTHOR_REFINE_PROMPT.version == "min_author_refine_v1_3"
    assert "rendered-reference" in MIN_AUTHOR_REFINE_PROMPT.prompt
    assert "active_feature_summary" in MIN_AUTHOR_REFINE_PROMPT.prompt
    assert "recent_rejected_patch_summaries" in MIN_AUTHOR_REFINE_PROMPT.prompt


@pytest.mark.anyio
async def test_min_author_initial_accepts_complete_strict_scene(tmp_path) -> None:
    state = _author_state()
    data = dict(state["scene"])  # type: ignore[arg-type]
    data["object"] = dict(data["object"])
    data["object"]["color_field"] = {
        "model": "solid",
        "color": [0.9, 0.4, 0.5],
    }
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
async def test_initial_model_scene_cannot_replace_better_rendered_fallback(
    tmp_path,
) -> None:
    fallback = perceive_min_target(_pink_orb_png()).fallback_scene
    fallback_data = fallback.model_dump(mode="json")
    fallback_data["object"]["color_field"] = SolidColorField(
        model="solid", color=(0.0, 0.0, 0.0)
    ).model_dump(mode="json")
    fallback_data = MinScene.model_validate(fallback_data).model_dump(mode="json")
    model_data = MinScene.model_validate(fallback_data).model_dump(mode="json")
    model_data["object"]["color_field"] = SolidColorField(
        model="solid", color=(1.0, 1.0, 1.0)
    ).model_dump(mode="json")
    state = {
        "project_id": "arbitration-project",
        "run_id": "arbitration-run",
        "scene": model_data,
        "fallback_scene": fallback_data,
        "target_rgb": np.zeros((fallback.canvas.height, fallback.canvas.width, 3)),
        "render_count": 0,
        "render_budget": 2,
        "trace": (),
    }
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_UniformValueRenderer),  # type: ignore[arg-type]
        _FakeGateway(RuntimeError("unused")),
    )

    update = await nodes["render_and_evaluate"](state)

    assert update["render_count"] == 2
    assert update["current_best"]["scene"] == fallback_data
    assert update["current_best_mae"] == 0.0
    assert update["trace"][-1]["selected_source"] == "perception_fallback"
    assert update["trace"][-1]["working_scene_mae"] == 1.0
    assert update["trace"][-1]["fallback_mae"] == 0.0


@pytest.mark.anyio
async def test_initial_selection_builds_feature_queue_from_winning_scene_ids(
    tmp_path,
) -> None:
    fallback = perceive_min_target(_pink_orb_png()).fallback_scene
    model = fallback.model_copy(
        update={
            "object": fallback.object.model_copy(
                update={
                    "features": (
                        Feature(id="highlight_top", type="polar_arc"),
                        Feature(id="highlight_bottom", type="edge_line"),
                        Feature(id="edge_ring", type="rim"),
                    )
                }
            )
        }
    )
    state = {
        "project_id": "queue-project",
        "run_id": "queue-run",
        "scene": model.model_dump(mode="json"),
        "fallback_scene": fallback.model_dump(mode="json"),
        "target_rgb": np.ones((fallback.canvas.height, fallback.canvas.width, 3)),
        "render_count": 0,
        "render_budget": 2,
        "trace": (),
    }
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_UniformValueRenderer),  # type: ignore[arg-type]
        _FakeGateway(RuntimeError("unused")),
    )

    update = await nodes["render_and_evaluate"](state)

    assert update["trace"][-1]["selected_source"] == "working_scene"
    assert update["feature_queue"] == (
        "highlight_top",
        "highlight_bottom",
        "edge_ring",
    )


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
    rendered = await nodes["render_and_evaluate"](
        {
            **state,
            **update,
            "render_count": 0,
            "render_budget": 12,
            "recent_rejected_patch_summaries": (),
            "patch_evidence": (),
        }
    )

    assert update["scene"] == state["current_best"]["scene"]  # type: ignore[index]
    assert update["llm_call_count"] == 2
    assert update["refine_count"] == 1
    assert update["author_error"] == "invalid_min_author_patch_json"
    assert rendered["render_count"] == 0
    assert rendered["current_best"] == state["current_best"]
    assert rendered["patch_evidence"][-1]["rejected_reason"] == "invalid_patch"


@pytest.mark.anyio
async def test_refine_candidate_cannot_overwrite_better_current_best(tmp_path) -> None:
    state = _author_state(llm_budget=1)
    state["current_best"] = {
        **state["current_best"],  # type: ignore[dict-item]
        "mae": 0.0,
    }
    gateway = _FakeGateway(
        '{"operation":"replace","path":"/object/color_field","value":{"model":"solid","color":[0.9,0.4,0.5]}}'
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
async def test_structural_patch_can_mature_before_competing_with_current_best(
    tmp_path,
) -> None:
    image = _pink_orb_png()
    fallback = perceive_min_target(image).fallback_scene
    scene_data = fallback.model_dump(mode="python")
    scene_data["object"]["features"] = ()
    best_scene = MinScene.model_validate(scene_data)
    target_value = 56 / 255.0
    anchor_value = 64 / 255.0
    target_rgb = np.full(
        (best_scene.canvas.height, best_scene.canvas.width, 3),
        target_value,
        dtype=np.float32,
    )
    anchor_rgb = np.full_like(target_rgb, anchor_value)
    anchor_metric = evaluate_min_scene(
        target_rgb,
        anchor_rgb,
        best_scene.canvas.background,
    )
    anchor_png = Image.new(
        "RGB",
        (best_scene.canvas.width, best_scene.canvas.height),
        (64, 64, 64),
    )
    anchor_buffer = BytesIO()
    anchor_png.save(anchor_buffer, format="PNG")
    best = {
        "scene": best_scene.model_dump(mode="json"),
        "mae": anchor_metric.global_mae,
        "loss": anchor_metric.total_loss,
        "metrics": anchor_metric.to_dict(),
        "residual_summary": {},
        "glsl": "anchor-glsl",
        "render": anchor_buffer.getvalue(),
    }
    patch_json = (
        '{"operation":"add","path":"/object/features","value":'
        '{"id":"local_highlight","type":"gaussian_lobe","center":[0,0],'
        '"axes":[0.4,0.3],"color":[1,1,1],"intensity":0.3}}'
    )
    state = {
        **_author_state(llm_budget=1),
        "scene": best["scene"],
        "current_best": best,
        "target_rgb": target_rgb,
        "metric_background": best_scene.canvas.background,
        "render_count": 0,
        "render_budget": 12,
        "recent_rejected_patch_summaries": (),
        "patch_evidence": (),
    }
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_PatchMaturityRenderer),  # type: ignore[arg-type]
        _FakeGateway(patch_json),
    )

    refined = await nodes["author_refine"](state)
    update = await nodes["render_and_evaluate"]({**state, **refined})

    evidence = update["patch_evidence"][-1]
    assert evidence["raw_candidate_loss"] > evidence["best_loss_before"]
    assert evidence["matured_candidate_loss"] < evidence["best_loss_before"]
    assert evidence["accepted"] is True
    assert evidence["rejected_reason"] is None
    assert evidence["maturity_draw_count"] == 11
    assert evidence["total_candidate_draw_count"] == 12
    assert update["current_best_loss"] < anchor_metric.total_loss
    assert update["current_best"]["scene"]["object"]["features"][0]["intensity"] == (
        pytest.approx(0.22)
    )


@pytest.mark.anyio
async def test_duplicate_rejected_patch_receives_no_render_or_maturity_budget(
    tmp_path,
) -> None:
    patch_json = (
        '{"operation":"add","path":"/object/features","value":'
        '{"id":"repeat","type":"gaussian_lobe","center":[0,0],'
        '"axes":[0.4,0.3],"color":[1,1,1],"intensity":0.3}}'
    )
    patch = parse_min_author_patch(patch_json)
    summary = summarize_min_author_patch(patch)
    state = {
        **_author_state(llm_budget=1),
        "render_count": 4,
        "render_budget": 12,
        "recent_rejected_patch_summaries": (
            {**summary, "rejected_reason": "no_strict_loss_improvement"},
        ),
        "patch_evidence": (),
    }
    registry = MinRendererRegistry(_PatchMaturityRenderer)  # type: ignore[arg-type]
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path),
        registry,
        _FakeGateway(patch_json),
    )

    refined = await nodes["author_refine"](state)
    update = await nodes["render_and_evaluate"]({**state, **refined})

    assert refined["author_error"] == "duplicate_recent_patch"
    assert update["render_count"] == 4
    assert update["current_best"] == state["current_best"]
    assert update["patch_evidence"][-1]["duplicate_of_recent"] is True
    assert update["patch_evidence"][-1]["maturity_draw_count"] == 0
    assert update["patch_evidence"][-1]["total_candidate_draw_count"] == 0


@pytest.mark.anyio
async def test_refine_renderer_failure_cannot_pollute_current_best(tmp_path) -> None:
    patch_json = (
        '{"operation":"replace","path":"/object/color_field","value":'
        '{"model":"solid","color":[0.9,0.4,0.5]}}'
    )
    state = {
        **_author_state(llm_budget=1),
        "metric_background": (1.0, 1.0, 1.0),
        "render_count": 0,
        "render_budget": 12,
        "recent_rejected_patch_summaries": (),
        "patch_evidence": (),
    }
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_FailingRenderer),  # type: ignore[arg-type]
        _FakeGateway(patch_json),
    )

    refined = await nodes["author_refine"](state)
    update = await nodes["render_and_evaluate"]({**state, **refined})

    assert update["current_best"] == state["current_best"]
    assert update["error"] is None
    assert update["render_count"] == 1
    assert update["patch_evidence"][-1]["rejected_reason"] == "renderer_failed"
    assert update["patch_evidence"][-1]["maturity_draw_count"] == 0


@pytest.mark.anyio
async def test_remove_feature_uses_only_raw_draw_without_local_maturity(
    tmp_path,
) -> None:
    state = {
        **_author_state(llm_budget=1),
        "metric_background": (1.0, 1.0, 1.0),
        "render_count": 0,
        "render_budget": 12,
        "recent_rejected_patch_summaries": (),
        "patch_evidence": (),
    }
    best_scene = MinScene.model_validate(state["current_best"]["scene"])  # type: ignore[index]
    feature_id = best_scene.object.features[0].id
    patch_json = (
        f'{{"operation":"remove","path":"/object/features","value":"{feature_id}"}}'
    )
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_FakeRenderer),  # type: ignore[arg-type]
        _FakeGateway(patch_json),
    )

    refined = await nodes["author_refine"](state)
    update = await nodes["render_and_evaluate"]({**state, **refined})

    evidence = update["patch_evidence"][-1]
    assert evidence["patch_operation"] == "remove_feature"
    assert evidence["maturity_draw_count"] == 0
    assert evidence["total_candidate_draw_count"] == 1
    assert update["render_count"] == 1


@pytest.mark.anyio
async def test_min_author_total_calls_never_exceed_configured_maximum(
    tmp_path,
) -> None:
    state = _author_state(llm_budget=99)
    gateway = _FakeGateway(*("invalid" for _ in range(MAX_MIN_LLM_CALLS)))
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_FakeRenderer),  # type: ignore[arg-type]
        gateway,
    )

    for node_name in (
        "author_initial",
        *("author_refine" for _ in range(MAX_MIN_LLM_CALLS)),
    ):
        state.update(await nodes[node_name](state))
    no_budget_update = await nodes["author_refine"](state)

    assert state["llm_call_count"] == MAX_MIN_LLM_CALLS
    assert (
        no_budget_update.get("llm_call_count", MAX_MIN_LLM_CALLS) == MAX_MIN_LLM_CALLS
    )
    assert gateway.calls == MAX_MIN_LLM_CALLS


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
            "target_loss": 1.0,
        }
    )

    assert result["status"] == "completed"
    assert result["render_count"] == 1
    assert result["final_result"]["renderer_path"] == "prepared_uniforms_v1"
    assert result["final_result"]["template_version"] == (
        "png_to_shader_min_template_v3"
    )
    assert result["final_result"]["uniform_render_count"] == 1
    assert result["final_result"]["target_reached"] is True
    assert result["final_result"]["run_classification"] == ("independent_experiment")
    assert result["final_result"]["experiment_id"] == (
        "scene-mvp-agent-optimization-20260723"
    )
    assert len(result["final_result"]["config_fingerprint"]) == 64
    assert result["final_result"]["patch_candidate_draw_budget"] == 12
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
    assert b'"template_version":"png_to_shader_min_template_v3"' in metrics
    assert b'"uniform_render_p95_ms":1.25' in metrics
    manifest = run.read_bytes("final/manifest.json")
    assert b'"renderer_path":"prepared_uniforms_v1"' in manifest
    assert b'"template_version":"png_to_shader_min_template_v3"' in manifest
    assert b'"target_reached":true' in manifest
    assert b'"run_classification":"independent_experiment"' in manifest
    assert b'"report_schema_version":"scene_mvp_run_report_v1"' in manifest
    assert b'"patch_candidate_draw_budget":12' in metrics


@pytest.mark.anyio
async def test_min_graph_publishes_non_authoritative_shader_graph_shadow(
    tmp_path,
) -> None:
    class FakeShadowRunner:
        async def run(self, _scene):
            return ShaderGraphShadowResult(
                summary={
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
                fragment_source="void main(){gl_FragColor=vec4(1.0);}",
                image_bytes=b"shadow-png",
            )

    artifacts = LocalArtifactStore(tmp_path)
    graph = build_png_to_shader_min_graph(
        artifact_store=artifacts,
        renderer_registry=MinRendererRegistry(_FakeRenderer),  # type: ignore[arg-type]
        shader_graph_shadow=FakeShadowRunner(),  # type: ignore[arg-type]
    )

    result = await graph.ainvoke(
        {
            "project_id": "project-shadow",
            "run_id": "run-shadow",
            "image": _pink_orb_png(),
            "content_type": "image/png",
            "render_budget": 1,
            "llm_budget": 0,
            "refine_budget": 0,
            "target_mae": 1.0,
            "target_loss": 1.0,
        }
    )

    summary = result["final_result"]["shader_graph_shadow"]
    assert summary["status"] == "rendered"
    assert summary["shader_graph"]["layers"][0]["id"] == "legacy_body"
    run = artifacts.resolve_run("run-shadow")
    assert run.read_bytes("final/shader-graph-shadow.glsl")
    assert run.read_bytes("final/shader-graph-shadow.png") == b"shadow-png"
    assert b'"shader_graph_shadow"' in run.read_bytes("final/manifest.json")


@pytest.mark.anyio
async def test_min_graph_builder_injects_fake_gateway_for_model_author(
    tmp_path,
) -> None:
    image = _pink_orb_png()
    data = perceive_min_target(image).fallback_scene.model_dump(mode="json")
    data["object"]["color_field"] = SolidColorField(
        model="solid", color=(0.9, 0.4, 0.5)
    ).model_dump(mode="json")
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
            "target_loss": 1.0,
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
    base_trace = next(
        item for item in result["trace"] if item["phase"] == "optimize_base"
    )
    assert base_trace["candidates_evaluated"] == 2
