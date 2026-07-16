"""执行 PNG-to-Shader V1 模型调用并最多修复一次结构化 JSON."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any, Generic, Literal, TypeVar

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from agent.app.config.model_config import NodeModelConfig
from agent.app.contracts.llm import LLMCallOptions, LLMGateway, LLMResponse
from agent.app.contracts.png_to_shader_v1 import AuthorMode, ModelCallAudit
from agent.app.messages.png_to_shader_v1 import canonical_json
from agent.app.observability.model_reasoning import log_reasoning_content
from agent.app.parsers.png_to_shader_v1 import PngToShaderParseError
from agent.app.prompts.prompt_loader import load_prompt_definition

_ResultT = TypeVar("_ResultT", bound=BaseModel)
StructuredRole = Literal["visual_analysis", "shader_author", "visual_critic"]
LocalRepair = Callable[
    [str, PngToShaderParseError],
    tuple[_ResultT, dict[str, Any]] | None,
]
STRUCTURED_OUTPUT_REPAIR_PROMPT = load_prompt_definition("structured_output_repair_v1")


@dataclass(frozen=True)
class StructuredCallResult(Generic[_ResultT]):
    """成功解析后的业务值、审计链和最终模型响应."""

    value: _ResultT
    audits: tuple[ModelCallAudit, ...]
    final_response: LLMResponse
    local_repair: dict[str, Any] | None = None


class StructuredOutputExhaustedError(RuntimeError):
    """表示当前调用预算内的结构化输出尝试均不合法."""

    def __init__(
        self,
        *,
        audits: tuple[ModelCallAudit, ...],
        last_error: PngToShaderParseError,
    ) -> None:
        """保留可审计元数据，但不泄露原始模型输出."""
        self.audits = audits
        self.last_error = last_error
        attempts = len(audits)
        super().__init__(
            f"结构化输出在 {attempts} 次允许尝试后仍不合法："
            + ",".join(last_error.error_codes)
            + "。"
        )


class StructuredOutputInvocationError(RuntimeError):
    """表示结构修复调用本身失败，并保留已完成的安全审计."""

    def __init__(
        self,
        *,
        audits: tuple[ModelCallAudit, ...],
        attempted_calls: int,
        error_type: str,
    ) -> None:
        """记录尝试数和错误类型，不泄露供应商异常文本."""
        self.audits = audits
        self.attempted_calls = attempted_calls
        self.error_type = error_type
        super().__init__(f"结构化输出调用失败：{error_type}。")


def _audit(
    *,
    response: LLMResponse,
    options: LLMCallOptions,
    role: StructuredRole,
    mode: AuthorMode | None,
    attempt: int,
    prompt_version: str,
    repair_prompt_version: str | None,
    parse_error: PngToShaderParseError | None,
) -> ModelCallAudit:
    usage = response.usage
    return ModelCallAudit(
        role="json_repair" if attempt == 2 else role,
        mode=mode,
        attempt=attempt,
        requested_model_ref=response.requested_model_ref or options.model_ref,
        model_ref=response.model_ref,
        model_identity_source=response.model_identity_source,
        response_format=options.response_format,
        prompt_version=prompt_version,
        repair_prompt_version=repair_prompt_version,
        latency_ms=response.latency_ms,
        output_sha256=sha256(response.text.encode("utf-8")).hexdigest(),
        parse_status="invalid" if parse_error else "valid",
        error_codes=list(parse_error.error_codes) if parse_error else [],
        validation_issues=(
            [issue.to_dict() for issue in parse_error.issues] if parse_error else []
        ),
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        total_tokens=usage.total_tokens if usage else None,
    )


def _repair_messages(
    *,
    source_prompt_version: str,
    schema: dict[str, object],
    error: PngToShaderParseError,
    original_output: str,
) -> list[BaseMessage]:
    repair_payload = {
        "source_prompt_version": source_prompt_version,
        "validation_errors": [issue.to_dict() for issue in error.issues],
        "expected_json_schema": schema,
        "untrusted_original_output": original_output,
    }
    return [
        SystemMessage(content=STRUCTURED_OUTPUT_REPAIR_PROMPT.prompt),
        HumanMessage(
            content=(
                "请仅修复下面数据的 JSON 结构，不增加新语义。\n"
                + canonical_json(repair_payload)
            )
        ),
    ]


async def invoke_structured_output(
    *,
    gateway: LLMGateway,
    messages: Sequence[BaseMessage],
    config: NodeModelConfig,
    role: StructuredRole,
    mode: AuthorMode | None,
    prompt_version: str,
    parser: Callable[[str], _ResultT],
    schema_model: type[_ResultT],
    max_attempts: int = 2,
    local_repair: LocalRepair[_ResultT] | None = None,
) -> StructuredCallResult[_ResultT]:
    """执行语义调用，并在预算允许时追加一次低成本 JSON 修复."""
    if max_attempts not in {1, 2}:
        raise ValueError("max_attempts 只能是 1 或 2。")
    semantic_response = await gateway.ainvoke(messages, config.call)
    if config.print_reasoning:
        log_reasoning_content(role, semantic_response.reasoning_content)
    try:
        value = parser(semantic_response.text)
    except PngToShaderParseError as first_error:
        first_audit = _audit(
            response=semantic_response,
            options=config.call,
            role=role,
            mode=mode,
            attempt=1,
            prompt_version=prompt_version,
            repair_prompt_version=None,
            parse_error=first_error,
        )
        if local_repair is not None:
            repaired = local_repair(semantic_response.text, first_error)
            if repaired is not None:
                value, local_repair_audit = repaired
                return StructuredCallResult(
                    value=value,
                    audits=(first_audit,),
                    final_response=semantic_response,
                    local_repair=local_repair_audit,
                )
        if max_attempts == 1:
            raise StructuredOutputExhaustedError(
                audits=(first_audit,),
                last_error=first_error,
            ) from first_error
        repair_options = replace(
            config.call,
            temperature=0,
            thinking="off",
            capture_reasoning=False,
        )
        try:
            repair_response = await gateway.ainvoke(
                _repair_messages(
                    source_prompt_version=prompt_version,
                    schema=schema_model.model_json_schema(mode="validation"),
                    error=first_error,
                    original_output=semantic_response.text,
                ),
                repair_options,
            )
        except Exception as exc:
            raise StructuredOutputInvocationError(
                audits=(first_audit,),
                attempted_calls=2,
                error_type=type(exc).__name__,
            ) from exc
        try:
            value = parser(repair_response.text)
        except PngToShaderParseError as second_error:
            second_audit = _audit(
                response=repair_response,
                options=repair_options,
                role=role,
                mode=mode,
                attempt=2,
                prompt_version=prompt_version,
                repair_prompt_version=STRUCTURED_OUTPUT_REPAIR_PROMPT.version,
                parse_error=second_error,
            )
            raise StructuredOutputExhaustedError(
                audits=(first_audit, second_audit),
                last_error=second_error,
            ) from second_error
        second_audit = _audit(
            response=repair_response,
            options=repair_options,
            role=role,
            mode=mode,
            attempt=2,
            prompt_version=prompt_version,
            repair_prompt_version=STRUCTURED_OUTPUT_REPAIR_PROMPT.version,
            parse_error=None,
        )
        return StructuredCallResult(
            value=value,
            audits=(first_audit, second_audit),
            final_response=repair_response,
        )

    first_audit = _audit(
        response=semantic_response,
        options=config.call,
        role=role,
        mode=mode,
        attempt=1,
        prompt_version=prompt_version,
        repair_prompt_version=None,
        parse_error=None,
    )
    return StructuredCallResult(
        value=value,
        audits=(first_audit,),
        final_response=semantic_response,
    )
