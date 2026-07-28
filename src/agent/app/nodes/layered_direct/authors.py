"""Default direct layered GLSL Initial/Refine Author helpers.

两个 helper 复用当前通用有界调用/结构修复、Gateway 的 effective identity，
并将模型 JSON 交给 layered-spec 可信层装配；不会输出或信任模型自报身份。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from langchain_core.messages import SystemMessage

from agent.app.contracts.layered_direct_glsl import (
    LayeredShaderSpecV1,
    LayerPatchV1,
    assemble_layer_patch,
    assemble_layered_shader_spec,
    layer_patch_json_schema,
    layered_shader_spec_json_schema,
    parse_layer_patch_semantics,
    parse_layered_shader_spec_semantics,
)
from agent.app.contracts.llm import EffectiveCallIdentity, LLMGateway
from agent.app.messages.structured_multimodal import (
    labeled_image_parts,
    multimodal_human_message,
    text_part,
)
from agent.app.nodes.layered_direct.structured_author import (
    StructuredAuthorCallResult,
    invoke_structured_author,
)
from agent.app.prompts.prompt_loader import load_prompt_definition
from shaderforge.layered_spec import LayeredSpecError
from shaderforge.program_spec import (
    AuthorIdentity,
    LayerPlanV1,
    ShaderProgramSpecV1,
    build_author_identity,
    canonical_json,
    sha256_hex_text,
)

DIRECT_LAYERED_INITIAL_PROMPT = load_prompt_definition("direct_layered_initial_v1")
DIRECT_LAYERED_REFINE_PROMPT = load_prompt_definition("direct_layered_refine_v1")
DIRECT_LAYERED_REPAIR_PROMPT = load_prompt_definition("direct_layered_repair_v1")
DEFAULT_LAYERED_INITIAL_MAX_OUTPUT_TOKENS = 8192
DEFAULT_LAYER_PATCH_MAX_OUTPUT_TOKENS = 4096
AUTHOR_IDENTITY_UNAVAILABLE = "author_identity_unavailable"
_INPUT_CONTEXT_VERSION = "direct_layered_author_input_context_v1"


@dataclass(frozen=True)
class ValidatedLayeredIncumbent:
    """Refine 的可信 current_best：Layered 表示、执行 Spec 与整图评估."""

    layered_spec: LayeredShaderSpecV1
    compiled_program_spec: ShaderProgramSpecV1
    mae: float
    loss: float
    metrics: Mapping[str, float] = field(default_factory=dict)
    residual_summary: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InitialLayeredAuthorResult:
    """Initial Author 的安全调用结果."""

    layered_spec: LayeredShaderSpecV1 | None
    call_count: int
    model_ref: str | None
    error_code: str | None
    repaired: bool = False
    latency_ms: int = 0
    total_tokens: int | None = None


@dataclass(frozen=True)
class RefineLayeredAuthorResult:
    """Refine Author 的单 Layer Patch 安全调用结果."""

    patch: LayerPatchV1 | None
    author_identity: AuthorIdentity | None
    parent_layered_spec_sha256: str | None
    target_layer_id: str | None
    call_count: int
    model_ref: str | None
    error_code: str | None
    repaired: bool = False
    latency_ms: int = 0
    total_tokens: int | None = None


def _effective_identity(
    result: StructuredAuthorCallResult,
) -> EffectiveCallIdentity | None:
    identity = result.effective_identity
    return identity if identity is not None and identity.model_ref.strip() else None


def _sampling_params(identity: EffectiveCallIdentity) -> dict[str, Any]:
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


def _initial_context_sha256(
    *, content_type: str, canvas: Mapping[str, int], plan: LayerPlanV1
) -> str:
    return sha256_hex_text(
        canonical_json(
            {
                "version": _INPUT_CONTEXT_VERSION,
                "role": "initial",
                "reference_content_type": content_type,
                "canvas": dict(canvas),
                "plan_sha256": plan.plan_sha256,
            }
        )
    )


def _refine_context_sha256(
    *,
    content_type: str,
    current_render: bytes,
    current_render_content_type: str,
    incumbent: ValidatedLayeredIncumbent,
    plan: LayerPlanV1,
) -> str:
    return sha256_hex_text(
        canonical_json(
            {
                "version": _INPUT_CONTEXT_VERSION,
                "role": "refine",
                "reference_content_type": content_type,
                "current_render_sha256": sha256(current_render).hexdigest(),
                "current_render_content_type": current_render_content_type,
                "base_layered_spec_sha256": incumbent.layered_spec.layered_spec_sha256,
                "metrics": dict(incumbent.metrics),
                "residual_summary": dict(incumbent.residual_summary),
                "loss": incumbent.loss,
                "mae": incumbent.mae,
                "plan_sha256": plan.plan_sha256,
            }
        )
    )


def _author_identity(
    *,
    identity: EffectiveCallIdentity,
    reference_image: bytes,
    user_instruction: str,
    prompt_version: str,
    role: str,
    plan: LayerPlanV1,
    content_type: str,
    input_context_sha256: str,
    repair_context_sha256: str | None,
    parent_spec_sha256: str | None = None,
) -> AuthorIdentity:
    return build_author_identity(
        reference_sha256=sha256(reference_image).hexdigest(),
        instruction_sha256=sha256(user_instruction.encode("utf-8")).hexdigest(),
        model_ref=identity.model_ref,
        prompt_version=prompt_version,
        role=role,  # type: ignore[arg-type]
        sampling_params=_sampling_params(identity),
        plan_sha256=plan.plan_sha256,
        parent_spec_sha256=parent_spec_sha256,
        reference_content_type=content_type,
        input_context_sha256=input_context_sha256,
        repair_context_sha256=repair_context_sha256,
    )


async def run_initial_layered_glsl_author(
    *,
    gateway: LLMGateway,
    reference_image: bytes,
    layer_plan: LayerPlanV1,
    canvas_width: int,
    canvas_height: int,
    content_type: str = "image/png",
    user_instruction: str = "",
    remaining_calls: int,
    max_output_tokens: int = DEFAULT_LAYERED_INITIAL_MAX_OUTPUT_TOKENS,
) -> InitialLayeredAuthorResult:
    """生成并以可信 effective identity 装配完整 LayeredShaderSpecV1."""
    schema = layered_shader_spec_json_schema(
        layer_plan=layer_plan,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )
    canvas = {"width": canvas_width, "height": canvas_height}
    repair_hints = {
        "canvas": canvas,
        "required_layers": [
            {
                "layer_id": layer.layer_id,
                "role": layer.role,
                "z_index": layer.z_index,
            }
            for layer in layer_plan.layers
        ],
    }
    parts = [
        text_part("user_instruction", user_instruction),
        text_part("canvas", canvas),
        text_part("canonical_layer_plan", layer_plan),
        text_part("expected_json_schema", schema),
        *labeled_image_parts("reference_image", reference_image, content_type),
    ]
    result = await invoke_structured_author(
        gateway=gateway,
        messages=[
            SystemMessage(content=DIRECT_LAYERED_INITIAL_PROMPT.prompt),
            multimodal_human_message(parts),
        ],
        prompt=DIRECT_LAYERED_INITIAL_PROMPT,
        schema=schema,
        parser=lambda text: parse_layered_shader_spec_semantics(
            text, layer_plan=layer_plan
        ),
        remaining_calls=remaining_calls,
        max_output_tokens=max_output_tokens,
        repair_prompt=DIRECT_LAYERED_REPAIR_PROMPT,
        repair_hints_builder=lambda _error: repair_hints,
    )
    layered_spec: LayeredShaderSpecV1 | None = None
    error_code = result.error_code
    identity = _effective_identity(result)
    if isinstance(result.value, dict):
        if identity is None:
            error_code = AUTHOR_IDENTITY_UNAVAILABLE
        else:
            try:
                author_identity = _author_identity(
                    identity=identity,
                    reference_image=reference_image,
                    user_instruction=user_instruction,
                    prompt_version=DIRECT_LAYERED_INITIAL_PROMPT.version,
                    role="initial",
                    plan=layer_plan,
                    content_type=content_type,
                    input_context_sha256=_initial_context_sha256(
                        content_type=content_type, canvas=canvas, plan=layer_plan
                    ),
                    repair_context_sha256=result.repair_context_sha256,
                )
                layered_spec = assemble_layered_shader_spec(
                    result.value, layer_plan=layer_plan, author_identity=author_identity
                )
            except LayeredSpecError:
                error_code = "author_output_invalid"
            except ValueError:
                error_code = AUTHOR_IDENTITY_UNAVAILABLE
    return InitialLayeredAuthorResult(
        layered_spec,
        result.call_count,
        result.model_ref,
        error_code,
        result.repaired,
        result.latency_ms,
        result.total_tokens,
    )


async def run_refine_layered_glsl_author(
    *,
    gateway: LLMGateway,
    reference_image: bytes,
    current_render: bytes,
    incumbent: ValidatedLayeredIncumbent,
    layer_plan: LayerPlanV1,
    content_type: str = "image/png",
    current_render_content_type: str = "image/png",
    user_instruction: str = "",
    remaining_calls: int,
    max_output_tokens: int = DEFAULT_LAYER_PATCH_MAX_OUTPUT_TOKENS,
) -> RefineLayeredAuthorResult:
    """只生成一个 canonical replace-layer Patch，并返回应用所需可信身份."""
    schema = layer_patch_json_schema()
    parts = [
        text_part("user_instruction", user_instruction),
        text_part("canonical_layer_plan", layer_plan),
        text_part("current_best_layered_spec", incumbent.layered_spec),
        text_part(
            "metrics",
            {"mae": incumbent.mae, "loss": incumbent.loss, **dict(incumbent.metrics)},
        ),
        text_part("residual_summary", dict(incumbent.residual_summary)),
        text_part("expected_json_schema", schema),
        *labeled_image_parts("reference_image", reference_image, content_type),
        *labeled_image_parts(
            "current_render", current_render, current_render_content_type
        ),
    ]
    result = await invoke_structured_author(
        gateway=gateway,
        messages=[
            SystemMessage(content=DIRECT_LAYERED_REFINE_PROMPT.prompt),
            multimodal_human_message(parts),
        ],
        prompt=DIRECT_LAYERED_REFINE_PROMPT,
        schema=schema,
        parser=parse_layer_patch_semantics,
        remaining_calls=remaining_calls,
        max_output_tokens=max_output_tokens,
        repair_prompt=DIRECT_LAYERED_REPAIR_PROMPT,
    )
    patch: LayerPatchV1 | None = None
    author_identity: AuthorIdentity | None = None
    error_code = result.error_code
    identity = _effective_identity(result)
    if isinstance(result.value, dict):
        if identity is None:
            error_code = AUTHOR_IDENTITY_UNAVAILABLE
        else:
            try:
                patch = assemble_layer_patch(result.value)
                author_identity = _author_identity(
                    identity=identity,
                    reference_image=reference_image,
                    user_instruction=user_instruction,
                    prompt_version=DIRECT_LAYERED_REFINE_PROMPT.version,
                    role="refine",
                    plan=layer_plan,
                    content_type=content_type,
                    input_context_sha256=_refine_context_sha256(
                        content_type=content_type,
                        current_render=current_render,
                        current_render_content_type=current_render_content_type,
                        incumbent=incumbent,
                        plan=layer_plan,
                    ),
                    repair_context_sha256=result.repair_context_sha256,
                    # AuthorIdentity 的既有父血缘语义绑定实际执行的 ProgramSpec；
                    # Patch 自身的 base_layered_spec_sha256 另行保护 Layered 父对象。
                    parent_spec_sha256=incumbent.compiled_program_spec.spec_sha256,
                )
            except LayeredSpecError:
                patch = None
                author_identity = None
                error_code = "author_output_invalid"
            except ValueError:
                patch = None
                author_identity = None
                error_code = AUTHOR_IDENTITY_UNAVAILABLE
    return RefineLayeredAuthorResult(
        patch,
        author_identity,
        incumbent.layered_spec.layered_spec_sha256 if patch else None,
        patch.target_layer_id if patch else None,
        result.call_count,
        result.model_ref,
        error_code,
        result.repaired,
        result.latency_ms,
        result.total_tokens,
    )


__all__ = [
    "AUTHOR_IDENTITY_UNAVAILABLE",
    "DEFAULT_LAYERED_INITIAL_MAX_OUTPUT_TOKENS",
    "DEFAULT_LAYER_PATCH_MAX_OUTPUT_TOKENS",
    "DIRECT_LAYERED_INITIAL_PROMPT",
    "DIRECT_LAYERED_REFINE_PROMPT",
    "DIRECT_LAYERED_REPAIR_PROMPT",
    "InitialLayeredAuthorResult",
    "RefineLayeredAuthorResult",
    "ValidatedLayeredIncumbent",
    "run_initial_layered_glsl_author",
    "run_refine_layered_glsl_author",
]
