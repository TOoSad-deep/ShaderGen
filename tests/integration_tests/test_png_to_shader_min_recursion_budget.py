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
    def __init__(self, initial_scene: str, patch: str) -> None:
        self.responses = [initial_scene, *(patch for _ in range(8))]
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


@pytest.mark.anyio
async def test_high_policy_four_features_finishes_after_more_than_64_graph_steps(
    tmp_path,
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
    gateway = _MaximumLoopGateway(initial.model_dump_json(), patch)
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
        llm_budget=9,
        refine_budget=9,
    )
    events: list[dict[str, object]] = []

    result = await service.generate(
        image,
        "image/png",
        project_id="max-loop-project",
        run_id="max-loop-run",
        quality_preset="high",
        on_progress=lambda event, _render: events.append(event),
    )

    policy = MIN_PIPELINE_CONFIG.quality_presets["high"]
    assert policy.recursion_limit == 69
    assert len(events) == required_min_graph_steps(
        llm_budget=policy.llm_budget,
        refine_budget=policy.refine_budget,
    )
    assert 64 < len(events) < policy.recursion_limit
    assert events[-1]["node"] == "finalize"
    assert gateway.calls == 9
    assert result.render_count < result.render_budget == 640
    assert result.llm_call_count == result.llm_budget == 9
    assert result.stop_reason == "bounded_mvp_complete"
    assert result.target_reached is False
