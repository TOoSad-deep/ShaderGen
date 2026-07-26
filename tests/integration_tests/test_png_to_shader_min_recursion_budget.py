"""scene_mvp 最大合法循环必须由业务预算终止，不能先撞 Graph 安全上限."""

from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from PIL import Image, ImageDraw

from agent.app.config.png_to_shader_min import (
    MIN_PIPELINE_CONFIG,
    max_min_refine_iterations,
    required_min_graph_steps,
)
from agent.app.contracts.llm import LLMResponse
from agent.app.graphs.png_to_shader_min_graph import build_png_to_shader_min_graph
from agent.app.nodes.png_to_shader_min import MinRendererRegistry
from agent.app.services.png_to_shader_min import PngToShaderMinService
from shaderforge.perception import perceive_min_target
from shaderforge.scene import Feature
from shaderforge.store import LocalArtifactStore


def _small_orb_png() -> bytes:
    image = Image.new("RGB", (16, 16), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, 13, 13), fill=(245, 80, 130))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _PreparedWhiteRenderer:
    def __init__(self) -> None:
        self.width = 0
        self.height = 0
        self.prepare_duration_ms = 1.0
        self.render_durations_ms: list[float] = []

    @property
    def render_count(self) -> int:
        return len(self.render_durations_ms)

    async def render_uniforms(self, _values, *, capture_png=False):
        self.render_durations_ms.append(0.1)
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
        return None


class _WhiteRenderer:
    def __init__(self) -> None:
        self.prepared = _PreparedWhiteRenderer()

    async def prepare(self, _source, width, height, _uniform_schema):
        self.prepared.width = width
        self.prepared.height = height
        return self.prepared

    async def close(self) -> None:
        return None


class _MaximumLoopGateway:
    def __init__(self, initial_scene: str, patch: str, *, llm_budget: int) -> None:
        self.responses = [initial_scene, *(patch for _ in range(llm_budget - 1))]
        self.calls = 0

    async def ainvoke(self, _messages, _options):
        self.calls += 1
        text = self.responses.pop(0)
        return LLMResponse(
            message=AIMessage(content=text),
            text=text,
            reasoning_content=None,
            model_ref="fake:max-loop-author",
            requested_model_ref="fake:max-loop-author",
            latency_ms=1,
        )


class _ShaderGraphMaximumLoopGateway:
    def __init__(self, initial_document: str) -> None:
        self.initial_document = initial_document
        self.calls = 0

    @staticmethod
    def _labeled_json(messages, label: str):
        start, end = f"<{label}>", f"</{label}>"
        for part in messages[-1].content:
            if not isinstance(part, dict) or part.get("type") != "text":
                continue
            text = str(part.get("text", ""))
            if start in text and end in text:
                return json.loads(text.split(start, 1)[1].split(end, 1)[0])
        raise AssertionError(f"missing structured prompt field: {label}")

    async def ainvoke(self, messages, _options):
        self.calls += 1
        if self.calls == 1:
            text = self.initial_document
        else:
            document = self._labeled_json(messages, "current_best_shader_graph")
            base_hash = self._labeled_json(messages, "base_document_sha256")
            layer = document["layers"][0]
            candidate_layer = json.loads(json.dumps(layer))
            candidate_layer["fill"]["color"] = [
                0.1 + (self.calls - 2) * 0.01,
                0.2,
                0.3,
                0.4,
            ]
            text = json.dumps(
                {
                    "operation": "replace_layer_bundle",
                    "base_document_sha256": base_hash,
                    "value": {
                        "layer_id": layer["id"],
                        "layer": candidate_layer,
                    },
                }
            )
        return LLMResponse(
            message=AIMessage(content=text),
            text=text,
            reasoning_content=None,
            model_ref="fake:shader-graph-max-loop",
            requested_model_ref="fake:shader-graph-max-loop",
            latency_ms=1,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("quality_preset", "expected_render_budget", "expected_steps"),
    (("high", 640, 65), ("manual", 1000, 197)),
)
async def test_large_policy_four_features_finishes_before_graph_limit(
    tmp_path,
    quality_preset: str,
    expected_render_budget: int,
    expected_steps: int,
) -> None:
    image = _small_orb_png()
    fallback = perceive_min_target(image).fallback_scene
    features = (
        Feature(id="slot_0", type="rim", intensity=0.2),
        Feature(id="slot_1", type="shadow", intensity=0.2),
        Feature(id="slot_2", type="gaussian_lobe", intensity=0.2),
        Feature(id="slot_3", type="glow", intensity=0.2),
    )
    initial = fallback.model_copy(
        update={"object": fallback.object.model_copy(update={"features": features})}
    )
    replacement = features[0].model_dump(mode="json")
    patch = json.dumps(
        {
            "operation": "replace",
            "path": "/object/features",
            "value": {
                "feature_id": "slot_0",
                "feature": replacement,
            },
        }
    )
    policy = MIN_PIPELINE_CONFIG.quality_presets[quality_preset]
    gateway = _MaximumLoopGateway(
        initial.model_dump_json(),
        patch,
        llm_budget=policy.llm_budget,
    )
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    registry = MinRendererRegistry(_WhiteRenderer)  # type: ignore[arg-type]
    service = PngToShaderMinService(
        build_png_to_shader_min_graph(
            artifact_store=artifacts,
            renderer_registry=registry,
            gateway=gateway,
        ),
        artifacts,
        registry,
        llm_budget=MIN_PIPELINE_CONFIG.max_llm_budget,
        refine_budget=MIN_PIPELINE_CONFIG.max_refine_budget,
    )
    events: list[dict[str, object]] = []

    result = await service.generate(
        image,
        "image/png",
        project_id="max-loop-project",
        run_id=f"max-loop-{quality_preset}",
        quality_preset=quality_preset,
        on_progress=lambda event, _render: events.append(event),
    )

    assert (
        len(events)
        == required_min_graph_steps(
            llm_budget=policy.llm_budget,
            refine_budget=policy.refine_budget,
            max_features=4,
        )
        == expected_steps
    )
    assert len(events) < policy.recursion_limit
    assert events[-1]["node"] == "finalize"
    expected_calls = 1 + max_min_refine_iterations(
        policy.llm_budget,
        policy.refine_budget,
    )
    assert gateway.calls == expected_calls
    assert result.render_count < result.render_budget == expected_render_budget
    assert result.llm_call_count == expected_calls
    assert result.llm_budget == policy.llm_budget
    assert result.stop_reason == "bounded_mvp_complete"
    assert result.target_reached is False


@pytest.mark.anyio
async def test_shader_graph_manual_refines_do_not_hit_program_compile_budget(
    tmp_path,
) -> None:
    image = _small_orb_png()
    fallback = perceive_min_target(image).fallback_document
    gateway = _ShaderGraphMaximumLoopGateway(
        fallback.model_dump_json(by_alias=True)
    )
    artifacts = LocalArtifactStore(tmp_path / "shader-graph-artifacts")
    registry = MinRendererRegistry(_WhiteRenderer)  # type: ignore[arg-type]
    service = PngToShaderMinService(
        build_png_to_shader_min_graph(
            artifact_store=artifacts,
            renderer_registry=registry,
            gateway=gateway,
            shader_graph_product=True,
        ),
        artifacts,
        registry,
        llm_budget=MIN_PIPELINE_CONFIG.max_llm_budget,
        refine_budget=MIN_PIPELINE_CONFIG.max_refine_budget,
    )

    result = await service.generate(
        image,
        "image/png",
        project_id="shader-graph-budget-project",
        run_id="shader-graph-budget-manual",
        quality_preset="manual",
    )

    metrics = json.loads(
        artifacts.resolve_run("shader-graph-budget-manual")
        .read_bytes("final/metrics.json")
        .decode("utf-8")
    )
    assert gateway.calls == 31
    assert result.stop_reason == "bounded_mvp_complete"
    assert metrics["compile_count"] > 16
    assert metrics["max_compiles"] == 45


@pytest.mark.anyio
async def test_shader_graph_invalid_high_refines_do_not_rebuild_parameter_queue(
    tmp_path,
) -> None:
    image = _small_orb_png()
    fallback = perceive_min_target(image).fallback_document
    policy = MIN_PIPELINE_CONFIG.quality_presets["high"]
    invalid_patch = json.dumps(
        {
            "operation": "replace_canvas_background",
            "base_document_sha256": "0" * 64,
            "value": [0.8, 0.8, 0.8, 1.0],
        }
    )
    gateway = _MaximumLoopGateway(
        fallback.model_dump_json(by_alias=True),
        invalid_patch,
        llm_budget=policy.llm_budget,
    )
    artifacts = LocalArtifactStore(tmp_path / "invalid-refine-artifacts")
    registry = MinRendererRegistry(_WhiteRenderer)  # type: ignore[arg-type]
    service = PngToShaderMinService(
        build_png_to_shader_min_graph(
            artifact_store=artifacts,
            renderer_registry=registry,
            gateway=gateway,
            shader_graph_product=True,
        ),
        artifacts,
        registry,
        llm_budget=MIN_PIPELINE_CONFIG.max_llm_budget,
        refine_budget=MIN_PIPELINE_CONFIG.max_refine_budget,
    )
    events: list[dict[str, object]] = []

    result = await service.generate(
        image,
        "image/png",
        project_id="invalid-refine-project",
        run_id="invalid-refine-high",
        quality_preset="high",
        on_progress=lambda event, _render: events.append(event),
    )

    assert gateway.calls == policy.llm_budget
    assert result.stop_reason == "bounded_mvp_complete"
    assert len(events) < policy.recursion_limit
    assert sum(event["node"] == "optimize_feature" for event in events) <= 12
