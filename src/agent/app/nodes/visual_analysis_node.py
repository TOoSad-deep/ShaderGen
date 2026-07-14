"""VisualAnalysisAgent 的单职责 Gateway Node."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain_core.messages import SystemMessage

from agent.app.config.model_config import SHADER_GEN_MODEL_NAME, NodeModelConfig
from agent.app.contracts.llm import LLMCallOptions, LLMGateway
from agent.app.contracts.png_to_shader_v1 import VisualAnalysis
from agent.app.messages.png_to_shader_v1 import (
    context_part,
    labeled_image_parts,
    multimodal_human_message,
    text_part,
)
from agent.app.nodes.structured_output import invoke_structured_output
from agent.app.parsers.png_to_shader_v1 import (
    parse_visual_analysis,
    repair_visual_analysis_roi_purposes,
)
from agent.app.prompts.prompt_loader import load_prompt_definition
from shaderforge.contracts import WEBGL1_STATIC_NO_TEXTURE_V1

VISUAL_ANALYSIS_PROMPT = load_prompt_definition("visual_analysis_v1")
VISUAL_ANALYSIS_MODEL_CONFIG = NodeModelConfig(
    call=LLMCallOptions(
        model_ref=SHADER_GEN_MODEL_NAME,
        temperature=0,
        thinking="off",
        capture_reasoning=False,
        response_format="json_object",
    ),
    print_reasoning=False,
)
VisualAnalysisNode = Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]
logger = logging.getLogger("agent.png_to_shader")


def make_visual_analysis_node(
    gateway: LLMGateway,
    config: NodeModelConfig = VISUAL_ANALYSIS_MODEL_CONFIG,
) -> VisualAnalysisNode:
    """创建只负责图片结构分析的 Node."""

    async def visual_analysis(state: Mapping[str, Any]) -> dict[str, Any]:
        """返回严格 VisualAnalysis 与完整模型调用审计."""
        render_contract = state.get(
            "render_contract", WEBGL1_STATIC_NO_TEXTURE_V1.to_dict()
        )
        content: list[dict[str, Any]] = [
            text_part("render_contract", render_contract),
            text_part("target_measurements", state["target_measurements"]),
            text_part(
                "visual_analysis_output_schema",
                VisualAnalysis.model_json_schema(mode="validation"),
            ),
        ]
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
        result = await invoke_structured_output(
            gateway=gateway,
            messages=[
                SystemMessage(content=VISUAL_ANALYSIS_PROMPT.prompt),
                multimodal_human_message(content),
            ],
            config=config,
            role="visual_analysis",
            mode=None,
            prompt_version=VISUAL_ANALYSIS_PROMPT.version,
            parser=lambda text: parse_visual_analysis(
                text,
                expected_version=VISUAL_ANALYSIS_PROMPT.version,
            ),
            schema_model=VisualAnalysis,
            max_attempts=int(state.get("structured_output_max_attempts", 2)),
            local_repair=lambda text, error: repair_visual_analysis_roi_purposes(
                text,
                error,
                expected_version=VISUAL_ANALYSIS_PROMPT.version,
            ),
        )
        update: dict[str, Any] = {
            "visual_analysis": result.value.to_dict(),
            "visual_analysis_model": result.final_response.model_ref,
            "model_calls": (
                *state.get("model_calls", ()),
                *(audit.to_dict() for audit in result.audits),
            ),
        }
        if result.local_repair is not None:
            logger.warning(
                "shader.pipeline.local_repair run_id=%s project_id=%s stage=visual_analysis "
                "diagnostics=%s",
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
                    "source": "agent.visual_analysis",
                    "message": "视觉分析 ROI purpose 已执行本地受限归一化",
                    "context": result.local_repair,
                },
            )
        return update

    return visual_analysis
