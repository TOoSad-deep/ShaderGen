"""Runtime context and private state for the LayerPlan Direct graph."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

import numpy as np

from agent.app.contracts.layer_plan import LayerPlanV1
from agent.app.contracts.layerplan_glsl_direct import (
    AttemptLedger,
    DirectAttemptResult,
    DirectCandidate,
    DirectPreparedRenderer,
    DirectRenderer,
    LayerPlanGlslDirectConfig,
    PlanLedger,
)
from agent.app.contracts.llm import LLMGateway
from shaderforge.layered_spec import LayeredShaderSpecV1, LayerPatchV1
from shaderforge.program_spec import (
    AuthorIdentity,
    ExecutionReceipt,
    ShaderProgramSpecV1,
    TrustedReceiptVerifier,
)
from shaderforge.rendering import PreparedRenderResult

NodeRoute = Literal[
    "author_initial",
    "compile_candidate",
    "validate_candidate",
    "prepare_program",
    "render_program",
    "verify_receipt",
    "attest_candidate",
    "evaluate_candidate",
    "decide_refinement",
    "author_refinement",
    "apply_refinement",
    "release_resources",
]

NodeProgressStatus = Literal["running", "completed", "failed"]
NodeProgressCallback = Callable[[str, NodeProgressStatus, float | None], None]
DIRECT_GRAPH_NODE_NAMES = (
    "prepare_reference",
    "author_layer_plan",
    "author_initial",
    "compile_candidate",
    "validate_candidate",
    "prepare_program",
    "render_program",
    "verify_receipt",
    "attest_candidate",
    "evaluate_candidate",
    "select_candidate",
    "decide_refinement",
    "author_refinement",
    "apply_refinement",
    "release_resources",
    "finalize_attempt",
)


@dataclass
class DirectGraphContext:
    """Attempt-local dependencies and renderer resources."""

    gateway: LLMGateway
    renderer: DirectRenderer
    config: LayerPlanGlslDirectConfig
    receipt_issuer: TrustedReceiptVerifier
    clock: Callable[[], float] = time.perf_counter
    program_cache: dict[tuple[object, ...], DirectPreparedRenderer] = field(
        default_factory=dict
    )
    node_progress_callback: NodeProgressCallback | None = None

    def publish_node_progress(
        self,
        node_name: str,
        status: NodeProgressStatus,
        duration_ms: float | None = None,
    ) -> None:
        """Publish a safe lifecycle event without affecting graph execution."""
        callback = self.node_progress_callback
        if callback is None:
            return
        try:
            callback(node_name, status, duration_ms)
        except Exception:
            # Progress delivery is optional observability, never graph control flow.
            pass

    async def release_programs(self) -> None:
        """Close every prepared program once and clear the attempt-local cache."""
        prepared_programs = tuple(self.program_cache.values())
        self.program_cache.clear()
        for prepared in prepared_programs:
            try:
                await prepared.close()
            except Exception:  # noqa: BLE001 - cleanup must not hide the result
                pass


class LayerPlanGlslDirectInput(TypedDict):
    """Public invocation input for one graph run."""

    reference_image: bytes
    content_type: str
    instruction: str


class LayerPlanGlslDirectOutput(TypedDict):
    """Graph output retained by the service runner and topology tests."""

    result: DirectAttemptResult
    completed_nodes: tuple[str, ...]


class LayerPlanGlslDirectState(TypedDict, total=False):
    """Private, attempt-local workflow state."""

    reference_image: bytes
    content_type: str
    instruction: str
    target_rgb: np.ndarray
    background: tuple[float, float, float]
    canvas_width: int
    canvas_height: int
    next_sequence: int
    plan_ledger: PlanLedger
    direct_ledger: AttemptLedger
    events: list[dict[str, Any]]
    layer_plan: LayerPlanV1 | None
    candidate_role: Literal["initial", "refine"]
    candidate_sequence: int
    candidate_layered_spec: LayeredShaderSpecV1 | None
    candidate_compiled_spec: ShaderProgramSpecV1 | None
    candidate_attested_spec: ShaderProgramSpecV1 | None
    candidate_parent_sha256: str | None
    candidate_patched_layer_id: str | None
    prepared_cache_key: tuple[object, ...] | None
    candidate_cache_hit: bool
    draw_result: PreparedRenderResult | None
    verified_receipt: ExecutionReceipt | None
    pending_candidate: DirectCandidate | None
    candidates: list[DirectCandidate]
    current_best: DirectCandidate | None
    refinement_count: int
    refinement_blocked: bool
    should_refine: bool
    failure_code: str | None
    completed_nodes: tuple[str, ...]
    result: DirectAttemptResult
    refine_patch: LayerPatchV1 | None
    refine_author_identity: AuthorIdentity | None


__all__ = [
    "DIRECT_GRAPH_NODE_NAMES",
    "DirectGraphContext",
    "LayerPlanGlslDirectInput",
    "LayerPlanGlslDirectOutput",
    "LayerPlanGlslDirectState",
    "NodeProgressCallback",
    "NodeProgressStatus",
    "NodeRoute",
]
