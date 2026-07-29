"""Runtime context and private state for the LayerPlan Direct graph."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from inspect import signature
from typing import Any, Literal, TypedDict, cast

import numpy as np

from agent.app.contracts.layer_plan import LayerPlanV1
from agent.app.contracts.layerplan_glsl_direct import (
    AttemptLedger,
    DirectAttemptResult,
    DirectCandidate,
    DirectOptimizationPolicy,
    DirectPreparedRenderer,
    DirectRenderer,
    LayerPlanGlslDirectConfig,
    PlanLedger,
    RefineFeedback,
    RefinementStopReason,
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
from shaderforge.uniform_optimization import (
    CoordinateMove,
    CoordinatePatternSession,
    UniformOptimizationSummaryV2,
    UniformPatchV1,
)

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
    "decide_uniform_optimization",
    "propose_uniform_candidate",
    "apply_uniform_candidate",
    "record_uniform_outcome",
    "author_refinement",
    "apply_refinement",
    "release_resources",
]

NodeProgressStatus = Literal["running", "completed", "failed"]
NodeProgressUpdate = dict[str, Any]
NodeProgressLifecycleCallback = Callable[[str, NodeProgressStatus, float | None], None]
NodeProgressIncrementCallback = Callable[
    [str, NodeProgressStatus, float | None, NodeProgressUpdate], None
]
# Legacy lifecycle consumers receive three arguments.  Extended consumers can
# receive a fourth, explicitly projected mapping; this remains observability,
# never graph input.
NodeProgressCallback = NodeProgressLifecycleCallback | NodeProgressIncrementCallback
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
    "decide_uniform_optimization",
    "propose_uniform_candidate",
    "apply_uniform_candidate",
    "record_uniform_outcome",
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
    optimization_policy: DirectOptimizationPolicy = field(
        default_factory=DirectOptimizationPolicy
    )
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
        update: NodeProgressUpdate | None = None,
    ) -> None:
        """Publish a lifecycle event and optional safe projection, best-effort."""
        callback = self.node_progress_callback
        if callback is None:
            return
        try:
            supports_update = False
            if update is not None:
                try:
                    signature(callback).bind(node_name, status, duration_ms, update)
                    supports_update = True
                except (TypeError, ValueError):
                    # Existing consumers only implement the lifecycle signature.
                    pass
            if supports_update:
                cast(NodeProgressIncrementCallback, callback)(
                    node_name,
                    status,
                    duration_ms,
                    cast(NodeProgressUpdate, update),
                )
            else:
                cast(NodeProgressLifecycleCallback, callback)(
                    node_name,
                    status,
                    duration_ms,
                )
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
    candidate_role: Literal["initial", "refine", "uniform_optimize"]
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
    optimization_policy: DirectOptimizationPolicy
    consecutive_non_improving: int
    previous_refine_feedback: RefineFeedback | None
    attempted_patch_fingerprints: tuple[str, ...]
    duplicate_patch_detected: bool
    duplicate_patch_count: int
    refinement_stop_reason: RefinementStopReason | None
    candidate_selected: bool
    candidate_loss_delta: float | None
    candidate_mae_delta: float | None
    candidate_material_improvement: bool
    should_uniform_optimize: bool
    uniform_release_requested: bool
    uniform_search_session: CoordinatePatternSession | None
    uniform_pending_move: CoordinateMove | None
    uniform_candidate_patch: UniformPatchV1 | None
    uniform_optimized_source_sha256s: tuple[str, ...]
    uniform_search_source_sha256: str | None
    uniform_search_base_spec_sha256: str | None
    uniform_search_selected_spec_sha256: str | None
    uniform_search_initial_loss: float | None
    uniform_search_initial_mae: float | None
    uniform_search_selected_loss: float | None
    uniform_search_selected_mae: float | None
    uniform_search_initial_draw_count: int | None
    uniform_search_trace_start_index: int | None
    uniform_tuning_stop_reason: str | None
    uniform_candidate_failed: bool
    uniform_optimization_summary: UniformOptimizationSummaryV2 | None
    uniform_optimization_trace: list[dict[str, Any]]
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
    "NodeProgressUpdate",
    "NodeProgressStatus",
    "NodeRoute",
]
