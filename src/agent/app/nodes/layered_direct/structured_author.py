"""当前 Layered Direct 链路的有界结构化模型调用."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import TypeVar

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

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
    """最多语义调用一次、结构修复一次，并把异常收敛为安全结果。."""
    allowed = min(MAX_STRUCTURED_ATTEMPTS, max(0, remaining_calls))
    if allowed == 0:
        return StructuredAuthorCallResult(None, 0, None, "llm_budget_exhausted")

    options = LLMCallOptions(
        model_ref=SHADER_GEN_MODEL_NAME,
        temperature=0,
        thinking="off",
        capture_reasoning=False,
        response_format="json_object",
        max_output_tokens=max_output_tokens,
    )
    calls = 1
    try:
        response = await gateway.ainvoke(messages, options)
    except LLMInvocationError as exc:
        return StructuredAuthorCallResult(
            None,
            calls,
            None,
            "llm_transient_failure" if exc.retryable else "llm_invocation_failed",
        )
    except LLMGatewayError:
        return StructuredAuthorCallResult(
            None,
            calls,
            None,
            "llm_invocation_failed",
        )
    except Exception:
        return StructuredAuthorCallResult(
            None,
            calls,
            None,
            "llm_invocation_failed",
        )
    latency_ms = max(0, int(response.latency_ms))
    total_tokens = _response_total_tokens(response)
    try:
        value = parser(response.text)
    except ValueError as first_error:
        repair_hints = (
            repair_hints_builder(first_error)
            if repair_hints_builder is not None
            else None
        )
        first_error_code = getattr(first_error, "code", "invalid_structured_output")
        if allowed < 2:
            return StructuredAuthorCallResult(
                None,
                calls,
                response.model_ref,
                first_error_code,
                latency_ms=latency_ms,
                total_tokens=total_tokens,
                effective_identity=response.effective_identity,
            )
        calls += 1
        repair_options = replace(options, max_output_tokens=max_output_tokens)
        try:
            repaired = await gateway.ainvoke(
                _repair_messages(
                    source_prompt=prompt,
                    schema=schema,
                    error=first_error,
                    original_output=response.text,
                    repair_prompt=repair_prompt,
                    repair_hints=repair_hints,
                ),
                repair_options,
            )
        except LLMInvocationError as exc:
            return StructuredAuthorCallResult(
                None,
                calls,
                response.model_ref,
                ("llm_transient_failure" if exc.retryable else "llm_invocation_failed"),
                latency_ms=latency_ms,
                total_tokens=total_tokens,
                effective_identity=response.effective_identity,
            )
        except LLMGatewayError:
            return StructuredAuthorCallResult(
                None,
                calls,
                response.model_ref,
                "llm_invocation_failed",
                latency_ms=latency_ms,
                total_tokens=total_tokens,
                effective_identity=response.effective_identity,
            )
        except Exception:
            return StructuredAuthorCallResult(
                None,
                calls,
                response.model_ref,
                "llm_invocation_failed",
                latency_ms=latency_ms,
                total_tokens=total_tokens,
                effective_identity=response.effective_identity,
            )
        latency_ms += max(0, int(repaired.latency_ms))
        repaired_tokens = _response_total_tokens(repaired)
        total_tokens = _combine_token_totals(total_tokens, repaired_tokens)
        try:
            value = parser(repaired.text)
        except ValueError as second_error:
            return StructuredAuthorCallResult(
                None,
                calls,
                repaired.model_ref,
                getattr(second_error, "code", "invalid_structured_output"),
                latency_ms=latency_ms,
                total_tokens=total_tokens,
                effective_identity=repaired.effective_identity,
            )
        return StructuredAuthorCallResult(
            value,
            calls,
            repaired.model_ref,
            None,
            repaired=True,
            latency_ms=latency_ms,
            total_tokens=total_tokens,
            effective_identity=repaired.effective_identity,
            repair_context_sha256=_repair_context_sha256(
                source_prompt=prompt,
                schema=schema,
                error=first_error,
                original_output=response.text,
                original_identity=response.effective_identity,
                repaired_identity=repaired.effective_identity,
                repair_prompt=repair_prompt,
                repair_hints=repair_hints,
            ),
        )
    return StructuredAuthorCallResult(
        value,
        calls,
        response.model_ref,
        None,
        latency_ms=latency_ms,
        total_tokens=total_tokens,
        effective_identity=response.effective_identity,
    )


__all__ = [
    "MAX_STRUCTURED_ATTEMPTS",
    "StructuredAuthorCallResult",
    "invoke_structured_author",
]
