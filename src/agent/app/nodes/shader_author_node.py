"""ShaderAuthorAgent 三种受限模式的统一 Gateway Node."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain_core.messages import SystemMessage

from agent.app.config.model_config import SHADER_GEN_MODEL_NAME, NodeModelConfig
from agent.app.contracts.llm import LLMCallOptions, LLMGateway
from agent.app.contracts.png_to_shader_v1 import (
    AuthorMode,
    CandidateProvenance,
    CandidateRecordInput,
    RenderEvidenceBinding,
    ShaderAuthorResult,
    VisualAnalysis,
    VisualReview,
)
from agent.app.messages.png_to_shader_v1 import (
    canonical_json,
    context_part,
    glsl_part,
    labeled_image_parts,
    multimodal_human_message,
    sha256_text,
    text_part,
    validate_render_evidence_binding,
)
from agent.app.nodes.structured_output import invoke_structured_output
from agent.app.parsers.png_to_shader_v1 import (
    parser_for_author,
    repair_shader_author_initial_bindings,
)
from agent.app.prompts.prompt_loader import PromptDefinition, load_prompt_definition
from shaderforge.contracts import WEBGL1_STATIC_NO_TEXTURE_V1

AUTHOR_PROMPTS: dict[AuthorMode, PromptDefinition] = {
    AuthorMode.INITIAL: load_prompt_definition("shader_author_initial_v1"),
    AuthorMode.COMPILE_REPAIR: load_prompt_definition(
        "shader_author_compile_repair_v1"
    ),
    AuthorMode.VISUAL_REFINE: load_prompt_definition("shader_author_visual_refine_v1"),
}
SHADER_AUTHOR_MODEL_CONFIG = NodeModelConfig(
    call=LLMCallOptions(
        model_ref=SHADER_GEN_MODEL_NAME,
        temperature=0,
        thinking="off",
        capture_reasoning=False,
        response_format="json_object",
    ),
    print_reasoning=False,
)
ShaderAuthorNode = Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]
logger = logging.getLogger("agent.png_to_shader")


def _analysis(value: Any) -> VisualAnalysis:
    return (
        value
        if isinstance(value, VisualAnalysis)
        else VisualAnalysis.model_validate(value)
    )


def _author_result(value: Any) -> ShaderAuthorResult:
    return (
        value
        if isinstance(value, ShaderAuthorResult)
        else ShaderAuthorResult.model_validate(value)
    )


def _candidate(value: Any) -> CandidateRecordInput:
    return (
        value
        if isinstance(value, CandidateRecordInput)
        else CandidateRecordInput.model_validate(value)
    )


def _binding(value: Any) -> RenderEvidenceBinding:
    return (
        value
        if isinstance(value, RenderEvidenceBinding)
        else RenderEvidenceBinding.model_validate(value)
    )


def _review(value: Any) -> VisualReview:
    return (
        value if isinstance(value, VisualReview) else VisualReview.model_validate(value)
    )


def _base_content(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        text_part(
            "render_contract",
            state.get("render_contract", WEBGL1_STATIC_NO_TEXTURE_V1.to_dict()),
        ),
        text_part(
            "shader_author_output_schema",
            ShaderAuthorResult.model_json_schema(mode="validation"),
        ),
    ]


def _initial_content(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    content = _base_content(state)
    content.extend(
        [
            text_part("target_measurements", state["target_measurements"]),
            text_part("visual_analysis", _analysis(state["visual_analysis"])),
        ]
    )
    if instruction := state.get("instruction"):
        content.append(
            text_part("current_user_constraints", {"instruction": instruction})
        )
    if history := context_part(state.get("context_pack")):
        content.append(history)
    content.extend(
        labeled_image_parts(
            "reference_image",
            state["image"],
            state.get("content_type", "image/png"),
        )
    )
    return content


def _compile_content(
    state: Mapping[str, Any],
    previous: ShaderAuthorResult,
) -> tuple[list[dict[str, Any]], str]:
    diagnostics_payload = {
        "static_validation": state["static_validation"],
        "compile_result": state["compile_result"],
    }
    diagnostics = canonical_json(diagnostics_payload)
    content = _base_content(state)
    content.extend(
        [
            text_part("static_and_webgl_diagnostics", diagnostics_payload),
            text_part("previous_author_result", previous),
            text_part("repair_budget", state.get("repair_budget", {})),
        ]
    )
    if history := context_part(state.get("context_pack")):
        content.append(history)
    content.append(glsl_part(state["glsl"]))
    return content, diagnostics


def _refine_content(
    state: Mapping[str, Any],
    candidate: CandidateRecordInput,
    review: VisualReview,
) -> list[dict[str, Any]]:
    content = _base_content(state)
    content.extend(
        [
            text_part("target_measurements", state["target_measurements"]),
            text_part("visual_analysis", _analysis(state["visual_analysis"])),
            text_part("current_best_candidate", candidate),
            text_part("score_breakdown", state["score_breakdown"]),
            text_part("visual_review", review),
            text_part("residual_summary", state.get("residual_summary", {})),
        ]
    )
    if history := context_part(state.get("context_pack")):
        content.append(history)
    content.extend(
        labeled_image_parts(
            "reference_image",
            state["image"],
            state.get("content_type", "image/png"),
        )
    )
    content.extend(
        labeled_image_parts(
            "current_best_render",
            state["rendered_image"],
            state.get("rendered_content_type", "image/png"),
        )
    )
    content.append(glsl_part(state["glsl"], label="current_best_glsl"))
    return content


def make_shader_author_node(
    gateway: LLMGateway,
    mode: AuthorMode,
    config: NodeModelConfig = SHADER_AUTHOR_MODEL_CONFIG,
) -> ShaderAuthorNode:
    """创建绑定 initial、compile_repair 或 visual_refine 的 Author Node."""
    prompt = AUTHOR_PROMPTS[mode]

    async def shader_author(state: Mapping[str, Any]) -> dict[str, Any]:
        """生成完整候选并返回 Prompt/模型 provenance."""
        previous: ShaderAuthorResult | None = None
        candidate: CandidateRecordInput | None = None
        review: VisualReview | None = None
        diagnostics = ""
        protected_regions: tuple[str, ...] = ()

        if mode == AuthorMode.INITIAL:
            content = _initial_content(state)
        elif mode == AuthorMode.COMPILE_REPAIR:
            previous = _author_result(state["previous_author_result"])
            content, diagnostics = _compile_content(state, previous)
            protected_regions = tuple(previous.protected_regions)
        else:
            candidate = _candidate(state["current_best_candidate"])
            review = _review(state["visual_review"])
            binding = _binding(state["render_evidence_binding"])
            validate_render_evidence_binding(
                candidate,
                state["glsl"],
                state["rendered_image"],
                binding,
            )
            content = _refine_content(state, candidate, review)
            protected_regions = tuple(review.protected_regions)

        expected_domain = review.primary_problem_domain if review else None
        result = await invoke_structured_output(
            gateway=gateway,
            messages=[
                SystemMessage(content=prompt.prompt),
                multimodal_human_message(content),
            ],
            config=config,
            role="shader_author",
            mode=mode,
            prompt_version=prompt.version,
            parser=parser_for_author(
                expected_mode=mode,
                expected_base_candidate_id=(
                    candidate.candidate_id if candidate is not None else None
                ),
                expected_problem_domain=expected_domain,
                previous_result=previous,
                compile_diagnostics=diagnostics,
                expected_protected_regions=protected_regions,
            ),
            schema_model=ShaderAuthorResult,
            max_attempts=int(state.get("structured_output_max_attempts", 2)),
            local_repair=(
                repair_shader_author_initial_bindings
                if mode == AuthorMode.INITIAL
                else None
            ),
        )
        final_audit = result.audits[-1]
        provenance = CandidateProvenance(
            mode=mode,
            model_ref=result.final_response.model_ref,
            requested_model_ref=(
                result.final_response.requested_model_ref or config.call.model_ref
            ),
            model_identity_source=result.final_response.model_identity_source,
            prompt_version=prompt.version,
            final_attempt=final_audit.attempt,
            repair_prompt_version=final_audit.repair_prompt_version,
            output_sha256=final_audit.output_sha256,
            glsl_sha256=sha256_text(result.value.glsl),
        )
        update: dict[str, Any] = {
            "author_result": result.value.to_dict(),
            "glsl": result.value.glsl,
            "author_model": result.final_response.model_ref,
            "candidate_provenance": provenance.to_dict(),
            "candidate_origin": "model",
            "candidate_generator_version": None,
            "model_calls": (
                *state.get("model_calls", ()),
                *(audit.to_dict() for audit in result.audits),
            ),
        }
        if result.local_repair is not None:
            logger.warning(
                "shader.pipeline.local_repair run_id=%s project_id=%s "
                "stage=author_initial diagnostics=%s",
                state.get("run_id", "unknown"),
                state.get("project_id", "unknown"),
                json.dumps(
                    result.local_repair,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            update["logs"] = (
                *state.get("logs", ()),
                {
                    "level": "warning",
                    "source": "agent.shader_author",
                    "message": "Initial Author 固定绑定字段已执行本地受限归一化",
                    "context": result.local_repair,
                },
            )
        return update

    return shader_author


def make_shader_author_initial_node(
    gateway: LLMGateway,
    config: NodeModelConfig = SHADER_AUTHOR_MODEL_CONFIG,
) -> ShaderAuthorNode:
    """创建 initial Author Node."""
    return make_shader_author_node(gateway, AuthorMode.INITIAL, config)


def make_shader_author_compile_repair_node(
    gateway: LLMGateway,
    config: NodeModelConfig = SHADER_AUTHOR_MODEL_CONFIG,
) -> ShaderAuthorNode:
    """创建 compile_repair Author Node."""
    return make_shader_author_node(gateway, AuthorMode.COMPILE_REPAIR, config)


def make_shader_author_visual_refine_node(
    gateway: LLMGateway,
    config: NodeModelConfig = SHADER_AUTHOR_MODEL_CONFIG,
) -> ShaderAuthorNode:
    """创建 visual_refine Author Node."""
    return make_shader_author_node(gateway, AuthorMode.VISUAL_REFINE, config)
