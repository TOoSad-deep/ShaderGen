"""JSON-safe LangGraph Studio adapter for the private direct-attempt graph."""

from __future__ import annotations

import base64
import binascii
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent.app.observability.langgraph_privacy import (
    configure_private_graph_observability,
)
from agent.app.services.layerplan_glsl_direct import (
    LayerPlanGlslDirectConfig,
    create_owned_layerplan_glsl_direct_runner,
    current_layered_direct_glsl_implementation_identity,
)

configure_private_graph_observability()

MAX_REFERENCE_IMAGE_BYTES = 8 * 1024 * 1024
MAX_REFERENCE_IMAGE_BASE64_CHARS = ((MAX_REFERENCE_IMAGE_BYTES + 2) // 3) * 4


class StudioDirectInput(TypedDict):
    """JSON-safe Studio input."""

    reference_image_base64: str
    content_type: str
    instruction: str


class StudioDirectOutput(TypedDict):
    """Public, redacted Studio output."""

    safe_summary: dict[str, Any]
    completed_nodes: tuple[str, ...]


class StudioDirectState(StudioDirectInput, StudioDirectOutput, total=False):
    """Adapter state containing only JSON-safe public values."""


def _decode_reference_base64(value: str) -> bytes:
    """Decode one strict, non-empty reference image bounded to 8 MiB."""
    if not value or len(value) > MAX_REFERENCE_IMAGE_BASE64_CHARS:
        raise ValueError("reference_image_base64 must encode 1 byte..8 MiB")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("reference_image_base64 must be strict base64") from exc
    if not decoded or len(decoded) > MAX_REFERENCE_IMAGE_BYTES:
        raise ValueError("reference_image_base64 must encode 1 byte..8 MiB")
    return decoded


async def run_owned_attempt(state: StudioDirectState) -> StudioDirectOutput:
    """Own runtime resources and expose only the attempt's safe summary."""
    reference_image = _decode_reference_base64(state["reference_image_base64"])
    identity = current_layered_direct_glsl_implementation_identity()
    runner = create_owned_layerplan_glsl_direct_runner(
        LayerPlanGlslDirectConfig(
            implementation_identity_sha256=identity["identity_sha256"]
        )
    )
    try:
        result = await runner.run(
            reference_image,
            content_type=state["content_type"],
            instruction=state["instruction"],
        )
        return {
            "safe_summary": result.to_safe_summary(),
            "completed_nodes": ("run_owned_attempt",),
        }
    finally:
        await runner.close()


def build_studio_direct_graph() -> CompiledStateGraph[
    StudioDirectState,
    None,
    StudioDirectInput,
    StudioDirectOutput,
]:
    """Build the JSON-safe Studio adapter graph."""
    builder = StateGraph(
        StudioDirectState,
        input_schema=StudioDirectInput,
        output_schema=StudioDirectOutput,
    )
    builder.add_node("run_owned_attempt", run_owned_attempt)
    builder.add_edge(START, "run_owned_attempt")
    builder.add_edge("run_owned_attempt", END)
    return builder.compile(name="layerplan_glsl_direct_studio")


graph = build_studio_direct_graph()


__all__ = [
    "StudioDirectInput",
    "StudioDirectOutput",
    "build_studio_direct_graph",
    "graph",
]
