"""Visual analysis author for the current Layered Direct pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage

from agent.app.contracts.layer_plan import (
    LayerPlanV1,
    assemble_layer_plan,
    layer_plan_json_schema,
    parse_layer_plan_semantics,
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
from shaderforge.program_spec import (
    ProgramSpecParseError,
    build_layer_author_identity,
    canonical_json,
    sha256_hex_text,
)

VISUAL_ANALYSIS_PROMPT = load_prompt_definition("layerplan_visual_analysis_v1")
DIRECT_LAYERED_REPAIR_PROMPT = load_prompt_definition("direct_layered_repair_v1")
DEFAULT_PLAN_MAX_OUTPUT_TOKENS = 2048
AUTHOR_IDENTITY_UNAVAILABLE = "author_identity_unavailable"


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


@dataclass(frozen=True)
class VisualAnalysisAuthorResult:
    """One bounded visual-analysis call and its trusted LayerPlan."""

    plan: LayerPlanV1 | None
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
    """Read the reference image and return a trusted advisory LayerPlan."""
    schema = layer_plan_json_schema()
    parts: list[dict[str, Any]] = [text_part("user_instruction", user_instruction)]
    if observations is not None:
        parts.append(text_part("perception_observations", dict(observations)))
    parts.append(text_part("expected_json_schema", schema))
    parts.extend(labeled_image_parts("reference_image", reference_image, content_type))
    messages: list[BaseMessage] = [
        SystemMessage(content=VISUAL_ANALYSIS_PROMPT.prompt),
        multimodal_human_message(parts),
    ]
    result = await invoke_structured_author(
        gateway=gateway,
        messages=messages,
        prompt=VISUAL_ANALYSIS_PROMPT,
        schema=schema,
        parser=parse_layer_plan_semantics,
        remaining_calls=remaining_calls,
        max_output_tokens=max_output_tokens,
        repair_prompt=DIRECT_LAYERED_REPAIR_PROMPT,
    )
    plan: LayerPlanV1 | None = None
    error_code = result.error_code
    identity = _effective_identity(result)
    if isinstance(result.value, dict):
        if identity is None:
            error_code = AUTHOR_IDENTITY_UNAVAILABLE
        else:
            try:
                author_identity = build_layer_author_identity(
                    model_ref=identity.model_ref,
                    prompt_version=VISUAL_ANALYSIS_PROMPT.version,
                    instruction_sha256=sha256(
                        user_instruction.encode("utf-8")
                    ).hexdigest(),
                    reference_content_type=content_type,
                    sampling_params=_sampling_params(identity),
                    repair_context_sha256=result.repair_context_sha256,
                )
                plan = assemble_layer_plan(
                    result.value,
                    reference_sha256=sha256(reference_image).hexdigest(),
                    author_identity=author_identity,
                    observations_ref=(
                        sha256_hex_text(canonical_json(dict(observations)))
                        if observations is not None
                        else None
                    ),
                )
            except ProgramSpecParseError:
                error_code = "author_output_invalid"
    return VisualAnalysisAuthorResult(
        plan,
        result.call_count,
        result.model_ref,
        error_code,
        result.repaired,
        result.latency_ms,
        result.total_tokens,
    )


__all__ = [
    "DEFAULT_PLAN_MAX_OUTPUT_TOKENS",
    "VISUAL_ANALYSIS_PROMPT",
    "VisualAnalysisAuthorResult",
    "run_visual_analysis_author",
]
