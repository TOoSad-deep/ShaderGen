"""Reusable fakes for the current Layered Direct tests."""

from __future__ import annotations

import json
from hashlib import sha256
from io import BytesIO
from typing import Any

from langchain_core.messages import AIMessage
from PIL import Image

from agent.app.contracts.llm import (
    EffectiveCallIdentity,
    EffectiveSamplingParams,
    LLMCallOptions,
    LLMResponse,
    TokenUsage,
)
from shaderforge.program_spec.receipt import _test_receipt_capabilities
from shaderforge.rendering.models import PreparedRenderResult

CANVAS = 64


def reference_png(gray: int = 128) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (CANVAS, CANVAS), (gray, gray, gray)).save(buffer, "PNG")
    return buffer.getvalue()


def _solid_png(gray: int) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (CANVAS, CANVAS), (gray, gray, gray)).save(buffer, "PNG")
    return buffer.getvalue()


def _plan_payload() -> dict[str, Any]:
    return {
        "schema_version": "layer_plan_v1",
        "layers": [
            {
                "layer_id": "bg",
                "role": "background",
                "z_index": 0,
                "region": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                "dominant_colors": [[0.5, 0.5, 0.5, 1.0]],
                "confidence": 0.9,
            }
        ],
    }


class FakeGateway:
    def __init__(
        self,
        *,
        plan_responses: list[str] | None = None,
        initial_responses: list[str] | None = None,
        refine_responses: list[str] | None = None,
        repair_responses: list[str] | None = None,
        usage: TokenUsage | None = TokenUsage(
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
        ),
    ) -> None:
        self._queues: dict[Any, list[str]] = {
            "plan": list(plan_responses or [json.dumps(_plan_payload())]),
            ("initial", False): list(initial_responses or ["{}"]),
            ("initial", True): list(initial_responses or ["{}"]),
            ("refine", False): list(refine_responses or ["{}"]),
            ("refine", True): list(refine_responses or ["{}"]),
            "repair": list(repair_responses or ["{}"]),
        }
        self._usage = usage
        self.calls: list[dict[str, Any]] = []
        self._last_text: str | None = None

    async def ainvoke(
        self,
        messages: Any,
        options: LLMCallOptions,
    ) -> LLMResponse:
        system = str(messages[0].content)
        if "视觉分析" in system:
            role, key = "plan", "plan"
        elif "Refine Author" in system:
            role, key = "refine", ("refine", False)
        elif "Initial Author" in system:
            role, key = "initial", ("initial", False)
        else:
            role, key = "repair", "repair"
        queue = self._queues[key]
        text = queue.pop(0) if len(queue) > 1 else queue[0]
        self._last_text = text
        self.calls.append(
            {"role": role, "messages": list(messages), "options": options}
        )
        return LLMResponse(
            message=AIMessage(content=text),
            text=text,
            reasoning_content=None,
            model_ref="fake-direct-model",
            latency_ms=1,
            usage=self._usage,
            effective_identity=EffectiveCallIdentity(
                provider="fake",
                model_ref="fake-direct-model",
                model_identity_source="response_metadata",
                sampling=EffectiveSamplingParams(
                    temperature=0.0,
                    thinking="off",
                    reasoning_effort=None,
                    response_format="json_object",
                    max_output_tokens=options.max_output_tokens,
                ),
            ),
        )


_TEST_SIGNER, TEST_ISSUER = _test_receipt_capabilities(
    issuer_id="test_only_direct_runner"
)


class FakePrepared:
    def __init__(
        self,
        renderer: FakeRenderer,
        fragment_source: str,
        width: int,
        height: int,
    ) -> None:
        self._renderer = renderer
        self._fragment_source = fragment_source
        self._width = width
        self._height = height

    async def render_uniforms(
        self,
        uniform_values: Any,
        *,
        capture_png: bool = False,
        receipt_spec_sha256: str | None = None,
    ) -> PreparedRenderResult:
        self._renderer.draw_calls.append(dict(uniform_values))
        if self._renderer.fail_draw:
            return PreparedRenderResult(
                success=False,
                rgb_bytes=None,
                image_bytes=None,
                width=self._width,
                height=self._height,
                console_errors=(),
                duration_ms=1.0,
                draw_error="fake_draw_failed",
            )
        gain = float(uniform_values.get("u_gain", 0.5))
        gray = round(gain * 255)
        rgb = bytes([gray, gray, gray]) * (self._width * self._height)
        png = _solid_png(gray) if capture_png else None
        assert receipt_spec_sha256 is not None
        receipt = (
            None
            if self._renderer.receipt_mode == "missing"
            else _TEST_SIGNER.issue_after_draw(
                source_sha256=sha256(self._fragment_source.encode()).hexdigest(),
                spec_sha256=receipt_spec_sha256,
                rgb_bytes=rgb,
                png_bytes=png,
                renderer_version="fake_direct_renderer_v1",
                runtime_metadata={
                    "browser_version": "fake-browser",
                    "gl_version": "fake-gl",
                    "glsl_version": "fake-glsl",
                },
            )
        )
        return PreparedRenderResult(
            success=True,
            rgb_bytes=rgb,
            image_bytes=png,
            width=self._width,
            height=self._height,
            console_errors=(),
            duration_ms=1.0,
            execution_receipt=receipt,
        )

    async def close(self) -> None:
        self._renderer.close_count += 1


class FakeRenderer:
    def __init__(
        self,
        *,
        fail_draw: bool = False,
        receipt_mode: str = "ok",
    ) -> None:
        self.fail_draw = fail_draw
        self.receipt_mode = receipt_mode
        self.prepare_calls: list[dict[str, Any]] = []
        self.draw_calls: list[dict[str, Any]] = []
        self.close_count = 0

    async def prepare(
        self,
        fragment_source: str,
        width: int,
        height: int,
        uniform_schema: Any,
    ) -> FakePrepared:
        self.prepare_calls.append(
            {"fragment_source": fragment_source, "width": width, "height": height}
        )
        return FakePrepared(self, fragment_source, width, height)
