"""Structured author 显式子图的拓扑和行为回归测试."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from agent.app.nodes.layered_direct.structured_author import (
    StructuredAuthorGraphContext,
    build_structured_author_graph,
    invoke_structured_author,
)
from agent.app.prompts.prompt_loader import PromptDefinition
from tests.direct_fakes import FakeGateway

_SOURCE_PROMPT = PromptDefinition(
    name="test_source",
    version="source-v1",
    prompt="Initial Author",
)
_REPAIR_PROMPT = PromptDefinition(
    name="test_repair",
    version="repair-v1",
    prompt="repair structured output",
)
_MESSAGES = (
    SystemMessage(content=_SOURCE_PROMPT.prompt),
    HumanMessage(content="{}"),
)


class _ParseError(ValueError):
    code = "invalid_test_payload"
    details = ("ok",)


def _parser(text: str) -> object:
    value = json.loads(text)
    if not isinstance(value, dict) or not isinstance(value.get("ok"), int):
        raise _ParseError("missing integer ok")
    return value["ok"]


def _repair_hints(error: ValueError) -> Mapping[str, object]:
    return {
        "error_code": getattr(error, "code", "invalid_structured_output"),
    }


def _context(gateway: FakeGateway) -> StructuredAuthorGraphContext:
    return StructuredAuthorGraphContext(
        gateway=gateway,
        messages=_MESSAGES,
        prompt=_SOURCE_PROMPT,
        schema={
            "type": "object",
            "properties": {"ok": {"type": "integer"}},
            "required": ["ok"],
        },
        parser=_parser,
        repair_prompt=_REPAIR_PROMPT,
        repair_hints_builder=_repair_hints,
    )


async def _node_updates(
    gateway: FakeGateway,
    *,
    remaining_calls: int,
) -> list[str]:
    updates: list[str] = []
    stream: AsyncIterator[dict[str, Any]] = build_structured_author_graph().astream(
        {
            "remaining_calls": remaining_calls,
            "max_output_tokens": 256,
        },
        context=_context(gateway),
        stream_mode="updates",
    )
    async for chunk in stream:
        updates.extend(chunk)
    return updates


def test_structured_author_graph_has_exact_control_flow() -> None:
    topology = build_structured_author_graph().get_graph()

    assert set(topology.nodes) == {
        "__start__",
        "invoke_original",
        "parse_original",
        "invoke_repair",
        "parse_repair",
        "finalize",
        "__end__",
    }
    assert {
        (edge.source, edge.target, edge.conditional) for edge in topology.edges
    } == {
        ("__start__", "invoke_original", False),
        ("invoke_original", "parse_original", False),
        ("parse_original", "invoke_repair", True),
        ("parse_original", "finalize", True),
        ("invoke_repair", "parse_repair", True),
        ("invoke_repair", "finalize", True),
        ("parse_repair", "finalize", False),
        ("finalize", "__end__", False),
    }


@pytest.mark.anyio
async def test_valid_output_skips_repair_nodes() -> None:
    gateway = FakeGateway(initial_responses=['{"ok": 7}'])

    result = await invoke_structured_author(
        gateway=gateway,
        messages=_MESSAGES,
        prompt=_SOURCE_PROMPT,
        schema=_context(gateway).schema,
        parser=_parser,
        remaining_calls=2,
        max_output_tokens=256,
        repair_prompt=_REPAIR_PROMPT,
    )

    assert result.value == 7
    assert result.call_count == 1
    assert result.error_code is None
    assert result.repaired is False
    assert result.latency_ms == 1
    assert result.total_tokens == 15
    assert result.effective_identity is not None
    assert [call["role"] for call in gateway.calls] == ["initial"]
    assert await _node_updates(
        FakeGateway(initial_responses=['{"ok": 7}']),
        remaining_calls=2,
    ) == ["invoke_original", "parse_original", "finalize"]


@pytest.mark.anyio
async def test_invalid_output_runs_repair_nodes_and_binds_context() -> None:
    gateway = FakeGateway(
        initial_responses=['{"bad": true}'],
        repair_responses=['{"ok": 9}'],
    )

    result = await invoke_structured_author(
        gateway=gateway,
        messages=_MESSAGES,
        prompt=_SOURCE_PROMPT,
        schema=_context(gateway).schema,
        parser=_parser,
        remaining_calls=2,
        max_output_tokens=256,
        repair_prompt=_REPAIR_PROMPT,
        repair_hints_builder=_repair_hints,
    )

    assert result.value == 9
    assert result.call_count == 2
    assert result.error_code is None
    assert result.repaired is True
    assert result.latency_ms == 2
    assert result.total_tokens == 30
    assert result.effective_identity is not None
    assert result.repair_context_sha256 is not None
    assert len(result.repair_context_sha256) == 64
    assert [call["role"] for call in gateway.calls] == ["initial", "repair"]
    repair_payload = str(gateway.calls[1]["messages"][1].content)
    assert '"source_prompt_version":"source-v1"' in repair_payload
    assert '"safe_repair_hints":{"error_code":"invalid_test_payload"}' in (
        repair_payload
    )
    assert await _node_updates(
        FakeGateway(
            initial_responses=['{"bad": true}'],
            repair_responses=['{"ok": 9}'],
        ),
        remaining_calls=2,
    ) == [
        "invoke_original",
        "parse_original",
        "invoke_repair",
        "parse_repair",
        "finalize",
    ]


@pytest.mark.anyio
async def test_second_parse_failure_keeps_last_call_accounting() -> None:
    gateway = FakeGateway(
        initial_responses=['{"bad": 1}'],
        repair_responses=['{"still_bad": 2}'],
    )

    result = await invoke_structured_author(
        gateway=gateway,
        messages=_MESSAGES,
        prompt=_SOURCE_PROMPT,
        schema=_context(gateway).schema,
        parser=_parser,
        remaining_calls=2,
        max_output_tokens=256,
        repair_prompt=_REPAIR_PROMPT,
    )

    assert result.value is None
    assert result.call_count == 2
    assert result.error_code == "invalid_test_payload"
    assert result.repaired is False
    assert result.latency_ms == 2
    assert result.total_tokens == 30
    assert result.effective_identity is not None
    assert result.repair_context_sha256 is None


@pytest.mark.anyio
async def test_zero_budget_finalizes_without_gateway_call() -> None:
    gateway = FakeGateway(initial_responses=['{"ok": 7}'])

    result = await invoke_structured_author(
        gateway=gateway,
        messages=_MESSAGES,
        prompt=_SOURCE_PROMPT,
        schema=_context(gateway).schema,
        parser=_parser,
        remaining_calls=0,
        max_output_tokens=256,
        repair_prompt=_REPAIR_PROMPT,
    )

    assert result.value is None
    assert result.call_count == 0
    assert result.error_code == "llm_budget_exhausted"
    assert result.latency_ms == 0
    assert result.total_tokens is None
    assert result.effective_identity is None
    assert gateway.calls == []
