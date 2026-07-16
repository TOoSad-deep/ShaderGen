"""VisualCriticAgent 的单职责 Gateway Node."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain_core.messages import SystemMessage

from agent.app.config.model_config import SHADER_GEN_MODEL_NAME, NodeModelConfig
from agent.app.contracts.llm import LLMCallOptions, LLMGateway
from agent.app.contracts.png_to_shader_v1 import (
    CandidateRecordInput,
    RenderEvidenceBinding,
    VisualAnalysis,
    VisualReview,
)
from agent.app.messages.png_to_shader_v1 import (
    context_part,
    glsl_part,
    labeled_image_parts,
    multimodal_human_message,
    text_part,
    validate_render_evidence_binding,
)
from agent.app.parsers.png_to_shader_v1 import parse_visual_review
from agent.app.prompts.prompt_loader import load_prompt_definition
from shaderforge.contracts import WEBGL1_STATIC_NO_TEXTURE_V1

from .structured_output import invoke_structured_output

VISUAL_CRITIC_PROMPT = load_prompt_definition("visual_critic_v1")
VISUAL_CRITIC_MODEL_CONFIG = NodeModelConfig(
    call=LLMCallOptions(
        model_ref=SHADER_GEN_MODEL_NAME,
        temperature=0,
        thinking="off",
        capture_reasoning=False,
        response_format="json_object",
    ),
    print_reasoning=False,
)
VisualCriticNode = Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]


def make_visual_critic_node(
    gateway: LLMGateway,
    config: NodeModelConfig = VISUAL_CRITIC_MODEL_CONFIG,
) -> VisualCriticNode:
    """创建只诊断当前候选、不修改 GLSL 的 Critic Node."""

    async def visual_critic(state: Mapping[str, Any]) -> dict[str, Any]:
        """验证证据绑定后返回严格 VisualReview."""
        candidate = CandidateRecordInput.model_validate(state["current_candidate"])
        binding = RenderEvidenceBinding.model_validate(state["render_evidence_binding"])
        validate_render_evidence_binding(
            candidate,
            state["glsl"],
            state["rendered_image"],
            binding,
        )
        analysis = VisualAnalysis.model_validate(state["visual_analysis"])
        content: list[dict[str, Any]] = [
            text_part(
                "render_contract",
                state.get("render_contract", WEBGL1_STATIC_NO_TEXTURE_V1.to_dict()),
            ),
            text_part(
                "visual_review_output_schema",
                VisualReview.model_json_schema(mode="validation"),
            ),
            text_part("target_measurements", state["target_measurements"]),
            text_part("visual_analysis", analysis),
            text_part("current_candidate", candidate),
            text_part("score_breakdown", state["score_breakdown"]),
            text_part("residual_summary", state.get("residual_summary", {})),
            text_part("render_evidence_binding", binding),
        ]
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
                "current_render",
                state["rendered_image"],
                state.get("rendered_content_type", "image/png"),
            )
        )
        content.append(glsl_part(state["glsl"]))

        result = await invoke_structured_output(
            gateway=gateway,
            messages=[
                SystemMessage(content=VISUAL_CRITIC_PROMPT.prompt),
                multimodal_human_message(content),
            ],
            config=config,
            role="visual_critic",
            mode=None,
            prompt_version=VISUAL_CRITIC_PROMPT.version,
            parser=lambda text: parse_visual_review(
                text,
                expected_candidate_id=candidate.candidate_id,
                expected_version=VISUAL_CRITIC_PROMPT.version,
            ),
            schema_model=VisualReview,
            max_attempts=int(state.get("structured_output_max_attempts", 2)),
        )
        return {
            "visual_review": result.value.to_dict(),
            "visual_critic_model": result.final_response.model_ref,
            "model_calls": (
                *state.get("model_calls", ()),
                *(audit.to_dict() for audit in result.audits),
            ),
        }

    return visual_critic
