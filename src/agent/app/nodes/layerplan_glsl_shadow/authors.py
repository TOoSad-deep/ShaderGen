"""LayerPlan + 直接 GLSL Author shadow 的三个独立有界 Author helper.

三个 Author 都直接读取参考图（多模态），复用统一 LLMGateway、
`invoke_min_author` 的有界调用与结构修复、以及稳定多模态消息构造。
错误全部收敛为安全结果；本模块不接入生产 Graph，也不签发任何
attestation——哈希与身份字段全部由 shaderforge 可信层重算或绑定。

模型输出经可信 Parser 校验为 canonical 语义 mapping 后，由本模块用真实
author/input 身份（参考图/指令/父 Spec 哈希、content_type、refine 的
current_render 与评估上下文、以及 Gateway 记录的 effective
model_ref/prompt_version/实际采样参数）调用
``build_program_spec``/``build_layer_plan`` 装配；返回的
``LayerPlanV1``/``ShaderProgramSpecV1`` 一律是 shaderforge canonical 类型。
真实响应缺少可信 effective 身份时 fail-closed
（``author_identity_unavailable``），绝不记录 "unknown" 或请求假值。

A/B 实验控制：Initial/Refine 的 `layer_plan` 参数是预期控制差异；两臂
尽量使用同一 Prompt 主体、同一模型、同一请求采样参数与同一预算语义。
无 seed 的模型采样、执行顺序和服务端漂移仍是混杂因素，单次运行只具
探索性，不能声称 LayerPlan 是唯一因果变量。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage

from agent.app.contracts.layerplan_glsl_shadow import (
    LayerPlanV1,
    ShaderProgramSpecV1,
    ValidatedIncumbent,
    assemble_layer_plan,
    assemble_program_spec,
    initial_input_context_sha256,
    layer_plan_json_schema,
    parse_layer_plan_semantics,
    parse_program_spec_semantics,
    program_spec_json_schema,
    refine_evaluation_context,
    refine_input_context_sha256,
)
from agent.app.contracts.llm import EffectiveCallIdentity, LLMGateway
from agent.app.messages.structured_multimodal import (
    labeled_image_parts,
    multimodal_human_message,
    text_part,
)
from agent.app.nodes.png_to_shader_min.model_author import (
    MinAuthorCallResult,
    invoke_min_author,
)
from agent.app.prompts.prompt_loader import load_prompt_definition
from shaderforge.program_spec import (
    build_author_identity,
    build_layer_author_identity,
    canonical_json,
    sha256_hex_text,
)

VISUAL_ANALYSIS_PROMPT = load_prompt_definition("layerplan_visual_analysis_v1")
DIRECT_GLSL_INITIAL_PROMPT = load_prompt_definition("direct_glsl_initial_v1")
DIRECT_GLSL_REFINE_PROMPT = load_prompt_definition("direct_glsl_refine_v1")

DEFAULT_PLAN_MAX_OUTPUT_TOKENS = 2048
DEFAULT_SPEC_MAX_OUTPUT_TOKENS = 4096

# 真实响应缺少可信 effective 身份（model_ref/实际采样参数）时的 fail-closed
# 错误码：shadow 绝不接受 "unknown" 身份或请求假值。
AUTHOR_IDENTITY_UNAVAILABLE = "author_identity_unavailable"


def _effective_identity(result: MinAuthorCallResult) -> EffectiveCallIdentity | None:
    """返回 Gateway 记录的可信 effective 身份；缺失或空 model_ref 一律拒绝."""
    identity = result.effective_identity
    if identity is None or not identity.model_ref.strip():
        return None
    return identity


def _sampling_params(identity: EffectiveCallIdentity) -> dict[str, Any]:
    """把 Gateway 记录的 effective 身份转成 author_identity 采样事实.

    只记录实际生效值（例如 kimi 的 temperature=1 与 reasoning_effort），
    绝不回写 ``LLMCallOptions`` 里的请求假值。
    """
    params: dict[str, Any] = {
        "provider": identity.provider,
        "identity_source": identity.model_identity_source,
        "temperature": identity.sampling.temperature,
        "response_format": identity.sampling.response_format,
    }
    if identity.sampling.thinking is not None:
        params["thinking"] = identity.sampling.thinking
    if identity.sampling.reasoning_effort is not None:
        params["reasoning_effort"] = identity.sampling.reasoning_effort
    if identity.sampling.max_output_tokens is not None:
        params["max_output_tokens"] = identity.sampling.max_output_tokens
    return params


@dataclass(frozen=True)
class VisualAnalysisAuthorResult:
    """视觉分析 Author 一次有界调用的安全结果."""

    plan: LayerPlanV1 | None
    call_count: int
    model_ref: str | None
    error_code: str | None
    repaired: bool = False
    latency_ms: int = 0
    total_tokens: int | None = None


@dataclass(frozen=True)
class InitialGLSLAuthorResult:
    """Initial GLSL Author 一次有界调用的安全结果."""

    spec: ShaderProgramSpecV1 | None
    call_count: int
    model_ref: str | None
    error_code: str | None
    repaired: bool = False
    latency_ms: int = 0
    total_tokens: int | None = None


@dataclass(frozen=True)
class RefineGLSLAuthorResult:
    """Refine GLSL Author 一次有界调用的安全结果，父绑定来自可信输入."""

    spec: ShaderProgramSpecV1 | None
    parent_spec_sha256: str | None
    call_count: int
    model_ref: str | None
    error_code: str | None
    repaired: bool = False
    latency_ms: int = 0
    total_tokens: int | None = None


async def run_visual_analysis_author(
    *,
    gateway: LLMGateway,
    reference_image: bytes,
    content_type: str = "image/png",
    user_instruction: str = "",
    observations: Mapping[str, Any] | None = None,
    remaining_calls: int,
    max_output_tokens: int = DEFAULT_PLAN_MAX_OUTPUT_TOKENS,
) -> VisualAnalysisAuthorResult:
    """直接读取参考图，输出可信装配后的 canonical LayerPlanV1（永久 advisory）."""
    schema = layer_plan_json_schema()
    parts: list[dict[str, Any]] = [
        text_part("user_instruction", user_instruction),
    ]
    if observations is not None:
        parts.append(text_part("perception_observations", dict(observations)))
    parts.append(text_part("expected_json_schema", schema))
    parts.extend(labeled_image_parts("reference_image", reference_image, content_type))
    messages: list[BaseMessage] = [
        SystemMessage(content=VISUAL_ANALYSIS_PROMPT.prompt),
        multimodal_human_message(parts),
    ]
    result = await invoke_min_author(
        gateway=gateway,
        messages=messages,
        prompt=VISUAL_ANALYSIS_PROMPT,
        schema=schema,
        parser=parse_layer_plan_semantics,
        remaining_calls=remaining_calls,
        max_output_tokens=max_output_tokens,
    )
    plan: LayerPlanV1 | None = None
    error_code = result.error_code
    identity = _effective_identity(result)
    if isinstance(result.value, dict):
        if identity is None:
            # fail-closed：真实响应缺有效身份时绝不装配 "unknown" LayerPlan。
            error_code = AUTHOR_IDENTITY_UNAVAILABLE
        else:
            plan = assemble_layer_plan(
                result.value,
                reference_sha256=sha256(reference_image).hexdigest(),
                author_identity=build_layer_author_identity(
                    model_ref=identity.model_ref,
                    prompt_version=VISUAL_ANALYSIS_PROMPT.version,
                    instruction_sha256=sha256(
                        user_instruction.encode("utf-8")
                    ).hexdigest(),
                    reference_content_type=content_type,
                    sampling_params=_sampling_params(identity),
                    repair_context_sha256=result.repair_context_sha256,
                ),
                observations_ref=(
                    sha256_hex_text(canonical_json(dict(observations)))
                    if observations is not None
                    else None
                ),
            )
    return VisualAnalysisAuthorResult(
        plan,
        result.call_count,
        result.model_ref,
        error_code,
        result.repaired,
        result.latency_ms,
        result.total_tokens,
    )


def _glsl_spec_parts(
    *,
    user_instruction: str,
    canvas: Mapping[str, int],
    layer_plan: LayerPlanV1 | None,
    schema: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """装配 Initial/Refine 共享文本块；LayerPlan 是预期控制的可选输入."""
    parts: list[dict[str, Any]] = [
        text_part("user_instruction", user_instruction),
        text_part("canvas", dict(canvas)),
    ]
    if layer_plan is not None:
        parts.append(text_part("layer_plan_advisory", layer_plan))
    parts.append(text_part("expected_json_schema", dict(schema)))
    return parts


async def run_initial_glsl_author(
    *,
    gateway: LLMGateway,
    reference_image: bytes,
    content_type: str = "image/png",
    user_instruction: str = "",
    canvas_width: int,
    canvas_height: int,
    layer_plan: LayerPlanV1 | None = None,
    remaining_calls: int,
    max_output_tokens: int = DEFAULT_SPEC_MAX_OUTPUT_TOKENS,
) -> InitialGLSLAuthorResult:
    """直接读取参考图 + 用户意图 + 可选 LayerPlan，输出 canonical ProgramSpec."""
    schema = program_spec_json_schema()
    parts = _glsl_spec_parts(
        user_instruction=user_instruction,
        canvas={"width": canvas_width, "height": canvas_height},
        layer_plan=layer_plan,
        schema=schema,
    )
    parts.extend(labeled_image_parts("reference_image", reference_image, content_type))
    messages: list[BaseMessage] = [
        SystemMessage(content=DIRECT_GLSL_INITIAL_PROMPT.prompt),
        multimodal_human_message(parts),
    ]
    result = await invoke_min_author(
        gateway=gateway,
        messages=messages,
        prompt=DIRECT_GLSL_INITIAL_PROMPT,
        schema=schema,
        parser=lambda text: parse_program_spec_semantics(
            text,
            expected_width=canvas_width,
            expected_height=canvas_height,
        ),
        remaining_calls=remaining_calls,
        max_output_tokens=max_output_tokens,
    )
    spec: ShaderProgramSpecV1 | None = None
    error_code = result.error_code
    identity = _effective_identity(result)
    if isinstance(result.value, dict):
        if identity is None:
            # fail-closed：真实响应缺有效身份时绝不装配 "unknown" Spec。
            error_code = AUTHOR_IDENTITY_UNAVAILABLE
        else:
            spec = assemble_program_spec(
                result.value,
                author_identity=build_author_identity(
                    reference_sha256=sha256(reference_image).hexdigest(),
                    instruction_sha256=sha256(
                        user_instruction.encode("utf-8")
                    ).hexdigest(),
                    model_ref=identity.model_ref,
                    prompt_version=DIRECT_GLSL_INITIAL_PROMPT.version,
                    role="initial",
                    sampling_params=_sampling_params(identity),
                    plan_sha256=(
                        layer_plan.plan_sha256 if layer_plan is not None else None
                    ),
                    reference_content_type=content_type,
                    input_context_sha256=initial_input_context_sha256(
                        reference_content_type=content_type,
                        canvas_width=canvas_width,
                        canvas_height=canvas_height,
                        layer_plan_sha256=(
                            layer_plan.plan_sha256 if layer_plan is not None else None
                        ),
                    ),
                    repair_context_sha256=result.repair_context_sha256,
                ),
            )
    return InitialGLSLAuthorResult(
        spec,
        result.call_count,
        result.model_ref,
        error_code,
        result.repaired,
        result.latency_ms,
        result.total_tokens,
    )


async def run_refine_glsl_author(
    *,
    gateway: LLMGateway,
    reference_image: bytes,
    content_type: str = "image/png",
    current_render: bytes,
    current_render_content_type: str = "image/png",
    user_instruction: str = "",
    incumbent: ValidatedIncumbent,
    layer_plan: LayerPlanV1 | None = None,
    remaining_calls: int,
    max_output_tokens: int = DEFAULT_SPEC_MAX_OUTPUT_TOKENS,
) -> RefineGLSLAuthorResult:
    """基于 validated incumbent 提案新 Spec 并绑定 canonical 父 Spec 哈希."""
    schema = program_spec_json_schema()
    parent = incumbent.program_spec
    parts = _glsl_spec_parts(
        user_instruction=user_instruction,
        canvas={"width": parent.canvas.width, "height": parent.canvas.height},
        layer_plan=layer_plan,
        schema=schema,
    )
    parts.insert(
        2,
        text_part(
            "incumbent",
            {
                "program_spec": parent,
                "mae": incumbent.mae,
                "loss": incumbent.loss,
                "metrics": dict(incumbent.metrics),
                "residual_summary": dict(incumbent.residual_summary),
            },
        ),
    )
    parts.extend(labeled_image_parts("reference_image", reference_image, content_type))
    parts.extend(
        labeled_image_parts(
            "current_render", current_render, current_render_content_type
        )
    )
    messages: list[BaseMessage] = [
        SystemMessage(content=DIRECT_GLSL_REFINE_PROMPT.prompt),
        multimodal_human_message(parts),
    ]
    result = await invoke_min_author(
        gateway=gateway,
        messages=messages,
        prompt=DIRECT_GLSL_REFINE_PROMPT,
        schema=schema,
        parser=lambda text: parse_program_spec_semantics(
            text,
            expected_width=parent.canvas.width,
            expected_height=parent.canvas.height,
        ),
        remaining_calls=remaining_calls,
        max_output_tokens=max_output_tokens,
    )
    spec: ShaderProgramSpecV1 | None = None
    error_code = result.error_code
    identity = _effective_identity(result)
    if isinstance(result.value, dict):
        if identity is None:
            # fail-closed：真实响应缺有效身份时绝不装配 "unknown" Spec。
            error_code = AUTHOR_IDENTITY_UNAVAILABLE
        else:
            spec = assemble_program_spec(
                result.value,
                author_identity=build_author_identity(
                    reference_sha256=sha256(reference_image).hexdigest(),
                    instruction_sha256=sha256(
                        user_instruction.encode("utf-8")
                    ).hexdigest(),
                    model_ref=identity.model_ref,
                    prompt_version=DIRECT_GLSL_REFINE_PROMPT.version,
                    role="refine",
                    sampling_params=_sampling_params(identity),
                    plan_sha256=(
                        layer_plan.plan_sha256 if layer_plan is not None else None
                    ),
                    parent_spec_sha256=parent.spec_sha256,
                    reference_content_type=content_type,
                    input_context_sha256=refine_input_context_sha256(
                        reference_content_type=content_type,
                        current_render_sha256=sha256(current_render).hexdigest(),
                        current_render_content_type=current_render_content_type,
                        evaluation=refine_evaluation_context(incumbent),
                        layer_plan_sha256=(
                            layer_plan.plan_sha256 if layer_plan is not None else None
                        ),
                    ),
                    repair_context_sha256=result.repair_context_sha256,
                ),
            )
    return RefineGLSLAuthorResult(
        spec,
        parent.spec_sha256 if spec is not None else None,
        result.call_count,
        result.model_ref,
        error_code,
        result.repaired,
        result.latency_ms,
        result.total_tokens,
    )


__all__ = [
    "AUTHOR_IDENTITY_UNAVAILABLE",
    "DIRECT_GLSL_INITIAL_PROMPT",
    "DIRECT_GLSL_REFINE_PROMPT",
    "VISUAL_ANALYSIS_PROMPT",
    "InitialGLSLAuthorResult",
    "RefineGLSLAuthorResult",
    "VisualAnalysisAuthorResult",
    "run_initial_glsl_author",
    "run_refine_glsl_author",
    "run_visual_analysis_author",
]
