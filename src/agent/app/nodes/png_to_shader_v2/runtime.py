"""V2.4 Graph 的 production node factories 与显式依赖边界。"""
# ruff: noqa: D101, D102, D103, D401, D415

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal, Protocol, cast

from pydantic import Field

from agent.app.states.png_to_shader_v2_state import (
    BudgetStateV2,
    BudgetVectorV2,
    HypothesisBranchStateV2,
    PngToShaderV2State,
    commit_budget_v2,
    reserve_budget_v2,
)
from agent.app.states.png_to_shader_v2_state_store import (
    V2StateCheckpointNotFoundError,
)
from shaderforge.analysis import TargetMeasurementsV2
from shaderforge.compiler import (
    DIAGNOSTIC_OWNERSHIP_POLICY_VERSION,
    CompilationBundle,
    CompilerDefectError,
    DiagnosticCompilationBundleV3,
    compile_diagnostic_passes,
    compile_effect_genome,
    materialize_compilation,
    materialize_diagnostic_compilation,
)
from shaderforge.contracts import FiniteFloat, FrozenModel
from shaderforge.evaluation import (
    BEAUTY_CAPTURE_COUNT,
    COMPILATION_ARTIFACT_KIND,
    CONSTRAINT_EVALUATION_ARTIFACT_KIND,
    EVALUATION_ARTIFACT_KIND,
    GENOME_ARTIFACT_KIND,
    GENOME_ARTIFACT_SCHEMA_VERSION,
    INTENT_ARTIFACT_KIND,
    INTENT_ARTIFACT_SCHEMA_VERSION,
    RENDER_ARTIFACT_KIND,
    RENDER_ARTIFACT_SCHEMA_VERSION,
    TYPED_COMPILATION_ARTIFACT_SCHEMA_VERSION,
    TYPED_CONSTRAINT_EVALUATION_ARTIFACT_SCHEMA_VERSION,
    TYPED_EVALUATION_ARTIFACT_SCHEMA_VERSION,
    CandidateAttemptEvidenceV1,
    CandidateAttemptRecord,
    CandidateMaterializationInputV2,
    DiagnosticRenderReceiptV3,
    MeasurementSeedAdmissionPolicy,
    PromotionOperationV1,
    PromotionReceiptV1,
    PromotionSinkResultV1,
    RenderCallOutcomeV2,
    RenderedStructureEvidenceV4,
    RenderedStructureVerificationV4,
    RendererEnvironmentReceiptV3,
    RendererRequestReceiptV2,
    RenderPlanItemV2,
    RenderPlanV2,
    RenderProgressV2,
    RenderRepeatabilityEvidenceV2,
    RuntimeAdmissionRejected,
    build_repeatability_evidence,
    compute_promotion_operation_id,
    compute_render_plan_hash,
    compute_render_progress_hash,
    compute_rendered_structure_evidence_hash,
    compute_renderer_environment_hash,
    compute_renderer_request_hash,
    decide_trusted_runtime_admission,
    evaluate_intent_genome_constraints_v3,
    evaluate_render,
    load_promotion_operation,
    load_promotion_receipt,
    load_render_model,
    load_renderer_request,
    load_trusted_runtime_selector_input,
    load_typed_candidate_artifacts,
    materialize_attempt_evidence,
    materialize_candidate_attempt,
    materialize_promotion_operation,
    materialize_promotion_receipt,
    materialize_render_model,
    materialize_renderer_request,
    materialize_typed_candidate_artifacts,
    rendered_structure_diagnostic_size_v2,
    verify_rendered_structure_evidence,
    with_basic_evaluation_record_hash,
)
from shaderforge.genome import TypedEffectGenome, compute_genome_hashes
from shaderforge.intent import (
    IntentBuildContext,
    IntentIR,
    RequestConstraintSet,
    VisualInterpretationV2,
    build_intent_variants,
)
from shaderforge.rendering import RendererUnavailableError, RenderResult
from shaderforge.seeding import (
    SeedPlanV1,
    build_seed_plans,
    expand_seed_plans,
)
from shaderforge.store import ArtifactCatalog, ArtifactRefV2, ArtifactResolver

_JSON_CONTENT_TYPE = "application/json"
_PNG_CONTENT_TYPE = "image/png"

PNG_TO_SHADER_V2_NODE_IDS = (
    "initialize_run_v2",
    "prepare_context_v2",
    "ingest_target_v2",
    "measure_target_v2",
    "analyze_visual_layers_v2",
    "build_intent_variants_v2",
    "dequeue_hypothesis_v2",
    "plan_strategy_v2",
    "propose_seed_plans_v2",
    "expand_validate_seeds_v2",
    "dequeue_seed_v2",
    "prepare_candidate_attempt_v2",
    "compile_genome_v2",
    "render_candidate_v2",
    "evaluate_structure_and_basic_score_v2",
    "materialize_immutable_candidate_v2",
    "select_hypothesis_best_v2",
    "next_seed_v2",
    "next_hypothesis_v2",
    "select_cross_hypothesis_best_v2",
    "promote_or_skip_v2",
    "finalize_v2",
)


class BasicMetricVectorV2(FrozenModel):
    """Evaluator 注入点返回的有限基础指标。"""

    metric_version: str = Field(min_length=1)
    total_loss: FiniteFloat = Field(ge=0.0)
    global_rmse: FiniteFloat = Field(ge=0.0)
    edge_loss: FiniteFloat = Field(ge=0.0)
    geometry_loss: FiniteFloat = Field(ge=0.0)
    alpha_loss: FiniteFloat = Field(ge=0.0)
    diagnostics: tuple[str, ...] = ()


class V2Renderer(Protocol):
    async def render(
        self, fragment_source: str, width: int, height: int
    ) -> RenderResult:
        """编译、绘制并返回无陈旧帧结果。"""

    async def close(self) -> None:
        """幂等关闭本次 renderer session。"""


CatalogFactory = Callable[[PngToShaderV2State], ArtifactCatalog]
InterpretationProvider = Callable[[PngToShaderV2State, ArtifactCatalog], ArtifactRefV2]
IntentContextProvider = Callable[
    [
        PngToShaderV2State,
        TargetMeasurementsV2,
        VisualInterpretationV2,
        RequestConstraintSet,
    ],
    IntentBuildContext,
]
RendererFactory = Callable[[PngToShaderV2State], V2Renderer]
MetricEvaluator = Callable[
    [PngToShaderV2State, ArtifactRefV2, ArtifactResolver], BasicMetricVectorV2
]
StructureEnvelopeProvider = Callable[
    [PngToShaderV2State, ArtifactResolver], ArtifactRefV2
]


class PromotionSink(Protocol):
    """按稳定 operation id 提供 execute/recover 的幂等外部 sink。"""

    def execute(
        self,
        operation: PromotionOperationV1,
        state: PngToShaderV2State,
        trusted_input: object,
    ) -> PromotionSinkResultV1: ...

    def recover(self, operation_id: str) -> PromotionSinkResultV1: ...


RuntimeFaultInjector = Callable[[str], None]
ReferenceArtifactProvider = Callable[
    [PngToShaderV2State, ArtifactResolver], ArtifactRefV2
]
StateTransitionCommitter = Callable[
    [PngToShaderV2State, Mapping[str, Any]], PngToShaderV2State
]


class V2StateStore(Protocol):
    def initialize(self, state: PngToShaderV2State) -> PngToShaderV2State: ...

    def load_last_confirmed(self, run_id: str) -> PngToShaderV2State: ...

    def compare_and_swap_run(
        self,
        run_id: str,
        *,
        expected_run_revision: int,
        changes: Mapping[str, Any],
    ) -> PngToShaderV2State: ...

    def reserve_budget(
        self,
        run_id: str,
        delta: BudgetVectorV2,
        *,
        expected_budget_revision: int,
    ) -> PngToShaderV2State: ...

    def commit_budget(
        self,
        run_id: str,
        *,
        reservation: BudgetVectorV2,
        used: BudgetVectorV2,
        expected_budget_revision: int,
    ) -> PngToShaderV2State: ...


@dataclass(frozen=True)
class PngToShaderV2NodeRuntime:
    """Graph 的 run-scoped 依赖组合根；默认不隐式调用模型或浏览器。"""

    catalog_factory: CatalogFactory
    interpretation_provider: InterpretationProvider | None = None
    intent_context_provider: IntentContextProvider | None = None
    renderer_factory: RendererFactory | None = None
    metric_evaluator: MetricEvaluator | None = None
    state_store: V2StateStore | None = None
    state_committer: StateTransitionCommitter | None = None
    production_admission_enabled: bool = False
    structure_envelope_provider: StructureEnvelopeProvider | None = None
    promotion_sink: PromotionSink | None = None
    fault_injector: RuntimeFaultInjector = lambda _point: None
    admission_policy: MeasurementSeedAdmissionPolicy = field(
        default_factory=MeasurementSeedAdmissionPolicy
    )


class ArtifactBudgetExceeded(ValueError):
    """表示完整 Artifact bytes 无法在七维账本中预留。"""


class PromotionSinkOutcomeUncertain(RuntimeError):
    """表示 sink 调用已越过边界但本地尚不能证明结果。"""


def _execute_promotion_sink(
    sink: PromotionSink,
    operation: PromotionOperationV1,
    state: PngToShaderV2State,
    trusted_input: object,
) -> PromotionSinkResultV1:
    try:
        return sink.execute(operation, state, trusted_input)
    except Exception as exc:
        raise PromotionSinkOutcomeUncertain(
            "Promotion sink execute 结果未知；必须通过 operation_id recover。"
        ) from exc


def _recover_promotion_sink(
    sink: PromotionSink, operation_id: str
) -> PromotionSinkResultV1:
    try:
        return sink.recover(operation_id)
    except Exception as exc:
        raise PromotionSinkOutcomeUncertain(
            "Promotion sink recover 暂时无法证明结果；不得 execute。"
        ) from exc


class _MeteredArtifactCatalog:
    """在每次 Artifact put 前 reserve，成功后按真实 bytes commit。"""

    def __init__(
        self,
        runtime: PngToShaderV2NodeRuntime,
        state: PngToShaderV2State,
        delegate: ArtifactCatalog,
    ) -> None:
        self.runtime = runtime
        self.state = state
        self.delegate = delegate
        self.run_id = state.run_id

    def sync_state(self, state: PngToShaderV2State) -> None:
        self.state = state

    def resolve(self, artifact_id: str) -> ArtifactRefV2:
        return self.delegate.resolve(artifact_id)

    def read_bytes(self, artifact_id: str) -> bytes:
        return self.delegate.read_bytes(artifact_id)

    def put(
        self,
        *,
        run_id: str,
        kind: str,
        schema_version: str,
        content_type: str,
        data: bytes,
    ) -> ArtifactRefV2:
        if run_id != self.run_id:
            raise ValueError("Metered Catalog run_id 不一致。")
        if not _can_consume(self.state.budget_state, artifact_bytes=len(data)):
            raise ArtifactBudgetExceeded("artifact_budget_exhausted")
        reservation = _budget_vector(artifact_bytes=len(data))
        reserved = _reserve_external_effect(self.runtime, self.state, reservation)
        try:
            ref = self.delegate.put(
                run_id=run_id,
                kind=kind,
                schema_version=schema_version,
                content_type=content_type,
                data=data,
            )
        except (OSError, TypeError, ValueError):
            self.state = _commit_external_effect(
                self.runtime,
                reserved,
                reservation=reservation,
                used=_zero_vector(),
            )
            raise
        self.state = _commit_external_effect(
            self.runtime,
            reserved,
            reservation=reservation,
            used=reservation,
        )
        return ref


def make_basic_metric_evaluator_v2(
    reference_provider: ReferenceArtifactProvider,
) -> MetricEvaluator:
    """用真实 Basic Oracle 构造 V2 typed metric adapter，不生成 fixture score。"""

    def evaluate(
        state: PngToShaderV2State,
        render_ref: ArtifactRefV2,
        resolver: ArtifactResolver,
    ) -> BasicMetricVectorV2:
        reference_ref = reference_provider(state, resolver)
        score = evaluate_render(
            _read_exact(resolver, reference_ref),
            _read_exact(resolver, render_ref),
        )
        return BasicMetricVectorV2(
            metric_version=score.metric_version,
            total_loss=score.total_loss,
            global_rmse=score.global_rmse,
            edge_loss=score.edge_loss,
            geometry_loss=score.geometry_loss or 0.0,
            # V1 Basic Oracle 的 RGB composite 没有独立 alpha metric；明确记 0，
            # 不把它用于 topology/structure admission。
            alpha_loss=0.0,
            diagnostics=score.diagnostics,
        )

    return evaluate


def build_png_to_shader_v2_fixture_runtime(
    *,
    catalog_factory: CatalogFactory,
    intent_context_provider: IntentContextProvider,
    renderer_factory: RendererFactory,
    reference_artifact_provider: ReferenceArtifactProvider,
    interpretation_provider: InterpretationProvider | None = None,
    state_store: V2StateStore | None = None,
) -> PngToShaderV2NodeRuntime:
    """构造 validation/Node Lab 共用的 0-model production runtime。

    Interpretation 应优先由 initial State 的 ref 提供；本 factory 不启用
    production admission，也不提供 Memory promotion。
    """
    return PngToShaderV2NodeRuntime(
        catalog_factory=catalog_factory,
        interpretation_provider=interpretation_provider,
        intent_context_provider=intent_context_provider,
        renderer_factory=renderer_factory,
        metric_evaluator=make_basic_metric_evaluator_v2(reference_artifact_provider),
        state_store=state_store,
        production_admission_enabled=False,
    )


def _state(value: PngToShaderV2State | Mapping[str, Any]) -> PngToShaderV2State:
    if isinstance(value, PngToShaderV2State):
        return value
    return PngToShaderV2State.model_validate(value, strict=True)


def _transition(
    runtime: PngToShaderV2NodeRuntime,
    state: PngToShaderV2State,
    **updates: Any,
) -> dict[str, Any]:
    """生成完整、revision+1 的 State；可把提交委托给持久化 CAS adapter。"""
    if runtime.state_store is not None:
        current = runtime.state_store.load_last_confirmed(state.run_id)
        if current != state:
            same_without_budget = (
                current.model_copy(update={"budget_state": state.budget_state}) == state
            )
            monotonic_metering = (
                same_without_budget
                and current.budget_state.limits == state.budget_state.limits
                and current.budget_state.policy_hash == state.budget_state.policy_hash
                and current.budget_state.revision > state.budget_state.revision
                and current.budget_state.reserved == _zero_vector()
                and all(
                    getattr(current.budget_state.used, name)
                    >= getattr(state.budget_state.used, name)
                    for name in BudgetVectorV2.model_fields
                )
            )
            if not monotonic_metering:
                raise RuntimeError("V2 Graph 输入不是 State Store 最后确认 State。")
            state = current
        changes = dict(updates)
        requested_budget = changes.pop("budget_state", current.budget_state)
        if requested_budget != current.budget_state:
            raise ValueError("持久化 Graph transition 的预算必须先 reserve/commit。")
        confirmed = runtime.state_store.compare_and_swap_run(
            state.run_id,
            expected_run_revision=current.run_revision,
            changes=changes,
        )
    elif runtime.state_committer is not None:
        confirmed = runtime.state_committer(state, updates)
    else:
        candidate = state.model_copy(
            update={**updates, "run_revision": state.run_revision + 1}
        )
        confirmed = PngToShaderV2State.model_validate_json(
            candidate.model_dump_json(warnings="none"), strict=True
        )
    # LangGraph 的 strict Pydantic State 需要保留 ArtifactRefV2 dataclass 实例；
    # model_dump(mode="python") 会把它递归转成 dict，下一节点无法 strict 恢复。
    return {name: getattr(confirmed, name) for name in PngToShaderV2State.model_fields}


def _catalog(
    runtime: PngToShaderV2NodeRuntime, state: PngToShaderV2State
) -> ArtifactCatalog:
    delegate = runtime.catalog_factory(state)
    catalog: ArtifactCatalog = (
        cast(ArtifactCatalog, _MeteredArtifactCatalog(runtime, state, delegate))
        if runtime.state_store is not None
        else delegate
    )
    bound_run_id = getattr(catalog, "run_id", state.run_id)
    if bound_run_id != state.run_id:
        raise ValueError("V2 Node ArtifactCatalog 与 State run_id 不一致。")
    return catalog


def _sync_catalog_state(catalog: ArtifactCatalog, state: PngToShaderV2State) -> None:
    if isinstance(catalog, _MeteredArtifactCatalog):
        catalog.sync_state(state)


def _catalog_state(
    catalog: ArtifactCatalog, fallback: PngToShaderV2State
) -> PngToShaderV2State:
    if isinstance(catalog, _MeteredArtifactCatalog):
        return catalog.state
    return fallback


def _read_exact(resolver: ArtifactResolver, ref: ArtifactRefV2) -> bytes:
    if resolver.resolve(ref.artifact_id) != ref:
        raise ValueError("Artifact resolver 返回的引用身份不一致。")
    data = resolver.read_bytes(ref.artifact_id)
    if len(data) != ref.size_bytes or sha256(data).hexdigest() != ref.sha256:
        raise ValueError("Artifact bytes 与引用 size/SHA-256 不一致。")
    return data


def _load_model(
    resolver: ArtifactResolver,
    ref: ArtifactRefV2,
    model_type: type[Any],
    *,
    kind: str,
    schema_version: str,
    content_type: str = _JSON_CONTENT_TYPE,
) -> Any:
    if (
        ref.kind != kind
        or ref.schema_version != schema_version
        or ref.content_type != content_type
    ):
        raise ValueError(f"{kind} ArtifactRef 元数据不符合冻结契约。")
    return model_type.model_validate_json(_read_exact(resolver, ref), strict=True)


def _put_model(
    catalog: ArtifactCatalog,
    state: PngToShaderV2State,
    value: Any,
    *,
    kind: str,
    schema_version: str,
) -> ArtifactRefV2:
    return catalog.put(
        run_id=state.run_id,
        kind=kind,
        schema_version=schema_version,
        content_type=_JSON_CONTENT_TYPE,
        data=value.model_dump_json().encode("utf-8"),
    )


def _zero_vector() -> BudgetVectorV2:
    return BudgetVectorV2(
        wall_time_ms=0,
        model_calls=0,
        model_tokens=0,
        render_calls=0,
        candidate_attempts=0,
        artifact_bytes=0,
        cost_usd_micros=0,
    )


def _consume_budget(state: BudgetStateV2, **increments: int) -> BudgetStateV2:
    values = _zero_vector().model_dump()
    values.update(increments)
    delta = BudgetVectorV2(**values)
    reserved = reserve_budget_v2(state, delta, expected_revision=state.revision)
    return commit_budget_v2(
        reserved,
        reservation=delta,
        used=delta,
        expected_revision=reserved.revision,
    )


def _budget_vector(**increments: int) -> BudgetVectorV2:
    values = _zero_vector().model_dump()
    values.update(increments)
    return BudgetVectorV2(**values)


def _render_request_for_item(
    *,
    state: PngToShaderV2State,
    plan: RenderPlanV2,
    item: RenderPlanItemV2,
) -> RendererRequestReceiptV2:
    raw: dict[str, Any] = {
        "schema_version": "renderer_request_receipt_v2",
        "hash_version": "renderer_request_hash_v2",
        "run_id": state.run_id,
        "attempt_id": plan.attempt_id,
        "target_hypothesis_hash": plan.target_hypothesis_hash,
        "semantic_genome_hash": plan.semantic_genome_hash,
        "compilation_ref": item.compilation_ref,
        "glsl_ref": item.source_ref,
        "render_profile": item.profile,
        "logical_request_ordinal": item.logical_request_ordinal,
        "beauty_capture_index": item.beauty_capture_index,
        "diagnostic_pass_id": item.diagnostic_pass_id,
        "width": item.width,
        "height": item.height,
        "request_hash": "0" * 64,
    }
    raw["request_hash"] = compute_renderer_request_hash(
        {
            "schema_version": "renderer_request_receipt_v2",
            "hash_version": "renderer_request_hash_v2",
            **raw,
        }
    )
    return RendererRequestReceiptV2.model_validate(raw, strict=True)


def _progress_with_outcomes(
    progress: RenderProgressV2,
    outcomes: tuple[RenderCallOutcomeV2, ...],
) -> RenderProgressV2:
    raw = {
        name: getattr(progress, name)
        for name in RenderProgressV2.model_fields
        if name != "record_hash"
    }
    raw["outcomes"] = outcomes
    raw["record_hash"] = "0" * 64
    raw["record_hash"] = compute_render_progress_hash(raw)
    return RenderProgressV2.model_validate(raw, strict=True)


def _reserve_external_effect(
    runtime: PngToShaderV2NodeRuntime,
    state: PngToShaderV2State,
    reservation: BudgetVectorV2,
) -> PngToShaderV2State:
    """在模型/Renderer/attempt 副作用前持久化 reservation。"""
    if runtime.state_store is not None:
        current = runtime.state_store.load_last_confirmed(state.run_id)
        if current != state:
            raise RuntimeError("预算 reservation 前 State 已陈旧或被篡改。")
        return runtime.state_store.reserve_budget(
            state.run_id,
            reservation,
            expected_budget_revision=state.budget_state.revision,
        )
    budget = reserve_budget_v2(
        state.budget_state,
        reservation,
        expected_revision=state.budget_state.revision,
    )
    return PngToShaderV2State.model_validate_json(
        state.model_copy(update={"budget_state": budget}).model_dump_json(
            warnings="none"
        ),
        strict=True,
    )


def _commit_external_effect(
    runtime: PngToShaderV2NodeRuntime,
    state: PngToShaderV2State,
    *,
    reservation: BudgetVectorV2,
    used: BudgetVectorV2,
) -> PngToShaderV2State:
    """副作用完成或已知失败后结算 reservation；崩溃则保留供恢复。"""
    if runtime.state_store is not None:
        current = runtime.state_store.load_last_confirmed(state.run_id)
        if current != state:
            raise RuntimeError("预算 commit 前 State 已陈旧或被篡改。")
        return runtime.state_store.commit_budget(
            state.run_id,
            reservation=reservation,
            used=used,
            expected_budget_revision=state.budget_state.revision,
        )
    budget = commit_budget_v2(
        state.budget_state,
        reservation=reservation,
        used=used,
        expected_revision=state.budget_state.revision,
    )
    return PngToShaderV2State.model_validate_json(
        state.model_copy(update={"budget_state": budget}).model_dump_json(
            warnings="none"
        ),
        strict=True,
    )


def recover_reserved_budget_v2(
    runtime: PngToShaderV2NodeRuntime,
    state: PngToShaderV2State,
) -> PngToShaderV2State:
    """恢复崩溃遗留 reservation，并把未知 Renderer 调用固化为已消费 slot。"""
    reservation = state.budget_state.reserved
    if reservation == _zero_vector():
        return state
    if not reservation.render_calls:
        return _commit_external_effect(
            runtime, state, reservation=reservation, used=reservation
        )
    if reservation.render_calls != 1:
        raise RuntimeError("Renderer 恢复只接受单个持久 call reservation。")
    reserved_revision = state.budget_state.revision
    if (
        state.active_render_plan_ref is None
        or state.active_render_progress_ref is None
        or state.active_render_call_ordinal is None
    ):
        raise RuntimeError("Renderer reservation 缺少稳定 plan/progress/call intent。")
    catalog = _catalog(runtime, state)
    plan = load_render_model(
        state.active_render_plan_ref, resolver=catalog, run_id=state.run_id
    )
    progress = load_render_model(
        state.active_render_progress_ref, resolver=catalog, run_id=state.run_id
    )
    if not isinstance(plan, RenderPlanV2) or not isinstance(progress, RenderProgressV2):
        raise RuntimeError("Renderer recovery plan/progress 类型错误。")
    logical = progress.next_logical_request_ordinal
    if logical > len(plan.items):
        raise RuntimeError("Renderer reservation 出现在已完成 plan。")
    item = plan.items[logical - 1]
    request = _render_request_for_item(state=state, plan=plan, item=item)
    request_ref = materialize_renderer_request(
        catalog=catalog, run_id=state.run_id, receipt=request
    )
    state = _catalog_state(catalog, state)
    if progress.has_uncommitted_outcome:
        latest = progress.outcomes[-1]
        if (
            latest.logical_request_ordinal != logical
            or latest.physical_call_ordinal != state.active_render_call_ordinal
            or latest.renderer_request_hash != request.request_hash
        ):
            raise RuntimeError("Renderer uncommitted progress 与 call intent 不一致。")
    else:
        call_ordinal = state.active_render_call_ordinal
        assert call_ordinal is not None
        unknown_ref = _attempt_evidence_ref(
            catalog,
            state,
            stage="render",
            code="renderer_call_outcome_unknown_after_crash",
            outcome="unknown",
            renderer_request_hash=request.request_hash,
            call_ordinal=call_ordinal,
        )
        state = _catalog_state(catalog, state)
        unknown = RenderCallOutcomeV2(
            logical_request_ordinal=logical,
            physical_call_ordinal=call_ordinal,
            renderer_request_ref=request_ref,
            renderer_request_artifact_sha256=request_ref.sha256,
            renderer_request_hash=request.request_hash,
            outcome="unknown",
            error_code="renderer_call_outcome_unknown_after_crash",
            attempt_evidence_ref=unknown_ref,
            budget_revision_reserved=reserved_revision,
        )
        progress = _progress_with_outcomes(
            progress, (*progress.outcomes, unknown)
        )
        progress_ref = materialize_render_model(
            catalog=catalog, run_id=state.run_id, value=progress
        )
        state = _catalog_state(catalog, state)
        state = _state(
            _transition(
                runtime,
                state,
                active_render_progress_ref=progress_ref,
                active_attempt_evidence_refs=_append_ref(
                    state.active_attempt_evidence_refs, unknown_ref
                ),
            )
        )
        _sync_catalog_state(catalog, state)
    runtime.fault_injector("render_recovery.after_evidence_before_budget_commit")
    recovered = _commit_external_effect(
        runtime, state, reservation=reservation, used=reservation
    )
    committed_outcome = progress.outcomes[-1].model_copy(
        update={"budget_revision_committed": recovered.budget_state.revision}
    )
    progress = _progress_with_outcomes(
        progress, (*progress.outcomes[:-1], committed_outcome)
    )
    _sync_catalog_state(catalog, recovered)
    progress_ref = materialize_render_model(
        catalog=catalog, run_id=recovered.run_id, value=progress
    )
    recovered = _catalog_state(catalog, recovered)
    return _state(
        _transition(
            runtime,
            recovered,
            active_render_progress_ref=progress_ref,
            active_render_call_ordinal=None,
        )
    )


def _can_consume(state: BudgetStateV2, **increments: int) -> bool:
    for name, increment in increments.items():
        if getattr(state.used, name) + getattr(
            state.reserved, name
        ) + increment > getattr(state.limits, name):
            return False
    return True


def _replace_branch(
    state: PngToShaderV2State,
    index: int,
    branch: HypothesisBranchStateV2,
) -> tuple[HypothesisBranchStateV2, ...]:
    branches = list(state.hypothesis_branches)
    branches[index] = branch
    return tuple(branches)


def _candidate_id(state: PngToShaderV2State, genome: TypedEffectGenome) -> str:
    digest = sha256(
        json.dumps(
            {
                "run_id": state.run_id,
                "genome_id": genome.genome_id,
                "semantic_genome_hash": compute_genome_hashes(
                    genome
                ).semantic_genome_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"candidate-v2-{digest[:24]}"


def _active_branch(state: PngToShaderV2State) -> HypothesisBranchStateV2:
    if state.hypothesis_cursor >= len(state.hypothesis_branches):
        raise ValueError("当前没有 active hypothesis branch。")
    return state.hypothesis_branches[state.hypothesis_cursor]


def _attempt_id(
    state: PngToShaderV2State,
    *,
    target_hypothesis_hash: str,
    semantic_genome_hash: str,
) -> str:
    digest = sha256(
        json.dumps(
            {
                "run_id": state.run_id,
                "target_hypothesis_hash": target_hypothesis_hash,
                "semantic_genome_hash": semantic_genome_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"attempt-v2-{digest[:24]}"


def _active_attempt_identity(
    catalog: ArtifactResolver,
    state: PngToShaderV2State,
) -> tuple[str, str, str]:
    if (
        state.active_attempt_id is None
        or state.active_semantic_genome_hash is None
        or state.active_genome_ref is None
    ):
        raise ValueError("当前 candidate attempt identity 不完整。")
    branch = _active_branch(state)
    intent = cast(
        IntentIR,
        _load_model(
            catalog,
            branch.intent_ref,
            IntentIR,
            kind=INTENT_ARTIFACT_KIND,
            schema_version=INTENT_ARTIFACT_SCHEMA_VERSION,
        ),
    )
    genome = cast(
        TypedEffectGenome,
        _load_model(
            catalog,
            state.active_genome_ref,
            TypedEffectGenome,
            kind=GENOME_ARTIFACT_KIND,
            schema_version=GENOME_ARTIFACT_SCHEMA_VERSION,
        ),
    )
    semantic_hash = compute_genome_hashes(genome).semantic_genome_hash
    if (
        intent.target_hypothesis_id != branch.target_hypothesis_id
        or intent.target_hypothesis_hash != branch.target_hypothesis_hash
        or genome.provenance.intent_id != intent.intent_id
        or genome.provenance.target_hypothesis_id != branch.target_hypothesis_id
        or genome.provenance.target_hypothesis_hash != branch.target_hypothesis_hash
        or state.active_semantic_genome_hash != semantic_hash
    ):
        raise ValueError("active attempt 与 Intent/Genome identity 不一致。")
    expected_attempt_id = _attempt_id(
        state,
        target_hypothesis_hash=branch.target_hypothesis_hash,
        semantic_genome_hash=semantic_hash,
    )
    if state.active_attempt_id != expected_attempt_id:
        raise ValueError("active_attempt_id 与 hypothesis/genome identity 不一致。")
    return expected_attempt_id, branch.target_hypothesis_hash, semantic_hash


def _attempt_evidence_ref(
    catalog: ArtifactCatalog,
    state: PngToShaderV2State,
    *,
    stage: Literal["compile", "render", "evaluate", "materialize"],
    code: str,
    outcome: Literal["transient_failure", "failure", "success", "unknown"] = (
        "failure"
    ),
    renderer_request_hash: str | None = None,
    call_ordinal: int | None = None,
) -> ArtifactRefV2:
    attempt_id, target_hash, semantic_hash = _active_attempt_identity(catalog, state)
    return materialize_attempt_evidence(
        catalog=catalog,
        run_id=state.run_id,
        evidence=CandidateAttemptEvidenceV1(
            run_id=state.run_id,
            attempt_id=attempt_id,
            target_hypothesis_hash=target_hash,
            semantic_genome_hash=semantic_hash,
            stage=stage,
            outcome=outcome,
            error_code=None if outcome == "success" else code,
            renderer_request_hash=renderer_request_hash,
            call_ordinal=call_ordinal,
        ),
    )


def _close_attempt_failure(
    catalog: ArtifactCatalog,
    state: PngToShaderV2State,
    *,
    stage: Literal["compile", "render", "evaluate", "materialize"],
    code: str,
    status: Literal["rejected", "compile_failed", "render_failed", "evaluation_failed"],
    extra_evidence_refs: tuple[ArtifactRefV2, ...] = (),
) -> ArtifactRefV2:
    attempt_id, target_hash, semantic_hash = _active_attempt_identity(catalog, state)
    error_ref = _attempt_evidence_ref(
        catalog,
        state,
        stage=stage,
        code=code,
    )
    evidence_refs = state.active_attempt_evidence_refs
    for ref in (*extra_evidence_refs, error_ref):
        evidence_refs = _append_ref(evidence_refs, ref)
    return materialize_candidate_attempt(
        catalog=catalog,
        run_id=state.run_id,
        attempt=CandidateAttemptRecord(
            attempt_id=attempt_id,
            run_id=state.run_id,
            target_hypothesis_hash=target_hash,
            semantic_genome_hash=semantic_hash,
            status=status,
            error_code=code,
            evidence_refs=evidence_refs,
        ),
    )


def _append_ref(
    refs: tuple[ArtifactRefV2, ...], ref: ArtifactRefV2
) -> tuple[ArtifactRefV2, ...]:
    return refs if ref in refs else (*refs, ref)


def make_initialize_run_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def initialize(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        _catalog(runtime, state)
        if runtime.state_store is not None:
            try:
                recovered = runtime.state_store.load_last_confirmed(state.run_id)
            except V2StateCheckpointNotFoundError:
                recovered = runtime.state_store.initialize(state)
            if recovered != state:
                same_without_budget = (
                    recovered.model_copy(update={"budget_state": state.budget_state})
                    == state
                )
                if (
                    not same_without_budget
                    or recovered.budget_state.reserved == _zero_vector()
                ):
                    raise RuntimeError(
                        "V2 initialize 输入与最后确认 checkpoint 不一致。"
                    )
            state = recovered
            if state.budget_state.reserved != _zero_vector():
                state = recover_reserved_budget_v2(runtime, state)
        # 恢复不得把 measured/interpreted/loop/finalized 降级为 initialized。
        return _transition(runtime, state)

    return initialize


def make_prepare_context_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def prepare(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        try:
            _catalog(runtime, state)
        except (FileNotFoundError, TypeError, ValueError):
            return _transition(
                runtime, state, stop_reason="artifact_context_unavailable"
            )
        return _transition(runtime, state)

    return prepare


def make_ingest_target_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def ingest(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        catalog = _catalog(runtime, state)
        try:
            _load_model(
                catalog,
                state.request_constraint_set_ref,
                RequestConstraintSet,
                kind="request_constraint_set",
                schema_version="request_constraint_set_v1",
            )
            _read_exact(catalog, state.measurements_ref)
        except (FileNotFoundError, TypeError, ValueError):
            return _transition(
                runtime, state, stop_reason="target_ingest_recovery_failed"
            )
        return _transition(runtime, state)

    return ingest


def make_measure_target_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def measure(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        catalog = _catalog(runtime, state)
        try:
            _load_model(
                catalog,
                state.measurements_ref,
                TargetMeasurementsV2,
                kind="target_measurements",
                schema_version="target_measurements_v2_2",
            )
        except (FileNotFoundError, TypeError, ValueError):
            return _transition(
                runtime, state, stop_reason="measurement_recovery_failed"
            )
        return _transition(runtime, state, phase="measured")

    return measure


def make_interpret_target_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def interpret(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        catalog = _catalog(runtime, state)
        ref = state.visual_interpretation_ref
        if ref is None:
            if runtime.interpretation_provider is None:
                return _transition(
                    runtime, state, stop_reason="interpretation_provider_unavailable"
                )
            if not _can_consume(state.budget_state, model_calls=1):
                return _transition(runtime, state, stop_reason="model_budget_exhausted")
            reservation = _budget_vector(model_calls=1)
            reserved_state = _reserve_external_effect(runtime, state, reservation)
            _sync_catalog_state(catalog, reserved_state)
            try:
                ref = runtime.interpretation_provider(reserved_state, catalog)
            except (FileNotFoundError, TypeError, ValueError):
                committed = _commit_external_effect(
                    runtime,
                    _catalog_state(catalog, reserved_state),
                    reservation=reservation,
                    used=reservation,
                )
                _sync_catalog_state(catalog, committed)
                return _transition(
                    runtime, committed, stop_reason="visual_interpretation_failed"
                )
            state = _commit_external_effect(
                runtime,
                _catalog_state(catalog, reserved_state),
                reservation=reservation,
                used=reservation,
            )
            _sync_catalog_state(catalog, state)
        try:
            _load_model(
                catalog,
                ref,
                VisualInterpretationV2,
                kind="visual_interpretation",
                schema_version="visual_interpretation_v2_1",
            )
        except (FileNotFoundError, TypeError, ValueError):
            return _transition(
                runtime, state, stop_reason="visual_interpretation_recovery_failed"
            )
        return _transition(
            runtime,
            state,
            phase="interpreted",
            visual_interpretation_ref=ref,
        )

    return interpret


def make_analyze_visual_layers_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    """§12 名称下复用严格 Interpretation 恢复/注入节点。"""
    return make_interpret_target_v2_node(runtime)


def make_build_intent_variants_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def build(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        catalog = _catalog(runtime, state)
        if state.visual_interpretation_ref is None:
            return _transition(
                runtime, state, stop_reason="visual_interpretation_missing"
            )
        if runtime.intent_context_provider is None:
            return _transition(
                runtime, state, stop_reason="intent_context_provider_unavailable"
            )
        try:
            measurements = cast(
                TargetMeasurementsV2,
                _load_model(
                    catalog,
                    state.measurements_ref,
                    TargetMeasurementsV2,
                    kind="target_measurements",
                    schema_version="target_measurements_v2_2",
                ),
            )
            interpretation = cast(
                VisualInterpretationV2,
                _load_model(
                    catalog,
                    state.visual_interpretation_ref,
                    VisualInterpretationV2,
                    kind="visual_interpretation",
                    schema_version="visual_interpretation_v2_1",
                ),
            )
            constraints = cast(
                RequestConstraintSet,
                _load_model(
                    catalog,
                    state.request_constraint_set_ref,
                    RequestConstraintSet,
                    kind="request_constraint_set",
                    schema_version="request_constraint_set_v1",
                ),
            )
            context = runtime.intent_context_provider(
                state, measurements, interpretation, constraints
            )
            result = build_intent_variants(
                measurements,
                interpretation,
                constraints,
                context,
            )
            _put_model(
                catalog,
                state,
                result,
                kind="intent_build_result",
                schema_version="intent_build_result_v3_1",
            )
            branches = tuple(
                HypothesisBranchStateV2(
                    target_hypothesis_id=intent.target_hypothesis_id,
                    target_hypothesis_hash=intent.target_hypothesis_hash,
                    intent_ref=_put_model(
                        catalog,
                        state,
                        intent,
                        kind=INTENT_ARTIFACT_KIND,
                        schema_version=INTENT_ARTIFACT_SCHEMA_VERSION,
                    ),
                    strategy_ref=None,
                    seed_refs=(),
                    seed_cursor=0,
                    hypothesis_best_id=None,
                    status="pending",
                )
                for intent in result.variants
            )
        except (FileNotFoundError, TypeError, ValueError):
            return _transition(runtime, state, stop_reason="intent_build_failed")
        if not branches:
            return _transition(
                runtime,
                state,
                phase="intent_built",
                hypothesis_branches=(),
                stop_reason="no_feasible_intent_hypothesis",
            )
        return _transition(
            runtime,
            state,
            phase="intent_built",
            hypothesis_branches=branches,
            hypothesis_cursor=0,
        )

    return build


def make_dequeue_hypothesis_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def dequeue(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        cursor = state.hypothesis_cursor
        branches = state.hypothesis_branches
        while cursor < len(branches) and branches[cursor].status in {
            "completed",
            "failed",
        }:
            cursor += 1
        if cursor >= len(branches):
            return _transition(
                runtime,
                state,
                hypothesis_cursor=len(branches),
                active_seed_ref=None,
                active_genome_ref=None,
            )
        branch = branches[cursor]
        if branch.status == "pending":
            branch = branch.model_copy(update={"status": "running"})
            branches = tuple(
                branch if index == cursor else item
                for index, item in enumerate(branches)
            )
        return _transition(
            runtime,
            state,
            phase="seeding",
            hypothesis_cursor=cursor,
            hypothesis_branches=branches,
            active_seed_ref=None,
            active_genome_ref=None,
            active_compilation_ref=None,
            active_diagnostic_compilation_ref=None,
            active_render_plan_ref=None,
            active_render_progress_ref=None,
            active_render_repeatability_ref=None,
            active_rendered_structure_evidence_ref=None,
            active_rendered_structure_verification_ref=None,
            active_evaluation_refs=(),
        )

    return dequeue


def make_plan_strategy_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def plan(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        catalog = _catalog(runtime, state)
        branch = _active_branch(state)
        try:
            intent = cast(
                IntentIR,
                _load_model(
                    catalog,
                    branch.intent_ref,
                    IntentIR,
                    kind=INTENT_ARTIFACT_KIND,
                    schema_version=INTENT_ARTIFACT_SCHEMA_VERSION,
                ),
            )
            strategy_ref = catalog.put(
                run_id=state.run_id,
                kind="strategy_plan",
                schema_version="strategy_plan_v2_3",
                content_type=_JSON_CONTENT_TYPE,
                data=json.dumps(
                    {
                        "schema_version": "strategy_plan_v2_3",
                        "intent_id": intent.intent_id,
                        "target_hypothesis_id": intent.target_hypothesis_id,
                        "policy": "deterministic_three_seed_v2_2",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
            updated = branch.model_copy(update={"strategy_ref": strategy_ref})
        except (FileNotFoundError, TypeError, ValueError):
            return _transition(runtime, state, stop_reason="strategy_planning_failed")
        return _transition(
            runtime,
            state,
            hypothesis_branches=_replace_branch(
                state, state.hypothesis_cursor, updated
            ),
        )

    return plan


def make_propose_seed_plans_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def propose(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        catalog = _catalog(runtime, state)
        branch = _active_branch(state)
        try:
            intent = cast(
                IntentIR,
                _load_model(
                    catalog,
                    branch.intent_ref,
                    IntentIR,
                    kind=INTENT_ARTIFACT_KIND,
                    schema_version=INTENT_ARTIFACT_SCHEMA_VERSION,
                ),
            )
            plans = build_seed_plans(intent, random_seed=0)
            plan_ref = catalog.put(
                run_id=state.run_id,
                kind="seed_plan_set",
                schema_version="seed_plan_set_v1",
                content_type=_JSON_CONTENT_TYPE,
                data=json.dumps(
                    [json.loads(item.model_dump_json()) for item in plans],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
            updated = branch.model_copy(update={"strategy_ref": plan_ref})
        except (FileNotFoundError, TypeError, ValueError):
            return _transition(runtime, state, stop_reason="seed_plan_proposal_failed")
        return _transition(
            runtime,
            state,
            hypothesis_branches=_replace_branch(
                state, state.hypothesis_cursor, updated
            ),
        )

    return propose


def make_expand_validate_seeds_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def expand(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        catalog = _catalog(runtime, state)
        branch = _active_branch(state)
        if branch.strategy_ref is None:
            return _transition(runtime, state, stop_reason="seed_plan_set_missing")
        try:
            intent = cast(
                IntentIR,
                _load_model(
                    catalog,
                    branch.intent_ref,
                    IntentIR,
                    kind=INTENT_ARTIFACT_KIND,
                    schema_version=INTENT_ARTIFACT_SCHEMA_VERSION,
                ),
            )
            raw_plans = json.loads(_read_exact(catalog, branch.strategy_ref))
            plans = tuple(
                SeedPlanV1.model_validate_json(
                    json.dumps(
                        item,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    strict=True,
                )
                for item in raw_plans
            )
            if len(plans) != 3:
                raise ValueError("SeedPlan set 必须恰好包含三个计划。")
            result = expand_seed_plans(
                intent,
                plans=plans,
            )
            if not result.diversity.gate_passed:
                return _transition(
                    runtime, state, stop_reason="seed_diversity_gate_failed"
                )
            expansion_ref = _put_model(
                catalog,
                state,
                result,
                kind="seed_expansion_result",
                schema_version="seed_expansion_result_v2",
            )
            genome_refs = tuple(
                _put_model(
                    catalog,
                    state,
                    item.genome,
                    kind=GENOME_ARTIFACT_KIND,
                    schema_version=GENOME_ARTIFACT_SCHEMA_VERSION,
                )
                for item in result.expanded_seeds
            )
            updated = branch.model_copy(
                update={
                    "strategy_ref": expansion_ref,
                    "seed_refs": genome_refs,
                    "seed_cursor": 0,
                }
            )
        except (FileNotFoundError, TypeError, ValueError, json.JSONDecodeError):
            return _transition(runtime, state, stop_reason="seed_expansion_failed")
        return _transition(
            runtime,
            state,
            hypothesis_branches=_replace_branch(
                state, state.hypothesis_cursor, updated
            ),
        )

    return expand


def make_dequeue_seed_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def dequeue(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        branch = _active_branch(state)
        if branch.seed_cursor >= len(branch.seed_refs):
            return _transition(
                runtime,
                state,
                active_seed_ref=None,
                active_genome_ref=None,
            )
        genome_ref = branch.seed_refs[branch.seed_cursor]
        updated = branch.model_copy(update={"seed_cursor": branch.seed_cursor + 1})
        return _transition(
            runtime,
            state,
            phase="seeding",
            hypothesis_branches=_replace_branch(
                state, state.hypothesis_cursor, updated
            ),
            active_seed_ref=genome_ref,
            active_genome_ref=genome_ref,
            active_compilation_ref=None,
            active_diagnostic_compilation_ref=None,
            active_render_plan_ref=None,
            active_render_progress_ref=None,
            active_render_repeatability_ref=None,
            active_rendered_structure_evidence_ref=None,
            active_rendered_structure_verification_ref=None,
            active_evaluation_refs=(),
        )

    return dequeue


def make_prepare_candidate_attempt_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def prepare(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        if state.active_genome_ref is None:
            return _transition(runtime, state, stop_reason="active_genome_missing")
        catalog = _catalog(runtime, state)
        try:
            branch = _active_branch(state)
            genome = cast(
                TypedEffectGenome,
                _load_model(
                    catalog,
                    state.active_genome_ref,
                    TypedEffectGenome,
                    kind=GENOME_ARTIFACT_KIND,
                    schema_version=GENOME_ARTIFACT_SCHEMA_VERSION,
                ),
            )
            semantic_hash = compute_genome_hashes(genome).semantic_genome_hash
            attempt_id = _attempt_id(
                state,
                target_hypothesis_hash=branch.target_hypothesis_hash,
                semantic_genome_hash=semantic_hash,
            )
        except (FileNotFoundError, TypeError, ValueError):
            return _transition(runtime, state, stop_reason="attempt_identity_failed")
        if not _can_consume(state.budget_state, candidate_attempts=1):
            return _transition(
                runtime, state, stop_reason="candidate_attempt_budget_exhausted"
            )
        reservation = _budget_vector(candidate_attempts=1)
        reserved = _reserve_external_effect(runtime, state, reservation)
        committed = _commit_external_effect(
            runtime,
            reserved,
            reservation=reservation,
            used=reservation,
        )
        return _transition(
            runtime,
            committed,
            phase="compiling",
            active_compilation_ref=None,
            active_diagnostic_compilation_ref=None,
            active_render_plan_ref=None,
            active_render_progress_ref=None,
            active_render_repeatability_ref=None,
            active_rendered_structure_evidence_ref=None,
            active_rendered_structure_verification_ref=None,
            active_evaluation_refs=(),
            active_attempt_id=attempt_id,
            active_semantic_genome_hash=semantic_hash,
            active_attempt_evidence_refs=(),
        )

    return prepare


def make_compile_genome_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def compile_node(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        catalog = _catalog(runtime, state)
        if state.active_genome_ref is None:
            return _transition(runtime, state, stop_reason="active_genome_missing")
        try:
            _active_attempt_identity(catalog, state)
            genome = cast(
                TypedEffectGenome,
                _load_model(
                    catalog,
                    state.active_genome_ref,
                    TypedEffectGenome,
                    kind=GENOME_ARTIFACT_KIND,
                    schema_version=GENOME_ARTIFACT_SCHEMA_VERSION,
                ),
            )
            product = compile_effect_genome(genome)
            bundle = materialize_compilation(
                product,
                catalog=catalog,
                run_id=state.run_id,
            )
            bundle_ref = _put_model(
                catalog,
                state,
                bundle,
                kind=COMPILATION_ARTIFACT_KIND,
                schema_version=TYPED_COMPILATION_ARTIFACT_SCHEMA_VERSION,
            )
            diagnostic_product = compile_diagnostic_passes(genome)
            diagnostic_bundle = materialize_diagnostic_compilation(
                diagnostic_product,
                catalog=catalog,
                run_id=state.run_id,
            )
            diagnostic_bundle_ref = _put_model(
                catalog,
                state,
                diagnostic_bundle,
                kind="diagnostic_compilation_bundle",
                schema_version="diagnostic_compilation_bundle_v3",
            )
        except CompilerDefectError as exc:
            failure = _close_attempt_failure(
                catalog,
                state,
                stage="compile",
                code=exc.code,
                status="compile_failed",
            )
            return _transition(
                runtime,
                state,
                stop_reason=f"compiler_defect:{exc.code}",
                active_compilation_ref=None,
                active_diagnostic_compilation_ref=None,
                candidate_summary_refs=_append_ref(
                    state.candidate_summary_refs, failure
                ),
            )
        except (FileNotFoundError, TypeError, ValueError):
            failure = _close_attempt_failure(
                catalog,
                state,
                stage="compile",
                code="genome_recovery_failed",
                status="compile_failed",
            )
            return _transition(
                runtime,
                state,
                active_seed_ref=None,
                active_genome_ref=None,
                active_compilation_ref=None,
                active_attempt_id=None,
                active_semantic_genome_hash=None,
                active_attempt_evidence_refs=(),
                candidate_summary_refs=_append_ref(
                    state.candidate_summary_refs, failure
                ),
            )
        return _transition(
            runtime,
            state,
            phase="compiling",
            active_compilation_ref=bundle_ref,
            active_diagnostic_compilation_ref=diagnostic_bundle_ref,
        )

    return compile_node


async def _close_renderer(renderer: V2Renderer) -> None:
    result = renderer.close()
    if inspect.isawaitable(result):
        await cast(Awaitable[None], result)


def _render_plan_for_attempt(
    *,
    state: PngToShaderV2State,
    bundle: CompilationBundle,
    diagnostic_bundle: DiagnosticCompilationBundleV3,
    width: int,
    height: int,
) -> RenderPlanV2:
    """从冻结 Compilation refs 构造唯一、不可变的 Renderer plan。"""
    if (
        state.active_attempt_id is None
        or state.active_semantic_genome_hash is None
        or state.active_compilation_ref is None
        or state.active_diagnostic_compilation_ref is None
    ):
        raise ValueError("Render plan 缺少 attempt/Compilation identity。")
    branch = _active_branch(state)
    items: list[RenderPlanItemV2] = [
        RenderPlanItemV2(
            logical_request_ordinal=index + 1,
            profile="beauty_full_v1",
            compilation_ref=state.active_compilation_ref,
            source_ref=bundle.glsl_ref,
            width=width,
            height=height,
            beauty_capture_index=index,
        )
        for index in range(BEAUTY_CAPTURE_COUNT)
    ]
    for diagnostic in diagnostic_bundle.passes:
        diagnostic_width, diagnostic_height = rendered_structure_diagnostic_size_v2(
            pass_kind=diagnostic.pass_kind,
            width=width,
            height=height,
        )
        profile: Literal[
            "subject_visible_delta_full_v1",
            "instance_visible_delta_full_v1",
            "layer_visible_delta_lowres_v1",
        ] = (
            "subject_visible_delta_full_v1"
            if diagnostic.pass_kind == "subject_visible_delta"
            else (
                "instance_visible_delta_full_v1"
                if diagnostic.pass_kind == "instance_visible_delta"
                else "layer_visible_delta_lowres_v1"
            )
        )
        items.append(
            RenderPlanItemV2(
                logical_request_ordinal=len(items) + 1,
                profile=profile,
                compilation_ref=state.active_diagnostic_compilation_ref,
                source_ref=diagnostic.source_ref,
                width=diagnostic_width,
                height=diagnostic_height,
                diagnostic_pass_id=diagnostic.pass_id,
            )
        )
    raw: dict[str, Any] = {
        "schema_version": "renderer_plan_v3",
        "hash_version": "renderer_plan_hash_v3",
        "run_id": state.run_id,
        "attempt_id": state.active_attempt_id,
        "target_hypothesis_hash": branch.target_hypothesis_hash,
        "semantic_genome_hash": state.active_semantic_genome_hash,
        "budget_policy_hash": state.budget_state.policy_hash,
        "ownership_policy_version": DIAGNOSTIC_OWNERSHIP_POLICY_VERSION,
        "items": tuple(items),
        "plan_hash": "0" * 64,
    }
    raw["plan_hash"] = compute_render_plan_hash(raw)
    return RenderPlanV2.model_validate(raw, strict=True)


def _initial_render_progress(
    *, state: PngToShaderV2State, plan: RenderPlanV2, plan_ref: ArtifactRefV2
) -> RenderProgressV2:
    raw: dict[str, Any] = {
        "schema_version": "renderer_progress_v2",
        "hash_version": "renderer_progress_hash_v2",
        "run_id": state.run_id,
        "attempt_id": plan.attempt_id,
        "plan_ref": plan_ref,
        "plan_hash": plan.plan_hash,
        "budget_policy_hash": state.budget_state.policy_hash,
        "outcomes": (),
        "record_hash": "0" * 64,
    }
    raw["record_hash"] = compute_render_progress_hash(raw)
    return RenderProgressV2.model_validate(raw, strict=True)


def _renderer_environment_from_result(result: RenderResult) -> RendererEnvironmentReceiptV3:
    """把成功 RenderResult 的像素相关环境冻结为 typed receipt。"""
    if result.metadata is None:
        raise ValueError("成功 Renderer result 缺少环境 metadata。")
    metadata = result.metadata
    raw: dict[str, Any] = {
        "schema_version": "renderer_environment_receipt_v3",
        "hash_version": "renderer_environment_hash_v3",
        "renderer_version": metadata.renderer_version,
        "browser_version": metadata.browser_version,
        "gl_version": metadata.gl_version,
        "glsl_version": metadata.glsl_version,
        "gl_vendor": metadata.gl_vendor,
        "gl_renderer": metadata.gl_renderer,
        "webgl_context_kind": metadata.webgl_context_kind,
        "canvas_alpha": metadata.canvas_alpha,
        "canvas_antialias": metadata.canvas_antialias,
        "canvas_depth": metadata.canvas_depth,
        "canvas_stencil": metadata.canvas_stencil,
        "canvas_alpha_mode": (
            "preserve_transparent_alpha_v1"
            if metadata.canvas_alpha
            else "force_opaque_alpha_v1"
        ),
        "canvas_clear_color_rgba": metadata.canvas_clear_color_rgba,
        "premultiplied_alpha": metadata.premultiplied_alpha,
        "preserve_drawing_buffer": metadata.preserve_drawing_buffer,
        "environment_hash": "0" * 64,
    }
    raw["environment_hash"] = compute_renderer_environment_hash(raw)
    return RendererEnvironmentReceiptV3.model_validate(raw, strict=True)


def _settle_uncommitted_render_outcome(
    runtime: PngToShaderV2NodeRuntime,
    state: PngToShaderV2State,
    catalog: ArtifactCatalog,
) -> PngToShaderV2State:
    """恢复 budget 已 commit、但 progress 尚未确认的崩溃窗口。"""
    if state.active_render_progress_ref is None:
        return state
    loaded = load_render_model(
        state.active_render_progress_ref, resolver=catalog, run_id=state.run_id
    )
    if not isinstance(loaded, RenderProgressV2) or not loaded.has_uncommitted_outcome:
        return state
    latest = loaded.outcomes[-1]
    if state.budget_state.reserved.render_calls:
        return state
    if state.active_render_call_ordinal != latest.physical_call_ordinal:
        raise RuntimeError("未结算 Render progress 与持久 call intent 不一致。")
    committed = latest.model_copy(
        update={"budget_revision_committed": state.budget_state.revision}
    )
    progress = _progress_with_outcomes(
        loaded, (*loaded.outcomes[:-1], committed)
    )
    progress_ref = materialize_render_model(
        catalog=catalog, run_id=state.run_id, value=progress
    )
    state = _catalog_state(catalog, state)
    return _state(
        _transition(
            runtime,
            state,
            active_render_progress_ref=progress_ref,
            active_render_call_ordinal=None,
        )
    )


def _render_failure_transition(
    runtime: PngToShaderV2NodeRuntime,
    state: PngToShaderV2State,
    catalog: ArtifactCatalog,
    *,
    code: str,
) -> dict[str, Any]:
    failure = _close_attempt_failure(
        catalog,
        state,
        stage="render",
        code=code,
        status="render_failed",
    )
    state = _catalog_state(catalog, state)
    return _transition(
        runtime,
        state,
        active_seed_ref=None,
        active_genome_ref=None,
        active_compilation_ref=None,
        active_diagnostic_compilation_ref=None,
        active_render_plan_ref=None,
        active_render_progress_ref=None,
        active_render_repeatability_ref=None,
        active_rendered_structure_evidence_ref=None,
        active_rendered_structure_verification_ref=None,
        active_evaluation_refs=(),
        active_attempt_id=None,
        active_semantic_genome_hash=None,
        active_attempt_evidence_refs=(),
        active_render_call_ordinal=None,
        candidate_summary_refs=_append_ref(state.candidate_summary_refs, failure),
    )


def make_render_candidate_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], Awaitable[dict[str, Any]]]:
    async def render_node(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        catalog = _catalog(runtime, state)
        if (
            state.active_compilation_ref is None
            or state.active_diagnostic_compilation_ref is None
        ):
            return _transition(runtime, state, stop_reason="active_compilation_missing")
        if runtime.renderer_factory is None:
            return _transition(runtime, state, stop_reason="renderer_unavailable")
        try:
            bundle = cast(
                CompilationBundle,
                _load_model(
                    catalog,
                    state.active_compilation_ref,
                    CompilationBundle,
                    kind=COMPILATION_ARTIFACT_KIND,
                    schema_version=TYPED_COMPILATION_ARTIFACT_SCHEMA_VERSION,
                ),
            )
            diagnostic_bundle = cast(
                DiagnosticCompilationBundleV3,
                _load_model(
                    catalog,
                    state.active_diagnostic_compilation_ref,
                    DiagnosticCompilationBundleV3,
                    kind="diagnostic_compilation_bundle",
                    schema_version="diagnostic_compilation_bundle_v3",
                ),
            )
            branch = _active_branch(state)
            intent = cast(
                IntentIR,
                _load_model(
                    catalog,
                    branch.intent_ref,
                    IntentIR,
                    kind=INTENT_ARTIFACT_KIND,
                    schema_version=INTENT_ARTIFACT_SCHEMA_VERSION,
                ),
            )
            _active_attempt_identity(catalog, state)
        except (FileNotFoundError, TypeError, UnicodeDecodeError, ValueError):
            return _render_failure_transition(
                runtime, state, catalog, code="render_input_recovery_failed"
            )

        width, height = intent.canvas.image_size
        try:
            expected_plan = _render_plan_for_attempt(
                state=state,
                bundle=bundle,
                diagnostic_bundle=diagnostic_bundle,
                width=width,
                height=height,
            )
            if state.active_render_plan_ref is None:
                plan_ref = materialize_render_model(
                    catalog=catalog, run_id=state.run_id, value=expected_plan
                )
                state = _catalog_state(catalog, state)
                progress = _initial_render_progress(
                    state=state, plan=expected_plan, plan_ref=plan_ref
                )
                progress_ref = materialize_render_model(
                    catalog=catalog, run_id=state.run_id, value=progress
                )
                state = _catalog_state(catalog, state)
                state = _state(
                    _transition(
                        runtime,
                        state,
                        phase="rendering",
                        active_render_plan_ref=plan_ref,
                        active_render_progress_ref=progress_ref,
                    )
                )
                _sync_catalog_state(catalog, state)
            else:
                if state.active_render_progress_ref is None:
                    raise ValueError("Render plan 缺少 progress ref。")
                plan = load_render_model(
                    state.active_render_plan_ref,
                    resolver=catalog,
                    run_id=state.run_id,
                )
                if not isinstance(plan, RenderPlanV2) or plan != expected_plan:
                    raise ValueError("持久化 Render plan 与当前输入 identity 不一致。")
        except (FileNotFoundError, TypeError, ValueError):
            return _render_failure_transition(
                runtime, state, catalog, code="renderer_plan_recovery_failed"
            )

        if state.budget_state.reserved.render_calls:
            state = recover_reserved_budget_v2(runtime, state)
            _sync_catalog_state(catalog, state)
        try:
            state = _settle_uncommitted_render_outcome(runtime, state, catalog)
            _sync_catalog_state(catalog, state)
            assert state.active_render_plan_ref is not None
            assert state.active_render_progress_ref is not None
            plan = load_render_model(
                state.active_render_plan_ref, resolver=catalog, run_id=state.run_id
            )
            loaded_progress = load_render_model(
                state.active_render_progress_ref,
                resolver=catalog,
                run_id=state.run_id,
            )
            if not isinstance(plan, RenderPlanV2) or not isinstance(
                loaded_progress, RenderProgressV2
            ):
                raise ValueError("Render plan/progress 类型错误。")
            progress = loaded_progress
            if progress.has_uncommitted_outcome:
                raise ValueError("Render progress 仍有未结算 outcome。")
            if progress.completed_logical_requests == len(plan.items):
                successful = tuple(
                    outcome for outcome in progress.outcomes if outcome.outcome == "success"
                )
                beauty = successful[:BEAUTY_CAPTURE_COUNT]
                if len(beauty) != BEAUTY_CAPTURE_COUNT:
                    raise ValueError("完整 Render plan 缺少五次 beauty capture。")
                environment_ref = beauty[0].renderer_environment_ref
                assert environment_ref is not None
                repeatability = build_repeatability_evidence(
                    run_id=state.run_id,
                    attempt_id=plan.attempt_id,
                    capture_request_refs=tuple(
                        item.renderer_request_ref for item in beauty
                    ),
                    capture_render_refs=tuple(
                        cast(ArtifactRefV2, item.render_ref) for item in beauty
                    ),
                    renderer_environment_ref=environment_ref,
                    resolver=catalog,
                )
                repeatability_ref = materialize_render_model(
                    catalog=catalog, run_id=state.run_id, value=repeatability
                )
                state = _catalog_state(catalog, state)
                return _transition(
                    runtime,
                    state,
                    phase="rendering",
                    active_render_repeatability_ref=repeatability_ref,
                )
            item = plan.items[progress.next_logical_request_ordinal - 1]
            try:
                physical_ordinal = progress.next_physical_call_ordinal
            except ValueError:
                latest = progress.outcomes[-1]
                code = (
                    "renderer_unavailable_after_single_replay"
                    if latest.outcome == "transient_failure"
                    else "renderer_call_outcome_unknown_after_single_replay"
                )
                return _render_failure_transition(runtime, state, catalog, code=code)
            request = _render_request_for_item(state=state, plan=plan, item=item)
            request_ref = materialize_renderer_request(
                catalog=catalog, run_id=state.run_id, receipt=request
            )
            state = _catalog_state(catalog, state)
        except (FileNotFoundError, TypeError, ValueError):
            return _render_failure_transition(
                runtime, state, catalog, code="renderer_progress_recovery_failed"
            )

        if not _can_consume(state.budget_state, render_calls=1):
            return _render_failure_transition(
                runtime, state, catalog, code="render_replay_budget_exhausted"
            )
        if state.active_render_call_ordinal is None:
            state = _state(
                _transition(
                    runtime,
                    state,
                    phase="rendering",
                    active_render_call_ordinal=physical_ordinal,
                )
            )
            _sync_catalog_state(catalog, state)
        elif state.active_render_call_ordinal != physical_ordinal:
            raise RuntimeError("Renderer call intent ordinal 与 progress 不一致。")
        runtime.fault_injector("render.after_call_intent_before_budget_reserve")
        reservation = _budget_vector(render_calls=1)
        reserved_state = _reserve_external_effect(runtime, state, reservation)
        reserved_revision = reserved_state.budget_state.revision
        _sync_catalog_state(catalog, reserved_state)
        runtime.fault_injector("render.after_budget_reserve_before_call")

        renderer: V2Renderer | None = None
        result: RenderResult | None = None
        transient_error: str | None = None
        try:
            source = _read_exact(catalog, item.source_ref).decode("utf-8")
            renderer = runtime.renderer_factory(reserved_state)
            result = await renderer.render(source, item.width, item.height)
        except (RendererUnavailableError, OSError) as exc:
            transient_error = type(exc).__name__
        finally:
            if renderer is not None:
                await _close_renderer(renderer)

        state = _catalog_state(catalog, reserved_state)
        environment: RendererEnvironmentReceiptV3 | None = None
        call_environment_ref: ArtifactRefV2 | None = None
        render_ref: ArtifactRefV2 | None = None
        if transient_error is not None:
            outcome_name: Literal["transient_failure", "failure", "success"] = (
                "transient_failure"
            )
            error_code: str | None = "renderer_transient_unavailable"
        elif result is None or not result.success or result.image_bytes is None:
            outcome_name = "failure"
            error_code = "webgl_compile_or_draw_failed"
        elif (result.width, result.height) != (item.width, item.height):
            outcome_name = "failure"
            error_code = "renderer_result_dimension_mismatch"
        else:
            try:
                environment = _renderer_environment_from_result(result)
                call_environment_ref = _put_model(
                    catalog,
                    state,
                    environment,
                    kind="renderer_environment",
                    schema_version="renderer_environment_receipt_v3",
                )
                state = _catalog_state(catalog, state)
                render_ref = catalog.put(
                    run_id=state.run_id,
                    kind=(
                        RENDER_ARTIFACT_KIND
                        if item.profile == "beauty_full_v1"
                        else "diagnostic_render_png"
                    ),
                    schema_version=(
                        RENDER_ARTIFACT_SCHEMA_VERSION
                        if item.profile == "beauty_full_v1"
                        else "diagnostic_render_png_v3"
                    ),
                    content_type=_PNG_CONTENT_TYPE,
                    data=result.image_bytes,
                )
                state = _catalog_state(catalog, state)
            except (TypeError, ValueError):
                outcome_name = "failure"
                error_code = "renderer_environment_invalid"
            else:
                outcome_name = "success"
                error_code = None
        evidence_ref = _attempt_evidence_ref(
            catalog,
            state,
            stage="render",
            code=error_code or "render_success",
            outcome=outcome_name,
            renderer_request_hash=request.request_hash,
            call_ordinal=physical_ordinal,
        )
        state = _catalog_state(catalog, state)
        call_outcome = RenderCallOutcomeV2(
            logical_request_ordinal=item.logical_request_ordinal,
            physical_call_ordinal=physical_ordinal,
            renderer_request_ref=request_ref,
            renderer_request_artifact_sha256=request_ref.sha256,
            renderer_request_hash=request.request_hash,
            outcome=outcome_name,
            error_code=error_code,
            renderer_environment_ref=call_environment_ref,
            renderer_environment_artifact_sha256=(
                call_environment_ref.sha256
                if call_environment_ref is not None
                else None
            ),
            renderer_environment_hash=(
                environment.environment_hash if environment is not None else None
            ),
            render_ref=render_ref,
            render_sha256=render_ref.sha256 if render_ref is not None else None,
            attempt_evidence_ref=evidence_ref,
            budget_revision_reserved=reserved_revision,
        )
        progress = _progress_with_outcomes(
            progress, (*progress.outcomes, call_outcome)
        )
        progress_ref = materialize_render_model(
            catalog=catalog, run_id=state.run_id, value=progress
        )
        state = _catalog_state(catalog, state)
        state = _state(
            _transition(
                runtime,
                state,
                phase="rendering",
                active_render_progress_ref=progress_ref,
                active_attempt_evidence_refs=_append_ref(
                    state.active_attempt_evidence_refs, evidence_ref
                ),
            )
        )
        _sync_catalog_state(catalog, state)
        runtime.fault_injector("render.after_evidence_before_budget_commit")
        state = _commit_external_effect(
            runtime, state, reservation=reservation, used=reservation
        )
        committed = call_outcome.model_copy(
            update={"budget_revision_committed": state.budget_state.revision}
        )
        progress = _progress_with_outcomes(
            progress, (*progress.outcomes[:-1], committed)
        )
        _sync_catalog_state(catalog, state)
        progress_ref = materialize_render_model(
            catalog=catalog, run_id=state.run_id, value=progress
        )
        state = _catalog_state(catalog, state)
        state = _state(
            _transition(
                runtime,
                state,
                active_render_progress_ref=progress_ref,
                active_render_call_ordinal=None,
            )
        )
        _sync_catalog_state(catalog, state)
        if outcome_name == "failure":
            return _render_failure_transition(
                runtime, state, catalog, code=error_code or "renderer_call_failed"
            )
        if outcome_name in {"transient_failure", "unknown"} and physical_ordinal == 2:
            return _render_failure_transition(
                runtime,
                state,
                catalog,
                code=(
                    "renderer_unavailable_after_single_replay"
                    if outcome_name == "transient_failure"
                    else "renderer_call_outcome_unknown_after_single_replay"
                ),
            )
        return {name: getattr(state, name) for name in PngToShaderV2State.model_fields}

    return render_node


def make_evaluate_structure_and_basic_score_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def evaluate(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        catalog = _catalog(runtime, state)
        if (
            state.active_genome_ref is None
            or state.active_compilation_ref is None
            or state.active_diagnostic_compilation_ref is None
            or state.active_render_plan_ref is None
            or state.active_render_progress_ref is None
            or state.active_render_repeatability_ref is None
        ):
            return _transition(runtime, state, stop_reason="evaluation_inputs_missing")
        if runtime.metric_evaluator is None:
            # 没有 Oracle 时不能伪造 score；已有 best 安全停止，否则该 seed 失败。
            if state.objective_best_ref is not None:
                return _transition(
                    runtime, state, stop_reason="oracle_unavailable_with_best"
                )
            failure = _close_attempt_failure(
                catalog,
                state,
                stage="evaluate",
                code="oracle_unavailable",
                status="evaluation_failed",
            )
            return _transition(
                runtime,
                _catalog_state(catalog, state),
                active_seed_ref=None,
                active_genome_ref=None,
                active_compilation_ref=None,
                active_diagnostic_compilation_ref=None,
                active_render_plan_ref=None,
                active_render_progress_ref=None,
                active_render_repeatability_ref=None,
                active_rendered_structure_evidence_ref=None,
                active_rendered_structure_verification_ref=None,
                active_evaluation_refs=(),
                active_attempt_id=None,
                active_semantic_genome_hash=None,
                active_attempt_evidence_refs=(),
                candidate_summary_refs=_append_ref(
                    state.candidate_summary_refs, failure
                ),
            )
        try:
            _active_attempt_identity(catalog, state)
            branch = _active_branch(state)
            intent = cast(
                IntentIR,
                _load_model(
                    catalog,
                    branch.intent_ref,
                    IntentIR,
                    kind=INTENT_ARTIFACT_KIND,
                    schema_version=INTENT_ARTIFACT_SCHEMA_VERSION,
                ),
            )
            genome = cast(
                TypedEffectGenome,
                _load_model(
                    catalog,
                    state.active_genome_ref,
                    TypedEffectGenome,
                    kind=GENOME_ARTIFACT_KIND,
                    schema_version=GENOME_ARTIFACT_SCHEMA_VERSION,
                ),
            )
            bundle = cast(
                CompilationBundle,
                _load_model(
                    catalog,
                    state.active_compilation_ref,
                    CompilationBundle,
                    kind=COMPILATION_ARTIFACT_KIND,
                    schema_version=TYPED_COMPILATION_ARTIFACT_SCHEMA_VERSION,
                ),
            )
            diagnostic_bundle = cast(
                DiagnosticCompilationBundleV3,
                _load_model(
                    catalog,
                    state.active_diagnostic_compilation_ref,
                    DiagnosticCompilationBundleV3,
                    kind="diagnostic_compilation_bundle",
                    schema_version="diagnostic_compilation_bundle_v3",
                ),
            )
            plan = load_render_model(
                state.active_render_plan_ref, resolver=catalog, run_id=state.run_id
            )
            progress = load_render_model(
                state.active_render_progress_ref,
                resolver=catalog,
                run_id=state.run_id,
            )
            repeatability = load_render_model(
                state.active_render_repeatability_ref,
                resolver=catalog,
                run_id=state.run_id,
            )
            if (
                not isinstance(plan, RenderPlanV2)
                or not isinstance(progress, RenderProgressV2)
                or not isinstance(repeatability, RenderRepeatabilityEvidenceV2)
                or progress.has_uncommitted_outcome
                or progress.completed_logical_requests != len(plan.items)
                or not repeatability.passed
            ):
                raise ValueError("Render suite 未形成完整、可复现的 success closure。")
            successful = tuple(
                item for item in progress.outcomes if item.outcome == "success"
            )
            if len(successful) != len(plan.items):
                raise ValueError("Render progress 包含未闭合 physical failures。")
            beauty = successful[:BEAUTY_CAPTURE_COUNT]
            diagnostics = successful[BEAUTY_CAPTURE_COUNT:]
            if len(beauty) != BEAUTY_CAPTURE_COUNT or len(diagnostics) != len(
                diagnostic_bundle.passes
            ):
                raise ValueError("Render suite beauty/diagnostic 分母不完整。")
            candidate_id = _candidate_id(state, genome)
            genome_ref = state.active_genome_ref
            compilation_ref = state.active_compilation_ref
            diagnostic_compilation_ref = state.active_diagnostic_compilation_ref
            assert genome_ref is not None
            assert compilation_ref is not None
            assert diagnostic_compilation_ref is not None
            evaluation_refs: list[ArtifactRefV2] = []
            for outcome in beauty:
                assert outcome.render_ref is not None
                metrics = runtime.metric_evaluator(state, outcome.render_ref, catalog)
                evaluation = with_basic_evaluation_record_hash(
                    {
                        "schema_version": "basic_evaluation_record_v2",
                        "hash_version": "basic_evaluation_record_hash_v2",
                        "run_id": state.run_id,
                        "candidate_id": candidate_id,
                        "intent_id": intent.intent_id,
                        "target_hypothesis_hash": intent.target_hypothesis_hash,
                        "genome_id": genome.genome_id,
                        "semantic_genome_hash": bundle.semantic_genome_hash,
                        "compilation_sha256": state.active_compilation_ref.sha256,
                        "glsl_sha256": bundle.glsl_ref.sha256,
                        "render_ref": outcome.render_ref,
                        "render_sha256": outcome.render_ref.sha256,
                        "metric_version": metrics.metric_version,
                        "total_loss": metrics.total_loss,
                        "global_rmse": metrics.global_rmse,
                        "edge_loss": metrics.edge_loss,
                        "geometry_loss": metrics.geometry_loss,
                        "alpha_loss": metrics.alpha_loss,
                        "diagnostics": metrics.diagnostics,
                        "record_hash": "0" * 64,
                    }
                )
                evaluation_refs.append(
                    _put_model(
                        catalog,
                        state,
                        evaluation,
                        kind=EVALUATION_ARTIFACT_KIND,
                        schema_version=TYPED_EVALUATION_ARTIFACT_SCHEMA_VERSION,
                    )
                )
                state = _catalog_state(catalog, state)

            primary = beauty[0]
            assert primary.render_ref is not None
            assert primary.renderer_environment_ref is not None
            primary_request = load_renderer_request(
                primary.renderer_request_ref, resolver=catalog, run_id=state.run_id
            )
            if not isinstance(primary_request, RendererRequestReceiptV2):
                raise ValueError("主 beauty request 禁止旧 Renderer schema。")
            diagnostic_receipts: list[DiagnosticRenderReceiptV3] = []
            for diagnostic, outcome in zip(
                diagnostic_bundle.passes, diagnostics, strict=True
            ):
                assert outcome.render_ref is not None
                assert outcome.renderer_environment_ref is not None
                diagnostic_receipts.append(
                    DiagnosticRenderReceiptV3(
                        pass_id=diagnostic.pass_id,
                        pass_kind=diagnostic.pass_kind,
                        canonical_node_id=diagnostic.canonical_node_id,
                        ownership_policy_version=(
                            diagnostic.ownership_policy_version
                        ),
                        source_ref=diagnostic.source_ref,
                        source_sha256=diagnostic.source_sha256,
                        instance_index=diagnostic.instance_index,
                        layer=diagnostic.layer,
                        renderer_request_ref=outcome.renderer_request_ref,
                        renderer_request_artifact_sha256=(
                            outcome.renderer_request_artifact_sha256
                        ),
                        renderer_request_hash=outcome.renderer_request_hash,
                        renderer_environment_ref=outcome.renderer_environment_ref,
                        renderer_environment_artifact_sha256=cast(
                            str, outcome.renderer_environment_artifact_sha256
                        ),
                        renderer_environment_hash=cast(
                            str, outcome.renderer_environment_hash
                        ),
                        render_ref=outcome.render_ref,
                        render_sha256=outcome.render_ref.sha256,
                    )
                )
            evidence_payload: dict[str, Any] = {
                "run_id": state.run_id,
                "candidate_id": candidate_id,
                "intent_id": intent.intent_id,
                "intent_ref": branch.intent_ref,
                "intent_sha256": branch.intent_ref.sha256,
                "target_hypothesis_id": intent.target_hypothesis_id,
                "target_hypothesis_hash": intent.target_hypothesis_hash,
                "genome_id": genome.genome_id,
                "genome_ref": genome_ref,
                "genome_sha256": genome_ref.sha256,
                "semantic_genome_hash": bundle.semantic_genome_hash,
                "ownership_policy_version": (
                    diagnostic_bundle.ownership_policy_version
                ),
                "compilation_ref": compilation_ref,
                "compilation_sha256": compilation_ref.sha256,
                "diagnostic_compilation_ref": diagnostic_compilation_ref,
                "diagnostic_compilation_sha256": (
                    diagnostic_compilation_ref.sha256
                ),
                "beauty_renderer_request_ref": primary.renderer_request_ref,
                "beauty_renderer_request_artifact_sha256": (
                    primary.renderer_request_artifact_sha256
                ),
                "beauty_renderer_request_hash": primary_request.request_hash,
                "renderer_environment_ref": primary.renderer_environment_ref,
                "renderer_environment_artifact_sha256": cast(
                    str, primary.renderer_environment_artifact_sha256
                ),
                "renderer_environment_hash": cast(
                    str, primary.renderer_environment_hash
                ),
                "beauty_render_ref": primary.render_ref,
                "beauty_render_sha256": primary.render_ref.sha256,
                "diagnostic_receipts": tuple(diagnostic_receipts),
                "record_hash": "0" * 64,
            }
            evidence_payload["record_hash"] = compute_rendered_structure_evidence_hash(
                evidence_payload
            )
            structure_evidence = RenderedStructureEvidenceV4.model_validate(
                evidence_payload, strict=True
            )
            structure_evidence_ref = _put_model(
                catalog,
                state,
                structure_evidence,
                kind="rendered_structure_evidence",
                schema_version="rendered_structure_evidence_v4",
            )
            state = _catalog_state(catalog, state)
            structure_verification = verify_rendered_structure_evidence(
                structure_evidence,
                resolver=catalog,
                intent=intent,
                genome=genome,
                compilation_bundle=bundle,
                diagnostic_bundle=diagnostic_bundle,
            )
            structure_verification_ref = _put_model(
                catalog,
                state,
                structure_verification,
                kind="rendered_structure_verification",
                schema_version="rendered_structure_verification_v4",
            )
            state = _catalog_state(catalog, state)
            if structure_verification.status != "structure_verified":
                code = (
                    structure_verification.reason_codes[0]
                    if structure_verification.reason_codes
                    else "rendered_structure_rejected"
                )
                failure = _close_attempt_failure(
                    catalog,
                    state,
                    stage="evaluate",
                    code=code,
                    status="evaluation_failed",
                )
                state = _catalog_state(catalog, state)
                return _transition(
                    runtime,
                    state,
                    active_seed_ref=None,
                    active_genome_ref=None,
                    active_compilation_ref=None,
                    active_diagnostic_compilation_ref=None,
                    active_render_plan_ref=None,
                    active_render_progress_ref=None,
                    active_render_repeatability_ref=None,
                    active_rendered_structure_evidence_ref=None,
                    active_rendered_structure_verification_ref=None,
                    active_evaluation_refs=(),
                    active_attempt_id=None,
                    active_semantic_genome_hash=None,
                    active_attempt_evidence_refs=(),
                    candidate_summary_refs=_append_ref(
                        state.candidate_summary_refs, failure
                    ),
                )
        except (FileNotFoundError, TypeError, ValueError):
            failure = _close_attempt_failure(
                catalog,
                state,
                stage="evaluate",
                code="evaluation_failed",
                status="evaluation_failed",
            )
            return _transition(
                runtime,
                _catalog_state(catalog, state),
                active_seed_ref=None,
                active_genome_ref=None,
                active_compilation_ref=None,
                active_diagnostic_compilation_ref=None,
                active_render_plan_ref=None,
                active_render_progress_ref=None,
                active_render_repeatability_ref=None,
                active_rendered_structure_evidence_ref=None,
                active_rendered_structure_verification_ref=None,
                active_evaluation_refs=(),
                active_attempt_id=None,
                active_semantic_genome_hash=None,
                active_attempt_evidence_refs=(),
                candidate_summary_refs=_append_ref(
                    state.candidate_summary_refs, failure
                ),
            )
        return _transition(
            runtime,
            state,
            phase="evaluating",
            evaluation_revision=state.evaluation_revision + 1,
            active_rendered_structure_evidence_ref=structure_evidence_ref,
            active_rendered_structure_verification_ref=structure_verification_ref,
            active_evaluation_refs=tuple(evaluation_refs),
        )

    return evaluate


def make_materialize_immutable_candidate_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def materialize(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        catalog = _catalog(runtime, state)
        if (
            state.active_genome_ref is None
            or state.active_compilation_ref is None
            or state.active_diagnostic_compilation_ref is None
            or state.active_render_plan_ref is None
            or state.active_render_progress_ref is None
            or state.active_render_repeatability_ref is None
            or state.active_rendered_structure_evidence_ref is None
            or state.active_rendered_structure_verification_ref is None
            or len(state.active_evaluation_refs) != BEAUTY_CAPTURE_COUNT
        ):
            return _transition(runtime, state, stop_reason="candidate_closure_missing")
        try:
            _active_attempt_identity(catalog, state)
            branch = _active_branch(state)
            intent = cast(
                IntentIR,
                _load_model(
                    catalog,
                    branch.intent_ref,
                    IntentIR,
                    kind=INTENT_ARTIFACT_KIND,
                    schema_version=INTENT_ARTIFACT_SCHEMA_VERSION,
                ),
            )
            genome = cast(
                TypedEffectGenome,
                _load_model(
                    catalog,
                    state.active_genome_ref,
                    TypedEffectGenome,
                    kind=GENOME_ARTIFACT_KIND,
                    schema_version=GENOME_ARTIFACT_SCHEMA_VERSION,
                ),
            )
            product = compile_effect_genome(genome)
            hashes = compute_genome_hashes(genome)
            bundle = cast(
                CompilationBundle,
                _load_model(
                    catalog,
                    state.active_compilation_ref,
                    CompilationBundle,
                    kind=COMPILATION_ARTIFACT_KIND,
                    schema_version=TYPED_COMPILATION_ARTIFACT_SCHEMA_VERSION,
                ),
            )
            structure_evidence = cast(
                RenderedStructureEvidenceV4,
                _load_model(
                    catalog,
                    state.active_rendered_structure_evidence_ref,
                    RenderedStructureEvidenceV4,
                    kind="rendered_structure_evidence",
                    schema_version="rendered_structure_evidence_v4",
                ),
            )
            structure_verification = cast(
                RenderedStructureVerificationV4,
                _load_model(
                    catalog,
                    state.active_rendered_structure_verification_ref,
                    RenderedStructureVerificationV4,
                    kind="rendered_structure_verification",
                    schema_version="rendered_structure_verification_v4",
                ),
            )
            constraint_evaluation = evaluate_intent_genome_constraints_v3(
                intent,
                genome,
                product,
                candidate_id=_candidate_id(state, genome),
                target_measurements_ref=state.measurements_ref,
                intent_ref=branch.intent_ref,
                genome_ref=state.active_genome_ref,
                compilation_ref=state.active_compilation_ref,
                rendered_structure_evidence_ref=(
                    state.active_rendered_structure_evidence_ref
                ),
                rendered_structure_evidence=structure_evidence,
                rendered_structure_verification_ref=(
                    state.active_rendered_structure_verification_ref
                ),
                rendered_structure_verification=structure_verification,
            )
            constraint_ref = _put_model(
                catalog,
                state,
                constraint_evaluation,
                kind=CONSTRAINT_EVALUATION_ARTIFACT_KIND,
                schema_version=TYPED_CONSTRAINT_EVALUATION_ARTIFACT_SCHEMA_VERSION,
            )
            plan = load_render_model(
                state.active_render_plan_ref, resolver=catalog, run_id=state.run_id
            )
            progress = load_render_model(
                state.active_render_progress_ref,
                resolver=catalog,
                run_id=state.run_id,
            )
            if not isinstance(plan, RenderPlanV2) or not isinstance(
                progress, RenderProgressV2
            ):
                raise ValueError("Candidate render plan/progress 类型错误。")
            successful = tuple(
                item for item in progress.outcomes if item.outcome == "success"
            )
            if len(successful) != len(plan.items):
                raise ValueError("Candidate render plan 未完整成功。")
            beauty_refs = tuple(
                cast(ArtifactRefV2, item.render_ref)
                for item in successful[:BEAUTY_CAPTURE_COUNT]
            )
            request_refs = tuple(item.renderer_request_ref for item in successful)
            candidate = materialize_typed_candidate_artifacts(
                catalog=catalog,
                run_id=state.run_id,
                candidate_input=CandidateMaterializationInputV2(
                    run_id=state.run_id,
                    candidate_id=_candidate_id(state, genome),
                    parent_candidate_id=None,
                    origin="deterministic",
                    generator_id="effect-genome-expander",
                    generator_version="effect_genome_expander_v2",
                    target_hypothesis_id=intent.target_hypothesis_id,
                    target_hypothesis_hash=intent.target_hypothesis_hash,
                    constraint_set_hash=intent.constraint_set_hash,
                    intent_ref=branch.intent_ref,
                    genome_ref=state.active_genome_ref,
                    topology_hash=hashes.topology_hash,
                    parameter_layout_hash=hashes.parameter_layout_hash,
                    semantic_genome_hash=hashes.semantic_genome_hash,
                    compilation_ref=state.active_compilation_ref,
                    diagnostic_compilation_ref=(
                        state.active_diagnostic_compilation_ref
                    ),
                    glsl_ref=bundle.glsl_ref,
                    render_refs=beauty_refs,
                    render_plan_ref=state.active_render_plan_ref,
                    render_progress_ref=state.active_render_progress_ref,
                    render_repeatability_ref=state.active_render_repeatability_ref,
                    rendered_structure_evidence_ref=(
                        state.active_rendered_structure_evidence_ref
                    ),
                    rendered_structure_verification_ref=(
                        state.active_rendered_structure_verification_ref
                    ),
                    constraint_evaluation_ref=constraint_ref,
                    evaluation_refs=state.active_evaluation_refs,
                    attempt_id=state.active_attempt_id,
                    renderer_request_refs=request_refs,
                    attempt_evidence_refs=state.active_attempt_evidence_refs,
                ),
            )
        except CompilerDefectError as exc:
            return _transition(
                runtime, state, stop_reason=f"compiler_defect:{exc.code}"
            )
        except (FileNotFoundError, TypeError, ValueError):
            failure = _close_attempt_failure(
                catalog,
                state,
                stage="materialize",
                code="typed_candidate_closure_failed",
                status="rejected",
            )
            return _transition(
                runtime,
                state,
                active_seed_ref=None,
                active_genome_ref=None,
                active_compilation_ref=None,
                active_diagnostic_compilation_ref=None,
                active_render_plan_ref=None,
                active_render_progress_ref=None,
                active_render_repeatability_ref=None,
                active_rendered_structure_evidence_ref=None,
                active_rendered_structure_verification_ref=None,
                active_evaluation_refs=(),
                active_attempt_id=None,
                active_semantic_genome_hash=None,
                active_attempt_evidence_refs=(),
                candidate_summary_refs=_append_ref(
                    state.candidate_summary_refs, failure
                ),
            )
        return _transition(
            runtime,
            _catalog_state(catalog, state),
            phase="selecting",
            active_diagnostic_compilation_ref=None,
            active_render_plan_ref=None,
            active_render_progress_ref=None,
            active_render_repeatability_ref=None,
            active_rendered_structure_evidence_ref=None,
            active_rendered_structure_verification_ref=None,
            active_evaluation_refs=(),
            active_attempt_id=None,
            active_semantic_genome_hash=None,
            active_attempt_evidence_refs=(),
            candidate_summary_refs=_append_ref(
                state.candidate_summary_refs, candidate.candidate_ref
            ),
        )

    return materialize


def _candidate_score(
    catalog: ArtifactResolver, state: PngToShaderV2State, ref: ArtifactRefV2
) -> tuple[float, str]:
    loaded = load_typed_candidate_artifacts(
        ref,
        resolver=catalog,
        run_id=state.run_id,
    )
    return loaded.basic_evaluations[-1].total_loss, loaded.candidate.candidate_id


def make_select_hypothesis_best_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def select(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        catalog = _catalog(runtime, state)
        branch = _active_branch(state)
        candidates: list[tuple[float, str, ArtifactRefV2]] = []
        for ref in state.candidate_summary_refs:
            if ref.kind != "candidate_record":
                continue
            try:
                loaded = load_typed_candidate_artifacts(
                    ref,
                    resolver=catalog,
                    run_id=state.run_id,
                )
            except (FileNotFoundError, TypeError, ValueError):
                continue
            if loaded.candidate.target_hypothesis_hash != branch.target_hypothesis_hash:
                continue
            candidates.append(
                (
                    loaded.basic_evaluations[-1].total_loss,
                    loaded.candidate.candidate_id,
                    ref,
                )
            )
        if not candidates:
            return _transition(runtime, state)
        _, candidate_id, _ = min(candidates, key=lambda item: (item[0], item[1]))
        updated = branch.model_copy(update={"hypothesis_best_id": candidate_id})
        return _transition(
            runtime,
            state,
            hypothesis_branches=_replace_branch(
                state, state.hypothesis_cursor, updated
            ),
        )

    return select


def make_next_seed_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def next_seed(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        return _transition(
            runtime,
            state,
            phase="seeding",
            active_seed_ref=None,
            active_genome_ref=None,
            active_compilation_ref=None,
            active_diagnostic_compilation_ref=None,
            active_render_plan_ref=None,
            active_render_progress_ref=None,
            active_render_repeatability_ref=None,
            active_rendered_structure_evidence_ref=None,
            active_rendered_structure_verification_ref=None,
            active_evaluation_refs=(),
        )

    return next_seed


def make_next_hypothesis_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def next_hypothesis(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        if state.hypothesis_cursor >= len(state.hypothesis_branches):
            return _transition(runtime, state)
        branch = state.hypothesis_branches[state.hypothesis_cursor]
        status = "completed" if branch.hypothesis_best_id is not None else "failed"
        updated = branch.model_copy(update={"status": status})
        return _transition(
            runtime,
            state,
            hypothesis_branches=_replace_branch(
                state, state.hypothesis_cursor, updated
            ),
            hypothesis_cursor=state.hypothesis_cursor + 1,
            active_seed_ref=None,
            active_genome_ref=None,
            active_compilation_ref=None,
            active_diagnostic_compilation_ref=None,
            active_render_plan_ref=None,
            active_render_progress_ref=None,
            active_render_repeatability_ref=None,
            active_rendered_structure_evidence_ref=None,
            active_rendered_structure_verification_ref=None,
            active_evaluation_refs=(),
        )

    return next_hypothesis


def make_select_cross_hypothesis_best_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def select(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        catalog = _catalog(runtime, state)
        candidates: list[tuple[float, str, ArtifactRefV2]] = []
        for ref in state.candidate_summary_refs:
            if ref.kind != "candidate_record":
                continue
            try:
                score, candidate_id = _candidate_score(catalog, state, ref)
            except (FileNotFoundError, TypeError, ValueError):
                continue
            candidates.append((score, candidate_id, ref))
        if not candidates:
            # 三个 seed 中 minimum_complexity 是冻结的 deterministic fallback；
            # 全部失败后才允许 no_valid_candidate。
            return _transition(runtime, state, stop_reason="no_valid_candidate")
        _, candidate_id, candidate_ref = min(
            candidates, key=lambda item: (item[0], item[1])
        )
        return _transition(
            runtime,
            state,
            phase="selecting",
            objective_best_id=candidate_id,
            objective_best_ref=candidate_ref,
        )

    return select


def make_promote_or_skip_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def promote(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        if state.objective_best_ref is None:
            return _transition(runtime, state)
        # V2.3 development Graph 默认跳过 production admission。
        if not runtime.production_admission_enabled:
            return _transition(
                runtime,
                state,
                phase="finalized",
                stop_reason="completed_with_objective_best",
            )
        if (
            runtime.structure_envelope_provider is None
            or runtime.promotion_sink is None
        ):
            return _transition(
                runtime,
                state,
                phase="finalized",
                stop_reason="production_admission_dependencies_missing",
            )
        catalog = _catalog(runtime, state)
        try:
            if state.promotion_receipt_ref is not None:
                if state.promotion_operation_ref is None:
                    raise ValueError("Promotion receipt 缺少 operation intent。")
                completed_operation = load_promotion_operation(
                    state.promotion_operation_ref,
                    resolver=catalog,
                    run_id=state.run_id,
                )
                load_promotion_receipt(
                    state.promotion_receipt_ref,
                    resolver=catalog,
                    run_id=state.run_id,
                    operation_ref=state.promotion_operation_ref,
                )
                if completed_operation.candidate_ref != state.objective_best_ref:
                    raise ValueError("Promotion receipt 与 objective best 不一致。")
                return _transition(
                    runtime,
                    state,
                    phase="finalized",
                    stop_reason="completed_with_objective_best",
                )
            structure_ref = runtime.structure_envelope_provider(state, catalog)
            trusted = load_trusted_runtime_selector_input(
                structure_ref,
                state.objective_best_ref,
                resolver=catalog,
                run_id=state.run_id,
            )
            candidate = load_typed_candidate_artifacts(
                state.objective_best_ref,
                resolver=catalog,
                run_id=state.run_id,
            )
            provenance = candidate.provenance
            render_ref = candidate.candidate.render_refs[0]
            decision = decide_trusted_runtime_admission(
                candidate_id=candidate.candidate.candidate_id,
                candidate_glsl_sha256=candidate.candidate.glsl_ref.sha256,
                candidate_glsl_ref=candidate.candidate.glsl_ref.artifact_id,
                candidate_render_sha256=render_ref.sha256,
                candidate_render_ref=render_ref.artifact_id,
                candidate_provenance_ref=candidate.candidate.provenance_ref.artifact_id,
                candidate_origin=provenance.origin,
                candidate_generator_version=provenance.generator_version,
                trusted_input=trusted,
                policy=runtime.admission_policy,
            )
            if decision.status != "admitted":
                reasons = ",".join(decision.reason_codes)
                return _transition(
                    runtime,
                    state,
                    phase="finalized",
                    stop_reason=(
                        f"runtime_admission_not_admitted:{decision.status}:{reasons}"
                    ),
                )
            operation_fields = {
                "schema_version": "promotion_operation_v1",
                "hash_version": "promotion_operation_hash_v1",
                "run_id": state.run_id,
                "candidate_ref": state.objective_best_ref,
                "candidate_id": candidate.candidate.candidate_id,
                "candidate_glsl_sha256": candidate.candidate.glsl_ref.sha256,
                "candidate_render_sha256": render_ref.sha256,
                "candidate_provenance_ref": (
                    candidate.candidate.provenance_ref.artifact_id
                ),
                "structure_envelope_ref": structure_ref,
                "admission_policy_version": decision.policy_version,
            }
            expected_operation_id = compute_promotion_operation_id(operation_fields)
            is_new_operation = state.promotion_operation_ref is None
            if is_new_operation:
                operation = PromotionOperationV1(
                    run_id=state.run_id,
                    candidate_ref=state.objective_best_ref,
                    candidate_id=candidate.candidate.candidate_id,
                    candidate_glsl_sha256=candidate.candidate.glsl_ref.sha256,
                    candidate_render_sha256=render_ref.sha256,
                    candidate_provenance_ref=(
                        candidate.candidate.provenance_ref.artifact_id
                    ),
                    structure_envelope_ref=structure_ref,
                    admission_policy_version=decision.policy_version,
                    operation_id=expected_operation_id,
                )
                operation_ref = materialize_promotion_operation(
                    catalog=catalog,
                    operation=operation,
                )
                state = _catalog_state(catalog, state)
                state = _state(
                    _transition(
                        runtime,
                        state,
                        promotion_operation_ref=operation_ref,
                    )
                )
                _sync_catalog_state(catalog, state)
                runtime.fault_injector("promotion.after_outbox_before_sink")
                sink_result = _execute_promotion_sink(
                    runtime.promotion_sink, operation, state, trusted
                )
            else:
                assert state.promotion_operation_ref is not None
                operation = load_promotion_operation(
                    state.promotion_operation_ref,
                    resolver=catalog,
                    run_id=state.run_id,
                )
                if operation.operation_id != expected_operation_id:
                    raise ValueError(
                        "Promotion operation 与当前 admitted identity 不一致。"
                    )
                sink_result = _recover_promotion_sink(
                    runtime.promotion_sink, operation.operation_id
                )
                if sink_result.status == "not_executed":
                    runtime.fault_injector(
                        "promotion.after_recover_not_executed_before_sink"
                    )
                    sink_result = _execute_promotion_sink(
                        runtime.promotion_sink, operation, state, trusted
                    )
            if sink_result.operation_id != operation.operation_id:
                raise ValueError("Promotion sink result operation_id 不一致。")
            runtime.fault_injector("promotion.after_sink_before_receipt")
            if sink_result.status != "completed":
                suffix = (
                    "outcome_unknown_fail_closed"
                    if sink_result.status == "unknown"
                    else "not_executed_fail_closed"
                )
                return _transition(
                    runtime,
                    state,
                    phase="finalized",
                    stop_reason=f"promotion_{suffix}:{sink_result.reason_code}",
                )
            assert sink_result.external_receipt_id is not None
            assert sink_result.external_receipt_sha256 is not None
            assert state.promotion_operation_ref is not None
            receipt_ref = materialize_promotion_receipt(
                catalog=catalog,
                receipt=PromotionReceiptV1(
                    run_id=state.run_id,
                    operation_ref=state.promotion_operation_ref,
                    operation_id=operation.operation_id,
                    external_receipt_id=sink_result.external_receipt_id,
                    external_receipt_sha256=sink_result.external_receipt_sha256,
                    sink_reason_code=sink_result.reason_code,
                ),
            )
            state = _catalog_state(catalog, state)
        except RuntimeAdmissionRejected as exc:
            # complex topology 没有 typed receipt 会由 sealed adapter 在这里拒绝。
            return _transition(
                runtime,
                state,
                phase="finalized",
                stop_reason=f"runtime_admission_rejected:{exc.code}",
            )
        except (FileNotFoundError, TypeError, ValueError):
            return _transition(
                runtime,
                state,
                phase="finalized",
                stop_reason="runtime_admission_failed",
            )
        return _transition(
            runtime,
            state,
            phase="finalized",
            stop_reason="completed_with_objective_best",
            promotion_receipt_ref=receipt_ref,
        )

    return promote


def make_finalize_v2_node(
    runtime: PngToShaderV2NodeRuntime,
) -> Callable[[PngToShaderV2State], dict[str, Any]]:
    def finalize(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        stop_reason = state.stop_reason
        if stop_reason is None:
            stop_reason = (
                "completed_with_objective_best"
                if state.objective_best_ref is not None
                else "no_valid_candidate"
            )
        return _transition(
            runtime,
            state,
            phase="finalized",
            stop_reason=stop_reason,
        )

    return finalize


def _guard_artifact_budget(
    runtime: PngToShaderV2NodeRuntime,
    node: Callable[..., Any],
) -> Callable[..., Any]:
    if inspect.iscoroutinefunction(node):

        async def async_guard(state_value: PngToShaderV2State) -> dict[str, Any]:
            state = _state(state_value)
            try:
                return cast(dict[str, Any], await node(state))
            except ArtifactBudgetExceeded:
                return _transition(
                    runtime, state, stop_reason="artifact_budget_exhausted"
                )

        return async_guard

    def guard(state_value: PngToShaderV2State) -> dict[str, Any]:
        state = _state(state_value)
        try:
            return cast(dict[str, Any], node(state))
        except ArtifactBudgetExceeded:
            return _transition(runtime, state, stop_reason="artifact_budget_exhausted")

    return guard


def build_png_to_shader_v2_node_callables(
    runtime: PngToShaderV2NodeRuntime,
) -> dict[str, Callable[..., Any]]:
    """返回 Graph 与 Node Lab 共用的唯一 production node 映射。"""
    nodes: dict[str, Callable[..., Any]] = {
        "initialize_run_v2": make_initialize_run_v2_node(runtime),
        "prepare_context_v2": make_prepare_context_v2_node(runtime),
        "ingest_target_v2": make_ingest_target_v2_node(runtime),
        "measure_target_v2": make_measure_target_v2_node(runtime),
        "analyze_visual_layers_v2": make_analyze_visual_layers_v2_node(runtime),
        "build_intent_variants_v2": make_build_intent_variants_v2_node(runtime),
        "dequeue_hypothesis_v2": make_dequeue_hypothesis_v2_node(runtime),
        "plan_strategy_v2": make_plan_strategy_v2_node(runtime),
        "propose_seed_plans_v2": make_propose_seed_plans_v2_node(runtime),
        "expand_validate_seeds_v2": make_expand_validate_seeds_v2_node(runtime),
        "dequeue_seed_v2": make_dequeue_seed_v2_node(runtime),
        "prepare_candidate_attempt_v2": make_prepare_candidate_attempt_v2_node(runtime),
        "compile_genome_v2": make_compile_genome_v2_node(runtime),
        "render_candidate_v2": make_render_candidate_v2_node(runtime),
        "evaluate_structure_and_basic_score_v2": make_evaluate_structure_and_basic_score_v2_node(
            runtime
        ),
        "materialize_immutable_candidate_v2": make_materialize_immutable_candidate_v2_node(
            runtime
        ),
        "select_hypothesis_best_v2": make_select_hypothesis_best_v2_node(runtime),
        "next_seed_v2": make_next_seed_v2_node(runtime),
        "next_hypothesis_v2": make_next_hypothesis_v2_node(runtime),
        "select_cross_hypothesis_best_v2": make_select_cross_hypothesis_best_v2_node(
            runtime
        ),
        "promote_or_skip_v2": make_promote_or_skip_v2_node(runtime),
        "finalize_v2": make_finalize_v2_node(runtime),
    }
    if tuple(nodes) != PNG_TO_SHADER_V2_NODE_IDS:
        raise RuntimeError("V2 production node map 与冻结 node id 顺序漂移。")
    return {
        node_id: _guard_artifact_budget(runtime, node)
        for node_id, node in nodes.items()
    }


__all__ = [
    "BasicMetricVectorV2",
    "CatalogFactory",
    "IntentContextProvider",
    "InterpretationProvider",
    "MetricEvaluator",
    "PNG_TO_SHADER_V2_NODE_IDS",
    "PngToShaderV2NodeRuntime",
    "PromotionSinkOutcomeUncertain",
    "PromotionSink",
    "ReferenceArtifactProvider",
    "RendererFactory",
    "StateTransitionCommitter",
    "StructureEnvelopeProvider",
    "V2Renderer",
    "V2StateStore",
    "build_png_to_shader_v2_node_callables",
    "build_png_to_shader_v2_fixture_runtime",
    "make_basic_metric_evaluator_v2",
    "recover_reserved_budget_v2",
    "make_analyze_visual_layers_v2_node",
    "make_build_intent_variants_v2_node",
    "make_compile_genome_v2_node",
    "make_dequeue_hypothesis_v2_node",
    "make_dequeue_seed_v2_node",
    "make_evaluate_structure_and_basic_score_v2_node",
    "make_expand_validate_seeds_v2_node",
    "make_finalize_v2_node",
    "make_ingest_target_v2_node",
    "make_initialize_run_v2_node",
    "make_interpret_target_v2_node",
    "make_materialize_immutable_candidate_v2_node",
    "make_measure_target_v2_node",
    "make_next_hypothesis_v2_node",
    "make_next_seed_v2_node",
    "make_plan_strategy_v2_node",
    "make_prepare_candidate_attempt_v2_node",
    "make_prepare_context_v2_node",
    "make_promote_or_skip_v2_node",
    "make_propose_seed_plans_v2_node",
    "make_render_candidate_v2_node",
    "make_select_cross_hypothesis_best_v2_node",
    "make_select_hypothesis_best_v2_node",
]
