"""当前 Layered Direct 链路的有界结构化模型调用.

Flow::

    START -> invoke_original -> parse_original
      -> invoke_repair -> parse_repair -> finalize -> END
      `--------------------------------> finalize -> END
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any, TypedDict, TypeVar, cast

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context

from agent.app.config.model_config import SHADER_GEN_MODEL_NAME
from agent.app.contracts.llm import (
    EffectiveCallIdentity,
    LLMCallOptions,
    LLMGateway,
    LLMGatewayError,
    LLMInvocationError,
    LLMResponse,
)
from agent.app.messages.structured_multimodal import canonical_json
from agent.app.prompts.prompt_loader import PromptDefinition

MAX_STRUCTURED_ATTEMPTS = 2

_ValueT = TypeVar("_ValueT")


@dataclass(frozen=True)
class StructuredAuthorCallResult:
    """一次语义调用（含可选结构修复）的安全结果摘要。."""

    value: object | None
    call_count: int
    model_ref: str | None
    error_code: str | None
    repaired: bool = False
    latency_ms: int = 0
    total_tokens: int | None = None
    effective_identity: EffectiveCallIdentity | None = None
    repair_context_sha256: str | None = None


@dataclass(frozen=True)
class StructuredAuthorGraphContext:
    """一次 structured author 子图执行所需的进程内依赖."""

    gateway: LLMGateway
    messages: Sequence[BaseMessage]
    prompt: PromptDefinition
    schema: dict[str, object]
    parser: Callable[[str], object]
    repair_prompt: PromptDefinition
    repair_hints_builder: Callable[[ValueError], Mapping[str, object] | None] | None


class StructuredAuthorGraphInput(TypedDict):
    """Structured author 子图的稳定输入."""

    remaining_calls: int
    max_output_tokens: int


class StructuredAuthorGraphOutput(TypedDict):
    """Structured author 子图的稳定输出."""

    result: StructuredAuthorCallResult


class StructuredAuthorGraphState(TypedDict, total=False):
    """Structured author 子图的内部状态."""

    remaining_calls: int
    max_output_tokens: int
    allowed_calls: int
    options: LLMCallOptions
    call_count: int
    response: LLMResponse
    repaired_response: LLMResponse
    value: object
    original_parse_succeeded: bool
    repair_parse_succeeded: bool
    first_error: ValueError
    repair_hints: Mapping[str, object] | None
    error_code: str | None
    latency_ms: int
    total_tokens: int | None
    result: StructuredAuthorCallResult


def _response_total_tokens(response: LLMResponse) -> int | None:
    """从统一 usage 中提取总 token，缺省时返回 None 而不是猜测。."""
    usage = response.usage
    if usage is None:
        return None
    if usage.total_tokens is not None:
        return usage.total_tokens
    known = [
        item
        for item in (usage.input_tokens, usage.output_tokens)
        if isinstance(item, int) and not isinstance(item, bool)
    ]
    return sum(known) if known else None


def _combine_token_totals(
    first: int | None,
    second: int | None,
) -> int | None:
    """仅在两次调用 usage 都完整时返回总量，任一缺失即保持未知."""
    if first is None or second is None:
        return None
    return first + second


def _repair_messages(
    *,
    source_prompt: PromptDefinition,
    schema: dict[str, object],
    error: ValueError,
    original_output: str,
    repair_prompt: PromptDefinition,
    repair_hints: Mapping[str, object] | None,
) -> list[BaseMessage]:
    error_code = getattr(error, "code", "invalid_structured_output")
    payload = {
        "source_prompt_version": source_prompt.version,
        "validation_error_code": error_code,
        "validation_error_details": getattr(error, "details", ()),
        "expected_json_schema": schema,
        "untrusted_original_output": original_output,
    }
    if repair_hints is not None:
        payload["safe_repair_hints"] = dict(repair_hints)
    return [
        SystemMessage(content=repair_prompt.prompt),
        HumanMessage(content=canonical_json(payload)),
    ]


def _repair_context_sha256(
    *,
    source_prompt: PromptDefinition,
    schema: dict[str, object],
    error: ValueError,
    original_output: str,
    original_identity: EffectiveCallIdentity | None,
    repaired_identity: EffectiveCallIdentity | None,
    repair_prompt: PromptDefinition,
    repair_hints: Mapping[str, object] | None,
) -> str:
    """绑定修复 Prompt、首轮输出/错误、Schema 与第二次真实调用身份."""
    payload = {
        "repair_prompt_version": repair_prompt.version,
        "source_prompt_version": source_prompt.version,
        "original_output_sha256": sha256(original_output.encode("utf-8")).hexdigest(),
        "validation_error_code": getattr(error, "code", "invalid_structured_output"),
        "validation_error_details": getattr(error, "details", ()),
        "schema_sha256": sha256(canonical_json(schema).encode("utf-8")).hexdigest(),
        "original_effective_identity": (
            original_identity.to_dict() if original_identity is not None else None
        ),
        "repair_effective_identity": (
            repaired_identity.to_dict() if repaired_identity is not None else None
        ),
    }
    if repair_hints is not None:
        payload["safe_repair_hints"] = dict(repair_hints)
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


async def invoke_original(
    state: StructuredAuthorGraphState,
    runtime: Runtime[StructuredAuthorGraphContext],
) -> dict[str, Any]:
    """执行首轮结构化模型调用并收敛 Gateway 异常."""
    allowed = min(
        MAX_STRUCTURED_ATTEMPTS,
        max(0, state["remaining_calls"]),
    )
    if allowed == 0:
        return {
            "allowed_calls": 0,
            "call_count": 0,
            "error_code": "llm_budget_exhausted",
            "latency_ms": 0,
            "total_tokens": None,
        }

    options = LLMCallOptions(
        model_ref=SHADER_GEN_MODEL_NAME,
        temperature=0,
        thinking="off",
        capture_reasoning=False,
        response_format="json_object",
        max_output_tokens=state["max_output_tokens"],
    )
    try:
        response = await runtime.context.gateway.ainvoke(
            runtime.context.messages,
            options,
        )
    except LLMInvocationError as exc:
        return {
            "allowed_calls": allowed,
            "options": options,
            "call_count": 1,
            "error_code": (
                "llm_transient_failure" if exc.retryable else "llm_invocation_failed"
            ),
            "latency_ms": 0,
            "total_tokens": None,
        }
    except LLMGatewayError:
        return {
            "allowed_calls": allowed,
            "options": options,
            "call_count": 1,
            "error_code": "llm_invocation_failed",
            "latency_ms": 0,
            "total_tokens": None,
        }
    except Exception:
        return {
            "allowed_calls": allowed,
            "options": options,
            "call_count": 1,
            "error_code": "llm_invocation_failed",
            "latency_ms": 0,
            "total_tokens": None,
        }
    return {
        "allowed_calls": allowed,
        "options": options,
        "call_count": 1,
        "response": response,
        "error_code": None,
        "latency_ms": max(0, int(response.latency_ms)),
        "total_tokens": _response_total_tokens(response),
    }


def parse_original(
    state: StructuredAuthorGraphState,
    runtime: Runtime[StructuredAuthorGraphContext],
) -> dict[str, Any]:
    """解析首轮输出，并为可能的 repair 固化安全提示."""
    response = state.get("response")
    if response is None:
        return {}
    try:
        value = runtime.context.parser(response.text)
    except ValueError as first_error:
        repair_hints = (
            runtime.context.repair_hints_builder(first_error)
            if runtime.context.repair_hints_builder is not None
            else None
        )
        return {
            "first_error": first_error,
            "repair_hints": repair_hints,
            "original_parse_succeeded": False,
            "error_code": getattr(
                first_error,
                "code",
                "invalid_structured_output",
            ),
        }
    return {
        "value": value,
        "original_parse_succeeded": True,
        "error_code": None,
    }


def route_after_original_parse(
    state: StructuredAuthorGraphState,
) -> str:
    """仅在首轮解析失败且仍有预算时进入 repair 调用."""
    if state.get("first_error") is not None and state.get("allowed_calls", 0) >= 2:
        return "invoke_repair"
    return "finalize"


async def invoke_repair(
    state: StructuredAuthorGraphState,
    runtime: Runtime[StructuredAuthorGraphContext],
) -> dict[str, Any]:
    """使用绑定首轮错误上下文的 Prompt 执行唯一一次 repair."""
    response = state["response"]
    first_error = state["first_error"]
    repair_options = replace(
        state["options"],
        max_output_tokens=state["max_output_tokens"],
    )
    try:
        repaired = await runtime.context.gateway.ainvoke(
            _repair_messages(
                source_prompt=runtime.context.prompt,
                schema=runtime.context.schema,
                error=first_error,
                original_output=response.text,
                repair_prompt=runtime.context.repair_prompt,
                repair_hints=state.get("repair_hints"),
            ),
            repair_options,
        )
    except LLMInvocationError as exc:
        return {
            "call_count": state["call_count"] + 1,
            "error_code": (
                "llm_transient_failure" if exc.retryable else "llm_invocation_failed"
            ),
        }
    except LLMGatewayError:
        return {
            "call_count": state["call_count"] + 1,
            "error_code": "llm_invocation_failed",
        }
    except Exception:
        return {
            "call_count": state["call_count"] + 1,
            "error_code": "llm_invocation_failed",
        }
    repaired_tokens = _response_total_tokens(repaired)
    return {
        "call_count": state["call_count"] + 1,
        "repaired_response": repaired,
        "latency_ms": state["latency_ms"] + max(0, int(repaired.latency_ms)),
        "total_tokens": _combine_token_totals(
            state.get("total_tokens"),
            repaired_tokens,
        ),
        "error_code": None,
    }


def route_after_repair_invoke(state: StructuredAuthorGraphState) -> str:
    """仅在 repair 调用成功时解析其输出."""
    if state.get("repaired_response") is not None:
        return "parse_repair"
    return "finalize"


def parse_repair(
    state: StructuredAuthorGraphState,
    runtime: Runtime[StructuredAuthorGraphContext],
) -> dict[str, Any]:
    """解析 repair 输出，第二次结构错误不再触发额外调用."""
    repaired = state["repaired_response"]
    try:
        value = runtime.context.parser(repaired.text)
    except ValueError as second_error:
        return {
            "repair_parse_succeeded": False,
            "error_code": getattr(
                second_error,
                "code",
                "invalid_structured_output",
            ),
        }
    return {
        "value": value,
        "repair_parse_succeeded": True,
        "error_code": None,
    }


def finalize_structured_author(
    state: StructuredAuthorGraphState,
    runtime: Runtime[StructuredAuthorGraphContext],
) -> dict[str, Any]:
    """按最后一次成功调用身份冻结兼容的公开结果."""
    response = state.get("response")
    repaired = state.get("repaired_response")
    final_response = repaired if repaired is not None else response
    repair_succeeded = state.get("repair_parse_succeeded", False)
    repair_context_sha256 = None
    if repair_succeeded:
        assert response is not None
        assert repaired is not None
        repair_context_sha256 = _repair_context_sha256(
            source_prompt=runtime.context.prompt,
            schema=runtime.context.schema,
            error=state["first_error"],
            original_output=response.text,
            original_identity=response.effective_identity,
            repaired_identity=repaired.effective_identity,
            repair_prompt=runtime.context.repair_prompt,
            repair_hints=state.get("repair_hints"),
        )
    return {
        "result": StructuredAuthorCallResult(
            state.get("value"),
            state.get("call_count", 0),
            final_response.model_ref if final_response is not None else None,
            state.get("error_code"),
            repaired=repair_succeeded,
            latency_ms=state.get("latency_ms", 0),
            total_tokens=state.get("total_tokens"),
            effective_identity=(
                final_response.effective_identity
                if final_response is not None
                else None
            ),
            repair_context_sha256=repair_context_sha256,
        )
    }


def build_structured_author_graph() -> CompiledStateGraph[
    StructuredAuthorGraphState,
    StructuredAuthorGraphContext,
    StructuredAuthorGraphInput,
    StructuredAuthorGraphOutput,
]:
    """构建显式 invoke/parse/repair/finalize 子图."""
    builder = StateGraph(
        StructuredAuthorGraphState,
        context_schema=StructuredAuthorGraphContext,
        input_schema=StructuredAuthorGraphInput,
        output_schema=StructuredAuthorGraphOutput,
    )
    builder.add_node("invoke_original", invoke_original)
    builder.add_node("parse_original", parse_original)
    builder.add_node("invoke_repair", invoke_repair)
    builder.add_node("parse_repair", parse_repair)
    builder.add_node("finalize", finalize_structured_author)
    builder.add_edge(START, "invoke_original")
    builder.add_edge("invoke_original", "parse_original")
    builder.add_conditional_edges(
        "parse_original",
        route_after_original_parse,
        {
            "invoke_repair": "invoke_repair",
            "finalize": "finalize",
        },
    )
    builder.add_conditional_edges(
        "invoke_repair",
        route_after_repair_invoke,
        {
            "parse_repair": "parse_repair",
            "finalize": "finalize",
        },
    )
    builder.add_edge("parse_repair", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(name="structured_author")


_structured_author_graph = build_structured_author_graph()


async def invoke_structured_author(
    *,
    gateway: LLMGateway,
    messages: Sequence[BaseMessage],
    prompt: PromptDefinition,
    schema: dict[str, object],
    parser: Callable[[str], _ValueT],
    remaining_calls: int,
    max_output_tokens: int,
    repair_prompt: PromptDefinition,
    repair_hints_builder: (
        Callable[[ValueError], Mapping[str, object] | None] | None
    ) = None,
) -> StructuredAuthorCallResult:
    """通过显式子图执行一次语义调用和至多一次结构修复."""
    graph_input: StructuredAuthorGraphInput = {
        "remaining_calls": remaining_calls,
        "max_output_tokens": max_output_tokens,
    }
    with tracing_context(enabled=False, parent=False):
        output = await _structured_author_graph.ainvoke(
            graph_input,
            context=StructuredAuthorGraphContext(
                gateway=gateway,
                messages=messages,
                prompt=prompt,
                schema=schema,
                parser=parser,
                repair_prompt=repair_prompt,
                repair_hints_builder=repair_hints_builder,
            ),
        )
    return cast(StructuredAuthorCallResult, output["result"])


__all__ = [
    "MAX_STRUCTURED_ATTEMPTS",
    "StructuredAuthorCallResult",
    "StructuredAuthorGraphContext",
    "StructuredAuthorGraphInput",
    "StructuredAuthorGraphOutput",
    "StructuredAuthorGraphState",
    "build_structured_author_graph",
    "invoke_structured_author",
]
