"""LayerPlan + 直接 GLSL Author shadow 的薄 adapter、可信 Parser 与 Author 单元测试.

Agent 侧只保留模型 JSON schema 与薄 adapter；最终 LayerPlanV1/
ShaderProgramSpecV1 一律是 shaderforge.program_spec 的 canonical 类型，
哈希与 author/input 身份绑定全部由 canonical 层完成。
"""

from __future__ import annotations

import inspect
import json
from hashlib import sha256
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

import agent.app.contracts.layerplan_glsl_shadow as shadow_contract
from agent.app.config.model_config import SHADER_GEN_MODEL_NAME
from agent.app.contracts.layerplan_glsl_shadow import (
    LayerPlanGlslAuthorParseError,
    ValidatedIncumbent,
    assemble_layer_plan,
    assemble_program_spec,
    layer_plan_json_schema,
    parse_layer_plan_semantics,
    parse_program_spec_semantics,
    program_spec_json_schema,
)
from agent.app.contracts.llm import (
    EffectiveCallIdentity,
    EffectiveSamplingParams,
    LLMCallOptions,
    LLMResponse,
    ThinkingMode,
    TokenUsage,
)
from agent.app.nodes.layerplan_glsl_shadow.authors import (
    DIRECT_GLSL_INITIAL_PROMPT,
    DIRECT_GLSL_REFINE_PROMPT,
    VISUAL_ANALYSIS_PROMPT,
    run_initial_glsl_author,
    run_refine_glsl_author,
    run_visual_analysis_author,
)
from shaderforge.program_spec import (
    LayerPlanV1,
    ProgramSpecParseError,
    ShaderProgramSpecV1,
    build_author_identity,
    build_layer_author_identity,
    recompute_plan_sha256,
    recompute_spec_sha256,
)

_IMAGE = b"fake-reference-png"
_RENDER = b"fake-current-render-png"


def _trusted_identity(
    *,
    temperature: float = 0.0,
    thinking: ThinkingMode | None = "off",
    reasoning_effort: str | None = None,
) -> EffectiveCallIdentity:
    """fake gateway 显式声明的可信 effective 身份（非请求假值）."""
    return EffectiveCallIdentity(
        provider="fake",
        model_ref="fake-shadow-model",
        model_identity_source="response_metadata",
        sampling=EffectiveSamplingParams(
            temperature=temperature,
            thinking=thinking,
            reasoning_effort=reasoning_effort,
            response_format="json_object",
            max_output_tokens=4096,
        ),
    )


class _FakeGateway:
    """按队列返回文本并记录每次调用的 messages/options.

    默认显式携带可信 effective 身份；``with_identity=False`` 模拟真实响应
    缺失有效身份，用于验证 shadow fail-closed。
    """

    def __init__(
        self,
        *responses: str,
        with_identity: bool = True,
        identity: EffectiveCallIdentity | None = None,
    ) -> None:
        self._responses = list(responses)
        self._with_identity = with_identity
        self._identity = identity or _trusted_identity()
        self.calls: list[tuple[list[BaseMessage], LLMCallOptions]] = []

    async def ainvoke(
        self,
        messages: Any,
        options: LLMCallOptions,
    ) -> LLMResponse:
        self.calls.append((list(messages), options))
        text = self._responses.pop(0)
        return LLMResponse(
            message=AIMessage(content=text),
            text=text,
            reasoning_content=None,
            model_ref="fake-shadow-model",
            latency_ms=1,
            usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
            effective_identity=self._identity if self._with_identity else None,
        )


def _plan_payload() -> dict[str, Any]:
    return {
        "schema_version": "layer_plan_v1",
        "layers": [
            {
                "layer_id": "bg",
                "role": "background",
                "z_index": 0,
                "region": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                "dominant_colors": [[0.1, 0.1, 0.2, 1.0]],
                "confidence": 0.9,
            },
            {
                "layer_id": "orb",
                "role": "subject",
                "z_index": 1,
                "region": {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5},
                "dominant_colors": [[0.9, 0.4, 0.5, 1.0]],
                "confidence": 0.8,
                "notes": "粉色球体主体",
            },
        ],
    }


_CANONICAL_DECLARATIONS = (
    "precision mediump float;\n"
    "varying vec2 v_uv;\n"
    "uniform sampler2D u_image;\n"
    "uniform vec2 u_resolution;\n"
    "uniform float u_time;\n"
)


def _spec_payload() -> dict[str, Any]:
    return {
        "schema_version": "shader_program_spec_v1",
        "fragment_source": (
            _CANONICAL_DECLARATIONS + "uniform float u_gain;\n"
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
        "canvas": {"width": 64, "height": 64},
        "renderer_contract_id": "webgl1_static_no_texture_v1",
    }


def _plan() -> LayerPlanV1:
    semantics = parse_layer_plan_semantics(json.dumps(_plan_payload()))
    return assemble_layer_plan(
        semantics,
        reference_sha256=sha256(_IMAGE).hexdigest(),
        author_identity=build_layer_author_identity(
            model_ref="fake-shadow-model",
            prompt_version=VISUAL_ANALYSIS_PROMPT.version,
        ),
    )


def _spec() -> ShaderProgramSpecV1:
    semantics = parse_program_spec_semantics(
        json.dumps(_spec_payload()), expected_width=64, expected_height=64
    )
    return assemble_program_spec(
        semantics,
        author_identity=build_author_identity(
            reference_sha256=sha256(_IMAGE).hexdigest(),
            instruction_sha256=sha256(b"make it softer").hexdigest(),
            model_ref="fake-shadow-model",
            prompt_version=DIRECT_GLSL_INITIAL_PROMPT.version,
            role="initial",
        ),
    )


def _incumbent() -> ValidatedIncumbent:
    return ValidatedIncumbent(
        program_spec=_spec(),
        mae=0.12,
        loss=0.2,
        metrics={"mae": 0.12},
        residual_summary={"bias_rgb": [0.01, -0.02, 0.0]},
    )


def _human_parts(gateway: _FakeGateway, call: int = 0) -> list[dict[str, Any]]:
    message = gateway.calls[call][0][1]
    assert isinstance(message, HumanMessage)
    return list(message.content)  # type: ignore[arg-type]


def _image_labels(parts: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for index, part in enumerate(parts):
        if part.get("type") == "image_url":
            assert part["image_url"]["url"].startswith("data:image/png;base64,")
            labels.append(str(parts[index - 1].get("text", "")).rstrip("："))
    return labels


# --- 单一契约：Agent 不再定义第二个 LayerPlanV1/ShaderProgramSpecV1 ---


def test_agent_contract_defines_no_duplicate_spec_or_plan_types() -> None:
    assert shadow_contract.LayerPlanV1 is LayerPlanV1
    assert shadow_contract.ShaderProgramSpecV1 is ShaderProgramSpecV1
    for name, obj in inspect.getmembers(shadow_contract, inspect.isclass):
        if name in {"LayerPlanV1", "ShaderProgramSpecV1"}:
            assert obj.__module__.startswith("shaderforge."), name
    assert type(_spec()) is ShaderProgramSpecV1
    assert type(_plan()) is LayerPlanV1


def test_validated_incumbent_requires_canonical_spec() -> None:
    with pytest.raises(TypeError):
        ValidatedIncumbent(program_spec=object(), mae=0.1, loss=0.2)  # type: ignore[arg-type]


# --- 可信 Parser：LayerPlan ---


def test_parse_layer_plan_valid() -> None:
    semantics = parse_layer_plan_semantics(json.dumps(_plan_payload()))

    assert semantics["schema_version"] == "layer_plan_v1"
    assert len(semantics["layers"]) == 2
    assert semantics["layers"][1]["role"] == "subject"


@pytest.mark.parametrize(
    "key",
    ["validation_attestation", "plan_sha256", "reference_sha256", "author_identity"],
)
def test_parse_layer_plan_rejects_trusted_fields(key: str) -> None:
    payload = _plan_payload()
    payload[key] = "a" * 64

    with pytest.raises(LayerPlanGlslAuthorParseError) as raised:
        parse_layer_plan_semantics(json.dumps(payload))
    assert raised.value.code == "untrusted_attestation_or_hash_field"


def test_parse_layer_plan_rejects_nested_trusted_field() -> None:
    payload = _plan_payload()
    payload["layers"][0]["notes_hash"] = "b" * 64

    with pytest.raises(LayerPlanGlslAuthorParseError) as raised:
        parse_layer_plan_semantics(json.dumps(payload))
    assert raised.value.code == "untrusted_attestation_or_hash_field"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["layers"][0].update(role="skybox"),
        lambda p: p["layers"][0]["region"].update(x=0.75, width=0.5),
        lambda p: p.update(layers=p["layers"] * 5),
        lambda p: p["layers"][0].update(extra_field=1),
    ],
)
def test_parse_layer_plan_rejects_invalid_semantics(mutate: Any) -> None:
    payload = _plan_payload()
    mutate(payload)

    with pytest.raises(LayerPlanGlslAuthorParseError) as raised:
        parse_layer_plan_semantics(json.dumps(payload))
    assert raised.value.code == "invalid_layer_plan_json"
    assert raised.value.details


def test_parse_layer_plan_rejects_non_strict_json() -> None:
    with pytest.raises(LayerPlanGlslAuthorParseError) as raised:
        parse_layer_plan_semantics('{"schema_version": NaN}')
    assert raised.value.code == "invalid_layer_plan_json"


# --- 可信 Parser：ProgramSpec ---


def test_parse_program_spec_valid() -> None:
    semantics = parse_program_spec_semantics(
        json.dumps(_spec_payload()), expected_width=64, expected_height=64
    )

    assert semantics["schema_version"] == "shader_program_spec_v1"
    assert semantics["uniform_values"]["u_gain"] == 0.5
    assert semantics["tunable_manifest"][0]["path"] == "u_gain"


@pytest.mark.parametrize(
    "key",
    ["validation_attestation", "source_sha256", "spec_sha256", "author_identity"],
)
def test_parse_program_spec_rejects_attestation_and_hash_fields(key: str) -> None:
    payload = _spec_payload()
    payload[key] = "c" * 64

    with pytest.raises(LayerPlanGlslAuthorParseError) as raised:
        parse_program_spec_semantics(
            json.dumps(payload), expected_width=64, expected_height=64
        )
    assert raised.value.code == "untrusted_attestation_or_hash_field"


def test_parse_program_spec_rejects_canvas_mismatch() -> None:
    with pytest.raises(LayerPlanGlslAuthorParseError) as raised:
        parse_program_spec_semantics(
            json.dumps(_spec_payload()), expected_width=128, expected_height=64
        )
    assert raised.value.code == "program_spec_canvas_mismatch"


@pytest.mark.parametrize(
    "source",
    [
        # 额外 sampler 声明：只放行 uniform sampler2D u_image;
        _CANONICAL_DECLARATIONS
        + "uniform sampler2D u_tex;\n"
        + "void main(){gl_FragColor=vec4(1.0);}",
        # 兼容 sampler 仅声明不可采样：texture2D 调用禁止
        _CANONICAL_DECLARATIONS + "void main(){gl_FragColor=texture2D(u_image, v_uv);}",
        # 扩展禁止
        "#extension GL_OES_standard_derivatives : enable\n"
        + _CANONICAL_DECLARATIONS
        + "void main(){gl_FragColor=vec4(1.0);}",
        # 缺 canonical 兼容声明（无 v_uv）
        "precision mediump float;\n"
        "uniform sampler2D u_image;\n"
        "uniform vec2 u_resolution;\n"
        "uniform float u_time;\n"
        "void main(){gl_FragColor=vec4(1.0);}",
        # 缺 precision 与全部声明
        "void main(){gl_FragColor=vec4(1.0);}",
    ],
)
def test_parse_program_spec_rejects_texture_and_extension(source: str) -> None:
    payload = _spec_payload()
    payload["fragment_source"] = source

    with pytest.raises(LayerPlanGlslAuthorParseError) as raised:
        parse_program_spec_semantics(
            json.dumps(payload), expected_width=64, expected_height=64
        )
    assert raised.value.code == "glsl_renderer_contract_violation"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(uniform_values={}),
        lambda p: p["uniform_values"].update(u_gain=1.5),
        lambda p: p["uniform_values"].update(u_gain=[0.5, 0.5]),
        lambda p: p["tunable_manifest"][0].update(path="u_missing"),
        lambda p: p["tunable_manifest"][0].update(maximum=2.0),
        lambda p: p["uniform_schema"]["u_gain"].update(default=2.0),
    ],
)
def test_parse_program_spec_rejects_inconsistent_uniforms(mutate: Any) -> None:
    payload = _spec_payload()
    mutate(payload)

    with pytest.raises(LayerPlanGlslAuthorParseError) as raised:
        parse_program_spec_semantics(
            json.dumps(payload), expected_width=64, expected_height=64
        )
    assert raised.value.code == "invalid_program_spec_json"


# --- 可信装配：canonical 哈希重算与真实 author/input 身份绑定 ---


def test_assemble_layer_plan_binds_input_identity_and_recomputes_hash() -> None:
    semantics = parse_layer_plan_semantics(json.dumps(_plan_payload()))
    identity = build_layer_author_identity(
        model_ref="fake-shadow-model", prompt_version="v1"
    )
    plan = assemble_layer_plan(
        semantics,
        reference_sha256=sha256(_IMAGE).hexdigest(),
        author_identity=identity,
    )

    assert type(plan) is LayerPlanV1
    assert plan.reference_sha256 == sha256(_IMAGE).hexdigest()
    assert plan.plan_sha256 == recompute_plan_sha256(plan)
    other = assemble_layer_plan(
        semantics,
        reference_sha256=sha256(b"other").hexdigest(),
        author_identity=identity,
    )
    assert other.plan_sha256 != plan.plan_sha256


def test_assemble_program_spec_binds_parent_and_uses_canonical_hash() -> None:
    semantics = parse_program_spec_semantics(
        json.dumps(_spec_payload()), expected_width=64, expected_height=64
    )
    with pytest.raises(ProgramSpecParseError) as raised:
        assemble_program_spec(
            semantics,
            author_identity=build_author_identity(
                reference_sha256=sha256(_IMAGE).hexdigest(),
                instruction_sha256=sha256(b"i").hexdigest(),
                model_ref="fake-shadow-model",
                prompt_version="v1",
                role="refine",
            ),
        )
    assert raised.value.code == "missing_parent_spec"

    spec = assemble_program_spec(
        semantics,
        author_identity=build_author_identity(
            reference_sha256=sha256(_IMAGE).hexdigest(),
            instruction_sha256=sha256(b"i").hexdigest(),
            model_ref="fake-shadow-model",
            prompt_version="v1",
            role="refine",
            parent_spec_sha256="d" * 64,
        ),
    )

    assert type(spec) is ShaderProgramSpecV1
    assert spec.validation_attestation is None
    assert spec.spec_sha256 == recompute_spec_sha256(spec)
    assert (
        spec.source_sha256 == sha256(spec.fragment_source.encode("utf-8")).hexdigest()
    )
    assert spec.author_identity.reference_sha256 == sha256(_IMAGE).hexdigest()
    assert spec.author_identity.parent_spec_sha256 == "d" * 64


def test_json_schemas_are_bounded_and_strict() -> None:
    plan_schema = layer_plan_json_schema()
    spec_schema = program_spec_json_schema()

    assert "layers" in json.dumps(plan_schema)
    spec_text = json.dumps(spec_schema)
    assert "fragment_source" in spec_text
    assert "uniform_schema" in spec_text
    assert "validation_attestation" not in spec_schema["properties"]
    assert "spec_sha256" not in spec_schema["properties"]


# --- Author：多模态输入与可信绑定 ---


@pytest.mark.anyio
async def test_visual_analysis_author_reads_reference_image() -> None:
    gateway = _FakeGateway(json.dumps(_plan_payload()))

    result = await run_visual_analysis_author(
        gateway=gateway,
        reference_image=_IMAGE,
        user_instruction="make it softer",
        observations={"palette": ["pink"]},
        remaining_calls=2,
    )

    assert result.error_code is None
    assert result.call_count == 1
    assert result.plan is not None
    assert type(result.plan) is LayerPlanV1
    assert result.plan.reference_sha256 == sha256(_IMAGE).hexdigest()
    assert result.plan.observations_ref is not None
    assert result.plan.author_identity.model_ref == "fake-shadow-model"
    parts = _human_parts(gateway)
    assert _image_labels(parts) == ["reference_image"]
    assert any("perception_observations" in str(p.get("text", "")) for p in parts)


@pytest.mark.anyio
async def test_initial_author_reads_reference_image_and_instruction() -> None:
    gateway = _FakeGateway(json.dumps(_spec_payload()))

    result = await run_initial_glsl_author(
        gateway=gateway,
        reference_image=_IMAGE,
        user_instruction="make it softer",
        canvas_width=64,
        canvas_height=64,
        remaining_calls=2,
    )

    assert result.error_code is None
    assert result.spec is not None
    assert type(result.spec) is ShaderProgramSpecV1
    identity = result.spec.author_identity
    assert identity.role == "initial"
    assert identity.reference_sha256 == sha256(_IMAGE).hexdigest()
    assert identity.instruction_sha256 == sha256(b"make it softer").hexdigest()
    assert identity.parent_spec_sha256 is None
    parts = _human_parts(gateway)
    assert _image_labels(parts) == ["reference_image"]
    assert any("user_instruction" in str(p.get("text", "")) for p in parts)
    options = gateway.calls[0][1]
    assert options.model_ref == SHADER_GEN_MODEL_NAME
    assert options.temperature == 0
    assert options.response_format == "json_object"


@pytest.mark.anyio
async def test_refine_author_reads_render_and_incumbent_and_binds_parent() -> None:
    gateway = _FakeGateway(json.dumps(_spec_payload()))
    incumbent = _incumbent()

    result = await run_refine_glsl_author(
        gateway=gateway,
        reference_image=_IMAGE,
        current_render=_RENDER,
        user_instruction="make it softer",
        incumbent=incumbent,
        remaining_calls=2,
    )

    assert result.error_code is None
    assert result.spec is not None
    assert type(result.spec) is ShaderProgramSpecV1
    assert result.parent_spec_sha256 == incumbent.program_spec.spec_sha256
    identity = result.spec.author_identity
    assert identity.role == "refine"
    assert identity.parent_spec_sha256 == incumbent.program_spec.spec_sha256
    parts = _human_parts(gateway)
    assert _image_labels(parts) == ["reference_image", "current_render"]
    assert any("incumbent" in str(p.get("text", "")) for p in parts)
    assert any("residual_summary" in str(p.get("text", "")) for p in parts)


# --- A/B：预期控制差异是 LayerPlan 注入 ---


def _without_plan_part(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        part
        for part in parts
        if not str(part.get("text", "")).startswith("layer_plan_advisory")
    ]


@pytest.mark.anyio
async def test_ab_arms_differ_only_by_layer_plan_injection() -> None:
    plan = _plan()
    gateway_a = _FakeGateway(json.dumps(_spec_payload()))
    gateway_b = _FakeGateway(json.dumps(_spec_payload()))

    await run_initial_glsl_author(
        gateway=gateway_a,
        reference_image=_IMAGE,
        user_instruction="make it softer",
        canvas_width=64,
        canvas_height=64,
        layer_plan=None,
        remaining_calls=2,
    )
    await run_initial_glsl_author(
        gateway=gateway_b,
        reference_image=_IMAGE,
        user_instruction="make it softer",
        canvas_width=64,
        canvas_height=64,
        layer_plan=plan,
        remaining_calls=2,
    )

    system_a, system_b = gateway_a.calls[0][0][0], gateway_b.calls[0][0][0]
    assert system_a.content == system_b.content
    assert gateway_a.calls[0][1] == gateway_b.calls[0][1]
    parts_a = _human_parts(gateway_a)
    parts_b = _human_parts(gateway_b)
    assert not any(
        str(p.get("text", "")).startswith("layer_plan_advisory") for p in parts_a
    )
    assert any(
        str(p.get("text", "")).startswith("layer_plan_advisory") for p in parts_b
    )
    assert parts_a == _without_plan_part(parts_b)


@pytest.mark.anyio
async def test_refine_ab_arms_differ_only_by_layer_plan_injection() -> None:
    plan = _plan()
    incumbent = _incumbent()
    gateway_a = _FakeGateway(json.dumps(_spec_payload()))
    gateway_b = _FakeGateway(json.dumps(_spec_payload()))

    await run_refine_glsl_author(
        gateway=gateway_a,
        reference_image=_IMAGE,
        current_render=_RENDER,
        incumbent=incumbent,
        layer_plan=None,
        remaining_calls=2,
    )
    await run_refine_glsl_author(
        gateway=gateway_b,
        reference_image=_IMAGE,
        current_render=_RENDER,
        incumbent=incumbent,
        layer_plan=plan,
        remaining_calls=2,
    )

    assert gateway_a.calls[0][0][0].content == gateway_b.calls[0][0][0].content
    assert gateway_a.calls[0][1] == gateway_b.calls[0][1]
    assert _human_parts(gateway_a) == _without_plan_part(_human_parts(gateway_b))


@pytest.mark.anyio
async def test_arm_b_spec_identity_binds_layer_plan_hash() -> None:
    plan = _plan()
    gateway = _FakeGateway(json.dumps(_spec_payload()))

    result = await run_initial_glsl_author(
        gateway=gateway,
        reference_image=_IMAGE,
        user_instruction="make it softer",
        canvas_width=64,
        canvas_height=64,
        layer_plan=plan,
        remaining_calls=2,
    )

    assert result.spec is not None
    assert result.spec.author_identity.plan_sha256 == plan.plan_sha256


# --- 预算与结构修复计数 ---


@pytest.mark.anyio
async def test_budget_exhausted_never_calls_gateway() -> None:
    gateway = _FakeGateway()

    result = await run_initial_glsl_author(
        gateway=gateway,
        reference_image=_IMAGE,
        canvas_width=64,
        canvas_height=64,
        remaining_calls=0,
    )

    assert result.error_code == "llm_budget_exhausted"
    assert result.call_count == 0
    assert result.spec is None
    assert gateway.calls == []


@pytest.mark.anyio
async def test_single_remaining_call_disables_repair() -> None:
    bad = _plan_payload() | {"plan_sha256": "e" * 64}
    gateway = _FakeGateway(json.dumps(bad))

    result = await run_visual_analysis_author(
        gateway=gateway,
        reference_image=_IMAGE,
        remaining_calls=1,
    )

    assert result.error_code == "untrusted_attestation_or_hash_field"
    assert result.call_count == 1
    assert result.plan is None
    assert len(gateway.calls) == 1


@pytest.mark.anyio
async def test_repair_recovers_attestation_laden_output() -> None:
    bad = _plan_payload() | {"validation_attestation": {"verdict": "pass"}}
    gateway = _FakeGateway(json.dumps(bad), json.dumps(_plan_payload()))

    result = await run_visual_analysis_author(
        gateway=gateway,
        reference_image=_IMAGE,
        remaining_calls=5,
    )

    assert result.error_code is None
    assert result.repaired is True
    assert result.call_count == 2
    assert result.plan is not None
    assert result.plan.author_identity.repair_context_sha256 is not None
    assert len(gateway.calls) == 2


@pytest.mark.anyio
async def test_failed_repair_converges_to_error_code() -> None:
    bad = json.dumps(_spec_payload() | {"spec_sha256": "f" * 64})
    gateway = _FakeGateway(bad, bad)

    result = await run_initial_glsl_author(
        gateway=gateway,
        reference_image=_IMAGE,
        canvas_width=64,
        canvas_height=64,
        remaining_calls=5,
    )

    assert result.error_code == "untrusted_attestation_or_hash_field"
    assert result.call_count == 2
    assert result.spec is None


# --- Prompt 装配 ---


def test_prompt_definitions_are_versioned_and_bind_contract() -> None:
    assert VISUAL_ANALYSIS_PROMPT.version
    assert DIRECT_GLSL_INITIAL_PROMPT.version
    assert DIRECT_GLSL_REFINE_PROMPT.version
    assert "layer_plan_v1" in VISUAL_ANALYSIS_PROMPT.prompt
    assert "webgl1_static_no_texture_v1" in DIRECT_GLSL_INITIAL_PROMPT.prompt
    assert "incumbent" in DIRECT_GLSL_REFINE_PROMPT.prompt
    for prompt in (
        VISUAL_ANALYSIS_PROMPT,
        DIRECT_GLSL_INITIAL_PROMPT,
        DIRECT_GLSL_REFINE_PROMPT,
    ):
        assert "attestation" in prompt.prompt


# --- 实际生效身份：fail-closed 与 effective 采样事实 ---


@pytest.mark.anyio
async def test_initial_author_fails_closed_without_effective_identity() -> None:
    gateway = _FakeGateway(json.dumps(_spec_payload()), with_identity=False)

    result = await run_initial_glsl_author(
        gateway=gateway,
        reference_image=_IMAGE,
        user_instruction="make it softer",
        canvas_width=64,
        canvas_height=64,
        remaining_calls=2,
    )

    assert result.error_code == "author_identity_unavailable"
    assert result.spec is None, "缺有效身份时绝不装配 unknown Spec"


@pytest.mark.anyio
async def test_refine_author_fails_closed_without_effective_identity() -> None:
    gateway = _FakeGateway(json.dumps(_spec_payload()), with_identity=False)

    result = await run_refine_glsl_author(
        gateway=gateway,
        reference_image=_IMAGE,
        current_render=_RENDER,
        user_instruction="make it softer",
        incumbent=_incumbent(),
        remaining_calls=2,
    )

    assert result.error_code == "author_identity_unavailable"
    assert result.spec is None


@pytest.mark.anyio
async def test_visual_analysis_fails_closed_without_effective_identity() -> None:
    gateway = _FakeGateway(json.dumps(_plan_payload()), with_identity=False)

    result = await run_visual_analysis_author(
        gateway=gateway,
        reference_image=_IMAGE,
        user_instruction="make it softer",
        remaining_calls=2,
    )

    assert result.error_code == "author_identity_unavailable"
    assert result.plan is None, "缺有效身份时绝不装配 unknown LayerPlan"


@pytest.mark.anyio
async def test_author_identity_records_effective_not_requested_params() -> None:
    """kimi 风格：请求 temperature=0/thinking=off，实际 temperature=1+effort."""
    kimi_style = _trusted_identity(
        temperature=1.0, thinking=None, reasoning_effort="low"
    )
    gateway = _FakeGateway(json.dumps(_spec_payload()), identity=kimi_style)

    result = await run_initial_glsl_author(
        gateway=gateway,
        reference_image=_IMAGE,
        user_instruction="make it softer",
        canvas_width=64,
        canvas_height=64,
        remaining_calls=2,
    )

    assert gateway.calls[0][1].temperature == 0, "请求值仍是 temperature=0"
    assert result.spec is not None
    params = result.spec.author_identity.sampling_params
    assert params["temperature"] == 1.0, "身份必须记录实际生效 temperature"
    assert params["reasoning_effort"] == "low"
    assert "thinking" not in params, "未生效的 thinking 不得写入身份"
    assert params["provider"] == "fake"
    assert params["identity_source"] == "response_metadata"
    assert params["response_format"] == "json_object"


# --- 输入身份绑定：content_type / current_render / 评估上下文 / 指令 ---


@pytest.mark.anyio
async def test_initial_spec_hash_binds_content_type() -> None:
    specs = []
    for content_type in ("image/png", "image/jpeg"):
        gateway = _FakeGateway(json.dumps(_spec_payload()))
        result = await run_initial_glsl_author(
            gateway=gateway,
            reference_image=_IMAGE,
            content_type=content_type,
            user_instruction="make it softer",
            canvas_width=64,
            canvas_height=64,
            remaining_calls=2,
        )
        assert result.spec is not None
        specs.append(result.spec)

    assert specs[0].author_identity.reference_content_type == "image/png"
    assert specs[1].author_identity.reference_content_type == "image/jpeg"
    assert specs[0].spec_sha256 != specs[1].spec_sha256
    assert specs[0].author_identity.input_context_sha256 is not None


@pytest.mark.anyio
async def test_refine_spec_hash_binds_current_render_and_evaluation() -> None:
    incumbent = _incumbent()

    async def _refine(render: bytes, inc: Any = incumbent) -> Any:
        gateway = _FakeGateway(json.dumps(_spec_payload()))
        result = await run_refine_glsl_author(
            gateway=gateway,
            reference_image=_IMAGE,
            current_render=render,
            user_instruction="make it softer",
            incumbent=inc,
            remaining_calls=2,
        )
        assert result.spec is not None
        return result.spec

    base = await _refine(_RENDER)
    other_render = await _refine(b"other-current-render")
    other_eval = await _refine(
        _RENDER,
        ValidatedIncumbent(
            program_spec=incumbent.program_spec,
            mae=0.99,
            loss=incumbent.loss,
            metrics=dict(incumbent.metrics),
            residual_summary=dict(incumbent.residual_summary),
            metric_version=incumbent.metric_version,
        ),
    )

    assert base.author_identity.input_context_sha256 is not None
    assert base.spec_sha256 != other_render.spec_sha256, (
        "current_render 必须绑定进 spec 哈希"
    )
    assert base.spec_sha256 != other_eval.spec_sha256, "评估上下文必须绑定进 spec 哈希"


@pytest.mark.anyio
async def test_layer_plan_hash_binds_instruction_and_sampling_identity() -> None:
    plans = []
    for instruction in ("make it softer", "make it sharper"):
        gateway = _FakeGateway(json.dumps(_plan_payload()))
        result = await run_visual_analysis_author(
            gateway=gateway,
            reference_image=_IMAGE,
            user_instruction=instruction,
            remaining_calls=2,
        )
        assert result.plan is not None
        plans.append(result.plan)

    identity = plans[0].author_identity
    assert identity.instruction_sha256 == sha256(b"make it softer").hexdigest()
    assert identity.reference_content_type == "image/png"
    assert identity.sampling_params is not None
    assert identity.sampling_params["temperature"] == 0.0
    assert plans[0].plan_sha256 != plans[1].plan_sha256, (
        "instruction 必须绑定进 plan 哈希"
    )
