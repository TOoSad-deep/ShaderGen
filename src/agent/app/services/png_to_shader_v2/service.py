"""PNG-to-Shader V2.3 development-only 可恢复 Service 组合根。"""
# ruff: noqa: D102, D107, D415

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Any, Literal, Protocol, cast

from langsmith import tracing_context
from pydantic import BaseModel

from agent.app.graphs.png_to_shader_v2_builder import build_png_to_shader_v2_graph
from agent.app.nodes.png_to_shader_v2 import (
    V2Renderer,
    build_png_to_shader_v2_fixture_runtime,
)
from agent.app.states.png_to_shader_v2_state import (
    BudgetStateV2,
    BudgetVectorV2,
    PngToShaderV2State,
    build_checkpoint_namespace_v2,
)
from agent.app.states.png_to_shader_v2_state_store import (
    LocalPngToShaderV2StateStore,
    V2StateCheckpointNotFoundError,
)
from shaderforge.analysis import (
    TargetMeasurementsV2ArtifactBundle,
    measure_target_v2,
    verify_radial_segment_structure_evidence_v1,
)
from shaderforge.contracts import canonical_sha256
from shaderforge.intent import (
    IntentBuildContext,
    RequestConstraintSet,
    VisualInterpretationV2,
    build_request_constraint_set,
    validate_request_constraint_set_policy,
)
from shaderforge.store import (
    ArtifactCatalog,
    ArtifactRefV2,
    LocalArtifactCatalog,
    LocalArtifactStore,
)

from .journal import (
    DurableArtifactPutSlotV1,
    LocalServiceRunJournalStore,
    ServiceRunJournalV2,
)
from .models import (
    FixtureIntentInputsV1,
    PngToShaderV2DevelopmentResult,
    PngToShaderV2RequestMetadata,
    PngToShaderV2ResumeContextV1,
    PngToShaderV2RunManifestV1,
    PngToShaderV2ServiceConfig,
)
from .real_model import (
    LocalRealModelOperationStore,
    RealModelCommittedFailure,
    VisualInterpretationGatewayAdapter,
    execute_real_visual_interpretation,
)
from .wall_time import (
    LocalServiceWallTimeLedgerStore,
    ServiceWallTimeLedgerNotFound,
)

_JSON_CONTENT_TYPE = "application/json"
_RESUME_CONTEXT_KIND = "png_to_shader_v2_resume_context"
_RESUME_CONTEXT_SCHEMA = "png_to_shader_v2_resume_context_v1"
_BUDGET_FIELDS = tuple(BudgetVectorV2.model_fields)
_JOURNAL_PHASE_ORDER = {
    phase: index
    for index, phase in enumerate(
        (
            "bootstrap",
            "source_put",
            "config_put",
            "metadata_put",
            "measurements_put",
            "intent_context_put",
            "preliminary_constraint_put",
            "state_initialized",
            "model_committed",
            "resume_context_put",
            "final_constraint_put",
            "real_closure_committed",
            "graph_finalized",
            "manifest_put",
            "terminal",
            "terminal_failure",
        )
    )
}


class FixtureIntentInputFactory(Protocol):
    """基于真实 Measurements 产生冻结 validation fixture 输入。"""

    def __call__(
        self,
        bundle: TargetMeasurementsV2ArtifactBundle,
        catalog: ArtifactCatalog,
    ) -> FixtureIntentInputsV1: ...


class FixtureRendererFactory(Protocol):
    """为一次 run 构造 Renderer；实现不得返回跨 run 陈旧帧。"""

    def __call__(
        self,
        state: PngToShaderV2State,
        normalized_reference_png: bytes,
    ) -> V2Renderer: ...


MonotonicClock = Callable[[], float]
FaultInjector = Callable[[str], None]


class V2DevelopmentServiceError(RuntimeError):
    """development Service 的安全失败基类。"""


class V2RealModelModeUnavailable(V2DevelopmentServiceError):
    """real receipt adapter 尚未接入 Graph runtime。"""


class V2WallTimeBudgetExceeded(V2DevelopmentServiceError):
    """Service monotonic deadline 已耗尽。"""


class _BootstrapMeteredCatalog:
    """每次 put 后把 Catalog 实际去重字节数推进到独立 journal。"""

    def __init__(
        self,
        catalog: LocalArtifactCatalog,
        journal_store: LocalServiceRunJournalStore,
        run_id: str,
        fault_injector: FaultInjector,
    ) -> None:
        self._catalog = catalog
        self._journal_store = journal_store
        self._run_id = run_id
        self._fault_injector = fault_injector

    def put(self, **kwargs: Any) -> ArtifactRefV2:
        ref = self._catalog.put(**kwargs)
        self._fault_injector("bootstrap.after_catalog_put_before_journal")
        current = self._journal_store.load(self._run_id)
        actual = self._catalog.total_size_bytes()
        if actual != current.catalog_artifact_bytes:
            self._journal_store.replace(
                self._run_id,
                expected_revision=current.revision,
                value=current.model_copy(
                    update={
                        "revision": current.revision + 1,
                        "catalog_artifact_bytes": actual,
                    }
                ),
            )
        self._fault_injector("bootstrap.after_journal_put")
        return ref

    def resolve(self, artifact_id: str) -> ArtifactRefV2:
        return self._catalog.resolve(artifact_id)

    def read_bytes(self, artifact_id: str) -> bytes:
        return self._catalog.read_bytes(artifact_id)

    def list_refs(self) -> tuple[ArtifactRefV2, ...]:
        return self._catalog.list_refs()

    def total_size_bytes(self) -> int:
        return self._catalog.total_size_bytes()


class PngToShaderV2DevelopmentService:
    """只供 V2.3 开发/验证的 source-to-Graph Application Service。"""

    def __init__(
        self,
        *,
        artifact_root: Path,
        state_root: Path,
        fixture_input_factory: FixtureIntentInputFactory,
        renderer_factory: FixtureRendererFactory,
        real_model_adapter: VisualInterpretationGatewayAdapter | None = None,
        clock: MonotonicClock = monotonic,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self._artifact_store = LocalArtifactStore(artifact_root)
        self._state_root = state_root
        self._wall_time_store = LocalServiceWallTimeLedgerStore(
            state_root / ".service-wall-time-v1"
        )
        self._journal_store = LocalServiceRunJournalStore(
            state_root / ".service-run-journal-v2"
        )
        self._fixture_input_factory = fixture_input_factory
        self._renderer_factory = renderer_factory
        self._real_model_adapter = real_model_adapter
        self._real_model_operation_store = LocalRealModelOperationStore(
            state_root / ".real-model-operation-v2"
        )
        self._clock = clock
        self._fault_injector = fault_injector or (lambda _point: None)

    async def invoke(
        self,
        *,
        project_id: str,
        run_id: str,
        source_bytes: bytes,
        request_metadata: PngToShaderV2RequestMetadata,
        config: PngToShaderV2ServiceConfig,
    ) -> PngToShaderV2DevelopmentResult:
        """测量真实 source、物化小型输入引用并调用 V2 Graph。"""
        self._guard_development_mode(config)
        if not isinstance(source_bytes, bytes) or not source_bytes:
            raise ValueError("source_bytes 必须是非空 bytes。")
        source_sha256 = sha256(source_bytes).hexdigest()
        if (
            request_metadata.expected_source_sha256 is not None
            and request_metadata.expected_source_sha256 != source_sha256
        ):
            raise ValueError("source bytes 与 expected_source_sha256 不一致。")

        state_store = LocalPngToShaderV2StateStore(self._state_root)
        try:
            state_store.load_last_confirmed(run_id)
        except V2StateCheckpointNotFoundError:
            pass
        else:
            raise V2DevelopmentServiceError("run_id 已有 State；请调用 resume()。")
        policy_hash = canonical_sha256(config.model_dump(mode="json"))
        self._journal_store.initialize(
            ServiceRunJournalV2(
                project_id=project_id,
                run_id=run_id,
                revision=0,
                phase="bootstrap",
                policy_hash=policy_hash,
                source_sha256=source_sha256,
                config_json=config.model_dump_json(),
                request_metadata_json=request_metadata.model_dump_json(),
                catalog_artifact_bytes=0,
            )
        )
        reserved_wall, started = self._begin_wall_session(
            run_id=run_id,
            policy_hash=policy_hash,
            limit_ms=config.budget_limits.wall_time_ms,
            state_store=state_store,
        )
        catalog = self._new_catalog(project_id=project_id, run_id=run_id)
        metered = _BootstrapMeteredCatalog(
            catalog,
            self._journal_store,
            run_id,
            self._fault_injector,
        )
        source_ref = metered.put(
            run_id=run_id,
            kind="png_to_shader_v2_source_input",
            schema_version="png_to_shader_v2_source_input_v1",
            content_type="application/octet-stream",
            data=source_bytes,
        )
        self._record_bootstrap_ref(run_id, "source_put", "source_ref", source_ref)
        config_ref = _put_model(
            metered,
            run_id,
            config,
            kind="png_to_shader_v2_service_config",
            schema_version=config.schema_version,
        )
        self._record_bootstrap_ref(run_id, "config_put", "config_ref", config_ref)
        metadata_ref = _put_model(
            metered,
            run_id,
            request_metadata,
            kind="png_to_shader_v2_request_metadata",
            schema_version=request_metadata.schema_version,
        )
        self._record_bootstrap_ref(
            run_id, "metadata_put", "request_metadata_ref", metadata_ref
        )
        bundle = measure_target_v2(source_bytes, catalog=metered, run_id=run_id)
        bundle_ref = _put_model(
            metered,
            run_id,
            bundle,
            kind="target_measurements_bundle",
            schema_version=bundle.schema_version,
        )
        self._record_bootstrap_ref(
            run_id, "measurements_put", "measurement_bundle_ref", bundle_ref
        )
        fixture = self._fixture_input_factory(bundle, metered)
        self._validate_fixture(
            bundle,
            fixture,
            metered,
            validate_visual_interpretation=config.execution_mode != "real",
        )
        context_ref = _put_model(
            metered,
            run_id,
            fixture.intent_context,
            kind="intent_build_context",
            schema_version=fixture.intent_context.schema_version,
        )
        self._record_bootstrap_ref(
            run_id, "intent_context_put", "intent_context_ref", context_ref
        )
        if config.execution_mode == "real":
            preliminary_constraints = build_request_constraint_set(
                constraint_set_id=fixture.request_constraint_set.constraint_set_id,
                target_sha256=fixture.request_constraint_set.target_sha256,
                request_revision=fixture.request_constraint_set.request_revision,
                constraints=fixture.request_constraint_set.constraints,
                evidence_refs=fixture.request_constraint_set.evidence_refs,
            )
            preliminary_constraint_ref = _put_model(
                metered,
                run_id,
                preliminary_constraints,
                kind="request_constraint_set",
                schema_version="request_constraint_set_v1",
            )
            self._record_bootstrap_ref(
                run_id,
                "preliminary_constraint_put",
                "preliminary_constraint_ref",
                preliminary_constraint_ref,
            )
            initial = self._build_initial_state(
                project_id=project_id,
                run_id=run_id,
                config=config,
                config_hash=policy_hash,
                bundle=bundle,
                measurements_ref=bundle.measurements_ref,
                interpretation_ref=None,
                constraint_ref=preliminary_constraint_ref,
                prereq_artifact_bytes=metered.total_size_bytes(),
                initial_wall_used_ms=0,
            )
            state_store.initialize(initial)
            self._fault_injector(
                "real_bootstrap.after_state_initialize_before_journal"
            )
            self._advance_journal_phase(run_id, "state_initialized")
            assert self._real_model_adapter is not None
            try:
                model_state, receipt, _audit_ref = await asyncio.wait_for(
                    execute_real_visual_interpretation(
                        state_store=state_store,
                        state=initial,
                        operation_store=self._real_model_operation_store,
                        adapter=self._real_model_adapter,
                        catalog=catalog,
                        normalized_reference_png=_read_exact(
                            catalog, bundle.normalized_reference_ref
                        ),
                        measurements=bundle.measurements,
                        constraints=preliminary_constraints,
                        context=fixture.intent_context,
                        fault_injector=self._fault_injector,
                    ),
                    timeout=reserved_wall.reserved_ms / 1000.0,
                )
            except RealModelCommittedFailure as exc:
                self._mark_model_failure_finalized(state_store, run_id, exc.status)
                self._finish_wall_session(
                    run_id=run_id,
                    reservation_ms=reserved_wall.reserved_ms,
                    reservation_revision=reserved_wall.revision,
                    started=started,
                    state_store=state_store,
                )
                self._close_terminal_failure(
                    run_id=run_id,
                    status=exc.status,
                    state_store=state_store,
                )
                raise
            interpretation_ref = receipt.interpretation_ref
            self._record_model_committed(run_id, receipt.interpretation_ref, _audit_ref)
        else:
            interpretation_ref = _put_model(
                metered,
                run_id,
                fixture.visual_interpretation,
                kind="visual_interpretation",
                schema_version=fixture.visual_interpretation.schema_version,
            )
            model_state = None
        resume_context = PngToShaderV2ResumeContextV1(
            project_id=project_id,
            run_id=run_id,
            source_sha256=source_sha256,
            config_ref=config_ref,
            request_metadata_ref=metadata_ref,
            measurement_bundle_ref=bundle_ref,
            normalized_reference_ref=bundle.normalized_reference_ref,
            visual_interpretation_ref=interpretation_ref,
            intent_context_ref=context_ref,
        )
        resume_context_ref = (
            self._put_model_after_state(
                catalog,
                state_store,
                run_id,
                resume_context,
                kind=_RESUME_CONTEXT_KIND,
                schema_version=_RESUME_CONTEXT_SCHEMA,
            )
            if model_state is not None
            else _put_model(
                metered,
                run_id,
                resume_context,
                kind=_RESUME_CONTEXT_KIND,
                schema_version=_RESUME_CONTEXT_SCHEMA,
            )
        )
        constraints = build_request_constraint_set(
            constraint_set_id=fixture.request_constraint_set.constraint_set_id,
            target_sha256=fixture.request_constraint_set.target_sha256,
            request_revision=fixture.request_constraint_set.request_revision,
            constraints=fixture.request_constraint_set.constraints,
            evidence_refs=(
                *fixture.request_constraint_set.evidence_refs,
                resume_context_ref,
            ),
        )
        if model_state is not None:
            constraint_ref = self._put_model_after_state(
                catalog,
                state_store,
                run_id,
                constraints,
                kind="request_constraint_set",
                schema_version="request_constraint_set_v1",
            )
            current = state_store.load_last_confirmed(run_id)
            initial = state_store.compare_and_swap_run(
                run_id,
                expected_run_revision=current.run_revision,
                changes={
                    "visual_interpretation_ref": interpretation_ref,
                    "request_constraint_set_ref": constraint_ref,
                },
            )
            self._advance_journal_phase(run_id, "real_closure_committed")
        else:
            constraint_ref = _put_model(
                metered,
                run_id,
                constraints,
                kind="request_constraint_set",
                schema_version="request_constraint_set_v1",
            )
            initial = self._build_initial_state(
                project_id=project_id,
                run_id=run_id,
                config=config,
                config_hash=policy_hash,
                bundle=bundle,
                measurements_ref=bundle.measurements_ref,
                interpretation_ref=interpretation_ref,
                constraint_ref=constraint_ref,
                prereq_artifact_bytes=metered.total_size_bytes(),
                initial_wall_used_ms=0,
            )
        final = await self._invoke_graph(
            initial,
            catalog=catalog,
            state_store=state_store,
            config=config,
            resume_context=resume_context,
            intent_context=fixture.intent_context,
            wall_reservation_ms=reserved_wall.reserved_ms,
        )
        self._advance_journal_phase(run_id, "graph_finalized")
        result = self._materialize_result(
            final,
            catalog=catalog,
            state_store=state_store,
            resume_context_ref=resume_context_ref,
            resume_context=resume_context,
        )
        self._finish_wall_session(
            run_id=run_id,
            reservation_ms=reserved_wall.reserved_ms,
            reservation_revision=reserved_wall.revision,
            started=started,
            state_store=state_store,
        )
        return self._close_terminal_result(result)

    def _reconcile_journal_catalog(
        self,
        journal: ServiceRunJournalV2,
        catalog: LocalArtifactCatalog,
    ) -> ServiceRunJournalV2:
        actual = catalog.total_size_bytes()
        if actual < journal.catalog_artifact_bytes:
            raise V2DevelopmentServiceError(
                "Catalog 实际字节小于 journal，疑似删除或篡改。"
            )
        if actual == journal.catalog_artifact_bytes:
            return journal
        return self._journal_store.replace(
            journal.run_id,
            expected_revision=journal.revision,
            value=journal.model_copy(
                update={
                    "revision": journal.revision + 1,
                    "catalog_artifact_bytes": actual,
                }
            ),
        )

    def _record_bootstrap_ref(
        self,
        run_id: str,
        phase: str,
        field: str,
        ref: ArtifactRefV2,
    ) -> ServiceRunJournalV2:
        journal = self._journal_store.load(run_id)
        existing = getattr(journal, field)
        if existing is not None and existing != ref:
            raise V2DevelopmentServiceError(
                f"Service journal {field} 与重放 Artifact identity 冲突。"
            )
        phase_value = (
            journal.phase
            if _JOURNAL_PHASE_ORDER[journal.phase] >= _JOURNAL_PHASE_ORDER[phase]
            else phase
        )
        if existing == ref and phase_value == journal.phase:
            return journal
        self._fault_injector(
            f"real_bootstrap.{field}.after_put_before_journal"
        )
        updated = self._journal_store.replace(
            run_id,
            expected_revision=journal.revision,
            value=journal.model_copy(
                update={
                    "revision": journal.revision + 1,
                    "phase": phase_value,
                    field: ref,
                }
            ),
        )
        self._fault_injector(f"real_bootstrap.{field}.after_journal")
        return updated

    def _advance_journal_phase(
        self, run_id: str, phase: str
    ) -> ServiceRunJournalV2:
        journal = self._journal_store.load(run_id)
        if _JOURNAL_PHASE_ORDER[journal.phase] >= _JOURNAL_PHASE_ORDER[phase]:
            return journal
        return self._journal_store.replace(
            run_id,
            expected_revision=journal.revision,
            value=journal.model_copy(
                update={"revision": journal.revision + 1, "phase": phase}
            ),
        )

    def _record_model_committed(
        self,
        run_id: str,
        interpretation_ref: ArtifactRefV2,
        audit_ref: ArtifactRefV2,
    ) -> ServiceRunJournalV2:
        journal = self._journal_store.load(run_id)
        if (
            journal.model_interpretation_ref not in {None, interpretation_ref}
            or journal.model_audit_ref not in {None, audit_ref}
        ):
            raise V2DevelopmentServiceError("Service journal model refs 身份冲突。")
        if (
            journal.model_interpretation_ref == interpretation_ref
            and journal.model_audit_ref == audit_ref
            and _JOURNAL_PHASE_ORDER[journal.phase]
            >= _JOURNAL_PHASE_ORDER["model_committed"]
        ):
            return journal
        return self._journal_store.replace(
            run_id,
            expected_revision=journal.revision,
            value=journal.model_copy(
                update={
                    "revision": journal.revision + 1,
                    "phase": "model_committed",
                    "model_interpretation_ref": interpretation_ref,
                    "model_audit_ref": audit_ref,
                    "catalog_artifact_bytes": max(
                        journal.catalog_artifact_bytes,
                        self._new_catalog(
                            project_id=journal.project_id, run_id=run_id
                        ).total_size_bytes(),
                    ),
                }
            ),
        )

    @staticmethod
    def _load_journal_source(
        journal: ServiceRunJournalV2,
        catalog: LocalArtifactCatalog,
    ) -> bytes:
        refs = tuple(
            ref
            for ref in catalog.list_refs()
            if ref.kind == "png_to_shader_v2_source_input"
            and ref.schema_version == "png_to_shader_v2_source_input_v1"
            and ref.sha256 == journal.source_sha256
        )
        if len(refs) != 1:
            raise V2DevelopmentServiceError(
                "bootstrap journal 未唯一绑定 source Artifact。"
            )
        return _read_exact(catalog, refs[0])

    async def _resume_bootstrap(
        self,
        *,
        journal: ServiceRunJournalV2,
        catalog: LocalArtifactCatalog,
        state_store: LocalPngToShaderV2StateStore,
        source_bytes: bytes,
        request_metadata: PngToShaderV2RequestMetadata,
        config: PngToShaderV2ServiceConfig,
    ) -> PngToShaderV2DevelopmentResult:
        if config.execution_mode == "real":
            return await self._resume_real_pre_state_bootstrap(
                journal=journal,
                catalog=catalog,
                state_store=state_store,
                source_bytes=source_bytes,
                request_metadata=request_metadata,
                config=config,
            )
        reserved_wall, started = self._begin_wall_session(
            run_id=journal.run_id,
            policy_hash=journal.policy_hash,
            limit_ms=config.budget_limits.wall_time_ms,
            state_store=state_store,
        )
        metered = _BootstrapMeteredCatalog(
            catalog,
            self._journal_store,
            journal.run_id,
            self._fault_injector,
        )
        config_ref = _put_model(
            metered,
            journal.run_id,
            config,
            kind="png_to_shader_v2_service_config",
            schema_version=config.schema_version,
        )
        metadata_ref = _put_model(
            metered,
            journal.run_id,
            request_metadata,
            kind="png_to_shader_v2_request_metadata",
            schema_version=request_metadata.schema_version,
        )
        bundle = measure_target_v2(source_bytes, catalog=metered, run_id=journal.run_id)
        bundle_ref = _put_model(
            metered,
            journal.run_id,
            bundle,
            kind="target_measurements_bundle",
            schema_version=bundle.schema_version,
        )
        fixture = self._fixture_input_factory(bundle, metered)
        self._validate_fixture(
            bundle,
            fixture,
            metered,
            validate_visual_interpretation=True,
        )
        interpretation_ref = _put_model(
            metered,
            journal.run_id,
            fixture.visual_interpretation,
            kind="visual_interpretation",
            schema_version=fixture.visual_interpretation.schema_version,
        )
        context_ref = _put_model(
            metered,
            journal.run_id,
            fixture.intent_context,
            kind="intent_build_context",
            schema_version=fixture.intent_context.schema_version,
        )
        resume_context = PngToShaderV2ResumeContextV1(
            project_id=journal.project_id,
            run_id=journal.run_id,
            source_sha256=journal.source_sha256,
            config_ref=config_ref,
            request_metadata_ref=metadata_ref,
            measurement_bundle_ref=bundle_ref,
            normalized_reference_ref=bundle.normalized_reference_ref,
            visual_interpretation_ref=interpretation_ref,
            intent_context_ref=context_ref,
        )
        resume_context_ref = _put_model(
            metered,
            journal.run_id,
            resume_context,
            kind=_RESUME_CONTEXT_KIND,
            schema_version=_RESUME_CONTEXT_SCHEMA,
        )
        constraints = build_request_constraint_set(
            constraint_set_id=fixture.request_constraint_set.constraint_set_id,
            target_sha256=fixture.request_constraint_set.target_sha256,
            request_revision=fixture.request_constraint_set.request_revision,
            constraints=fixture.request_constraint_set.constraints,
            evidence_refs=(
                *fixture.request_constraint_set.evidence_refs,
                resume_context_ref,
            ),
        )
        constraint_ref = _put_model(
            metered,
            journal.run_id,
            constraints,
            kind="request_constraint_set",
            schema_version="request_constraint_set_v1",
        )
        ledger = self._wall_time_store.load(journal.run_id)
        initial = self._build_initial_state(
            project_id=journal.project_id,
            run_id=journal.run_id,
            config=config,
            config_hash=journal.policy_hash,
            bundle=bundle,
            measurements_ref=bundle.measurements_ref,
            interpretation_ref=interpretation_ref,
            constraint_ref=constraint_ref,
            prereq_artifact_bytes=metered.total_size_bytes(),
            initial_wall_used_ms=ledger.used_ms,
        )
        final = await self._invoke_graph(
            initial,
            catalog=catalog,
            state_store=state_store,
            config=config,
            resume_context=resume_context,
            intent_context=fixture.intent_context,
            wall_reservation_ms=reserved_wall.reserved_ms,
        )
        result = self._materialize_result(
            final,
            catalog=catalog,
            state_store=state_store,
            resume_context_ref=resume_context_ref,
            resume_context=resume_context,
        )
        self._finish_wall_session(
            run_id=journal.run_id,
            reservation_ms=reserved_wall.reserved_ms,
            reservation_revision=reserved_wall.revision,
            started=started,
            state_store=state_store,
        )
        return self._close_terminal_result(result)

    def _begin_wall_session(
        self,
        *,
        run_id: str,
        policy_hash: str,
        limit_ms: int,
        state_store: LocalPngToShaderV2StateStore,
    ) -> tuple[Any, float]:
        try:
            ledger = self._wall_time_store.load(run_id)
        except ServiceWallTimeLedgerNotFound:
            ledger = self._wall_time_store.initialize(
                run_id=run_id,
                policy_hash=policy_hash,
                limit_ms=limit_ms,
            )
        if ledger.policy_hash != policy_hash or ledger.limit_ms != limit_ms:
            raise V2DevelopmentServiceError("wall-time ledger 与冻结 config 不一致。")
        started = self._clock()
        started_ms = max(0, math.floor(started * 1000.0))
        if ledger.reserved_ms:
            self._fault_injector("wall.before_orphan_recovery")
            ledger = self._wall_time_store.recover_orphan(
                run_id, now_monotonic_ms=started_ms
            )
            self._fault_injector("wall.after_ledger_recovery_before_state")
        self._reconcile_wall_state(run_id, state_store)
        if ledger.used_ms >= ledger.limit_ms:
            raise V2WallTimeBudgetExceeded("wall_time_ms budget 已被保守恢复耗尽。")
        reserved = self._wall_time_store.reserve_remaining(
            run_id,
            expected_revision=ledger.revision,
            started_monotonic_ms=started_ms,
        )
        self._fault_injector("wall.after_ledger_reserve_before_state")
        return reserved, started

    def _finish_wall_session(
        self,
        *,
        run_id: str,
        reservation_ms: int,
        reservation_revision: int,
        started: float,
        state_store: LocalPngToShaderV2StateStore,
    ) -> None:
        elapsed = max(0, math.ceil((self._clock() - started) * 1000.0))
        charge = min(reservation_ms, elapsed)
        self._fault_injector("wall.before_ledger_commit")
        self._wall_time_store.commit(
            run_id,
            reservation_ms=reservation_ms,
            used_ms=charge,
            expected_revision=reservation_revision,
        )
        self._fault_injector("wall.after_ledger_commit_before_state")
        self._reconcile_wall_state(run_id, state_store)
        self._fault_injector("wall.after_state_commit")

    def _reconcile_wall_state(
        self,
        run_id: str,
        state_store: LocalPngToShaderV2StateStore,
    ) -> None:
        try:
            state = state_store.load_last_confirmed(run_id)
        except V2StateCheckpointNotFoundError:
            return
        ledger = self._wall_time_store.load(run_id)
        state_used = state.budget_state.used.wall_time_ms
        state_reserved = state.budget_state.reserved.wall_time_ms
        if state_used > ledger.used_ms:
            raise V2DevelopmentServiceError(
                "State wall-time used 超过 authoritative ledger。"
            )
        if state_reserved:
            # Service 从不把外层 wall reservation 双写进 State；非零值只能来自
            # 崩溃中的旧实现或篡改，保守提交为 used。
            current = state_store.load_last_confirmed(run_id)
            reservation = _zero_budget().model_copy(
                update={"wall_time_ms": state_reserved}
            )
            state_store.commit_budget(
                run_id,
                reservation=reservation,
                used=reservation,
                expected_budget_revision=current.budget_state.revision,
            )
            state = state_store.load_last_confirmed(run_id)
            state_used = state.budget_state.used.wall_time_ms
        missing = ledger.used_ms - state_used
        if missing > 0:
            self._record_wall_time(state_store, run_id, missing)

    def _close_terminal_result(
        self, result: PngToShaderV2DevelopmentResult
    ) -> PngToShaderV2DevelopmentResult:
        journal = self._journal_store.load(result.run_id)
        state = LocalPngToShaderV2StateStore(self._state_root).load_last_confirmed(
            result.run_id
        )
        if journal.phase == "terminal":
            return result.__class__(
                project_id=result.project_id,
                run_id=result.run_id,
                final_state=state,
                run_manifest_ref=cast(ArtifactRefV2, journal.terminal_manifest_ref),
                resume_context_ref=result.resume_context_ref,
            )
        terminal = journal.model_copy(
            update={
                "revision": journal.revision + 1,
                "phase": "terminal",
                "terminal_budget_snapshot": state.budget_state.used,
            }
        )
        self._journal_store.replace(
            result.run_id,
            expected_revision=journal.revision,
            value=terminal,
        )
        return result.__class__(
            project_id=result.project_id,
            run_id=result.run_id,
            final_state=state,
            run_manifest_ref=result.run_manifest_ref,
            resume_context_ref=result.resume_context_ref,
        )

    def _load_terminal_result(
        self,
        journal: ServiceRunJournalV2,
        state: PngToShaderV2State,
        catalog: LocalArtifactCatalog,
    ) -> PngToShaderV2DevelopmentResult:
        manifest_ref = cast(ArtifactRefV2, journal.terminal_manifest_ref)
        _load_model(
            catalog,
            manifest_ref,
            PngToShaderV2RunManifestV1,
            kind="png_to_shader_v2_run_manifest",
            schema_version="png_to_shader_v2_run_manifest_v1",
        )
        if state.budget_state.reserved != _zero_budget():
            raise V2DevelopmentServiceError("terminal State 仍有 budget reservation。")
        if state.budget_state.used != journal.terminal_budget_snapshot:
            raise V2DevelopmentServiceError(
                "terminal State 与 journal 七维账本不一致。"
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
        refs = tuple(
            ref for ref in constraints.evidence_refs if ref.kind == _RESUME_CONTEXT_KIND
        )
        if len(refs) != 1:
            raise V2DevelopmentServiceError(
                "terminal closure 未唯一绑定 resume context。"
            )
        return PngToShaderV2DevelopmentResult(
            project_id=journal.project_id,
            run_id=journal.run_id,
            final_state=state,
            run_manifest_ref=manifest_ref,
            resume_context_ref=refs[0],
        )

    def _close_terminal_failure(
        self,
        *,
        run_id: str,
        status: str,
        state_store: LocalPngToShaderV2StateStore,
    ) -> None:
        """把已结算模型失败冻结成可零增量重放的 Service 终态。"""
        state = state_store.load_last_confirmed(run_id)
        expected_reason = f"visual_interpretation_{status}"
        if (
            state.phase != "finalized"
            or state.stop_reason != expected_reason
            or state.budget_state.reserved != _zero_budget()
        ):
            raise V2DevelopmentServiceError("模型失败终态与 State 闭包不一致。")
        journal = self._journal_store.load(run_id)
        if journal.phase == "terminal_failure":
            if (
                journal.terminal_failure_status != status
                or journal.terminal_budget_snapshot != state.budget_state.used
            ):
                raise V2DevelopmentServiceError("模型失败 terminal journal 身份冲突。")
            return
        if journal.phase in {"manifest_put", "terminal"}:
            raise V2DevelopmentServiceError("模型失败不得覆盖成功 terminal journal。")
        catalog_bytes = self._new_catalog(
            project_id=journal.project_id,
            run_id=run_id,
        ).total_size_bytes()
        self._journal_store.replace(
            run_id,
            expected_revision=journal.revision,
            value=journal.model_copy(
                update={
                    "revision": journal.revision + 1,
                    "phase": "terminal_failure",
                    "terminal_failure_status": status,
                    "terminal_budget_snapshot": state.budget_state.used,
                    "catalog_artifact_bytes": catalog_bytes,
                }
            ),
        )

    def _raise_terminal_failure(
        self,
        *,
        journal: ServiceRunJournalV2,
        state: PngToShaderV2State,
    ) -> None:
        status = journal.terminal_failure_status
        if (
            status is None
            or state.phase != "finalized"
            or state.stop_reason != f"visual_interpretation_{status}"
            or state.budget_state.reserved != _zero_budget()
            or state.budget_state.used != journal.terminal_budget_snapshot
        ):
            raise V2DevelopmentServiceError("模型失败 terminal journal/State 不一致。")
        ledger = self._wall_time_store.load(journal.run_id)
        if (
            ledger.reserved_ms != 0
            or ledger.used_ms != state.budget_state.used.wall_time_ms
        ):
            raise V2DevelopmentServiceError("模型失败 terminal wall ledger 不一致。")
        raise RealModelCommittedFailure(status)

    def _recover_manifest_budget(
        self,
        journal: ServiceRunJournalV2,
        state_store: LocalPngToShaderV2StateStore,
    ) -> PngToShaderV2State:
        ref = cast(ArtifactRefV2, journal.terminal_manifest_ref)
        pre_used = cast(int, journal.terminal_pre_artifact_bytes)
        state = state_store.load_last_confirmed(journal.run_id)
        delta = _zero_budget().model_copy(update={"artifact_bytes": ref.size_bytes})
        used = state.budget_state.used.artifact_bytes
        reserved = state.budget_state.reserved.artifact_bytes
        if used == pre_used and reserved == ref.size_bytes:
            return state_store.commit_budget(
                journal.run_id,
                reservation=delta,
                used=delta,
                expected_budget_revision=state.budget_state.revision,
            )
        if used == pre_used and reserved == 0:
            state = state_store.reserve_budget(
                journal.run_id,
                delta,
                expected_budget_revision=state.budget_state.revision,
            )
            return state_store.commit_budget(
                journal.run_id,
                reservation=delta,
                used=delta,
                expected_budget_revision=state.budget_state.revision,
            )
        if used == pre_used + ref.size_bytes and reserved == 0:
            return state
        raise V2DevelopmentServiceError(
            "manifest budget recovery 与 journal 起点不一致。"
        )

    def _load_manifest_put_result(
        self,
        journal: ServiceRunJournalV2,
        state: PngToShaderV2State,
        catalog: LocalArtifactCatalog,
    ) -> PngToShaderV2DevelopmentResult:
        ref = cast(ArtifactRefV2, journal.terminal_manifest_ref)
        manifest = cast(
            PngToShaderV2RunManifestV1,
            _load_model(
                catalog,
                ref,
                PngToShaderV2RunManifestV1,
                kind="png_to_shader_v2_run_manifest",
                schema_version="png_to_shader_v2_run_manifest_v1",
            ),
        )
        return PngToShaderV2DevelopmentResult(
            project_id=journal.project_id,
            run_id=journal.run_id,
            final_state=state,
            run_manifest_ref=ref,
            resume_context_ref=manifest.resume_context_ref,
        )

    async def resume(self, *, run_id: str) -> PngToShaderV2DevelopmentResult:
        """只依赖 run_id、Catalog、journal 和最后确认 State 恢复运行。"""
        state_store = LocalPngToShaderV2StateStore(self._state_root)
        journal = self._journal_store.load(run_id)
        catalog = LocalArtifactCatalog(
            self._artifact_store.resolve_run(run_id), run_id=run_id
        )
        journal = self._reconcile_journal_catalog(journal, catalog)
        config = PngToShaderV2ServiceConfig.model_validate_json(
            journal.config_json, strict=True
        )
        metadata = PngToShaderV2RequestMetadata.model_validate_json(
            journal.request_metadata_json, strict=True
        )
        if canonical_sha256(config.model_dump(mode="json")) != journal.policy_hash:
            raise V2DevelopmentServiceError(
                "journal config canonical policy hash 不一致。"
            )
        self._guard_development_mode(config)
        try:
            state = state_store.load_last_confirmed(run_id)
        except V2StateCheckpointNotFoundError:
            source = self._load_journal_source(journal, catalog)
            return await self._resume_bootstrap(
                journal=journal,
                catalog=catalog,
                state_store=state_store,
                source_bytes=source,
                request_metadata=metadata,
                config=config,
            )
        if journal.phase == "terminal_failure":
            self._raise_terminal_failure(journal=journal, state=state)
        ledger = self._wall_time_store.load(run_id)
        if ledger.reserved_ms:
            self._wall_time_store.recover_orphan(
                run_id,
                now_monotonic_ms=max(0, math.floor(self._clock() * 1000.0)),
            )
        self._reconcile_wall_state(run_id, state_store)
        state = state_store.load_last_confirmed(run_id)
        if config.execution_mode == "real" and state.visual_interpretation_ref is None:
            operation = self._real_model_operation_store.load_optional(run_id)
            if (
                operation is not None
                and operation.phase == "committed"
                and operation.failure_status is not None
                and state.phase == "finalized"
            ):
                self._close_terminal_failure(
                    run_id=run_id,
                    status=operation.failure_status,
                    state_store=state_store,
                )
                raise RealModelCommittedFailure(operation.failure_status)
        if config.execution_mode == "real" and state.visual_interpretation_ref is None:
            return await self._resume_real_model_bootstrap(
                journal=journal,
                catalog=catalog,
                state_store=state_store,
                state=state,
                config=config,
                metadata=metadata,
            )
        if (
            journal.phase not in {"manifest_put", "terminal"}
            and state.phase == "finalized"
            and state.budget_state.reserved.artifact_bytes > 0
        ):
            journal = self._recover_unjournaled_manifest_put(journal, state, catalog)
        if journal.phase == "manifest_put":
            state = self._recover_manifest_budget(journal, state_store)
            result = self._load_manifest_put_result(journal, state, catalog)
            return self._close_terminal_result(result)
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
        resume_refs = tuple(
            ref for ref in constraints.evidence_refs if ref.kind == _RESUME_CONTEXT_KIND
        )
        if len(resume_refs) != 1:
            raise V2DevelopmentServiceError("State 未唯一绑定 Service resume context。")
        resume_context_ref = resume_refs[0]
        resume_context = cast(
            PngToShaderV2ResumeContextV1,
            _load_model(
                catalog,
                resume_context_ref,
                PngToShaderV2ResumeContextV1,
                kind=_RESUME_CONTEXT_KIND,
                schema_version=_RESUME_CONTEXT_SCHEMA,
            ),
        )
        if (
            resume_context.run_id != run_id
            or resume_context.project_id != state.project_id
        ):
            raise V2DevelopmentServiceError("resume context 与 State 身份不一致。")
        artifact_config = cast(
            PngToShaderV2ServiceConfig,
            _load_model(
                catalog,
                resume_context.config_ref,
                PngToShaderV2ServiceConfig,
                kind="png_to_shader_v2_service_config",
                schema_version="png_to_shader_v2_service_config_v1",
            ),
        )
        if artifact_config != config:
            raise V2DevelopmentServiceError("journal 与 config Artifact 不一致。")
        intent_context = cast(
            IntentBuildContext,
            _load_model(
                catalog,
                resume_context.intent_context_ref,
                IntentBuildContext,
                kind="intent_build_context",
                schema_version="intent_build_context_v1",
            ),
        )
        self._validate_resume_closure(
            journal=journal,
            state=state,
            catalog=catalog,
            resume_context_ref=resume_context_ref,
            resume_context=resume_context,
            config=config,
            metadata=metadata,
            constraints=constraints,
            intent_context=intent_context,
        )
        if journal.phase == "terminal":
            return self._load_terminal_result(journal, state, catalog)
        reserved_wall, started = self._begin_wall_session(
            run_id=run_id,
            policy_hash=journal.policy_hash,
            limit_ms=config.budget_limits.wall_time_ms,
            state_store=state_store,
        )
        state = state_store.load_last_confirmed(run_id)
        final = state
        if state.phase != "finalized":
            final = await self._invoke_graph(
                state,
                catalog=catalog,
                state_store=state_store,
                config=config,
                resume_context=resume_context,
                intent_context=intent_context,
                wall_reservation_ms=reserved_wall.reserved_ms,
            )
        result = self._materialize_result(
            final,
            catalog=catalog,
            state_store=state_store,
            resume_context_ref=resume_context_ref,
            resume_context=resume_context,
        )
        self._finish_wall_session(
            run_id=run_id,
            reservation_ms=reserved_wall.reserved_ms,
            reservation_revision=reserved_wall.revision,
            started=started,
            state_store=state_store,
        )
        return self._close_terminal_result(result)

    def _recover_unjournaled_manifest_put(
        self,
        journal: ServiceRunJournalV2,
        state: PngToShaderV2State,
        catalog: LocalArtifactCatalog,
    ) -> ServiceRunJournalV2:
        """恢复 manifest put 已提交、journal 尚未推进的唯一孤立窗口。"""
        refs = tuple(
            ref
            for ref in catalog.list_refs()
            if ref.kind == "png_to_shader_v2_run_manifest"
            and ref.schema_version == "png_to_shader_v2_run_manifest_v1"
            and ref.content_type == _JSON_CONTENT_TYPE
        )
        if len(refs) != 1:
            raise V2DevelopmentServiceError(
                "finalized State 的孤立 Artifact reservation 未唯一绑定 manifest。"
            )
        ref = refs[0]
        manifest = cast(
            PngToShaderV2RunManifestV1,
            _load_model(
                catalog,
                ref,
                PngToShaderV2RunManifestV1,
                kind="png_to_shader_v2_run_manifest",
                schema_version="png_to_shader_v2_run_manifest_v1",
            ),
        )
        if (
            state.budget_state.reserved.artifact_bytes != ref.size_bytes
            or manifest.run_id != journal.run_id
            or manifest.project_id != journal.project_id
            or manifest.final_phase != state.phase
            or manifest.final_run_revision != state.run_revision
        ):
            raise V2DevelopmentServiceError(
                "孤立 manifest 与 State reservation/run identity 不一致。"
            )
        recovered = journal.model_copy(
            update={
                "revision": journal.revision + 1,
                "phase": "manifest_put",
                "terminal_manifest_ref": ref,
                "terminal_pre_budget_revision": state.budget_state.revision - 1,
                "terminal_pre_artifact_bytes": state.budget_state.used.artifact_bytes,
                "catalog_artifact_bytes": catalog.total_size_bytes(),
            }
        )
        self._journal_store.replace(
            journal.run_id,
            expected_revision=journal.revision,
            value=recovered,
        )
        return recovered

    async def _resume_real_pre_state_bootstrap(
        self,
        *,
        journal: ServiceRunJournalV2,
        catalog: LocalArtifactCatalog,
        state_store: LocalPngToShaderV2StateStore,
        source_bytes: bytes,
        request_metadata: PngToShaderV2RequestMetadata,
        config: PngToShaderV2ServiceConfig,
    ) -> PngToShaderV2DevelopmentResult:
        """从版本化 ref journal 恢复 real pre-State bootstrap，绝不走 fixture 输出。"""
        reserved_wall, started = self._begin_wall_session(
            run_id=journal.run_id,
            policy_hash=journal.policy_hash,
            limit_ms=config.budget_limits.wall_time_ms,
            state_store=state_store,
        )
        metered = _BootstrapMeteredCatalog(
            catalog, self._journal_store, journal.run_id, self._fault_injector
        )
        source_ref = metered.put(
            run_id=journal.run_id,
            kind="png_to_shader_v2_source_input",
            schema_version="png_to_shader_v2_source_input_v1",
            content_type="application/octet-stream",
            data=source_bytes,
        )
        self._record_bootstrap_ref(
            journal.run_id, "source_put", "source_ref", source_ref
        )
        config_ref = _put_model(
            metered,
            journal.run_id,
            config,
            kind="png_to_shader_v2_service_config",
            schema_version=config.schema_version,
        )
        self._record_bootstrap_ref(
            journal.run_id, "config_put", "config_ref", config_ref
        )
        metadata_ref = _put_model(
            metered,
            journal.run_id,
            request_metadata,
            kind="png_to_shader_v2_request_metadata",
            schema_version=request_metadata.schema_version,
        )
        self._record_bootstrap_ref(
            journal.run_id,
            "metadata_put",
            "request_metadata_ref",
            metadata_ref,
        )
        bundle = measure_target_v2(source_bytes, catalog=metered, run_id=journal.run_id)
        bundle_ref = _put_model(
            metered,
            journal.run_id,
            bundle,
            kind="target_measurements_bundle",
            schema_version=bundle.schema_version,
        )
        self._record_bootstrap_ref(
            journal.run_id,
            "measurements_put",
            "measurement_bundle_ref",
            bundle_ref,
        )
        fixture = self._fixture_input_factory(bundle, metered)
        self._validate_fixture(
            bundle,
            fixture,
            metered,
            validate_visual_interpretation=False,
        )
        context_ref = _put_model(
            metered,
            journal.run_id,
            fixture.intent_context,
            kind="intent_build_context",
            schema_version=fixture.intent_context.schema_version,
        )
        self._record_bootstrap_ref(
            journal.run_id,
            "intent_context_put",
            "intent_context_ref",
            context_ref,
        )
        preliminary = build_request_constraint_set(
            constraint_set_id=fixture.request_constraint_set.constraint_set_id,
            target_sha256=fixture.request_constraint_set.target_sha256,
            request_revision=fixture.request_constraint_set.request_revision,
            constraints=fixture.request_constraint_set.constraints,
            evidence_refs=fixture.request_constraint_set.evidence_refs,
        )
        preliminary_ref = _put_model(
            metered,
            journal.run_id,
            preliminary,
            kind="request_constraint_set",
            schema_version="request_constraint_set_v1",
        )
        self._record_bootstrap_ref(
            journal.run_id,
            "preliminary_constraint_put",
            "preliminary_constraint_ref",
            preliminary_ref,
        )
        ledger = self._wall_time_store.load(journal.run_id)
        initial = self._build_initial_state(
            project_id=journal.project_id,
            run_id=journal.run_id,
            config=config,
            config_hash=journal.policy_hash,
            bundle=bundle,
            measurements_ref=bundle.measurements_ref,
            interpretation_ref=None,
            constraint_ref=preliminary_ref,
            prereq_artifact_bytes=metered.total_size_bytes(),
            initial_wall_used_ms=ledger.used_ms,
        )
        state_store.initialize(initial)
        self._fault_injector("real_bootstrap.after_state_initialize_before_journal")
        self._advance_journal_phase(journal.run_id, "state_initialized")
        return await self._resume_real_model_bootstrap(
            journal=self._journal_store.load(journal.run_id),
            catalog=catalog,
            state_store=state_store,
            state=initial,
            config=config,
            metadata=request_metadata,
            wall_session=(reserved_wall, started),
        )

    async def _resume_real_model_bootstrap(
        self,
        *,
        journal: ServiceRunJournalV2,
        catalog: LocalArtifactCatalog,
        state_store: LocalPngToShaderV2StateStore,
        state: PngToShaderV2State,
        config: PngToShaderV2ServiceConfig,
        metadata: PngToShaderV2RequestMetadata,
        wall_session: tuple[Any, float] | None = None,
    ) -> PngToShaderV2DevelopmentResult:
        """恢复 real provider 已完成但 Service closure 尚未完成的两阶段 bootstrap。"""
        assert self._real_model_adapter is not None
        before_rebuild = catalog.total_size_bytes()
        source = self._load_journal_source(journal, catalog)
        metered = _BootstrapMeteredCatalog(
            catalog, self._journal_store, journal.run_id, self._fault_injector
        )
        config_ref = _put_model(
            metered,
            journal.run_id,
            config,
            kind="png_to_shader_v2_service_config",
            schema_version=config.schema_version,
        )
        metadata_ref = _put_model(
            metered,
            journal.run_id,
            metadata,
            kind="png_to_shader_v2_request_metadata",
            schema_version=metadata.schema_version,
        )
        bundle = measure_target_v2(source, catalog=metered, run_id=journal.run_id)
        bundle_ref = _put_model(
            metered,
            journal.run_id,
            bundle,
            kind="target_measurements_bundle",
            schema_version=bundle.schema_version,
        )
        fixture = self._fixture_input_factory(bundle, metered)
        self._validate_fixture(
            bundle,
            fixture,
            metered,
            validate_visual_interpretation=False,
        )
        context_ref = _put_model(
            metered,
            journal.run_id,
            fixture.intent_context,
            kind="intent_build_context",
            schema_version=fixture.intent_context.schema_version,
        )
        preliminary_constraints = build_request_constraint_set(
            constraint_set_id=fixture.request_constraint_set.constraint_set_id,
            target_sha256=fixture.request_constraint_set.target_sha256,
            request_revision=fixture.request_constraint_set.request_revision,
            constraints=fixture.request_constraint_set.constraints,
            evidence_refs=fixture.request_constraint_set.evidence_refs,
        )
        preliminary_ref = _put_model(
            metered,
            journal.run_id,
            preliminary_constraints,
            kind="request_constraint_set",
            schema_version="request_constraint_set_v1",
        )
        if (
            state.measurements_ref != bundle.measurements_ref
            or state.request_constraint_set_ref != preliminary_ref
            or catalog.total_size_bytes() != before_rebuild
        ):
            raise V2DevelopmentServiceError(
                "real model bootstrap 重放未保持 measurement/constraint/Catalog identity。"
            )
        if wall_session is None:
            reserved_wall, started = self._begin_wall_session(
                run_id=journal.run_id,
                policy_hash=journal.policy_hash,
                limit_ms=config.budget_limits.wall_time_ms,
                state_store=state_store,
            )
        else:
            reserved_wall, started = wall_session
        try:
            model_state, receipt, _audit_ref = await asyncio.wait_for(
                execute_real_visual_interpretation(
                    state_store=state_store,
                    state=state_store.load_last_confirmed(journal.run_id),
                    operation_store=self._real_model_operation_store,
                    adapter=self._real_model_adapter,
                    catalog=catalog,
                    normalized_reference_png=_read_exact(
                        catalog, bundle.normalized_reference_ref
                    ),
                    measurements=bundle.measurements,
                    constraints=preliminary_constraints,
                    context=fixture.intent_context,
                    fault_injector=self._fault_injector,
                ),
                timeout=reserved_wall.reserved_ms / 1000.0,
            )
        except RealModelCommittedFailure as exc:
            self._mark_model_failure_finalized(state_store, journal.run_id, exc.status)
            self._finish_wall_session(
                run_id=journal.run_id,
                reservation_ms=reserved_wall.reserved_ms,
                reservation_revision=reserved_wall.revision,
                started=started,
                state_store=state_store,
            )
            self._close_terminal_failure(
                run_id=journal.run_id,
                status=exc.status,
                state_store=state_store,
            )
            raise
        self._record_model_committed(
            journal.run_id, receipt.interpretation_ref, _audit_ref
        )
        resume_context = PngToShaderV2ResumeContextV1(
            project_id=journal.project_id,
            run_id=journal.run_id,
            source_sha256=journal.source_sha256,
            config_ref=config_ref,
            request_metadata_ref=metadata_ref,
            measurement_bundle_ref=bundle_ref,
            normalized_reference_ref=bundle.normalized_reference_ref,
            visual_interpretation_ref=receipt.interpretation_ref,
            intent_context_ref=context_ref,
        )
        resume_context_ref = self._put_model_after_state(
            catalog,
            state_store,
            journal.run_id,
            resume_context,
            kind=_RESUME_CONTEXT_KIND,
            schema_version=_RESUME_CONTEXT_SCHEMA,
        )
        constraints = build_request_constraint_set(
            constraint_set_id=fixture.request_constraint_set.constraint_set_id,
            target_sha256=fixture.request_constraint_set.target_sha256,
            request_revision=fixture.request_constraint_set.request_revision,
            constraints=fixture.request_constraint_set.constraints,
            evidence_refs=(
                *fixture.request_constraint_set.evidence_refs,
                resume_context_ref,
            ),
        )
        constraint_ref = self._put_model_after_state(
            catalog,
            state_store,
            journal.run_id,
            constraints,
            kind="request_constraint_set",
            schema_version="request_constraint_set_v1",
        )
        current = state_store.load_last_confirmed(journal.run_id)
        ready = state_store.compare_and_swap_run(
            journal.run_id,
            expected_run_revision=current.run_revision,
            changes={
                "visual_interpretation_ref": receipt.interpretation_ref,
                "request_constraint_set_ref": constraint_ref,
            },
        )
        self._advance_journal_phase(journal.run_id, "real_closure_committed")
        final = await self._invoke_graph(
            ready,
            catalog=catalog,
            state_store=state_store,
            config=config,
            resume_context=resume_context,
            intent_context=fixture.intent_context,
            wall_reservation_ms=reserved_wall.reserved_ms,
        )
        self._advance_journal_phase(journal.run_id, "graph_finalized")
        result = self._materialize_result(
            final,
            catalog=catalog,
            state_store=state_store,
            resume_context_ref=resume_context_ref,
            resume_context=resume_context,
        )
        self._finish_wall_session(
            run_id=journal.run_id,
            reservation_ms=reserved_wall.reserved_ms,
            reservation_revision=reserved_wall.revision,
            started=started,
            state_store=state_store,
        )
        del model_state
        return self._close_terminal_result(result)

    def _guard_development_mode(self, config: PngToShaderV2ServiceConfig) -> None:
        if config.execution_mode == "real" and self._real_model_adapter is None:
            raise V2RealModelModeUnavailable(
                "real mode 未注入具备 durable invocation recover/dedupe 的 adapter。"
            )
        if config.execution_mode == "real":
            assert config.real_model_call is not None
            assert self._real_model_adapter is not None
            if self._real_model_adapter.policy != config.real_model_call:
                raise V2DevelopmentServiceError(
                    "real model adapter 与冻结 config 错绑。"
                )
        if config.production_admission_enabled:
            raise V2DevelopmentServiceError(
                "development Service 禁止 production admission。"
            )

    def _new_catalog(self, *, project_id: str, run_id: str) -> LocalArtifactCatalog:
        run_store = self._artifact_store.register_run(project_id, run_id)
        return LocalArtifactCatalog(run_store, run_id=run_id)

    @staticmethod
    def _validate_fixture(
        bundle: TargetMeasurementsV2ArtifactBundle,
        fixture: FixtureIntentInputsV1,
        catalog: ArtifactCatalog,
        *,
        validate_visual_interpretation: bool = True,
    ) -> None:
        if (
            fixture.request_constraint_set.target_sha256
            != bundle.measurements.target_sha256
        ):
            raise ValueError("fixture constraint target 与真实 Measurements 不一致。")
        validate_request_constraint_set_policy(fixture.request_constraint_set)
        for ref in (
            *fixture.request_constraint_set.evidence_refs,
            *(
                fixture.visual_interpretation.evidence_refs
                if validate_visual_interpretation
                else ()
            ),
            *fixture.intent_context.allowed_interpretation_evidence_refs,
        ):
            _verify_ref(catalog, ref)

    @staticmethod
    def _validate_resume_closure(
        *,
        journal: ServiceRunJournalV2,
        state: PngToShaderV2State,
        catalog: LocalArtifactCatalog,
        resume_context_ref: ArtifactRefV2,
        resume_context: PngToShaderV2ResumeContextV1,
        config: PngToShaderV2ServiceConfig,
        metadata: PngToShaderV2RequestMetadata,
        constraints: RequestConstraintSet,
        intent_context: IntentBuildContext,
    ) -> None:
        if state.run_id != journal.run_id or state.project_id != journal.project_id:
            raise V2DevelopmentServiceError("State 与 Service journal 身份不一致。")
        if state.budget_state.policy_hash != journal.policy_hash:
            raise V2DevelopmentServiceError(
                "State budget policy hash 与 journal 不一致。"
            )
        artifact_config = _load_model(
            catalog,
            resume_context.config_ref,
            PngToShaderV2ServiceConfig,
            kind="png_to_shader_v2_service_config",
            schema_version="png_to_shader_v2_service_config_v1",
        )
        artifact_metadata = _load_model(
            catalog,
            resume_context.request_metadata_ref,
            PngToShaderV2RequestMetadata,
            kind="png_to_shader_v2_request_metadata",
            schema_version="png_to_shader_v2_request_metadata_v1",
        )
        if artifact_config != config or artifact_metadata != metadata:
            raise V2DevelopmentServiceError(
                "journal 与 config/request metadata Artifact 不一致。"
            )
        if (
            metadata.expected_source_sha256 is not None
            and metadata.expected_source_sha256 != journal.source_sha256
        ):
            raise V2DevelopmentServiceError("request metadata source identity 不一致。")
        bundle = cast(
            TargetMeasurementsV2ArtifactBundle,
            _load_model(
                catalog,
                resume_context.measurement_bundle_ref,
                TargetMeasurementsV2ArtifactBundle,
                kind="target_measurements_bundle",
                schema_version="target_measurements_v2_artifact_bundle_v2",
            ),
        )
        if (
            bundle.target_source_ref.sha256 != journal.source_sha256
            or resume_context.source_sha256 != journal.source_sha256
            or state.measurements_ref != bundle.measurements_ref
            or resume_context.normalized_reference_ref
            != bundle.normalized_reference_ref
            or state.visual_interpretation_ref
            != resume_context.visual_interpretation_ref
        ):
            raise V2DevelopmentServiceError(
                "source/measurement/normalized/interpretation 交叉身份不一致。"
            )
        expected_refs = (
            bundle.target_source_ref,
            bundle.normalized_reference_ref,
            bundle.evidence_index_ref,
            bundle.measurements_ref,
            *(
                ref
                for item in bundle.hypothesis_artifacts
                for ref in (
                    item.subject_mask_ref,
                    *item.instance_mask_refs,
                    item.edge_ref,
                    *((item.radial_segment_evidence_ref,)
                      if item.radial_segment_evidence_ref is not None
                      else ()),
                )
            ),
            resume_context.visual_interpretation_ref,
            resume_context.intent_context_ref,
            resume_context_ref,
            state.request_constraint_set_ref,
            *constraints.evidence_refs,
            *intent_context.allowed_interpretation_evidence_refs,
        )
        for ref in expected_refs:
            _verify_ref(catalog, ref)
        for item in bundle.hypothesis_artifacts:
            if item.radial_segment_evidence_ref is not None:
                verify_radial_segment_structure_evidence_v1(
                    item.radial_segment_evidence_ref,
                    resolver=catalog,
                )
        interpretation = _load_model(
            catalog,
            resume_context.visual_interpretation_ref,
            VisualInterpretationV2,
            kind="visual_interpretation",
            schema_version="visual_interpretation_v2_1",
        )
        assert isinstance(interpretation, VisualInterpretationV2)
        nested_interpretation_refs = (
            *interpretation.evidence_refs,
            *(
                ref
                for item in interpretation.layer_hypotheses
                for ref in item.evidence_refs
            ),
            *(
                ref
                for item in interpretation.required_layer_assessments
                for ref in item.evidence_refs
            ),
            *(
                ref
                for item in interpretation.primitive_candidates
                for ref in item.evidence_refs
            ),
            *(
                ref
                for item in interpretation.strategy_hypotheses
                for ref in item.evidence_refs
            ),
        )
        for ref in nested_interpretation_refs:
            _verify_ref(catalog, ref)
        if tuple(
            ref for ref in constraints.evidence_refs if ref.kind == _RESUME_CONTEXT_KIND
        ) != (resume_context_ref,):
            raise V2DevelopmentServiceError(
                "constraint closure 未唯一绑定 resume context。"
            )

    @staticmethod
    def _build_initial_state(
        *,
        project_id: str,
        run_id: str,
        config: PngToShaderV2ServiceConfig,
        config_hash: str,
        bundle: TargetMeasurementsV2ArtifactBundle,
        measurements_ref: ArtifactRefV2,
        interpretation_ref: ArtifactRefV2 | None,
        constraint_ref: ArtifactRefV2,
        prereq_artifact_bytes: int,
        initial_wall_used_ms: int,
    ) -> PngToShaderV2State:
        expected_attempts = len(bundle.measurements.target_hypotheses) * 3
        if config.budget_limits.render_calls < expected_attempts:
            raise ValueError("render_calls 上限不足以覆盖每假设三个 seed。")
        if config.budget_limits.candidate_attempts < expected_attempts:
            raise ValueError("candidate_attempts 上限不足以覆盖每假设三个 seed。")
        prereq_bytes = prereq_artifact_bytes
        if prereq_bytes > config.budget_limits.artifact_bytes:
            raise ValueError(
                "前置 measurement/fixture Artifact 已超过 artifact_bytes 上限。"
            )
        zero = _zero_budget()
        used = zero.model_copy(
            update={
                "artifact_bytes": prereq_bytes,
                "wall_time_ms": initial_wall_used_ms,
            }
        )
        return PngToShaderV2State(
            checkpoint_namespace=build_checkpoint_namespace_v2(run_id),
            project_id=project_id,
            run_id=run_id,
            run_revision=0,
            phase="initialized",
            evaluation_revision=0,
            measurements_ref=measurements_ref,
            visual_interpretation_ref=interpretation_ref,
            request_constraint_set_ref=constraint_ref,
            hypothesis_branches=(),
            hypothesis_cursor=0,
            objective_best_id=None,
            candidate_summary_refs=(),
            budget_state=BudgetStateV2(
                policy_hash=config_hash,
                revision=0,
                limits=config.budget_limits,
                used=used,
                reserved=zero,
                exhausted_dimensions=_exhausted(config.budget_limits, used, zero),
            ),
            stop_reason=None,
        )

    async def _invoke_graph(
        self,
        state: PngToShaderV2State,
        *,
        catalog: LocalArtifactCatalog,
        state_store: LocalPngToShaderV2StateStore,
        config: PngToShaderV2ServiceConfig,
        resume_context: PngToShaderV2ResumeContextV1,
        intent_context: IntentBuildContext,
        wall_reservation_ms: int,
    ) -> PngToShaderV2State:
        runtime = build_png_to_shader_v2_fixture_runtime(
            catalog_factory=lambda _state: catalog,
            intent_context_provider=lambda _state, _measurements, _interpretation, _constraints: (
                intent_context
            ),
            renderer_factory=lambda current: self._renderer_factory(
                current,
                _read_exact(catalog, resume_context.normalized_reference_ref),
            ),
            reference_artifact_provider=lambda _state, _resolver: (
                resume_context.normalized_reference_ref
            ),
            state_store=state_store,
        )
        if runtime.production_admission_enabled:
            raise V2DevelopmentServiceError(
                "fixture runtime 意外打开 production admission。"
            )
        remaining_ms = wall_reservation_ms
        if remaining_ms <= 0:
            raise V2WallTimeBudgetExceeded("wall_time_ms budget 已耗尽。")
        graph = build_png_to_shader_v2_graph(runtime)
        try:
            with tracing_context(enabled=False):
                raw = await asyncio.wait_for(
                    cast(Any, graph).ainvoke(state, config={"callbacks": []}),
                    timeout=remaining_ms / 1000.0,
                )
        except TimeoutError:
            ledger = self._wall_time_store.load(state.run_id)
            self._wall_time_store.commit(
                state.run_id,
                reservation_ms=ledger.reserved_ms,
                used_ms=ledger.reserved_ms,
                expected_revision=ledger.revision,
            )
            self._reconcile_wall_state(state.run_id, state_store)
            raise V2WallTimeBudgetExceeded(
                "V2 Graph 超过 monotonic wall-time deadline。"
            )
        final = state_store.load_last_confirmed(state.run_id)
        graph_state = PngToShaderV2State.model_validate(raw, strict=True)
        if (
            final.model_copy(update={"budget_state": graph_state.budget_state})
            != graph_state
        ):
            raise V2DevelopmentServiceError("Graph 输出与最后确认 State 不一致。")
        return final

    def _record_wall_time(
        self,
        state_store: LocalPngToShaderV2StateStore,
        run_id: str,
        elapsed_ms: int,
    ) -> None:
        if elapsed_ms <= 0:
            return
        current = state_store.load_last_confirmed(run_id)
        delta = _zero_budget().model_copy(update={"wall_time_ms": elapsed_ms})
        reserved = state_store.reserve_budget(
            run_id,
            delta,
            expected_budget_revision=current.budget_state.revision,
        )
        self._fault_injector("wall.after_state_reserve")
        state_store.commit_budget(
            run_id,
            reservation=delta,
            used=delta,
            expected_budget_revision=reserved.budget_state.revision,
        )
        self._fault_injector("wall.after_state_commit")

    def _put_model_after_state(
        self,
        catalog: LocalArtifactCatalog,
        state_store: LocalPngToShaderV2StateStore,
        run_id: str,
        value: BaseModel,
        *,
        kind: str,
        schema_version: str,
    ) -> ArtifactRefV2:
        """State 建立后的稳定 put intent；任一 reserve/put/commit 窗口可恢复。"""
        payload = value.model_dump_json().encode("utf-8")
        slot_name: Literal["real_resume_context", "real_final_constraint"] = (
            "real_resume_context"
            if kind == _RESUME_CONTEXT_KIND
            else "real_final_constraint"
        )
        result_field = (
            "resume_context_ref"
            if slot_name == "real_resume_context"
            else "final_constraint_ref"
        )
        result_phase = (
            "resume_context_put"
            if slot_name == "real_resume_context"
            else "final_constraint_put"
        )
        journal = self._journal_store.load(run_id)
        existing_ref = (
            journal.resume_context_ref
            if slot_name == "real_resume_context"
            else journal.final_constraint_ref
        )
        if existing_ref is not None:
            if (
                existing_ref.kind != kind
                or existing_ref.schema_version != schema_version
                or existing_ref.content_type != _JSON_CONTENT_TYPE
                or existing_ref.sha256 != sha256(payload).hexdigest()
                or _read_exact(catalog, existing_ref) != payload
            ):
                raise V2DevelopmentServiceError(
                    f"{slot_name} journal ref 与当前 payload identity 冲突。"
                )
            return existing_ref

        maximum = _zero_budget().model_copy(update={"artifact_bytes": len(payload)})
        slot = journal.active_artifact_put
        if slot is None:
            current = state_store.load_last_confirmed(run_id)
            slot = DurableArtifactPutSlotV1(
                slot=slot_name,
                phase="prepared",
                kind=kind,
                artifact_schema_version=schema_version,
                content_type=_JSON_CONTENT_TYPE,
                payload_sha256=sha256(payload).hexdigest(),
                payload_size_bytes=len(payload),
                pre_artifact_used=current.budget_state.used.artifact_bytes,
                pre_catalog_bytes=catalog.total_size_bytes(),
            )
            journal = self._journal_store.replace(
                run_id,
                expected_revision=journal.revision,
                value=journal.model_copy(
                    update={
                        "revision": journal.revision + 1,
                        "active_artifact_put": slot,
                    }
                ),
            )
        elif (
            slot.slot != slot_name
            or slot.kind != kind
            or slot.artifact_schema_version != schema_version
            or slot.content_type != _JSON_CONTENT_TYPE
            or slot.payload_sha256 != sha256(payload).hexdigest()
            or slot.payload_size_bytes != len(payload)
        ):
            raise V2DevelopmentServiceError("active Artifact put slot identity 冲突。")

        current = state_store.load_last_confirmed(run_id)
        if slot.phase == "prepared":
            if current.budget_state.reserved == _zero_budget():
                self._fault_injector(f"real_artifact_put.{slot_name}.before_reserve")
                reserved = state_store.reserve_budget(
                    run_id,
                    maximum,
                    expected_budget_revision=current.budget_state.revision,
                )
                self._fault_injector(f"real_artifact_put.{slot_name}.after_reserve")
            elif current.budget_state.reserved == maximum:
                reserved = current
            else:
                raise V2DevelopmentServiceError(
                    "prepared Artifact put 对应未知 State reservation。"
                )
            slot = slot.model_copy(
                update={
                    "phase": "reserved",
                    "reservation_budget_revision": reserved.budget_state.revision,
                }
            )
            journal = self._journal_store.replace(
                run_id,
                expected_revision=journal.revision,
                value=journal.model_copy(
                    update={
                        "revision": journal.revision + 1,
                        "active_artifact_put": slot,
                    }
                ),
            )
            current = reserved

        if slot.phase == "reserved":
            if current.budget_state.reserved != maximum:
                raise V2DevelopmentServiceError(
                    "reserved Artifact put 的 State reservation 不完整。"
                )
            ref = catalog.put(
                run_id=run_id,
                kind=kind,
                schema_version=schema_version,
                content_type=_JSON_CONTENT_TYPE,
                data=payload,
            )
            self._fault_injector(f"real_artifact_put.{slot_name}.after_put")
            actual_bytes = catalog.total_size_bytes() - slot.pre_catalog_bytes
            if actual_bytes not in {0, len(payload)}:
                raise V2DevelopmentServiceError(
                    "Artifact put 期间 Catalog 出现非预期 delta。"
                )
            slot = slot.model_copy(
                update={
                    "phase": "put",
                    "actual_artifact_bytes": actual_bytes,
                    "artifact_ref": ref,
                }
            )
            journal = self._journal_store.replace(
                run_id,
                expected_revision=journal.revision,
                value=journal.model_copy(
                    update={
                        "revision": journal.revision + 1,
                        "active_artifact_put": slot,
                    }
                ),
            )

        assert slot.phase == "put"
        assert slot.artifact_ref is not None and slot.actual_artifact_bytes is not None
        _read_exact(catalog, slot.artifact_ref)
        actual = _zero_budget().model_copy(
            update={"artifact_bytes": slot.actual_artifact_bytes}
        )
        current = state_store.load_last_confirmed(run_id)
        self._fault_injector(f"real_artifact_put.{slot_name}.before_commit")
        if current.budget_state.reserved == maximum:
            confirmed = state_store.commit_budget(
                run_id,
                reservation=maximum,
                used=actual,
                expected_budget_revision=current.budget_state.revision,
            )
            self._fault_injector(f"real_artifact_put.{slot_name}.after_commit")
        elif (
            current.budget_state.reserved == _zero_budget()
            and current.budget_state.used.artifact_bytes
            == slot.pre_artifact_used + slot.actual_artifact_bytes
        ):
            confirmed = current
        else:
            raise V2DevelopmentServiceError(
                "Artifact put commit 与冻结 budget 起点不一致。"
            )
        del confirmed
        committed_slot = slot.model_copy(update={"phase": "committed"})
        # committed slot 不单独暴露中间 revision；同一次 journal 原子替换直接折叠
        # 到固定 ref，避免成功 resume 再次记账。
        journal = self._journal_store.replace(
            run_id,
            expected_revision=journal.revision,
            value=journal.model_copy(
                update={
                    "revision": journal.revision + 1,
                    "phase": result_phase,
                    result_field: committed_slot.artifact_ref,
                    "active_artifact_put": None,
                    "catalog_artifact_bytes": catalog.total_size_bytes(),
                }
            ),
        )
        final_ref = (
            journal.resume_context_ref
            if slot_name == "real_resume_context"
            else journal.final_constraint_ref
        )
        assert final_ref is not None
        return final_ref

    @staticmethod
    def _mark_model_failure_finalized(
        state_store: LocalPngToShaderV2StateStore,
        run_id: str,
        status: str,
    ) -> PngToShaderV2State:
        current = state_store.load_last_confirmed(run_id)
        if current.phase == "finalized" and current.stop_reason == (
            f"visual_interpretation_{status}"
        ):
            return current
        return state_store.compare_and_swap_run(
            run_id,
            expected_run_revision=current.run_revision,
            changes={
                "phase": "finalized",
                "stop_reason": f"visual_interpretation_{status}",
            },
        )

    def _materialize_result(
        self,
        state: PngToShaderV2State,
        *,
        catalog: LocalArtifactCatalog,
        state_store: LocalPngToShaderV2StateStore,
        resume_context_ref: ArtifactRefV2,
        resume_context: PngToShaderV2ResumeContextV1,
    ) -> PngToShaderV2DevelopmentResult:
        manifest = PngToShaderV2RunManifestV1(
            project_id=state.project_id,
            run_id=state.run_id,
            config_ref=resume_context.config_ref,
            request_metadata_ref=resume_context.request_metadata_ref,
            measurement_bundle_ref=resume_context.measurement_bundle_ref,
            resume_context_ref=resume_context_ref,
            request_constraint_set_ref=state.request_constraint_set_ref,
            final_phase=state.phase,
            final_run_revision=state.run_revision,
            stop_reason=state.stop_reason,
            objective_best_ref=state.objective_best_ref,
            candidate_summary_refs=state.candidate_summary_refs,
        )
        payload = manifest.model_dump_json().encode("utf-8")
        delta = _zero_budget().model_copy(update={"artifact_bytes": len(payload)})
        current = state_store.load_last_confirmed(state.run_id)
        reserved = state_store.reserve_budget(
            state.run_id,
            delta,
            expected_budget_revision=current.budget_state.revision,
        )
        try:
            manifest_ref = catalog.put(
                run_id=state.run_id,
                kind="png_to_shader_v2_run_manifest",
                schema_version=manifest.schema_version,
                content_type=_JSON_CONTENT_TYPE,
                data=payload,
            )
            self._fault_injector("terminal.after_manifest_put_before_journal")
        except (OSError, TypeError, ValueError):
            state_store.commit_budget(
                state.run_id,
                reservation=delta,
                used=_zero_budget(),
                expected_budget_revision=reserved.budget_state.revision,
            )
            raise
        journal = self._journal_store.load(state.run_id)
        if journal.phase not in {"manifest_put", "terminal"}:
            journal = self._journal_store.replace(
                state.run_id,
                expected_revision=journal.revision,
                value=journal.model_copy(
                    update={
                        "revision": journal.revision + 1,
                        "phase": "manifest_put",
                        "terminal_manifest_ref": manifest_ref,
                        "terminal_pre_budget_revision": current.budget_state.revision,
                        "terminal_pre_artifact_bytes": (
                            current.budget_state.used.artifact_bytes
                        ),
                        "catalog_artifact_bytes": catalog.total_size_bytes(),
                    }
                ),
            )
        elif journal.terminal_manifest_ref != manifest_ref:
            raise V2DevelopmentServiceError("terminal manifest ref 与 journal 冲突。")
        self._fault_injector("terminal.after_journal_before_budget_commit")
        confirmed = state_store.commit_budget(
            state.run_id,
            reservation=delta,
            used=delta,
            expected_budget_revision=reserved.budget_state.revision,
        )
        self._fault_injector("terminal.after_budget_commit_before_terminal")
        return PngToShaderV2DevelopmentResult(
            project_id=confirmed.project_id,
            run_id=confirmed.run_id,
            final_state=confirmed,
            run_manifest_ref=manifest_ref,
            resume_context_ref=resume_context_ref,
        )


def create_png_to_shader_v2_development_service(
    *,
    artifact_root: Path,
    state_root: Path,
    fixture_input_factory: FixtureIntentInputFactory,
    renderer_factory: FixtureRendererFactory,
    real_model_adapter: VisualInterpretationGatewayAdapter | None = None,
    clock: MonotonicClock = monotonic,
    fault_injector: FaultInjector | None = None,
) -> PngToShaderV2DevelopmentService:
    """显式创建未注册 Backend、未启用 admission 的 V2 development Service。"""
    return PngToShaderV2DevelopmentService(
        artifact_root=artifact_root,
        state_root=state_root,
        fixture_input_factory=fixture_input_factory,
        renderer_factory=renderer_factory,
        real_model_adapter=real_model_adapter,
        clock=clock,
        fault_injector=fault_injector,
    )


def _put_model(
    catalog: ArtifactCatalog,
    run_id: str,
    value: BaseModel,
    *,
    kind: str,
    schema_version: str,
) -> ArtifactRefV2:
    return catalog.put(
        run_id=run_id,
        kind=kind,
        schema_version=schema_version,
        content_type=_JSON_CONTENT_TYPE,
        data=value.model_dump_json().encode("utf-8"),
    )


def _load_model(
    catalog: ArtifactCatalog,
    ref: ArtifactRefV2,
    model_type: type[BaseModel],
    *,
    kind: str,
    schema_version: str,
) -> BaseModel:
    if (
        ref.kind != kind
        or ref.schema_version != schema_version
        or ref.content_type != _JSON_CONTENT_TYPE
    ):
        raise ValueError(f"{kind} ArtifactRef 元数据不符合冻结契约。")
    return model_type.model_validate_json(_read_exact(catalog, ref), strict=True)


def _verify_ref(catalog: ArtifactCatalog, ref: ArtifactRefV2) -> None:
    _read_exact(catalog, ref)


def _read_exact(catalog: ArtifactCatalog, ref: ArtifactRefV2) -> bytes:
    if catalog.resolve(ref.artifact_id) != ref:
        raise ValueError("Artifact resolver 返回的引用身份不一致。")
    payload = catalog.read_bytes(ref.artifact_id)
    if len(payload) != ref.size_bytes or sha256(payload).hexdigest() != ref.sha256:
        raise ValueError("Artifact bytes 与引用 size/SHA-256 不一致。")
    return payload


def _zero_budget() -> BudgetVectorV2:
    return BudgetVectorV2(**{name: 0 for name in _BUDGET_FIELDS})


def _exhausted(
    limits: BudgetVectorV2,
    used: BudgetVectorV2,
    reserved: BudgetVectorV2,
) -> tuple[str, ...]:
    return tuple(
        name
        for name in _BUDGET_FIELDS
        if getattr(used, name) + getattr(reserved, name) == getattr(limits, name)
    )


__all__ = [
    "FixtureIntentInputFactory",
    "FixtureRendererFactory",
    "FaultInjector",
    "MonotonicClock",
    "PngToShaderV2DevelopmentService",
    "V2DevelopmentServiceError",
    "V2RealModelModeUnavailable",
    "V2WallTimeBudgetExceeded",
    "create_png_to_shader_v2_development_service",
]
