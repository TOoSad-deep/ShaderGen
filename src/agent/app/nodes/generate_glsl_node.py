"""生成 GLSL 的 LangGraph Node."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from langchain_core.messages import HumanMessage

from agent.app.config.model_config import SHADER_GEN_MODEL_NAME, NodeModelConfig
from agent.app.contracts.llm import LLMCallOptions, LLMGateway
from agent.app.messages.image_content import image_url_part
from agent.app.observability.model_reasoning import log_reasoning_content
from agent.app.parsers.shader_response import extract_glsl
from agent.app.prompts.prompt_loader import load_prompt_definition
from agent.app.states.agent_state import ShaderPipelineState

IMAGE_TO_GLSL_PROMPT = load_prompt_definition("image_to_glsl")

GENERATE_GLSL_MODEL_CONFIG = NodeModelConfig(
    call=LLMCallOptions(
        model_ref=SHADER_GEN_MODEL_NAME,
        thinking="on",
        capture_reasoning=True,
    ),
    print_reasoning=True,
)


def make_generate_glsl_node(
    gateway: LLMGateway,
    config: NodeModelConfig = GENERATE_GLSL_MODEL_CONFIG,
):
    """创建生成 GLSL 的 Gateway Node."""

    async def generate_glsl(state: ShaderPipelineState) -> ShaderPipelineState:
        """根据原图生成 GLSL partial State."""
        context_pack = state.get("context_pack")
        content: list[dict[str, Any]] = [
            {"type": "text", "text": IMAGE_TO_GLSL_PROMPT.prompt}
        ]
        if context_pack:
            from agent.app.context.builder import ContextPack

            content.append(
                {
                    "type": "text",
                    "text": ContextPack(**context_pack).to_prompt_text(),
                }
            )
        content.append(image_url_part(state["image"], state["content_type"]))

        response = await gateway.ainvoke(
            [HumanMessage(content=content)],
            config.call,
        )
        if config.print_reasoning:
            log_reasoning_content("generate_glsl", response.reasoning_content)

        glsl = extract_glsl(response.text)
        model_call: dict[str, Any] = {
            "model": response.model_ref,
            "prompt_version": IMAGE_TO_GLSL_PROMPT.version,
            "latency_ms": response.latency_ms,
            "output_chars": len(response.text),
            "glsl_chars": len(glsl),
        }
        if response.reasoning_content:
            model_call["reasoning_content"] = response.reasoning_content

        iteration = int(state.get("iteration", 0)) + 1
        return {
            "glsl": glsl,
            "phase": "generated",
            "iteration": iteration,
            "last_glsl_sha256": sha256(glsl.encode("utf-8")).hexdigest(),
            "last_generation_model": response.model_ref,
            "last_generated_at": datetime.now(UTC).isoformat(),
            "glsl_model_name": response.model_ref,
            "vision_model_name": response.model_ref,
            "model_calls": (*state.get("model_calls", ()), model_call),
        }

    return generate_glsl
