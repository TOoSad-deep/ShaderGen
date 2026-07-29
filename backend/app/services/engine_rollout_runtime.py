"""Direct-only Backend runtime and isolated attempt adapter."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from agent.app.services.engine_rollout_artifacts import (
    EngineRolloutArtifactService,
    SelectedEngineArtifacts,
    create_engine_rollout_artifact_service,
)
from agent.app.services.layerplan_glsl_direct import (
    DIRECT_ATTEMPT_RESULT_SCHEMA_VERSION,
    DIRECT_GRAPH_NODE_NAMES,
    DirectAttemptResult,
    LayerPlanGlslDirectConfig,
    NodeProgressCallback,
    create_owned_layerplan_glsl_direct_runner,
    current_layered_direct_glsl_implementation_identity,
)
from backend.app.services.engine_rollout import (
    DIRECT_ENGINE,
    DIRECT_REPRESENTATION,
    EngineAttemptContext,
    EngineAttemptFailure,
    EngineAttemptSuccess,
    EngineParentRunCoordinator,
    ParentRunPlan,
    ParentRunRequest,
    ParentRunResult,
    resolve_parent_run_plan,
)
from shaderforge.config import RUNTIME_TIMEOUTS
from shaderforge.store import LocalArtifactStore

_PUBLIC_ARTIFACTS = {
    "final-render": ("render.png", "image/png", "final-render.png"),
    "metrics": ("metrics.json", "application/json; charset=utf-8", "metrics.json"),
    "manifest": ("manifest.json", "application/json; charset=utf-8", "manifest.json"),
}
_PRIVATE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIRECT_GRAPH_NODES = frozenset(DIRECT_GRAPH_NODE_NAMES)
_SAFE_PROGRESS_REASON_CODES = frozenset(
    {
        "target_reached",
        "global_draw_budget_exhausted",
        "global_compile_budget_exhausted",
        "uniform_tuning_budget_exhausted",
        "no_tunables",
        "no_feasible_components",
        "local_optimum",
        "dimension_cap_reached_local_optimum",
        "candidate_failures_exhausted",
        "renderer_unavailable",
        "uniform_tuning_active",
        "uniform_candidate_accepted",
        "uniform_candidate_rejected",
        "uniform_candidate_failed",
    }
)
_SAFE_REFINEMENT_STOP_REASONS = frozenset(
    {
        "target_reached",
        "refine_budget_exhausted",
        "patience_exhausted",
        "duplicate_patch",
        "hard_resource_block",
        "no_valid_candidate",
    }
)


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class PublicArtifact:
    data: bytes
    content_type: str
    filename: str


class _DirectRunner(Protocol):
    async def run(
        self,
        reference_image: bytes,
        *,
        content_type: str = "image/png",
        instruction: str = "",
        quality_preset: str = "balanced",
        node_progress_callback: NodeProgressCallback | None = None,
    ) -> DirectAttemptResult: ...

    async def close(self) -> None: ...


DirectRunnerFactory = Callable[[LayerPlanGlslDirectConfig], _DirectRunner]


@dataclass(frozen=True, slots=True)
class EngineRolloutGenerationResult:
    project_id: str
    run_id: str
    glsl: str
    render_width: int
    render_height: int
    status: str
    stop_reason: str
    quality_preset: str
    current_best_mae: float
    current_best_loss: float
    metric_breakdown: dict[str, Any]
    template_version: str
    render_count: int
    render_budget: int
    llm_call_count: int
    llm_budget: int
    refine_budget: int
    config_fingerprint: str
    report_schema_version: str
    renderer_path: str
    target_mae: float
    target_loss: float
    target_reached: bool
    trace: tuple[dict[str, Any], ...]
    engine: str
    representation: str
    engine_run: dict[str, Any]
    optimization_policy_fingerprint: str = ""
    refinement_stop_reason: str | None = None
    non_improving_count: int = 0
    duplicate_patch_count: int = 0
    uniform_optimization: dict[str, Any] | None = None

    @classmethod
    def from_parent_result(
        cls,
        result: ParentRunResult,
    ) -> EngineRolloutGenerationResult:
        payload = result.response_payload
        pipeline = payload.get("pipeline")
        if not isinstance(pipeline, dict):
            raise EngineAttemptFailure("engine_response_contract_failed")
        try:
            return cls(
                project_id=str(payload["project_id"]),
                run_id=str(payload["run_id"]),
                glsl=str(payload["glsl"]),
                render_width=int(payload["render_width"]),
                render_height=int(payload["render_height"]),
                status="completed",
                stop_reason=str(payload["stop_reason"]),
                quality_preset=str(payload["quality_preset"]),
                current_best_mae=float(pipeline["mae"]),
                current_best_loss=float(pipeline["objective_loss"]),
                metric_breakdown=dict(pipeline["metric_breakdown"]),
                template_version=str(pipeline["implementation_identity"]),
                render_count=int(pipeline["render_count"]),
                render_budget=int(pipeline["render_budget"]),
                llm_call_count=int(pipeline["llm_call_count"]),
                llm_budget=int(pipeline["llm_budget"]),
                refine_budget=int(pipeline["refine_budget"]),
                config_fingerprint=str(pipeline["config_fingerprint"]),
                report_schema_version=str(pipeline["report_schema_version"]),
                renderer_path="direct_program_spec_v1",
                target_mae=float(pipeline["target_mae"]),
                target_loss=float(pipeline["target_loss"]),
                target_reached=bool(pipeline["target_reached"]),
                trace=tuple(dict(item) for item in pipeline["trace"]),
                engine=result.engine,
                representation=result.representation,
                engine_run=dict(result.engine_run),
                optimization_policy_fingerprint=str(
                    pipeline.get("optimization_policy_fingerprint", "")
                ),
                refinement_stop_reason=_optional_string(
                    pipeline.get("refinement_stop_reason")
                ),
                non_improving_count=_non_negative_int(
                    pipeline.get("non_improving_count", 0)
                ),
                duplicate_patch_count=_non_negative_int(
                    pipeline.get("duplicate_patch_count", 0)
                ),
                uniform_optimization=_safe_uniform_optimization_summary(
                    pipeline.get("uniform_optimization")
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EngineAttemptFailure("engine_response_contract_failed") from exc


def _publish_progress(
    request: ParentRunRequest,
    *,
    phase: str,
    status: str,
    attempt_index: int,
    failure_code: str | None = None,
    render: bytes | None = None,
    update: dict[str, Any] | None = None,
) -> None:
    if request.progress_callback is None:
        return
    event: dict[str, Any] = {
        "node": "direct_glsl",
        "phase": phase,
        "status": status,
        "engine": DIRECT_ENGINE,
        "attempt_index": attempt_index,
    }
    if failure_code is not None:
        event["failure_code"] = failure_code
    if update is not None:
        event.update(update)
    try:
        request.progress_callback(event, render)
    except Exception:
        pass


def _publish_node_progress(
    request: ParentRunRequest,
    *,
    node_name: str,
    status: str,
    attempt_index: int,
    duration_ms: float | None,
    update: dict[str, Any] | None = None,
) -> None:
    """Forward a graph lifecycle event through the public-safe progress channel."""
    if (
        request.progress_callback is None
        or node_name not in _DIRECT_GRAPH_NODES
        or status not in {"running", "completed", "failed"}
    ):
        return
    event = {
        "node": node_name,
        "phase": f"node_{status}",
        "status": status,
        "engine": DIRECT_ENGINE,
        "attempt_index": attempt_index,
    }
    if duration_ms is not None and isfinite(duration_ms):
        event["duration_ms"] = round(max(0.0, duration_ms), 2)
    event.update(_safe_node_progress_update(update))
    try:
        request.progress_callback(event, None)
    except Exception:
        pass


def _safe_node_progress_update(value: Any) -> dict[str, Any]:
    """Validate the Agent's tiny progress projection before it reaches HTTP."""
    if not isinstance(value, dict):
        return {}
    projected: dict[str, Any] = {}
    reason_code = value.get("reason_code")
    if isinstance(reason_code, str) and reason_code in _SAFE_PROGRESS_REASON_CODES:
        projected["reason_code"] = reason_code
    refinement_stop_reason = value.get("refinement_stop_reason")
    if (
        isinstance(refinement_stop_reason, str)
        and refinement_stop_reason in _SAFE_REFINEMENT_STOP_REASONS
    ):
        projected["refinement_stop_reason"] = refinement_stop_reason

    uniform = value.get("uniform_optimization")
    if not isinstance(uniform, dict):
        return projected
    allowed = {
        "draw_count",
        "draw_budget",
        "evaluated_count",
        "accepted_count",
        "stop_reason",
        "candidate_outcome",
    }
    if set(uniform) - allowed:
        return projected
    safe_uniform: dict[str, Any] = {}
    for key in {"draw_count", "draw_budget", "evaluated_count", "accepted_count"}:
        item = uniform.get(key)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            return projected
        safe_uniform[key] = item
    stop_reason = uniform.get("stop_reason")
    if stop_reason is not None:
        if (
            not isinstance(stop_reason, str)
            or stop_reason not in _SAFE_PROGRESS_REASON_CODES
        ):
            return projected
        safe_uniform["stop_reason"] = stop_reason
    candidate_outcome = uniform.get("candidate_outcome")
    if candidate_outcome is not None:
        if candidate_outcome not in {"accepted", "rejected", "failed"}:
            return projected
        safe_uniform["candidate_outcome"] = candidate_outcome
    projected["uniform_optimization"] = safe_uniform
    return projected


def _direct_response_payload(
    result: DirectAttemptResult,
    *,
    quality_preset: str,
) -> dict[str, Any]:
    best = result.current_best
    if result.status != "ok" or best is None:
        raise EngineAttemptFailure(result.failure_code or "direct_attempt_inconclusive")
    policy = getattr(result, "optimization_policy", None)
    if policy is None or getattr(policy, "quality_preset", None) != quality_preset:
        raise EngineAttemptFailure("engine_response_contract_failed")
    target_mae = _finite_unit_float(getattr(policy, "target_mae", None))
    target_loss = _finite_unit_float(getattr(policy, "target_loss", None))
    optimization_policy_fingerprint = _policy_fingerprint(result, policy)
    refinement_stop_reason = _optional_string(
        getattr(result, "refinement_stop_reason", None)
    )
    non_improving_count = _non_negative_int(getattr(result, "non_improving_count", 0))
    duplicate_patch_count = _non_negative_int(
        getattr(result, "duplicate_patch_count", 0)
    )
    uniform_optimization = _safe_uniform_optimization_summary(
        getattr(result, "uniform_optimization_summary", None)
    )
    total_llm = result.plan_ledger.llm_call_count + result.direct_ledger.llm_call_count
    return {
        "glsl": best.spec.fragment_source,
        "generation_mode": "scene_mvp",
        "quality_preset": quality_preset,
        "stop_reason": refinement_stop_reason or "direct_attempt_completed",
        "render_width": result.canvas_width,
        "render_height": result.canvas_height,
        "pipeline": {
            "mae": best.mae,
            "objective_loss": best.loss,
            "metric_breakdown": dict(best.metrics),
            "implementation_identity": result.identity.implementation_identity_sha256,
            "render_count": result.direct_ledger.draw_count,
            "render_budget": result.config.draw_budget,
            "llm_call_count": total_llm,
            "llm_budget": (
                result.config.plan_llm_budget + result.config.direct_author_llm_budget
            ),
            "refine_budget": result.config.refine_budget,
            "config_fingerprint": result.config_fingerprint,
            "report_schema_version": DIRECT_ATTEMPT_RESULT_SCHEMA_VERSION,
            "target_mae": target_mae,
            "target_loss": target_loss,
            "target_reached": best.mae <= target_mae and best.loss <= target_loss,
            "optimization_policy_fingerprint": optimization_policy_fingerprint,
            "refinement_stop_reason": refinement_stop_reason,
            "non_improving_count": non_improving_count,
            "duplicate_patch_count": duplicate_patch_count,
            "uniform_optimization": uniform_optimization,
            "trace": [
                {
                    "phase": "direct_glsl",
                    "status": "completed",
                    "draw_count": result.direct_ledger.draw_count,
                    "compile_count": result.direct_ledger.compile_count,
                }
            ],
        },
    }


def _optional_string(value: Any) -> str | None:
    """Return a non-empty public string or ``None`` without coercing objects."""
    return value if isinstance(value, str) and value else None


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EngineAttemptFailure("engine_response_contract_failed")
    return int(value)


def _finite_unit_float(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or not 0.0 <= float(value) <= 1.0
    ):
        raise EngineAttemptFailure("engine_response_contract_failed")
    return float(value)


def _policy_fingerprint(result: DirectAttemptResult, policy: Any) -> str:
    """Read the Agent-owned per-run policy identity without recreating policy here."""
    fingerprint = _optional_string(
        getattr(result, "optimization_policy_fingerprint", None)
    )
    if fingerprint is None:
        candidate = getattr(policy, "fingerprint", None)
        fingerprint = _optional_string(candidate() if callable(candidate) else None)
    if fingerprint is None:
        raise EngineAttemptFailure("engine_response_contract_failed")
    return fingerprint


def _safe_uniform_optimization_summary(value: Any) -> dict[str, Any] | None:
    """Project only the documented, non-secret optimizer outcome fields."""
    if value is None:
        return None
    raw = value.to_dict() if hasattr(value, "to_dict") else value
    if not isinstance(raw, dict):
        raise EngineAttemptFailure("engine_response_contract_failed")
    allowed = {
        "schema_version",
        "algorithm_id",
        "algorithm_version",
        "config_fingerprint",
        "active_component_count",
        "evaluated_count",
        "accepted_count",
        "draw_count",
        "draw_budget",
        "initial_loss",
        "initial_mae",
        "final_loss",
        "final_mae",
        "loss_delta",
        "mae_delta",
        "stop_reason",
        "base_spec_sha256",
        "selected_spec_sha256",
        "private_trace_sha256",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise EngineAttemptFailure("engine_response_contract_failed")
    summary: dict[str, Any] = {}
    for key, item in raw.items():
        if key in {
            "active_component_count",
            "evaluated_count",
            "accepted_count",
            "draw_count",
            "draw_budget",
        }:
            summary[key] = _non_negative_int(item)
        elif key in {
            "initial_loss",
            "initial_mae",
            "final_loss",
            "final_mae",
            "loss_delta",
            "mae_delta",
        }:
            if item is not None:
                if (
                    isinstance(item, bool)
                    or not isinstance(item, (int, float))
                    or not isfinite(item)
                ):
                    raise EngineAttemptFailure("engine_response_contract_failed")
                summary[key] = float(item)
        elif isinstance(item, str) and item:
            summary[key] = item
        elif item is not None:
            raise EngineAttemptFailure("engine_response_contract_failed")
    return summary


def _public_completion_update(response: dict[str, Any]) -> dict[str, Any]:
    """Extract the explicit public-safe progress increment from final payload data."""
    pipeline = response["pipeline"]
    return {
        "budgets": {
            "render_budget": pipeline["render_budget"],
            "llm_budget": pipeline["llm_budget"],
            "refine_budget": pipeline["refine_budget"],
            "scope": "attempt",
        },
        "counters": {
            "render_count": pipeline["render_count"],
            "llm_call_count": pipeline["llm_call_count"],
        },
        "best": {
            "mae": pipeline["mae"],
            "loss": pipeline["objective_loss"],
            "target_mae": pipeline["target_mae"],
            "target_loss": pipeline["target_loss"],
        },
        "reason_code": pipeline["refinement_stop_reason"],
        "optimization_policy_fingerprint": pipeline["optimization_policy_fingerprint"],
        "refinement_stop_reason": pipeline["refinement_stop_reason"],
        "non_improving_count": pipeline["non_improving_count"],
        "duplicate_patch_count": pipeline["duplicate_patch_count"],
        "uniform_optimization": pipeline["uniform_optimization"],
    }


def _private_layer_plan(result: DirectAttemptResult) -> dict[str, Any] | None:
    plan = result.layer_plan
    if plan is None:
        return None
    return {
        "schema_version": plan.schema_version,
        "layers": [layer.to_dict() for layer in plan.layers],
        "reference_sha256": plan.reference_sha256,
        "author_identity": plan.author_identity.to_dict(),
        "observations_ref": plan.observations_ref,
        "plan_sha256": plan.plan_sha256,
    }


def _private_program_spec(result: DirectAttemptResult) -> dict[str, Any] | None:
    best = result.current_best
    if best is None:
        return None
    spec = best.spec
    return {
        "schema_version": spec.schema_version,
        "fragment_source": spec.fragment_source,
        "uniform_schema": [item.to_dict() for item in spec.uniform_schema],
        "uniform_values": dict(spec.uniform_values),
        "tunable_manifest": [item.to_dict() for item in spec.tunable_manifest],
        "canvas": spec.canvas.to_dict(),
        "renderer_contract_id": spec.renderer_contract_id,
        "spec_sha256": spec.spec_sha256,
        "author_identity": spec.author_identity.to_dict(),
        "derivation_provenance": (
            spec.derivation_provenance.to_dict()
            if spec.derivation_provenance is not None
            else None
        ),
        "validation_attestation": (
            spec.validation_attestation.to_dict()
            if spec.validation_attestation is not None
            else None
        ),
    }


def _claim_private_attempt(
    store: LocalArtifactStore,
    request: ParentRunRequest,
    context: EngineAttemptContext,
) -> None:
    project_id = request.project_id
    attempt_id = str(context.attempt_id)
    if not (
        _PRIVATE_IDENTIFIER.fullmatch(project_id)
        and _PRIVATE_IDENTIFIER.fullmatch(attempt_id)
    ):
        raise EngineAttemptFailure("engine_attempt_identity_invalid")
    project_root = store.base_root / project_id
    project_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    project_root.chmod(0o700)
    try:
        (project_root / attempt_id).mkdir(mode=0o700)
    except FileExistsError as exc:
        raise EngineAttemptFailure("engine_attempt_duplicate") from exc
    store.register_run(project_id, attempt_id)


def _write_private_failure(
    store: LocalArtifactStore,
    context: EngineAttemptContext,
    failure_code: str,
    result: DirectAttemptResult | None,
) -> None:
    store.resolve_run(str(context.attempt_id)).write_json(
        "private/failure-summary.json",
        {
            "schema_version": "direct_attempt_failure_v1",
            "parent_run_id": str(context.parent_run_id),
            "attempt_id": str(context.attempt_id),
            "attempt_index": context.attempt_index,
            "status": "failed",
            "failure_code": failure_code,
            "safe_summary": result.to_safe_summary() if result is not None else None,
            "diagnostics": (
                result.to_private_diagnostics() if result is not None else []
            ),
        },
    )


def _write_private_success(
    store: LocalArtifactStore,
    context: EngineAttemptContext,
    result: DirectAttemptResult,
) -> None:
    best = result.current_best
    if best is None:
        raise EngineAttemptFailure("direct_attempt_inconclusive")
    run = store.resolve_run(str(context.attempt_id))
    run.write_json("private/layer-plan.json", _private_layer_plan(result))
    run.write_json(
        "private/layered-shader-spec.json",
        best.layered_spec.to_dict(),
    )
    run.write_json("private/program-spec.json", _private_program_spec(result))
    run.write_json(
        "private/uniform-optimization-trace.json",
        {
            "schema_version": "uniform_optimization_trace_v1",
            "items": result.to_private_uniform_optimization_trace(),
        },
    )
    run.write_text("private/shader.frag", best.spec.fragment_source)
    run.write_bytes("private/render.png", best.png_bytes, content_type="image/png")
    run.write_json(
        "private/metrics.json",
        {
            "mae": best.mae,
            "objective_loss": best.loss,
            "metric_breakdown": dict(best.metrics),
            "residual_summary": dict(best.residual_summary),
        },
    )
    run.write_json(
        "private/manifest.json",
        {
            "schema_version": "direct_private_attempt_v1",
            "parent_run_id": str(context.parent_run_id),
            "attempt_id": str(context.attempt_id),
            "attempt_index": context.attempt_index,
            "artifact_scope": context.artifact_scope,
            "safe_summary": result.to_safe_summary(),
        },
    )


class DirectEngineAttemptExecutor:
    def __init__(
        self,
        context: EngineAttemptContext,
        *,
        config: LayerPlanGlslDirectConfig,
        private_attempt_store: LocalArtifactStore,
        runner_factory: DirectRunnerFactory,
    ) -> None:
        self._context = context
        self._store = private_attempt_store
        self._runner = runner_factory(config)

    async def execute(
        self,
        request: ParentRunRequest,
        context: EngineAttemptContext,
    ) -> EngineAttemptSuccess:
        if context != self._context:
            raise EngineAttemptFailure("direct_attempt_identity_mismatch")
        _publish_progress(
            request,
            phase="direct_start",
            status="running",
            attempt_index=context.attempt_index,
        )
        claimed = False
        result: DirectAttemptResult | None = None

        def publish_node_progress(
            node_name: str,
            status: str,
            duration_ms: float | None,
            update: dict[str, Any] | None = None,
        ) -> None:
            _publish_node_progress(
                request,
                node_name=node_name,
                status=status,
                attempt_index=context.attempt_index,
                duration_ms=duration_ms,
                update=update,
            )

        try:
            await asyncio.to_thread(
                _claim_private_attempt, self._store, request, context
            )
            claimed = True
            result = await self._runner.run(
                request.image,
                content_type=request.content_type,
                instruction=request.instruction,
                # Agent owns preset -> DirectOptimizationPolicy resolution. Do not
                # recreate targets in Backend: every fresh attempt receives the
                # parent request's exact preset.
                quality_preset=request.quality_preset,
                node_progress_callback=publish_node_progress,
            )
            response = _direct_response_payload(
                result,
                quality_preset=request.quality_preset,
            )
            best = result.current_best
            assert best is not None
            await asyncio.to_thread(
                _write_private_success,
                self._store,
                context,
                result,
            )
            artifacts = SelectedEngineArtifacts(
                final_render=best.png_bytes,
                metrics_json=_json_bytes(
                    {
                        "schema_version": "direct_metrics_v1",
                        "mae": best.mae,
                        "objective_loss": best.loss,
                        "metric_breakdown": dict(best.metrics),
                        "residual_summary": dict(best.residual_summary),
                        "optimization_policy_fingerprint": response["pipeline"][
                            "optimization_policy_fingerprint"
                        ],
                        "refinement_stop_reason": response["pipeline"][
                            "refinement_stop_reason"
                        ],
                        "non_improving_count": response["pipeline"][
                            "non_improving_count"
                        ],
                        "duplicate_patch_count": response["pipeline"][
                            "duplicate_patch_count"
                        ],
                        "uniform_optimization": response["pipeline"][
                            "uniform_optimization"
                        ],
                    }
                ),
                engine_manifest_json=_json_bytes(
                    {
                        "schema_version": "direct_engine_manifest_v1",
                        "attempt_id": str(context.attempt_id),
                        "safe_summary": result.to_safe_summary(),
                    }
                ),
            )
        except EngineAttemptFailure as exc:
            if claimed:
                await asyncio.to_thread(
                    _write_private_failure,
                    self._store,
                    context,
                    exc.code,
                    result,
                )
            _publish_progress(
                request,
                phase="direct_failed",
                status="failed",
                attempt_index=context.attempt_index,
                failure_code=exc.code,
            )
            raise
        except asyncio.CancelledError:
            _publish_progress(
                request,
                phase="direct_failed",
                status="failed",
                attempt_index=context.attempt_index,
                failure_code="engine_attempt_cancelled",
            )
            raise
        except Exception as exc:
            if claimed:
                await asyncio.to_thread(
                    _write_private_failure,
                    self._store,
                    context,
                    "direct_attempt_failed",
                    result,
                )
            _publish_progress(
                request,
                phase="direct_failed",
                status="failed",
                attempt_index=context.attempt_index,
                failure_code="direct_attempt_failed",
            )
            raise EngineAttemptFailure("direct_attempt_failed") from exc
        _publish_progress(
            request,
            phase="direct_completed",
            status="completed",
            attempt_index=context.attempt_index,
            render=best.png_bytes,
            update=_public_completion_update(response),
        )
        return EngineAttemptSuccess(
            attempt_id=context.attempt_id,
            engine=DIRECT_ENGINE,
            representation=DIRECT_REPRESENTATION,
            response_payload=response,
            artifacts=artifacts,
        )

    async def close(self) -> None:
        await self._runner.close()


class EngineRolloutRuntime:
    def __init__(
        self,
        *,
        coordinator: EngineParentRunCoordinator,
        artifacts: EngineRolloutArtifactService,
    ) -> None:
        self.coordinator = coordinator
        self.artifacts = artifacts
        self._closed = False

    def plan(self, *, parent_run_id: UUID, project_id: str) -> ParentRunPlan:
        if self._closed:
            raise EngineAttemptFailure("engine_rollout_runtime_closed")
        return resolve_parent_run_plan(
            parent_run_id=parent_run_id,
            project_id=project_id,
        )

    async def generate(
        self,
        image: bytes,
        content_type: str,
        *,
        project_id: str,
        run_id: str,
        quality_preset: str = "balanced",
        instruction: str = "",
        on_progress: Callable[[dict[str, Any], bytes | None], None] | None = None,
    ) -> EngineRolloutGenerationResult:
        parent_run_id = UUID(run_id)
        plan = self.plan(parent_run_id=parent_run_id, project_id=project_id)
        if on_progress is not None:
            on_progress(
                {
                    "node": "engine_rollout",
                    "phase": "engine_start",
                    "status": "running",
                    "engine": DIRECT_ENGINE,
                },
                None,
            )
        result = await self.coordinator.execute(
            request=ParentRunRequest(
                parent_run_id=parent_run_id,
                project_id=project_id,
                image=image,
                content_type=content_type,
                instruction=instruction,
                quality_preset=quality_preset,
                progress_callback=on_progress,
            ),
            plan=plan,
        )
        if on_progress is not None:
            on_progress(
                {
                    "node": "engine_rollout",
                    "phase": "engine_completed",
                    "status": "completed",
                    "engine": DIRECT_ENGINE,
                },
                None,
            )
        return EngineRolloutGenerationResult.from_parent_result(result)

    async def read_public_artifact(
        self,
        parent_run_id: str,
        artifact_name: str,
    ) -> PublicArtifact:
        if self._closed:
            raise EngineAttemptFailure("engine_rollout_runtime_closed")
        descriptor = _PUBLIC_ARTIFACTS.get(artifact_name)
        if descriptor is None:
            raise ValueError("unsupported Direct artifact")

        def verify_and_read() -> bytes:
            self.artifacts.verify_parent(parent_run_id)
            files = self.artifacts.public_store.verify_public_final_bundle(
                parent_run_id
            )
            return files[descriptor[0]]

        data = await asyncio.to_thread(verify_and_read)
        return PublicArtifact(data, descriptor[1], descriptor[2])

    async def close(self) -> None:
        self._closed = True

    async def aclose(self) -> None:
        await self.close()


def build_engine_rollout_runtime(
    *,
    public_store: LocalArtifactStore,
    private_attempt_root: Path,
    attempt_timeout_seconds: float = RUNTIME_TIMEOUTS.engine.attempt_seconds,
    close_timeout_seconds: float = RUNTIME_TIMEOUTS.engine.close_seconds,
    direct_runner_factory: DirectRunnerFactory = (
        create_owned_layerplan_glsl_direct_runner
    ),
) -> EngineRolloutRuntime:
    identity = current_layered_direct_glsl_implementation_identity()["identity_sha256"]
    if not isinstance(identity, str):
        raise ValueError("invalid Direct implementation identity")
    private_attempt_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_attempt_root.chmod(0o700)
    artifacts = create_engine_rollout_artifact_service(
        public_store=public_store,
        private_attempt_root=private_attempt_root,
    )
    config = LayerPlanGlslDirectConfig(implementation_identity_sha256=identity)

    def direct_factory(context: EngineAttemptContext) -> DirectEngineAttemptExecutor:
        return DirectEngineAttemptExecutor(
            context,
            config=config,
            private_attempt_store=artifacts.private_attempt_store,
            runner_factory=direct_runner_factory,
        )

    coordinator = EngineParentRunCoordinator(
        direct_factory=direct_factory,
        artifacts=artifacts,
        attempt_timeout_seconds=attempt_timeout_seconds,
        close_timeout_seconds=close_timeout_seconds,
        direct_attempt_limit=3,
    )
    return EngineRolloutRuntime(coordinator=coordinator, artifacts=artifacts)


__all__ = [
    "DirectEngineAttemptExecutor",
    "DirectRunnerFactory",
    "EngineRolloutGenerationResult",
    "EngineRolloutRuntime",
    "PublicArtifact",
    "build_engine_rollout_runtime",
]
