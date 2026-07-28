"""Direct-only Backend runtime and isolated attempt adapter."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
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
    DirectAttemptResult,
    LayerPlanGlslDirectConfig,
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
_QUALITY_TARGETS = {
    "fast": (0.08, 0.10),
    "balanced": (0.06, 0.08),
    "high": (0.04, 0.06),
    "manual": (0.03, 0.05),
}


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
    try:
        request.progress_callback(event, render)
    except Exception:
        pass


def _direct_response_payload(
    result: DirectAttemptResult,
    *,
    quality_preset: str,
) -> dict[str, Any]:
    best = result.current_best
    if result.status != "ok" or best is None:
        raise EngineAttemptFailure(result.failure_code or "direct_attempt_inconclusive")
    try:
        target_mae, target_loss = _QUALITY_TARGETS[quality_preset]
    except KeyError as exc:
        raise EngineAttemptFailure("direct_quality_preset_invalid") from exc
    total_llm = result.plan_ledger.llm_call_count + result.direct_ledger.llm_call_count
    return {
        "glsl": best.spec.fragment_source,
        "generation_mode": "scene_mvp",
        "quality_preset": quality_preset,
        "stop_reason": "direct_attempt_completed",
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
            "target_reached": best.loss <= target_loss,
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
        try:
            await asyncio.to_thread(
                _claim_private_attempt, self._store, request, context
            )
            claimed = True
            result = await self._runner.run(
                request.image,
                content_type=request.content_type,
                instruction=request.instruction,
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
        except Exception as exc:
            if claimed:
                await asyncio.to_thread(
                    _write_private_failure,
                    self._store,
                    context,
                    "direct_attempt_failed",
                    result,
                )
            raise EngineAttemptFailure("direct_attempt_failed") from exc
        _publish_progress(
            request,
            phase="direct_completed",
            status="completed",
            attempt_index=context.attempt_index,
            render=best.png_bytes,
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
