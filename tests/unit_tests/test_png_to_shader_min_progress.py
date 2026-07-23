"""scene_mvp 运行进度事件的白名单契约与 author 耗时/成本数据."""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.app.contracts.llm import LLMResponse, TokenUsage
from agent.app.graphs.png_to_shader_min_graph import build_png_to_shader_min_graph
from agent.app.nodes.png_to_shader_min import MinRendererRegistry
from agent.app.nodes.png_to_shader_min.model_author import (
    MIN_AUTHOR_INITIAL_PROMPT,
    invoke_min_author,
)
from agent.app.parsers.png_to_shader_min import parse_min_scene
from agent.app.services.png_to_shader_min import PngToShaderMinService
from shaderforge.perception import perceive_min_target
from shaderforge.scene import MinScene
from shaderforge.store import LocalArtifactStore
from tests.unit_tests.test_png_to_shader_min import (
    _FakeGateway,
    _FakeRenderer,
    _pink_orb_png,
)

# 进度事件不得出现的大对象/内部字段（白名单边界）。
_BANNED_EVENT_KEYS = {
    "image",
    "scene",
    "fallback_scene",
    "current_best",
    "current_glsl",
    "current_render",
    "materialized",
    "perception",
    "target_rgb",
}


def _assert_no_bytes(value: Any) -> None:
    if isinstance(value, (bytes, bytearray)):
        raise AssertionError("进度事件不得携带字节对象。")
    if isinstance(value, dict):
        for item in value.values():
            _assert_no_bytes(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_no_bytes(item)


@pytest.mark.anyio
async def test_service_emits_whitelisted_progress_events(tmp_path) -> None:
    registry = MinRendererRegistry(_FakeRenderer)  # type: ignore[arg-type]
    artifacts = LocalArtifactStore(tmp_path)
    service = PngToShaderMinService(
        build_png_to_shader_min_graph(
            artifact_store=artifacts,
            renderer_registry=registry,
        ),
        artifacts,
        registry,
        llm_budget=0,
        refine_budget=0,
    )
    events: list[dict[str, Any]] = []
    renders: list[bytes] = []

    def on_progress(event: dict[str, Any], render: bytes | None) -> None:
        events.append(event)
        if render is not None:
            renders.append(render)

    result = await service.generate(
        _pink_orb_png(),
        "image/png",
        project_id="progress-project",
        run_id="progress-run",
        quality_preset="fast",
        on_progress=on_progress,
    )

    nodes = [event["node"] for event in events]
    assert nodes[:5] == [
        "initialize_run",
        "perceive_target",
        "author_initial",
        "materialize_shader",
        "render_and_evaluate",
    ]
    assert "decide_after_render" in nodes
    assert nodes[-1] == "finalize"

    for event in events:
        json.dumps(event)
        _assert_no_bytes(event)
        assert _BANNED_EVENT_KEYS.isdisjoint(event)
        assert event["budgets"]["render_budget"] == 48
        assert isinstance(event["elapsed_ms"], (int, float))
        assert isinstance(event["duration_ms"], (int, float))

    elapsed = [event["elapsed_ms"] for event in events]
    assert elapsed == sorted(elapsed)

    decide = next(event for event in events if event["node"] == "decide_after_render")
    assert decide["next_action"] in {"optimize_base", "finalize"}

    render_event = next(
        event for event in events if event["node"] == "render_and_evaluate"
    )
    assert render_event["counters"]["render_count"] >= 1
    assert render_event["best"]["loss"] >= 0
    assert renders, "render_and_evaluate 应通过第二参数回传当前渲染帧。"
    assert all(render[:8] == b"\x89PNG\r\n\x1a\n" for render in renders)

    trace_total = sum(len(event.get("trace", ())) for event in events)
    assert trace_total == len(result.trace)
    assert result.stop_reason


@pytest.mark.anyio
async def test_author_progress_includes_latency_and_call_count(tmp_path) -> None:
    image = _pink_orb_png()
    data = perceive_min_target(image).fallback_scene.model_dump(mode="json")
    gateway = _FakeGateway(MinScene.model_validate(data).model_dump_json())
    registry = MinRendererRegistry(_FakeRenderer)  # type: ignore[arg-type]
    artifacts = LocalArtifactStore(tmp_path)
    service = PngToShaderMinService(
        build_png_to_shader_min_graph(
            artifact_store=artifacts,
            renderer_registry=registry,
            gateway=gateway,
        ),
        artifacts,
        registry,
        llm_budget=2,
        refine_budget=0,
    )
    events: list[dict[str, Any]] = []

    await service.generate(
        image,
        "image/png",
        project_id="progress-author-project",
        run_id="progress-author-run",
        quality_preset="fast",
        on_progress=lambda event, _render: events.append(event),
    )

    author = next(event for event in events if event["node"] == "author_initial")
    assert author["counters"]["llm_call_count"] == 1
    author_trace = author["trace"][-1]
    assert author_trace["author_source"] == "model"
    assert author_trace["author_latency_ms"] == 1
    assert author_trace["author_tokens"] is None


@pytest.mark.anyio
async def test_invoke_min_author_accumulates_latency_and_tokens() -> None:
    scene_json = MinScene.model_validate(
        perceive_min_target(_pink_orb_png()).fallback_scene.model_dump(mode="json")
    ).model_dump_json()

    class _UsageGateway:
        async def ainvoke(self, _messages: object, _options: object) -> LLMResponse:
            return LLMResponse(
                message=AIMessage(content=scene_json),
                text=scene_json,
                reasoning_content=None,
                model_ref="fake:min-author",
                latency_ms=7,
                usage=TokenUsage(input_tokens=3, output_tokens=5, total_tokens=8),
            )

    result = await invoke_min_author(
        gateway=_UsageGateway(),
        messages=[HumanMessage(content="author")],
        prompt=MIN_AUTHOR_INITIAL_PROMPT,
        schema=MinScene.model_json_schema(mode="validation"),
        parser=lambda text: parse_min_scene(
            text,
            expected_width=96,
            expected_height=96,
        ),
        remaining_calls=1,
        max_output_tokens=1800,
    )

    assert result.call_count == 1
    assert result.error_code is None
    assert result.latency_ms == 7
    assert result.total_tokens == 8
