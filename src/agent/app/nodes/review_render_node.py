"""评审当前渲染图的 LangGraph Node."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage

from agent.app.config.model_config import SHADER_GEN_MODEL_NAME, NodeModelConfig
from agent.app.contracts.llm import LLMCallOptions, LLMGateway
from agent.app.memory.models import build_review_summary
from agent.app.messages.image_content import image_url_part
from agent.app.observability.model_reasoning import log_reasoning_content
from agent.app.parsers.shader_response import parse_shader_review_response
from agent.app.prompts.prompt_loader import load_prompt
from agent.app.states.agent_state import ShaderPipelineState

SHADER_REVIEW_PROMPT = load_prompt("shader_review")

REVIEW_RENDER_MODEL_CONFIG = NodeModelConfig(
    call=LLMCallOptions(
        model_ref=SHADER_GEN_MODEL_NAME,
        thinking="on",
        capture_reasoning=False,
    ),
    print_reasoning=False,
)

ReviewRenderNode = Callable[[ShaderPipelineState], Awaitable[ShaderPipelineState]]


def make_review_render_node(
    gateway: LLMGateway,
    config: NodeModelConfig = REVIEW_RENDER_MODEL_CONFIG,
) -> ReviewRenderNode:
    """创建渲染评审 Gateway Node."""

    async def review_render(state: ShaderPipelineState) -> ShaderPipelineState:
        """根据原图、渲染图和 GLSL 返回评审 partial State."""
        context_pack = state.get("context_pack")
        content: list[str | dict[Any, Any]] = [
            {"type": "text", "text": SHADER_REVIEW_PROMPT}
        ]
        if context_pack:
            from agent.app.context.builder import ContextPack

            content.append(
                {
                    "type": "text",
                    "text": ContextPack(**context_pack).to_prompt_text(),
                }
            )
        content.extend(
            [
                {"type": "text", "text": "原图："},
                image_url_part(state["image"], state["content_type"]),
                {"type": "text", "text": "当前渲染图："},
                image_url_part(state["rendered_image"], state["rendered_content_type"]),
                {
                    "type": "text",
                    "text": f"当前 GLSL 代码：\n```glsl\n{state['glsl']}\n```",
                },
            ]
        )
        response = await gateway.ainvoke(
            [HumanMessage(content=content)],
            config.call,
        )
        if config.print_reasoning:
            log_reasoning_content("review_render", response.reasoning_content)

        review = parse_shader_review_response(response.text)
        model_call: dict[str, Any] = {
            "model": response.model_ref,
            "prompt_version": "shader_review",
            "latency_ms": response.latency_ms,
            "output_chars": len(response.text),
        }
        if config.call.capture_reasoning is True and response.reasoning_content:
            model_call["reasoning_content"] = response.reasoning_content

        review_summary = build_review_summary(review.evaluation, review.suggestions)
        return {
            "evaluation": review.evaluation,
            "suggestions": review.suggestions,
            "phase": "reviewed",
            "last_review_summary": review_summary,
            "last_suggestions": review.suggestions,
            "review_model_name": response.model_ref,
            "model_calls": (*state.get("model_calls", ()), model_call),
            "events": (
                *state.get("events", ()),
                {
                    "stage": "review",
                    "event_type": "review_recorded",
                    "payload": {"suggestion_count": len(review.suggestions)},
                },
            ),
        }

    return review_render
