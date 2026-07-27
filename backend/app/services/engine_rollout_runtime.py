"""D095 canary/direct-default 的真实 Backend runtime 与 attempt 适配器."""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
)
from agent.app.services.layerplan_glsl_shadow_suite import (
    current_direct_glsl_implementation_identity,
)
from agent.app.services.png_to_shader_min import (
    MIN_QUALITY_BUDGETS,
    MinPublicArtifact,
    PngToShaderMinResult,
    create_isolated_png_to_shader_min_service,
)
from backend.app.core.engine_policy import (
    EnginePolicyResolution,
    PromotionAuthorizationV1,
    ShaderEnginePolicyV1,
    promotion_authorization_sha256,
)
from backend.app.core.promotion_authorization import (
    PromotionAuthorizationVerification,
)
from backend.app.services.engine_rollout import (
    EngineAttemptContext,
    EngineAttemptFailure,
    EngineAttemptSuccess,
    EngineParentRunCoordinator,
    EngineResponseContractFailure,
    ParentRunPlan,
    ParentRunRequest,
    ParentRunResult,
    PromotionAuthorityUnavailable,
    VerifiedPromotionEvidence,
    resolve_parent_run_plan,
)
from shaderforge.store import LocalArtifactStore

_PUBLIC_ARTIFACTS = {
    "final-render": ("render.png", "image/png", "final-render.png"),
    "metrics": ("metrics.json", "application/json; charset=utf-8", "metrics.json"),
    "manifest": (
        "manifest.json",
        "application/json; charset=utf-8",
        "manifest.json",
    ),
}
_PRIVATE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _json_bytes(value: dict[str, Any]) -> bytes:
    """生成确定性、拒绝 NaN 的 UTF-8 JSON."""
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


def _publish_direct_progress(
    request: ParentRunRequest,
    *,
    phase: str,
    status: str,
    failure_code: str | None = None,
) -> None:
    """发布不含输入、源码、plan/spec 或 child 路径的 direct 安全事件."""
    callback = request.progress_callback
    if callback is None:
        return
    event: dict[str, Any] = {
        "node": "direct_glsl",
        "phase": phase,
        "status": status,
        "engine": "direct_glsl_layerplan_v1",
    }
    if failure_code is not None:
        event["failure_code"] = failure_code
    try:
        callback(event, None)
    except Exception:
        # 可观测性故障不得改变 engine 选择、fallback 或产品结果。
        return


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
PrivateShaderGraphServiceFactory = Callable[[Path], Any]


class _EngineMinPipelinePayload(BaseModel):
    """选中引擎公开摘要的严格内部还原契约."""

    model_config = ConfigDict(extra="ignore", strict=True)

    mae: float
    objective_loss: float
    metric_breakdown: dict[str, Any] = Field(default_factory=dict)
    template_version: str
    render_count: int
    render_budget: int
    llm_call_count: int
    llm_budget: int
    refine_budget: int
    run_classification: str
    experiment_id: str | None = None
    config_fingerprint: str
    report_schema_version: str
    patch_candidate_draw_budget: int
    patch_evidence: list[dict[str, Any]] = Field(default_factory=list)
    renderer_path: str
    target_mae: float
    target_loss: float
    target_reached: bool
    prepare_duration_ms: float
    uniform_render_count: int
    uniform_render_p95_ms: float
    scene: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = Field(default_factory=list)
    shader_graph_shadow: dict[str, Any] | None = None


class _EngineParentResponsePayload(BaseModel):
    """协调器父响应在进入 Backend API 前的严格内部契约."""

    model_config = ConfigDict(extra="ignore", strict=True)

    project_id: str
    run_id: str
    glsl: str
    render_width: int
    render_height: int
    stop_reason: str = "completed"
    quality_preset: str
    min_pipeline: _EngineMinPipelinePayload


@dataclass(frozen=True, slots=True)
class EngineRolloutGenerationResult:
    """与旧 PngToShaderMinResult 字段兼容、附带 engine envelope 的父结果."""

    project_id: str
    run_id: str
    glsl: str
    render_width: int
    render_height: int
    status: str
    stop_reason: str
    template_version: str
    quality_preset: str
    current_best_mae: float
    current_best_loss: float
    metric_breakdown: dict[str, Any]
    render_count: int
    render_budget: int
    llm_call_count: int
    llm_budget: int
    refine_budget: int
    run_classification: str
    experiment_id: str | None
    config_fingerprint: str
    report_schema_version: str
    patch_candidate_draw_budget: int
    patch_evidence: tuple[dict[str, Any], ...]
    renderer_path: str
    target_mae: float
    target_loss: float
    target_reached: bool
    prepare_duration_ms: float
    uniform_render_count: int
    uniform_render_p95_ms: float
    scene: dict[str, Any] | None
    trace: tuple[dict[str, Any], ...]
    shader_graph_shadow: dict[str, Any] | None
    engine: str
    representation: str
    engine_run: dict[str, Any]

    @classmethod
    def from_parent_result(
        cls,
        result: ParentRunResult,
    ) -> EngineRolloutGenerationResult:
        """把协调器 envelope 恢复成既有 usecase 可消费的扁平结果."""
        try:
            payload = _EngineParentResponsePayload.model_validate(
                result.response_payload
            )
        except ValidationError as exc:
            first_error = exc.errors(include_url=False)[0]
            field = ".".join(str(part) for part in first_error.get("loc", ()))
            raise EngineResponseContractFailure(field or "unknown") from exc
        pipeline = payload.min_pipeline
        return cls(
            project_id=payload.project_id,
            run_id=payload.run_id,
            glsl=payload.glsl,
            render_width=payload.render_width,
            render_height=payload.render_height,
            status="completed",
            stop_reason=payload.stop_reason,
            template_version=pipeline.template_version,
            quality_preset=payload.quality_preset,
            current_best_mae=pipeline.mae,
            current_best_loss=pipeline.objective_loss,
            metric_breakdown=dict(pipeline.metric_breakdown),
            render_count=pipeline.render_count,
            render_budget=pipeline.render_budget,
            llm_call_count=pipeline.llm_call_count,
            llm_budget=pipeline.llm_budget,
            refine_budget=pipeline.refine_budget,
            run_classification=pipeline.run_classification,
            experiment_id=pipeline.experiment_id,
            config_fingerprint=pipeline.config_fingerprint,
            report_schema_version=pipeline.report_schema_version,
            patch_candidate_draw_budget=pipeline.patch_candidate_draw_budget,
            patch_evidence=tuple(
                dict(item) for item in pipeline.patch_evidence
            ),
            renderer_path=pipeline.renderer_path,
            target_mae=pipeline.target_mae,
            target_loss=pipeline.target_loss,
            target_reached=pipeline.target_reached,
            prepare_duration_ms=pipeline.prepare_duration_ms,
            uniform_render_count=pipeline.uniform_render_count,
            uniform_render_p95_ms=pipeline.uniform_render_p95_ms,
            scene=(
                dict(pipeline.scene)
                if pipeline.scene is not None
                else None
            ),
            trace=tuple(dict(item) for item in pipeline.trace),
            shader_graph_shadow=(
                dict(pipeline.shader_graph_shadow)
                if pipeline.shader_graph_shadow is not None
                else None
            ),
            engine=result.engine,
            representation=result.representation,
            engine_run=dict(result.engine_run),
        )


class FrozenPromotionEvidenceVerifier:
    """把启动期递归验证回执降为每个 parent run 可复用的只读 capability."""

    def __init__(self, receipt: PromotionAuthorizationVerification) -> None:
        """冻结已经由启动期 registry verifier 产生的可信回执."""
        self._receipt = receipt

    def verify(
        self,
        authorization: PromotionAuthorizationV1,
    ) -> VerifiedPromotionEvidence:
        """逐字段绑定当前 policy 授权；不在请求路径重新访问 registry."""
        digest = promotion_authorization_sha256(authorization)
        receipt = self._receipt
        if (
            digest is None
            or receipt.authorization_sha256 != digest
            or receipt.target_stage != authorization.target_stage
            or receipt.registry_entry_id
            != authorization.durable_registry_entry_id
            or receipt.durable_evidence_uri != authorization.durable_evidence_uri
            or receipt.durable_evidence_sha256
            != authorization.durable_evidence_sha256
            or receipt.direct_implementation_identity
            != authorization.direct_implementation_identity
        ):
            raise PromotionAuthorityUnavailable("promotion_receipt_identity_drift")
        return VerifiedPromotionEvidence(
            authorization_sha256=receipt.authorization_sha256,
            target_stage=receipt.target_stage,
            durable_registry_entry_id=receipt.registry_entry_id,
            durable_evidence_sha256=receipt.durable_evidence_sha256,
            direct_implementation_identity=(
                receipt.direct_implementation_identity
            ),
        )


def _old_response_payload(result: PngToShaderMinResult) -> dict[str, Any]:
    """把私有旧 engine child 收敛为父响应可选取的公开字段."""
    return {
        "glsl": result.glsl,
        "generation_mode": "scene_mvp",
        "quality_preset": result.quality_preset,
        "stop_reason": result.stop_reason,
        "render_width": result.render_width,
        "render_height": result.render_height,
        "min_pipeline": {
            "mae": result.current_best_mae,
            "objective_loss": result.current_best_loss,
            "metric_breakdown": dict(result.metric_breakdown),
            "template_version": result.template_version,
            "render_count": result.render_count,
            "render_budget": result.render_budget,
            "llm_call_count": result.llm_call_count,
            "llm_budget": result.llm_budget,
            "refine_budget": result.refine_budget,
            "run_classification": result.run_classification,
            "experiment_id": result.experiment_id,
            "config_fingerprint": result.config_fingerprint,
            "report_schema_version": result.report_schema_version,
            "patch_candidate_draw_budget": result.patch_candidate_draw_budget,
            "patch_evidence": [dict(item) for item in result.patch_evidence],
            "renderer_path": result.renderer_path,
            "target_mae": result.target_mae,
            "target_loss": result.target_loss,
            "target_reached": result.target_reached,
            "prepare_duration_ms": result.prepare_duration_ms,
            "uniform_render_count": result.uniform_render_count,
            "uniform_render_p95_ms": result.uniform_render_p95_ms,
            "scene": dict(result.scene),
            "trace": [dict(item) for item in result.trace],
            "shader_graph_shadow": (
                dict(result.shader_graph_shadow)
                if result.shader_graph_shadow is not None
                else None
            ),
        },
    }


def _direct_response_payload(
    result: DirectAttemptResult,
    *,
    quality_preset: str,
) -> dict[str, Any]:
    """把 direct candidate 映射为不含 ProgramSpec/LayerPlan 的公开响应摘要."""
    best = result.current_best
    if result.status != "ok" or best is None:
        raise EngineAttemptFailure("direct_attempt_inconclusive")
    try:
        quality = MIN_QUALITY_BUDGETS[quality_preset]
    except KeyError as exc:
        raise EngineAttemptFailure("direct_quality_preset_invalid") from exc
    total_llm_calls = (
        result.plan_ledger.llm_call_count + result.direct_ledger.llm_call_count
    )
    return {
        "glsl": best.spec.fragment_source,
        "generation_mode": "scene_mvp",
        "quality_preset": quality_preset,
        "stop_reason": "direct_attempt_completed",
        "render_width": result.canvas_width,
        "render_height": result.canvas_height,
        "min_pipeline": {
            "mae": best.mae,
            "objective_loss": best.loss,
            "metric_breakdown": dict(best.metrics),
            "template_version": result.identity.implementation_identity_sha256,
            "render_count": result.direct_ledger.draw_count,
            "render_budget": result.config.draw_budget,
            "llm_call_count": total_llm_calls,
            "llm_budget": (
                result.config.plan_llm_budget
                + result.config.direct_author_llm_budget
            ),
            "refine_budget": result.config.refine_budget,
            "run_classification": "independent_experiment",
            "experiment_id": "direct_glsl_production_rollout_v1",
            "config_fingerprint": result.config_fingerprint,
            "report_schema_version": DIRECT_ATTEMPT_RESULT_SCHEMA_VERSION,
            # Direct GLSL 不经过 ShaderGraph patch candidate 阶段；兼容字段必须
            # 显式为零，避免观测端把普通候选 draw 误记为 patch draw。
            "patch_candidate_draw_budget": 0,
            "patch_evidence": [],
            "renderer_path": "direct_program_spec_v1",
            "target_mae": quality.target_mae,
            "target_loss": quality.target_loss,
            # 与现有 ShaderGraph 权威路径保持一致：质量门禁由总目标函数 loss
            # 判定，MAE 继续作为可观测的分项指标。
            "target_reached": best.loss <= quality.target_loss,
            "prepare_duration_ms": 0.0,
            # Direct GLSL 当前没有 prepared-uniform 热路径。
            "uniform_render_count": 0,
            "uniform_render_p95_ms": 0.0,
            "scene": None,
            "trace": [
                {
                    "phase": "direct_glsl",
                    "status": "completed",
                    "draw_count": result.direct_ledger.draw_count,
                    "compile_count": result.direct_ledger.compile_count,
                }
            ],
            "shader_graph_shadow": None,
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
        "source_sha256": spec.source_sha256,
        "binding_sha256": spec.binding_sha256,
        "spec_sha256": spec.spec_sha256,
        "author_identity": spec.author_identity.to_dict(),
        "validation_attestation": (
            spec.validation_attestation.to_dict()
            if spec.validation_attestation is not None
            else None
        ),
    }


def _claim_private_attempt(
    *,
    store: LocalArtifactStore,
    request: ParentRunRequest,
    context: EngineAttemptContext,
) -> None:
    """以 attempt 目录的原子 mkdir 实现确定性 child 的 write-once claim."""
    project_id = request.project_id
    attempt_id = str(context.attempt_id)
    if (
        not _PRIVATE_IDENTIFIER.fullmatch(project_id)
        or not _PRIVATE_IDENTIFIER.fullmatch(attempt_id)
    ):
        raise EngineAttemptFailure("engine_attempt_identity_invalid")
    project_root = store.base_root / project_id
    project_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    project_root.chmod(0o700)
    attempt_root = project_root / attempt_id
    try:
        attempt_root.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise EngineAttemptFailure("engine_attempt_duplicate") from exc
    store.register_run(project_id, attempt_id)


def _write_private_failure(
    *,
    store: LocalArtifactStore,
    context: EngineAttemptContext,
    failure_code: str,
    result: DirectAttemptResult | None = None,
) -> None:
    """在已 claim 的 attempt 内只写预声明 code 与可选安全摘要."""
    run = store.resolve_run(str(context.attempt_id))
    run.write_json(
        "private/failure-summary.json",
        {
            "schema_version": "engine_attempt_failure_v1",
            "parent_run_id": str(context.parent_run_id),
            "attempt_id": str(context.attempt_id),
            "attempt_index": context.attempt_index,
            "engine": context.engine,
            "representation": context.representation,
            "status": "failed",
            "failure_code": failure_code,
            "safe_summary": result.to_safe_summary() if result is not None else None,
        },
    )


def _write_private_direct_attempt(
    *,
    store: LocalArtifactStore,
    request: ParentRunRequest,
    context: EngineAttemptContext,
    result: DirectAttemptResult,
) -> None:
    """写入不可公开的 plan/spec/GLSL/render/metric/manifest 详细证据."""
    best = result.current_best
    if best is None:
        raise EngineAttemptFailure("direct_attempt_inconclusive")
    run = store.resolve_run(str(context.attempt_id))
    run.write_json("private/layer-plan.json", _private_layer_plan(result))
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
            "schema_version": "direct_glsl_private_attempt_v1",
            "parent_run_id": str(context.parent_run_id),
            "attempt_id": str(context.attempt_id),
            "attempt_index": context.attempt_index,
            "engine": context.engine,
            "representation": context.representation,
            "artifact_scope": context.artifact_scope,
            "safe_summary": result.to_safe_summary(),
            "files": [
                "layer-plan.json",
                "program-spec.json",
                "shader.frag",
                "render.png",
                "metrics.json",
            ],
        },
    )


class DirectEngineAttemptExecutor:
    """一次全新 owned direct runner；成功 Artifact 只在内存中交给父发布器."""

    def __init__(
        self,
        context: EngineAttemptContext,
        *,
        config: LayerPlanGlslDirectConfig,
        private_attempt_store: LocalArtifactStore,
        runner_factory: DirectRunnerFactory = (
            create_owned_layerplan_glsl_direct_runner
        ),
    ) -> None:
        """为一个确定性 child 绑定私有 store 和全新 owned runner."""
        self._context = context
        self._private_attempt_store = private_attempt_store
        self._runner = runner_factory(config)

    async def execute(
        self,
        request: ParentRunRequest,
        context: EngineAttemptContext,
    ) -> EngineAttemptSuccess:
        """Claim 私有 attempt，执行 direct，并冻结可提升的内存 Artifact."""
        if context != self._context or context.engine != "direct_glsl_layerplan_v1":
            raise EngineAttemptFailure("direct_attempt_identity_mismatch")
        _publish_direct_progress(
            request,
            phase="direct_start",
            status="running",
        )
        claimed = False
        result: DirectAttemptResult | None = None
        try:
            await asyncio.to_thread(
                _claim_private_attempt,
                store=self._private_attempt_store,
                request=request,
                context=context,
            )
            claimed = True
            result = await self._runner.run(
                request.image,
                content_type=request.content_type,
                instruction=request.instruction,
            )
            response_payload = _direct_response_payload(
                result,
                quality_preset=request.quality_preset,
            )
            best = result.current_best
            assert best is not None
            await asyncio.to_thread(
                _write_private_direct_attempt,
                store=self._private_attempt_store,
                request=request,
                context=context,
                result=result,
            )
            safe_summary = result.to_safe_summary()
            artifacts = SelectedEngineArtifacts(
                final_render=best.png_bytes,
                metrics_json=_json_bytes(
                    {
                        "schema_version": "direct_glsl_metrics_v1",
                        "mae": best.mae,
                        "objective_loss": best.loss,
                        "metric_breakdown": dict(best.metrics),
                        "residual_summary": dict(best.residual_summary),
                    }
                ),
                engine_manifest_json=_json_bytes(
                    {
                        "schema_version": "direct_glsl_engine_manifest_v1",
                        "attempt_id": str(context.attempt_id),
                        "engine": context.engine,
                        "representation": context.representation,
                        "safe_summary": safe_summary,
                    }
                ),
            )
        except EngineAttemptFailure as exc:
            if claimed:
                await asyncio.to_thread(
                    _write_private_failure,
                    store=self._private_attempt_store,
                    context=context,
                    failure_code=exc.code,
                    result=result,
                )
            _publish_direct_progress(
                request,
                phase="direct_failed",
                status="failed",
                failure_code=exc.code,
            )
            raise
        except Exception as exc:
            if claimed:
                await asyncio.to_thread(
                    _write_private_failure,
                    store=self._private_attempt_store,
                    context=context,
                    failure_code="direct_attempt_failed",
                    result=result,
                )
            _publish_direct_progress(
                request,
                phase="direct_failed",
                status="failed",
                failure_code="direct_attempt_failed",
            )
            raise EngineAttemptFailure("direct_attempt_failed") from exc
        _publish_direct_progress(
            request,
            phase="direct_completed",
            status="completed",
        )
        return EngineAttemptSuccess(
            attempt_id=context.attempt_id,
            engine=context.engine,
            representation=context.representation,
            response_payload=response_payload,
            artifacts=artifacts,
        )

    async def close(self) -> None:
        """释放 attempt-local direct Renderer."""
        await self._runner.close()


class PrivateShaderGraphAttemptExecutor:
    """在独立 private store 上运行一个全新旧 ShaderGraph 产品组合根."""

    def __init__(
        self,
        context: EngineAttemptContext,
        *,
        artifact_service: EngineRolloutArtifactService,
        private_attempt_root: Path,
        service_factory: PrivateShaderGraphServiceFactory = (
            create_isolated_png_to_shader_min_service
        ),
    ) -> None:
        """为一个确定性 child 创建独立旧 Graph/Renderer 组合根."""
        self._context = context
        self._artifacts = artifact_service
        self._service = service_factory(private_attempt_root)

    async def execute(
        self,
        request: ParentRunRequest,
        context: EngineAttemptContext,
    ) -> EngineAttemptSuccess:
        """Claim 私有 attempt 并运行旧 ShaderGraph 产品路径."""
        if context != self._context or context.engine != "shader_graph_v1":
            raise EngineAttemptFailure("shader_graph_attempt_identity_mismatch")
        private_store = self._artifacts.private_attempt_store
        await asyncio.to_thread(
            _claim_private_attempt,
            store=private_store,
            request=request,
            context=context,
        )
        try:
            result = cast(
                PngToShaderMinResult,
                await self._service.generate(
                    request.image,
                    request.content_type,
                    project_id=request.project_id,
                    run_id=str(context.attempt_id),
                    quality_preset=request.quality_preset,
                    instruction=request.instruction,
                    on_progress=request.progress_callback,
                ),
            )
            if (
                result.project_id != request.project_id
                or result.run_id != str(context.attempt_id)
            ):
                raise EngineAttemptFailure(
                    "shader_graph_attempt_identity_mismatch"
                )
            artifacts = await asyncio.to_thread(
                self._artifacts.read_private_attempt,
                str(context.attempt_id),
            )
        except EngineAttemptFailure as exc:
            await asyncio.to_thread(
                _write_private_failure,
                store=private_store,
                context=context,
                failure_code=exc.code,
            )
            raise
        except Exception as exc:
            await asyncio.to_thread(
                _write_private_failure,
                store=private_store,
                context=context,
                failure_code="shader_graph_attempt_failed",
            )
            raise EngineAttemptFailure("shader_graph_attempt_failed") from exc
        return EngineAttemptSuccess(
            attempt_id=context.attempt_id,
            engine=context.engine,
            representation=context.representation,
            response_payload=_old_response_payload(result),
            artifacts=artifacts,
        )

    async def close(self) -> None:
        """释放 attempt-local 旧 Graph service（若实现声明 close）."""
        close = getattr(self._service, "aclose", None) or getattr(
            self._service,
            "close",
            None,
        )
        if close is None:
            return
        value = close()
        if inspect.isawaitable(value):
            await value


class EngineRolloutRuntime:
    """lifespan 可注入的冻结 rollout runtime；不持有可变 policy."""

    def __init__(
        self,
        *,
        policy: ShaderEnginePolicyV1,
        resolution: EnginePolicyResolution,
        promotion_verifier: FrozenPromotionEvidenceVerifier | None,
        direct_implementation_identity: str,
        coordinator: EngineParentRunCoordinator,
        artifacts: EngineRolloutArtifactService,
    ) -> None:
        """冻结 policy、promotion capability、协调器与 Artifact 边界."""
        self.policy = policy
        self.resolution = resolution
        self.promotion_verifier = promotion_verifier
        self.direct_implementation_identity = direct_implementation_identity
        self.coordinator = coordinator
        self.artifacts = artifacts
        self._closed = False

    @property
    def closed(self) -> bool:
        """返回 lifespan 是否已关闭该 runtime."""
        return self._closed

    def _require_open(self) -> None:
        if self._closed:
            raise EngineAttemptFailure("engine_rollout_runtime_closed")

    def plan(self, *, parent_run_id: Any, project_id: str) -> ParentRunPlan:
        """在任何 engine 执行前冻结 parent 选择并复核 promotion capability."""
        self._require_open()
        return resolve_parent_run_plan(
            policy=self.policy,
            resolution=self.resolution,
            parent_run_id=parent_run_id,
            project_id=project_id,
            promotion_verifier=self.promotion_verifier,
            direct_implementation_identity=self.direct_implementation_identity,
        )

    async def execute(
        self,
        *,
        request: ParentRunRequest,
        plan: ParentRunPlan | None = None,
    ) -> ParentRunResult:
        """执行 parent；未显式传 plan 时在执行前冻结一次."""
        self._require_open()
        frozen = plan or self.plan(
            parent_run_id=request.parent_run_id,
            project_id=request.project_id,
        )
        return await self.coordinator.execute(request=request, plan=frozen)

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
        """提供旧 service 同形入口，同时返回父 engine/representation envelope."""
        self._require_open()
        parent_run_id = UUID(run_id)
        plan = self.plan(
            parent_run_id=parent_run_id,
            project_id=project_id,
        )

        def publish(event: dict[str, Any], render: bytes | None = None) -> None:
            if on_progress is not None:
                on_progress(event, render)

        publish(
            {
                "node": "engine_rollout",
                "phase": "engine_start",
                "status": "running",
                "engine": plan.primary_engine,
            }
        )
        result = await self.execute(
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
        if result.engine_run.get("fallback_from") is not None:
            publish(
                {
                    "node": "engine_rollout",
                    "phase": "engine_fallback",
                    "status": "completed",
                    "engine": result.engine,
                    "fallback_from": result.engine_run["fallback_from"],
                    "fallback_reason": result.engine_run["fallback_reason"],
                }
            )
        publish(
            {
                "node": "engine_rollout",
                "phase": "engine_completed",
                "status": "completed",
                "engine": result.engine,
            }
        )
        return EngineRolloutGenerationResult.from_parent_result(result)

    async def read_public_artifact(
        self,
        parent_run_id: str,
        artifact_name: str,
    ) -> MinPublicArtifact:
        """递归复验 v2 parent bundle 后只返回固定公开白名单."""
        self._require_open()
        descriptor = _PUBLIC_ARTIFACTS.get(artifact_name)
        if descriptor is None:
            raise ValueError("不支持的 rollout Artifact 名称。")

        def verify_and_read() -> bytes:
            self.artifacts.verify_parent(parent_run_id)
            filename, _content_type, _download_name = descriptor
            files = self.artifacts.public_store.verify_public_final_bundle(
                parent_run_id
            )
            return files[filename]

        data = await asyncio.to_thread(verify_and_read)
        _filename, content_type, download_name = descriptor
        return MinPublicArtifact(data, content_type, download_name)

    async def close(self) -> None:
        """幂等关闭 runtime；attempt-local 资源由协调器每次执行时已关闭."""
        self._closed = True

    async def aclose(self) -> None:
        """提供 AsyncExitStack/通用 service helper 可识别的异步关闭别名."""
        await self.close()


def build_engine_rollout_runtime(
    *,
    policy: ShaderEnginePolicyV1,
    resolution: EnginePolicyResolution,
    promotion_verification: PromotionAuthorizationVerification | None,
    public_min_service: Any,
    private_attempt_root: Path,
    attempt_timeout_seconds: float = 300.0,
    close_timeout_seconds: float = 5.0,
    direct_runner_factory: DirectRunnerFactory = (
        create_owned_layerplan_glsl_direct_runner
    ),
    private_shader_graph_service_factory: PrivateShaderGraphServiceFactory = (
        create_isolated_png_to_shader_min_service
    ),
) -> EngineRolloutRuntime | None:
    """仅为有效 canary/direct-default 构造真实 runtime，其他阶段零副作用."""
    if resolution.effective_stage not in {"canary", "direct_default"}:
        return None
    if (
        promotion_verification is None
        and policy.promotion_authorization is not None
    ):
        raise PromotionAuthorityUnavailable("promotion_authority_unavailable")
    identity_value = current_direct_glsl_implementation_identity().get(
        "identity_sha256"
    )
    if not isinstance(identity_value, str):
        raise PromotionAuthorityUnavailable("direct_implementation_identity_invalid")
    if (
        promotion_verification is not None
        and identity_value
        != promotion_verification.direct_implementation_identity
    ):
        raise PromotionAuthorityUnavailable("direct_implementation_identity_drift")
    private_attempt_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_attempt_root.chmod(0o700)
    artifacts = create_engine_rollout_artifact_service(
        public_service=public_min_service,
        private_attempt_root=private_attempt_root,
    )
    direct_config = LayerPlanGlslDirectConfig(
        implementation_identity_sha256=identity_value
    )

    def direct_factory(context: EngineAttemptContext) -> DirectEngineAttemptExecutor:
        return DirectEngineAttemptExecutor(
            context,
            config=direct_config,
            private_attempt_store=artifacts.private_attempt_store,
            runner_factory=direct_runner_factory,
        )

    def old_factory(
        context: EngineAttemptContext,
    ) -> PrivateShaderGraphAttemptExecutor:
        return PrivateShaderGraphAttemptExecutor(
            context,
            artifact_service=artifacts,
            private_attempt_root=private_attempt_root,
            service_factory=private_shader_graph_service_factory,
        )

    coordinator = EngineParentRunCoordinator(
        direct_factory=direct_factory,
        shader_graph_factory=old_factory,
        artifacts=artifacts,
        attempt_timeout_seconds=attempt_timeout_seconds,
        close_timeout_seconds=close_timeout_seconds,
    )
    return EngineRolloutRuntime(
        policy=policy,
        resolution=resolution,
        promotion_verifier=(
            FrozenPromotionEvidenceVerifier(promotion_verification)
            if promotion_verification is not None
            else None
        ),
        direct_implementation_identity=identity_value,
        coordinator=coordinator,
        artifacts=artifacts,
    )


__all__ = [
    "DirectEngineAttemptExecutor",
    "DirectRunnerFactory",
    "EngineRolloutGenerationResult",
    "EngineRolloutRuntime",
    "FrozenPromotionEvidenceVerifier",
    "PrivateShaderGraphAttemptExecutor",
    "PrivateShaderGraphServiceFactory",
    "build_engine_rollout_runtime",
]
