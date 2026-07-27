"""固定 fake LLM + 真实 Playwright WebGL1 的 shadow 全 runner 链集成验收.

覆盖 canonical parse → 静态安全校验 → runner（真实 prepare+draw → receipt →
attestation → 真实 metric）→ 私有 evidence 写入与 verify_shadow_run 的完整
链路，证明各环节在真实 Chromium 渲染下仍 fail-closed 且内容寻址可复验。
"""

from __future__ import annotations

import json
from hashlib import sha256
from io import BytesIO
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from PIL import Image

from agent.app.contracts.llm import (
    EffectiveCallIdentity,
    EffectiveSamplingParams,
    LLMCallOptions,
    LLMResponse,
    TokenUsage,
)
from agent.app.services.layerplan_glsl_shadow import (
    LayerPlanGlslShadowRunner,
    ShadowABConfig,
    ShadowEvidenceError,
    verify_shadow_run,
    write_shadow_run,
)
from shaderforge.program_spec import is_executable
from shaderforge.rendering import PlaywrightWebGL1Renderer

CANVAS = 64


def _reference_png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (CANVAS, CANVAS), (128, 128, 128)).save(buffer, "PNG")
    return buffer.getvalue()


def _plan_payload() -> str:
    return json.dumps(
        {
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
    )


def _spec_payload() -> str:
    return json.dumps(
        {
            "schema_version": "shader_program_spec_v1",
            "fragment_source": (
                "precision mediump float;\n"
                "varying vec2 v_uv;\n"
                "uniform sampler2D u_image;\n"
                "uniform vec2 u_resolution;\n"
                "uniform float u_time;\n"
                "uniform float u_gain;\n"
                "void main(){gl_FragColor=vec4(vec3(u_gain),1.0);}\n"
            ),
            "uniform_schema": {
                "u_gain": {
                    "type": "float",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.5,
                }
            },
            "uniform_values": {"u_gain": 0.5},
            "tunable_manifest": [
                {
                    "path": "u_gain",
                    "type": "float",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "step": 0.01,
                }
            ],
            "canvas": {"width": CANVAS, "height": CANVAS},
            "renderer_contract_id": "webgl1_static_no_texture_v1",
        }
    )


class _FakeGateway:
    """确定性 fake LLM：按 System Prompt 角色分发固定响应.

    显式携带可信 effective 身份（fake provider、非请求假值），与真实
    Gateway 记录的 effective identity 形状一致。
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def ainvoke(
        self,
        messages: Any,
        options: LLMCallOptions,
    ) -> LLMResponse:
        system_text = str(messages[0].content)
        self.calls.append(system_text)
        text = _plan_payload() if "视觉分析" in system_text else _spec_payload()
        return LLMResponse(
            message=AIMessage(content=text),
            text=text,
            reasoning_content=None,
            model_ref="fake-shadow-model",
            latency_ms=1,
            usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            effective_identity=EffectiveCallIdentity(
                provider="fake",
                model_ref="fake-shadow-model",
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


@pytest.mark.anyio
async def test_full_runner_chain_with_real_renderer_and_private_evidence(
    tmp_path: Any,
) -> None:
    gateway = _FakeGateway()
    config = ShadowABConfig(
        direct_author_llm_budget=2,
        refine_budget_per_arm=0,
        plan_llm_budget=1,
        canvas_width=CANVAS,
        canvas_height=CANVAS,
    )
    async with PlaywrightWebGL1Renderer() as renderer:
        runner = LayerPlanGlslShadowRunner(
            gateway=gateway,
            renderer=renderer,
            config=config,
        )
        result = await runner.run(_reference_png(), instruction="match the gray square")

    assert result.status == "ok"
    assert result.layer_plan is not None
    for arm in result.arms:
        assert arm.status == "ok"
        best = arm.current_best
        assert best is not None
        # 真实 draw 后的 attestation 必须匹配且可执行（含 receipt 绑定）。
        assert best.spec.validation_attestation is not None
        assert is_executable(best.spec)
        assert best.metrics["metric_version"] == "min_scene_composite_v3"
        # author 身份必须记录 effective 采样事实与 content_type 绑定。
        identity = best.spec.author_identity
        assert identity.sampling_params["provider"] == "fake"
        assert identity.reference_content_type == "image/png"
        assert identity.input_context_sha256 is not None
        assert identity.model_ref == "fake-shadow-model"

    run_dir = write_shadow_run(result, tmp_path)
    payload = verify_shadow_run(run_dir)
    assert payload["status"] == "ok"
    assert payload["evaluation"]["metric_version"] == "min_scene_composite_v3"

    # 篡改任一证据文件即校验失败（全链 fail-closed 收口）。
    target = next(run_dir.glob("arms/*/candidates/*/render.png"))
    data = bytearray(target.read_bytes())
    data[-1] ^= 0xFF
    target.write_bytes(bytes(data))
    with pytest.raises(ShadowEvidenceError):
        verify_shadow_run(run_dir)


@pytest.mark.anyio
async def test_run_id_binds_reference_instruction_and_config(tmp_path: Any) -> None:
    from agent.app.services.layerplan_glsl_shadow import shadow_run_id

    gateway = _FakeGateway()
    config = ShadowABConfig(
        direct_author_llm_budget=2,
        refine_budget_per_arm=0,
        plan_llm_budget=1,
        canvas_width=CANVAS,
        canvas_height=CANVAS,
    )
    async with PlaywrightWebGL1Renderer() as renderer:
        runner = LayerPlanGlslShadowRunner(
            gateway=gateway,
            renderer=renderer,
            config=config,
        )
        result = await runner.run(_reference_png(), instruction="match")
    run_dir = write_shadow_run(result, tmp_path)
    assert run_dir.name == shadow_run_id(result)
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["reference_sha256"] == sha256(_reference_png()).hexdigest()
    assert report["config_fingerprint"] == config.fingerprint()
