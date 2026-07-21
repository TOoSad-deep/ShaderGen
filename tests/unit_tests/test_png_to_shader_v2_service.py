from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from agent.app.prompts.prompt_loader import PromptDefinition
from agent.app.services.png_to_shader_v2 import (
    DurableGatewayResultV1,
    FixtureIntentInputsV1,
    LocalServiceWallTimeLedgerStore,
    ModelCallReceiptV1,
    ModelCallReservationV1,
    PngToShaderV2RequestMetadata,
    PngToShaderV2ServiceConfig,
    RealModelCallPolicyV1,
    RealModelCommittedFailure,
    V2RealModelModeUnavailable,
    V2WallTimeBudgetExceeded,
    VisualInterpretationGatewayAdapter,
    commit_model_call_receipt_v1,
    create_png_to_shader_v2_development_service,
    reserve_model_call_v1,
)
from agent.app.services.png_to_shader_v2.journal import (
    LocalServiceRunJournalStore,
    ServiceRunJournalError,
    ServiceRunJournalV2,
)
from agent.app.services.png_to_shader_v2.wall_time import ServiceWallTimeLedgerError
from agent.app.states.png_to_shader_v2_state import (
    BudgetStateV2,
    BudgetVectorV2,
    PngToShaderV2State,
    build_checkpoint_namespace_v2,
)
from agent.app.states.png_to_shader_v2_state_store import (
    LocalPngToShaderV2StateStore,
)
from shaderforge.contracts import REQUIRED_LAYER_ORDER
from shaderforge.evaluation import load_candidate_attempt
from shaderforge.intent import (
    Constraint,
    ContractConstraintValue,
    IntentBuildContext,
    LayerHypothesis,
    PrimitiveCandidate,
    RequiredLayerAssessment,
    StrategyHypothesis,
    VisualInterpretationV2,
    build_request_constraint_set,
)
from shaderforge.rendering import (
    CompileResult,
    RendererMetadata,
    RendererUnavailableError,
    RenderResult,
)
from shaderforge.store import ArtifactRefV2, LocalArtifactCatalog, LocalArtifactStore
from shaderforge.validation import validate_shader


def _source_png() -> bytes:
    image = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
    ImageDraw.Draw(image).ellipse((12, 12, 52, 52), fill=(220, 70, 90, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _fixture(bundle, _catalog) -> FixtureIntentInputsV1:
    evidence = bundle.measurements_ref
    interpretation = VisualInterpretationV2(
        summary="离线单层椭圆 fixture。",
        layer_hypotheses=(
            LayerHypothesis(
                layer_id="fixture-base",
                role="base_fill",
                order=0,
                confidence=1.0,
                region_description="主体区域",
                primitive_candidates=("ellipse_sdf",),
                evidence_refs=(evidence,),
            ),
        ),
        required_layer_assessments=tuple(
            RequiredLayerAssessment(
                layer=layer,
                status="required" if layer == "base_fill" else "not_required",
                confidence=1.0,
                rationale="冻结 fixture 闭集。",
                evidence_refs=(evidence,),
            )
            for layer in REQUIRED_LAYER_ORDER
        ),
        primitive_candidates=(
            PrimitiveCandidate(
                candidate_id="fixture-ellipse",
                primitive_id="ellipse_sdf",
                layer_id="fixture-base",
                confidence=1.0,
                evidence_refs=(evidence,),
            ),
        ),
        strategy_hypotheses=(
            StrategyHypothesis(
                strategy_id="fixture-minimal",
                template_ids=("geometry.ellipse_sdf.v0",),
                required_layer_ids=("fixture-base",),
                complexity="low",
                confidence=1.0,
                evidence_refs=(evidence,),
            ),
        ),
        evidence_refs=(evidence,),
    )
    constraint_set = build_request_constraint_set(
        constraint_set_id="service-fixture-constraints",
        target_sha256=bundle.measurements.target_sha256,
        request_revision=0,
        constraints=(
            Constraint(
                constraint_id="normalized-by-builder",
                kind="contract",
                strength="hard",
                scope="global",
                value=ContractConstraintValue(
                    contract_id="webgl1_static_no_texture_v1"
                ),
                source="render_contract",
                source_revision=0,
                confidence=1.0,
                verification_status="verified",
            ),
        ),
        evidence_refs=(evidence,),
    )
    context = IntentBuildContext(
        contract_id="webgl1_static_no_texture_v1",
        primitive_catalog_version="png_to_shader_expected_primitives_v1",
        primitive_catalog_sha256="1" * 64,
        template_catalog_version="png_to_shader_expected_primitives_v1",
        template_catalog_sha256="2" * 64,
        allowed_primitive_ids=("ellipse_sdf",),
        allowed_template_ids=("geometry.ellipse_sdf.v0",),
        allowed_interpretation_evidence_refs=(evidence,),
    )
    return FixtureIntentInputsV1(
        request_constraint_set=constraint_set,
        visual_interpretation=interpretation,
        intent_context=context,
    )


def _put_model_interpretation(catalog: LocalArtifactCatalog, run_id: str):
    evidence = catalog.put(
        run_id=run_id,
        kind="target_measurements",
        schema_version="target_measurements_v2_2",
        content_type="application/json",
        data=b'{"fixture":"measurement-identity"}',
    )
    interpretation = VisualInterpretationV2(
        summary="typed model receipt fixture",
        layer_hypotheses=(
            LayerHypothesis(
                layer_id="receipt-base",
                role="base_fill",
                order=0,
                confidence=1.0,
                region_description="fixture",
                primitive_candidates=("ellipse_sdf",),
                evidence_refs=(evidence,),
            ),
        ),
        required_layer_assessments=tuple(
            RequiredLayerAssessment(
                layer=layer,
                status="required" if layer == "base_fill" else "not_required",
                confidence=1.0,
                rationale="receipt fixture",
                evidence_refs=(evidence,),
            )
            for layer in REQUIRED_LAYER_ORDER
        ),
        primitive_candidates=(
            PrimitiveCandidate(
                candidate_id="receipt-ellipse",
                primitive_id="ellipse_sdf",
                layer_id="receipt-base",
                confidence=1.0,
                evidence_refs=(evidence,),
            ),
        ),
        strategy_hypotheses=(
            StrategyHypothesis(
                strategy_id="receipt-strategy",
                template_ids=("geometry.ellipse_sdf.v0",),
                required_layer_ids=("receipt-base",),
                complexity="low",
                confidence=1.0,
                evidence_refs=(evidence,),
            ),
        ),
        evidence_refs=(evidence,),
    )
    return catalog.put(
        run_id=run_id,
        kind="visual_interpretation",
        schema_version="visual_interpretation_v2_1",
        content_type="application/json",
        data=interpretation.model_dump_json().encode(),
    )


def _model_identity(ref):
    return {
        "provider_id": "fixture-provider",
        "model_id": "fixture-model",
        "prompt_sha256": "1" * 64,
        "request_sha256": "3" * 64,
        "pricing_policy_sha256": "2" * 64,
        "measurements_ref": ref,
        "constraint_set_ref": ref,
    }


class _ReferenceRenderer:
    def __init__(self, reference: bytes, on_render=None) -> None:
        self._reference = reference
        self._on_render = on_render

    async def render(
        self, fragment_source: str, width: int, height: int
    ) -> RenderResult:
        if self._on_render is not None:
            self._on_render()
        validation = validate_shader(fragment_source)
        return RenderResult(
            success=validation.valid,
            image_bytes=self._reference if validation.valid else None,
            width=width,
            height=height,
            compile=CompileResult(
                success=validation.valid,
                vertex_log="",
                fragment_log="",
                link_log="",
                draw_error=None if validation.valid else "static_validation_failed",
                static_validation=validation,
            ),
            console_errors=(),
            metadata=RendererMetadata(
                renderer_version="service-v2-fixture",
                browser_version="fixture",
                gl_version="WebGL 1.0 fixture",
                glsl_version="WebGL GLSL ES 1.00 fixture",
                gl_vendor="fixture",
                gl_renderer="fixture",
                webgl_context_kind="webgl1",
                canvas_alpha=False,
                canvas_antialias=False,
                canvas_depth=False,
                canvas_stencil=False,
                premultiplied_alpha=False,
                preserve_drawing_buffer=True,
                canvas_clear_color_rgba=(1.0, 1.0, 1.0, 1.0),
            ),
            duration_ms=0.0,
        )

    async def close(self) -> None:
        return None


class _CrashTransientThenSuccessRendererFactory:
    def __init__(self) -> None:
        self.calls_by_request: dict[str, int] = {}
        self.outcomes: list[tuple[str, str]] = []
        self.first_request_id: str | None = None

    def __call__(self, state, reference: bytes):
        assert state.active_render_plan_ref is not None
        assert state.active_render_call_ordinal in {1, 2}
        if state.active_render_call_ordinal == 2:
            assert self.first_request_id is not None
            request_id = self.first_request_id
        else:
            request_id = (
                f"{state.active_render_plan_ref.artifact_id}:"
                f"logical-{len(self.calls_by_request) + 1}"
            )
        if self.first_request_id is None:
            self.first_request_id = request_id
        ordinal = self.calls_by_request.get(request_id, 0) + 1
        self.calls_by_request[request_id] = ordinal
        if request_id != self.first_request_id:
            self.outcomes.append((request_id, "success"))
            return _ReferenceRenderer(reference)

        outcome = "runtime_error" if ordinal == 1 else "transient"
        self.outcomes.append((request_id, outcome))

        class _FailingRenderer:
            async def render(self, _fragment_source: str, _width: int, _height: int):
                if outcome == "runtime_error":
                    raise RuntimeError("simulated renderer process crash")
                raise RendererUnavailableError("simulated transient renderer failure")

            async def close(self) -> None:
                return None

        return _FailingRenderer()


class _SlowRenderer(_ReferenceRenderer):
    async def render(
        self, fragment_source: str, width: int, height: int
    ) -> RenderResult:
        await asyncio.sleep(1.0)
        return await super().render(fragment_source, width, height)


def _budget(*, model: bool = False) -> BudgetVectorV2:
    return BudgetVectorV2(
        wall_time_ms=30_000,
        model_calls=1 if model else 0,
        model_tokens=256 if model else 0,
        render_calls=30,
        candidate_attempts=12,
        artifact_bytes=16_000_000,
        cost_usd_micros=1_000 if model else 0,
    )


def _real_call_policy() -> RealModelCallPolicyV1:
    return RealModelCallPolicyV1(
        provider_id="durable-fake",
        model_id="fake:model-v1",
        pricing_policy_id="fake-price-v1",
        input_micros_per_million_tokens=1_000_000,
        output_micros_per_million_tokens=2_000_000,
        max_input_tokens=100,
        max_output_tokens=100,
        max_cost_usd_micros=900,
        max_output_artifact_bytes=100_000,
    )


def _real_interpretation(evidence_ref: ArtifactRefV2) -> VisualInterpretationV2:
    return VisualInterpretationV2(
        summary="真实边界 fake gateway 的单层椭圆判断。",
        layer_hypotheses=(
            LayerHypothesis(
                layer_id="real-base",
                role="base_fill",
                order=0,
                confidence=0.9,
                region_description="主体区域",
                primitive_candidates=("ellipse_sdf",),
                evidence_refs=(evidence_ref,),
            ),
        ),
        required_layer_assessments=tuple(
            RequiredLayerAssessment(
                layer=layer,
                status="required" if layer == "base_fill" else "not_required",
                confidence=0.9,
                rationale="fake gateway 闭集判断。",
                evidence_refs=(evidence_ref,),
            )
            for layer in REQUIRED_LAYER_ORDER
        ),
        primitive_candidates=(
            PrimitiveCandidate(
                candidate_id="real-ellipse",
                primitive_id="ellipse_sdf",
                layer_id="real-base",
                confidence=0.9,
                evidence_refs=(evidence_ref,),
            ),
        ),
        strategy_hypotheses=(
            StrategyHypothesis(
                strategy_id="real-minimal",
                template_ids=("geometry.ellipse_sdf.v0",),
                required_layer_ids=("real-base",),
                complexity="low",
                confidence=0.9,
                evidence_refs=(evidence_ref,),
            ),
        ),
        evidence_refs=(evidence_ref,),
    )


class _DurableGateway:
    def __init__(self) -> None:
        self.calls = 0
        self.result: DurableGatewayResultV1 | None = None

    async def recover(self, invocation_id: str) -> DurableGatewayResultV1 | None:
        if self.result is None:
            return None
        assert self.result.invocation_id == invocation_id
        return self.result

    async def invoke_once(self, *, invocation_id: str, messages, options):
        del options
        human = messages[1]
        parts = human.content
        authorized_text = next(
            part["text"]
            for part in parts
            if isinstance(part, dict)
            and isinstance(part.get("text"), str)
            and part["text"].startswith("authorized_evidence_refs")
        )
        payload = authorized_text.split("<authorized_evidence_refs>", 1)[1].split(
            "</authorized_evidence_refs>", 1
        )[0]
        evidence_ref = ArtifactRefV2(**json.loads(payload)[0])
        self.calls += 1
        self.result = DurableGatewayResultV1(
            invocation_id=invocation_id,
            provider_receipt_id="receipt-1",
            provider_id="durable-fake",
            requested_model_id="fake:model-v1",
            actual_model_id="fake:model-v1",
            raw_response=_real_interpretation(evidence_ref).model_dump_json(),
            input_tokens=40,
            output_tokens=30,
        )
        return self.result


class _InvalidDurableGateway(_DurableGateway):
    async def invoke_once(self, *, invocation_id: str, messages, options):
        del messages, options
        self.calls += 1
        self.result = DurableGatewayResultV1(
            invocation_id=invocation_id,
            provider_receipt_id="receipt-invalid",
            provider_id="durable-fake",
            requested_model_id="fake:model-v1",
            actual_model_id="fake:model-v1",
            raw_response='{"invalid":true}',
            input_tokens=40,
            output_tokens=10,
        )
        return self.result


def test_development_service_runs_real_measurement_and_resumes_by_run_id(
    tmp_path: Path,
) -> None:
    clock_values = iter((100.0, 100.125))
    wall_store = LocalServiceWallTimeLedgerStore(
        tmp_path / "states" / ".service-wall-time-v1"
    )
    service = create_png_to_shader_v2_development_service(
        artifact_root=tmp_path / "artifacts",
        state_root=tmp_path / "states",
        fixture_input_factory=_fixture,
        renderer_factory=lambda _state, reference: _ReferenceRenderer(
            reference,
            on_render=lambda: (
                wall_store.load("service-v2-run").reserved_ms > 0
                or (_ for _ in ()).throw(AssertionError("wall 未预留"))
            ),
        ),
        clock=lambda: next(clock_values),
    )
    source = _source_png()
    result = asyncio.run(
        service.invoke(
            project_id="service-v2-project",
            run_id="service-v2-run",
            source_bytes=source,
            request_metadata=PngToShaderV2RequestMetadata(
                request_id="request-1",
                expected_source_sha256=None,
                source_label="unit fixture",
                source_license="test-only",
            ),
            config=PngToShaderV2ServiceConfig(budget_limits=_budget()),
        )
    )

    assert result.final_state.phase == "finalized"
    assert result.final_state.budget_state.used.model_calls == 0
    assert result.final_state.budget_state.used.wall_time_ms == 125
    assert not result.final_state.budget_state.reserved.model_calls
    assert result.run_manifest_ref.kind == "png_to_shader_v2_run_manifest"
    assert result.resume_context_ref.kind == "png_to_shader_v2_resume_context"

    resumed = asyncio.run(service.resume(run_id="service-v2-run"))
    assert resumed.final_state.phase == "finalized"
    assert resumed.final_state.run_revision == result.final_state.run_revision
    assert resumed.project_id == result.project_id
    assert resumed.run_id == result.run_id
    assert resumed.final_state.candidate_summary_refs == (
        result.final_state.candidate_summary_refs
    )
    assert (
        resumed.final_state.objective_best_ref == result.final_state.objective_best_ref
    )
    assert resumed.resume_context_ref == result.resume_context_ref
    assert resumed.run_manifest_ref == result.run_manifest_ref
    assert resumed.final_state.budget_state == result.final_state.budget_state


@pytest.mark.parametrize(
    "fault_point",
    (
        "bootstrap.after_catalog_put_before_journal",
        "bootstrap.after_journal_put",
    ),
)
def test_service_bootstrap_put_crash_recovers_from_journal_and_catalog_bytes(
    tmp_path: Path,
    fault_point: str,
) -> None:
    fired = False

    def inject(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise RuntimeError(f"injected {point}")

    state_root = tmp_path / "states"
    service = create_png_to_shader_v2_development_service(
        artifact_root=tmp_path / "artifacts",
        state_root=state_root,
        fixture_input_factory=_fixture,
        renderer_factory=lambda _state, reference: _ReferenceRenderer(reference),
        fault_injector=inject,
    )
    with pytest.raises(RuntimeError, match="injected bootstrap"):
        asyncio.run(
            service.invoke(
                project_id="bootstrap-project",
                run_id=f"bootstrap-{fault_point.rsplit('.', 1)[-1]}",
                source_bytes=_source_png(),
                request_metadata=PngToShaderV2RequestMetadata(
                    request_id="bootstrap-request",
                    source_label="generated",
                    source_license="test-only",
                ),
                config=PngToShaderV2ServiceConfig(budget_limits=_budget()),
            )
        )
    run_id = f"bootstrap-{fault_point.rsplit('.', 1)[-1]}"
    result = asyncio.run(service.resume(run_id=run_id))
    catalog = LocalArtifactCatalog(
        LocalArtifactStore(tmp_path / "artifacts").resolve_run(run_id),
        run_id=run_id,
    )
    assert result.final_state.phase == "finalized"
    assert result.final_state.budget_state.reserved == BudgetVectorV2(
        wall_time_ms=0,
        model_calls=0,
        model_tokens=0,
        render_calls=0,
        candidate_attempts=0,
        artifact_bytes=0,
        cost_usd_micros=0,
    )
    assert result.final_state.budget_state.used.artifact_bytes >= (
        catalog.total_size_bytes()
    )


@pytest.mark.parametrize(
    "fault_point",
    (
        "terminal.after_manifest_put_before_journal",
        "terminal.after_journal_before_budget_commit",
    ),
)
def test_terminal_manifest_put_commit_crash_is_idempotently_closed(
    tmp_path: Path,
    fault_point: str,
) -> None:
    fired = False

    def inject(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise RuntimeError("injected terminal commit gap")

    service = create_png_to_shader_v2_development_service(
        artifact_root=tmp_path / "artifacts",
        state_root=tmp_path / "states",
        fixture_input_factory=_fixture,
        renderer_factory=lambda _state, reference: _ReferenceRenderer(reference),
        fault_injector=inject,
    )
    with pytest.raises(RuntimeError, match="terminal commit gap"):
        asyncio.run(
            service.invoke(
                project_id="terminal-project",
                run_id=f"terminal-gap-{fault_point.rsplit('.', 1)[-1]}",
                source_bytes=_source_png(),
                request_metadata=PngToShaderV2RequestMetadata(
                    request_id="terminal-request",
                    source_label="generated",
                    source_license="test-only",
                ),
                config=PngToShaderV2ServiceConfig(budget_limits=_budget()),
            )
        )
    run_id = f"terminal-gap-{fault_point.rsplit('.', 1)[-1]}"
    closed = asyncio.run(service.resume(run_id=run_id))
    again = asyncio.run(service.resume(run_id=run_id))
    assert closed.final_state.budget_state.reserved == BudgetVectorV2(
        wall_time_ms=0,
        model_calls=0,
        model_tokens=0,
        render_calls=0,
        candidate_attempts=0,
        artifact_bytes=0,
        cost_usd_micros=0,
    )
    assert again.run_manifest_ref == closed.run_manifest_ref
    assert again.final_state.run_revision == closed.final_state.run_revision
    assert again.final_state.budget_state == closed.final_state.budget_state


@pytest.mark.parametrize(
    "fault_point",
    (
        "wall.after_ledger_commit_before_state",
        "wall.after_state_reserve",
        "wall.after_state_commit",
    ),
)
def test_wall_ledger_state_commit_gaps_reconcile_without_undercharge(
    tmp_path: Path,
    fault_point: str,
) -> None:
    fired = False

    def inject(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise RuntimeError(f"injected {point}")

    state_root = tmp_path / "states"
    run_id = f"wall-gap-{fault_point.rsplit('.', 1)[-1]}"
    service = create_png_to_shader_v2_development_service(
        artifact_root=tmp_path / "artifacts",
        state_root=state_root,
        fixture_input_factory=_fixture,
        renderer_factory=lambda _state, reference: _ReferenceRenderer(reference),
        fault_injector=inject,
    )
    with pytest.raises(RuntimeError, match="injected wall"):
        asyncio.run(
            service.invoke(
                project_id="wall-project",
                run_id=run_id,
                source_bytes=_source_png(),
                request_metadata=PngToShaderV2RequestMetadata(
                    request_id="wall-request",
                    source_label="generated",
                    source_license="test-only",
                ),
                config=PngToShaderV2ServiceConfig(budget_limits=_budget()),
            )
        )
    result = asyncio.run(service.resume(run_id=run_id))
    ledger = LocalServiceWallTimeLedgerStore(state_root / ".service-wall-time-v1").load(
        run_id
    )
    assert ledger.reserved_ms == 0
    assert result.final_state.budget_state.reserved.wall_time_ms == 0
    assert result.final_state.budget_state.used.wall_time_ms == ledger.used_ms


def test_real_mode_requires_double_opt_in_policy_and_durable_adapter(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="同时打开"):
        PngToShaderV2ServiceConfig(
            execution_mode="real",
            allow_model_calls=True,
            real_provider_enabled=False,
            budget_limits=_budget(model=True),
        )
    service = create_png_to_shader_v2_development_service(
        artifact_root=tmp_path / "artifacts",
        state_root=tmp_path / "states",
        fixture_input_factory=_fixture,
        renderer_factory=lambda _state, reference: _ReferenceRenderer(reference),
    )
    with pytest.raises(V2RealModelModeUnavailable, match="durable invocation"):
        asyncio.run(
            service.invoke(
                project_id="service-v2-project",
                run_id="service-v2-real",
                source_bytes=_source_png(),
                request_metadata=PngToShaderV2RequestMetadata(
                    request_id="request-real",
                    source_label="unit fixture",
                    source_license="test-only",
                ),
                config=PngToShaderV2ServiceConfig(
                    execution_mode="real",
                    allow_model_calls=True,
                    real_provider_enabled=True,
                    budget_limits=_budget(model=True),
                    real_model_call=_real_call_policy(),
                ),
            )
        )


def test_real_mode_runs_through_durable_adapter_and_repeated_resume_is_free(
    tmp_path: Path,
) -> None:
    gateway = _DurableGateway()
    policy = _real_call_policy()
    service = create_png_to_shader_v2_development_service(
        artifact_root=tmp_path / "artifacts",
        state_root=tmp_path / "states",
        fixture_input_factory=_fixture,
        renderer_factory=lambda _state, reference: _ReferenceRenderer(reference),
        real_model_adapter=VisualInterpretationGatewayAdapter(
            gateway=gateway,
            prompt=PromptDefinition(
                name="analyze_visual_layers_v2",
                version="analyze_visual_layers_v2_test",
                prompt="只返回 VisualInterpretationV2 JSON。",
            ),
            policy=policy,
        ),
    )
    config = PngToShaderV2ServiceConfig(
        execution_mode="real",
        allow_model_calls=True,
        real_provider_enabled=True,
        budget_limits=_budget(model=True),
        real_model_call=policy,
    )
    first = asyncio.run(
        service.invoke(
            project_id="service-v2-project",
            run_id="service-v2-real-success",
            source_bytes=_source_png(),
            request_metadata=PngToShaderV2RequestMetadata(
                request_id="request-real-success",
                source_label="unit fixture",
                source_license="test-only",
            ),
            config=config,
        )
    )
    before = first.final_state
    second = asyncio.run(service.resume(run_id=first.run_id))

    assert gateway.calls == 1
    assert first.final_state.budget_state.used.model_calls == 1
    assert first.final_state.budget_state.used.model_tokens == 70
    assert first.final_state.budget_state.used.cost_usd_micros == 100
    assert second.run_manifest_ref == first.run_manifest_ref
    assert second.final_state == before


def test_real_mode_service_recovers_provider_crash_without_second_call(
    tmp_path: Path,
) -> None:
    gateway = _DurableGateway()
    policy = _real_call_policy()
    crashed = False

    def fault(point: str) -> None:
        nonlocal crashed
        if point == "real_model.after_provider_before_materialize" and not crashed:
            crashed = True
            raise RuntimeError("provider-boundary-crash")

    service = create_png_to_shader_v2_development_service(
        artifact_root=tmp_path / "artifacts",
        state_root=tmp_path / "states",
        fixture_input_factory=_fixture,
        renderer_factory=lambda _state, reference: _ReferenceRenderer(reference),
        fault_injector=fault,
        real_model_adapter=VisualInterpretationGatewayAdapter(
            gateway=gateway,
            prompt=PromptDefinition(
                name="analyze_visual_layers_v2",
                version="analyze_visual_layers_v2_test",
                prompt="只返回 VisualInterpretationV2 JSON。",
            ),
            policy=policy,
        ),
    )
    config = PngToShaderV2ServiceConfig(
        execution_mode="real",
        allow_model_calls=True,
        real_provider_enabled=True,
        budget_limits=_budget(model=True),
        real_model_call=policy,
    )
    with pytest.raises(RuntimeError, match="provider-boundary-crash"):
        asyncio.run(
            service.invoke(
                project_id="service-v2-project",
                run_id="service-v2-real-crash",
                source_bytes=_source_png(),
                request_metadata=PngToShaderV2RequestMetadata(
                    request_id="request-real-crash",
                    source_label="unit fixture",
                    source_license="test-only",
                ),
                config=config,
            )
        )
    recovered = asyncio.run(service.resume(run_id="service-v2-real-crash"))
    assert gateway.calls == 1
    assert recovered.final_state.phase == "finalized"
    assert recovered.final_state.budget_state.reserved == BudgetVectorV2(
        wall_time_ms=0,
        model_calls=0,
        model_tokens=0,
        render_calls=0,
        candidate_attempts=0,
        artifact_bytes=0,
        cost_usd_micros=0,
    )


@pytest.mark.parametrize(
    "fault_point",
    tuple(
        f"real_bootstrap.{field}.{window}"
        for field in (
            "source_ref",
            "config_ref",
            "request_metadata_ref",
            "measurement_bundle_ref",
            "intent_context_ref",
            "preliminary_constraint_ref",
        )
        for window in ("after_put_before_journal", "after_journal")
    )
    + (
        "real_bootstrap.after_state_initialize_before_journal",
    ),
)
def test_real_pre_state_bootstrap_each_confirmed_phase_recovers_without_fixture_fallback(
    tmp_path: Path,
    fault_point: str,
) -> None:
    gateway = _DurableGateway()
    policy = _real_call_policy()
    fired = False

    def fault(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise RuntimeError("real-bootstrap-crash")

    run_id = f"real-bootstrap-{fault_point.split('.')[-2]}"
    service = create_png_to_shader_v2_development_service(
        artifact_root=tmp_path / "artifacts",
        state_root=tmp_path / "states",
        fixture_input_factory=_fixture,
        renderer_factory=lambda _state, reference: _ReferenceRenderer(reference),
        fault_injector=fault,
        real_model_adapter=VisualInterpretationGatewayAdapter(
            gateway=gateway,
            prompt=PromptDefinition(
                name="analyze_visual_layers_v2",
                version="analyze_visual_layers_v2_test",
                prompt="只返回 VisualInterpretationV2 JSON。",
            ),
            policy=policy,
        ),
    )
    config = PngToShaderV2ServiceConfig(
        execution_mode="real",
        allow_model_calls=True,
        real_provider_enabled=True,
        budget_limits=_budget(model=True),
        real_model_call=policy,
    )
    with pytest.raises(RuntimeError, match="real-bootstrap-crash"):
        asyncio.run(
            service.invoke(
                project_id="service-v2-project",
                run_id=run_id,
                source_bytes=_source_png(),
                request_metadata=PngToShaderV2RequestMetadata(
                    request_id=f"request-{run_id}",
                    source_label="unit fixture",
                    source_license="test-only",
                ),
                config=config,
            )
        )
    recovered = asyncio.run(service.resume(run_id=run_id))
    again = asyncio.run(service.resume(run_id=run_id))
    assert gateway.calls == 1
    assert recovered.final_state.phase == "finalized"
    assert recovered.final_state.visual_interpretation_ref is not None
    assert again.final_state == recovered.final_state
    assert again.run_manifest_ref == recovered.run_manifest_ref


@pytest.mark.parametrize(
    "fault_point",
    tuple(
        f"real_artifact_put.{slot}.{window}"
        for slot in ("real_resume_context", "real_final_constraint")
        for window in (
            "before_reserve",
            "after_reserve",
            "after_put",
            "before_commit",
            "after_commit",
        )
    ),
)
def test_real_post_model_artifact_put_windows_close_once_and_remain_stable(
    tmp_path: Path,
    fault_point: str,
) -> None:
    gateway = _DurableGateway()
    policy = _real_call_policy()
    fired = False

    def fault(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise RuntimeError("real-artifact-put-crash")

    run_id = "real-put-" + fault_point.replace(".", "-")
    service = create_png_to_shader_v2_development_service(
        artifact_root=tmp_path / "artifacts",
        state_root=tmp_path / "states",
        fixture_input_factory=_fixture,
        renderer_factory=lambda _state, reference: _ReferenceRenderer(reference),
        fault_injector=fault,
        real_model_adapter=VisualInterpretationGatewayAdapter(
            gateway=gateway,
            prompt=PromptDefinition(
                name="analyze_visual_layers_v2",
                version="analyze_visual_layers_v2_test",
                prompt="只返回 VisualInterpretationV2 JSON。",
            ),
            policy=policy,
        ),
    )
    config = PngToShaderV2ServiceConfig(
        execution_mode="real",
        allow_model_calls=True,
        real_provider_enabled=True,
        budget_limits=_budget(model=True),
        real_model_call=policy,
    )
    with pytest.raises(RuntimeError, match="real-artifact-put-crash"):
        asyncio.run(
            service.invoke(
                project_id="service-v2-project",
                run_id=run_id,
                source_bytes=_source_png(),
                request_metadata=PngToShaderV2RequestMetadata(
                    request_id=f"request-{run_id}",
                    source_label="unit fixture",
                    source_license="test-only",
                ),
                config=config,
            )
        )
    recovered = asyncio.run(service.resume(run_id=run_id))
    stable = asyncio.run(service.resume(run_id=run_id))
    assert gateway.calls == 1
    assert recovered.final_state.budget_state.reserved == BudgetVectorV2(
        wall_time_ms=0,
        model_calls=0,
        model_tokens=0,
        render_calls=0,
        candidate_attempts=0,
        artifact_bytes=0,
        cost_usd_micros=0,
    )
    assert stable.final_state == recovered.final_state
    assert stable.resume_context_ref == recovered.resume_context_ref
    assert stable.run_manifest_ref == recovered.run_manifest_ref


def test_real_mode_parse_failure_is_finalized_charged_and_never_retried(
    tmp_path: Path,
) -> None:
    gateway = _InvalidDurableGateway()
    policy = _real_call_policy()
    state_root = tmp_path / "states"
    service = create_png_to_shader_v2_development_service(
        artifact_root=tmp_path / "artifacts",
        state_root=state_root,
        fixture_input_factory=_fixture,
        renderer_factory=lambda _state, reference: _ReferenceRenderer(reference),
        real_model_adapter=VisualInterpretationGatewayAdapter(
            gateway=gateway,
            prompt=PromptDefinition(
                name="analyze_visual_layers_v2",
                version="analyze_visual_layers_v2_test",
                prompt="只返回 VisualInterpretationV2 JSON。",
            ),
            policy=policy,
        ),
    )
    config = PngToShaderV2ServiceConfig(
        execution_mode="real",
        allow_model_calls=True,
        real_provider_enabled=True,
        budget_limits=_budget(model=True),
        real_model_call=policy,
    )
    with pytest.raises(RealModelCommittedFailure, match="parse_failed"):
        asyncio.run(
            service.invoke(
                project_id="service-v2-project",
                run_id="service-v2-real-parse-failed",
                source_bytes=_source_png(),
                request_metadata=PngToShaderV2RequestMetadata(
                    request_id="request-real-parse-failed",
                    source_label="unit fixture",
                    source_license="test-only",
                ),
                config=config,
            )
        )
    failed = LocalPngToShaderV2StateStore(state_root).load_last_confirmed(
        "service-v2-real-parse-failed"
    )
    assert failed.phase == "finalized"
    assert failed.stop_reason == "visual_interpretation_parse_failed"
    assert failed.budget_state.used.model_calls == 1
    assert failed.budget_state.reserved == BudgetVectorV2(
        wall_time_ms=0,
        model_calls=0,
        model_tokens=0,
        render_calls=0,
        candidate_attempts=0,
        artifact_bytes=0,
        cost_usd_micros=0,
    )
    before_resume = failed
    journal_store = LocalServiceRunJournalStore(
        state_root / ".service-run-journal-v2"
    )
    before_failure_journal = journal_store.load("service-v2-real-parse-failed")
    with pytest.raises(RealModelCommittedFailure, match="parse_failed"):
        asyncio.run(service.resume(run_id="service-v2-real-parse-failed"))
    assert gateway.calls == 1
    assert (
        LocalPngToShaderV2StateStore(state_root).load_last_confirmed(
            "service-v2-real-parse-failed"
        )
        == before_resume
    )
    failure_journal = journal_store.load("service-v2-real-parse-failed")
    assert failure_journal == before_failure_journal
    assert failure_journal.phase == "terminal_failure"
    assert failure_journal.terminal_failure_status == "parse_failed"


def test_real_mode_ignores_fixture_visual_interpretation_evidence(tmp_path: Path) -> None:
    gateway = _DurableGateway()
    policy = _real_call_policy()

    def prerequisites_only(bundle, catalog) -> FixtureIntentInputsV1:
        fixture = _fixture(bundle, catalog)
        missing = ArtifactRefV2(
            artifact_id="fixture-visual-must-not-be-read",
            sha256="f" * 64,
            kind="fixture_visual_only",
            schema_version="fixture_visual_only_v1",
            content_type="application/json",
            size_bytes=1,
        )
        return FixtureIntentInputsV1(
            request_constraint_set=fixture.request_constraint_set,
            visual_interpretation=fixture.visual_interpretation.model_copy(
                update={"evidence_refs": (missing,)}
            ),
            intent_context=fixture.intent_context,
        )

    service = create_png_to_shader_v2_development_service(
        artifact_root=tmp_path / "artifacts",
        state_root=tmp_path / "states",
        fixture_input_factory=prerequisites_only,
        renderer_factory=lambda _state, reference: _ReferenceRenderer(reference),
        real_model_adapter=VisualInterpretationGatewayAdapter(
            gateway=gateway,
            prompt=PromptDefinition(
                name="analyze_visual_layers_v2",
                version="analyze_visual_layers_v2_test",
                prompt="只返回 VisualInterpretationV2 JSON。",
            ),
            policy=policy,
        ),
    )
    result = asyncio.run(
        service.invoke(
            project_id="project",
            run_id="real-prerequisites-only",
            source_bytes=_source_png(),
            request_metadata=PngToShaderV2RequestMetadata(
                request_id="real-prerequisites-only",
                source_label="unit fixture",
                source_license="test-only",
            ),
            config=PngToShaderV2ServiceConfig(
                execution_mode="real",
                allow_model_calls=True,
                real_provider_enabled=True,
                budget_limits=_budget(model=True),
                real_model_call=policy,
            ),
        )
    )
    assert result.final_state.phase == "finalized"
    assert gateway.calls == 1


@pytest.mark.parametrize("store_kind", ("service", "wall"))
@pytest.mark.parametrize(
    "payload_mutation",
    ("inner_duplicate", "inner_non_finite", "outer_duplicate", "outer_non_finite"),
)
def test_service_journals_reject_strict_inner_payload_json(
    tmp_path: Path,
    store_kind: str,
    payload_mutation: str,
) -> None:
    run_id = f"strict-json-{store_kind}-{payload_mutation}"
    if store_kind == "service":
        store = LocalServiceRunJournalStore(tmp_path / store_kind)
        store.initialize(
            ServiceRunJournalV2(
                project_id="project",
                run_id=run_id,
                revision=0,
                phase="bootstrap",
                policy_hash="a" * 64,
                source_sha256="b" * 64,
                config_json="{}",
                request_metadata_json="{}",
                catalog_artifact_bytes=0,
            )
        )
        expected_error = ServiceRunJournalError
    else:
        store = LocalServiceWallTimeLedgerStore(tmp_path / store_kind)
        store.initialize(run_id=run_id, policy_hash="a" * 64, limit_ms=100)
        expected_error = ServiceWallTimeLedgerError
    path = next((tmp_path / store_kind).glob("*.json"))
    envelope = json.loads(path.read_text())
    payload = envelope["payload"]
    if payload_mutation == "inner_duplicate":
        payload = payload.replace(
            f'"run_id":"{run_id}"',
            f'"run_id":"shadow","run_id":"{run_id}"',
            1,
        )
    elif payload_mutation == "inner_non_finite":
        payload = payload.replace('"revision":0', '"revision":NaN', 1)
    if payload_mutation == "outer_duplicate":
        digest = sha256(payload.encode()).hexdigest()
        path.write_text(
            "{" + f'"sha256":"{digest}","sha256":"{digest}",' +
            f'"payload":{json.dumps(payload)}' + "}"
        )
    elif payload_mutation == "outer_non_finite":
        path.write_text("{" + '"sha256":NaN,' + f'"payload":{json.dumps(payload)}' + "}")
    else:
        path.write_text(
            json.dumps(
                {"sha256": sha256(payload.encode()).hexdigest(), "payload": payload}
            )
        )
    with pytest.raises(expected_error, match="完整性"):
        store.load(run_id)


@pytest.mark.parametrize(
    "embedded_json",
    ('{"mode":"shadow","mode":"real"}', '{"limit":NaN}'),
)
def test_service_journal_rejects_ambiguous_embedded_json(
    embedded_json: str,
) -> None:
    with pytest.raises(ValueError, match="config_json"):
        ServiceRunJournalV2(
            project_id="project",
            run_id="embedded-json",
            revision=0,
            phase="bootstrap",
            policy_hash="a" * 64,
            source_sha256="b" * 64,
            config_json=embedded_json,
            request_metadata_json="{}",
            catalog_artifact_bytes=0,
        )


def test_service_resumes_after_unknown_mid_render_crash(tmp_path: Path) -> None:
    renderer_factory = _CrashTransientThenSuccessRendererFactory()
    state_root = tmp_path / "states"
    artifact_root = tmp_path / "artifacts"
    service = create_png_to_shader_v2_development_service(
        artifact_root=artifact_root,
        state_root=state_root,
        fixture_input_factory=_fixture,
        renderer_factory=renderer_factory,
    )
    with pytest.raises(RuntimeError, match="simulated renderer process crash"):
        asyncio.run(
            service.invoke(
                project_id="service-v2-project",
                run_id="service-v2-crash",
                source_bytes=_source_png(),
                request_metadata=PngToShaderV2RequestMetadata(
                    request_id="request-crash",
                    source_label="unit fixture",
                    source_license="test-only",
                ),
                config=PngToShaderV2ServiceConfig(budget_limits=_budget()),
            )
        )
    crashed = LocalPngToShaderV2StateStore(state_root).load_last_confirmed(
        "service-v2-crash"
    )
    assert crashed.budget_state.reserved.render_calls == 1

    recovered = asyncio.run(service.resume(run_id="service-v2-crash"))
    assert recovered.final_state.phase == "finalized"
    assert recovered.final_state.budget_state.reserved.render_calls == 0
    assert renderer_factory.first_request_id is not None
    assert renderer_factory.calls_by_request[renderer_factory.first_request_id] == 2
    assert max(renderer_factory.calls_by_request.values()) <= 2
    assert [outcome for _, outcome in renderer_factory.outcomes[:3]] == [
        "runtime_error",
        "transient",
        "success",
    ]
    assert recovered.final_state.budget_state.used.render_calls == sum(
        renderer_factory.calls_by_request.values()
    )

    catalog = LocalArtifactCatalog(
        LocalArtifactStore(artifact_root).start_run(
            "service-v2-project", "service-v2-crash"
        ),
        run_id="service-v2-crash",
    )
    failed_attempts = tuple(
        load_candidate_attempt(
            ref,
            resolver=catalog,
            run_id="service-v2-crash",
        )
        for ref in recovered.final_state.candidate_summary_refs
        if ref.kind == "candidate_attempt_record"
    )
    unknown_closure = next(
        attempt
        for attempt in failed_attempts
        if any(item.outcome == "unknown" for item in attempt.evidence)
    )
    renderer_outcomes = tuple(
        item.outcome
        for item in unknown_closure.evidence
        if item.renderer_request_hash is not None
    )
    assert renderer_outcomes == ("unknown", "transient_failure")
    assert unknown_closure.attempt.status == "render_failed"


def test_service_timeout_commits_full_wall_reservation(tmp_path: Path) -> None:
    state_root = tmp_path / "states"
    service = create_png_to_shader_v2_development_service(
        artifact_root=tmp_path / "artifacts",
        state_root=state_root,
        fixture_input_factory=_fixture,
        renderer_factory=lambda _state, reference: _SlowRenderer(reference),
    )
    limits = _budget().model_copy(update={"wall_time_ms": 100})
    with pytest.raises(V2WallTimeBudgetExceeded, match="deadline"):
        asyncio.run(
            service.invoke(
                project_id="service-v2-project",
                run_id="service-v2-timeout",
                source_bytes=_source_png(),
                request_metadata=PngToShaderV2RequestMetadata(
                    request_id="request-timeout",
                    source_label="unit fixture",
                    source_license="test-only",
                ),
                config=PngToShaderV2ServiceConfig(budget_limits=limits),
            )
        )
    ledger = LocalServiceWallTimeLedgerStore(state_root / ".service-wall-time-v1").load(
        "service-v2-timeout"
    )
    state = LocalPngToShaderV2StateStore(state_root).load_last_confirmed(
        "service-v2-timeout"
    )
    assert ledger.reserved_ms == 0
    assert ledger.used_ms == 100
    assert state.budget_state.reserved.wall_time_ms == 0
    assert state.budget_state.used.wall_time_ms == 100


def test_model_receipt_reserves_worst_case_and_commits_actual(tmp_path: Path) -> None:
    run_id = "model-receipt-run"
    catalog = LocalArtifactCatalog(
        LocalArtifactStore(tmp_path / "artifacts").register_run("project", run_id),
        run_id=run_id,
    )
    interpretation_ref = _put_model_interpretation(catalog, run_id)
    zero = BudgetVectorV2(
        wall_time_ms=0,
        model_calls=0,
        model_tokens=0,
        render_calls=0,
        candidate_attempts=0,
        artifact_bytes=0,
        cost_usd_micros=0,
    )
    state = PngToShaderV2State(
        checkpoint_namespace=build_checkpoint_namespace_v2(run_id),
        project_id="project",
        run_id=run_id,
        run_revision=0,
        phase="initialized",
        evaluation_revision=0,
        measurements_ref=interpretation_ref,
        visual_interpretation_ref=None,
        request_constraint_set_ref=interpretation_ref,
        hypothesis_branches=(),
        hypothesis_cursor=0,
        objective_best_id=None,
        candidate_summary_refs=(),
        budget_state=BudgetStateV2(
            policy_hash="a" * 64,
            revision=0,
            limits=_budget(model=True),
            used=zero,
            reserved=zero,
            exhausted_dimensions=(),
        ),
        stop_reason=None,
    )
    store = LocalPngToShaderV2StateStore(tmp_path / "states")
    store.initialize(state)
    reservation = ModelCallReservationV1(
        invocation_id="call-1",
        **_model_identity(interpretation_ref),
        max_input_tokens=100,
        max_output_tokens=100,
        max_cost_usd_micros=900,
        max_output_artifact_bytes=interpretation_ref.size_bytes,
    )

    reserved = reserve_model_call_v1(store, state, reservation)
    assert reserved.budget_state.reserved.model_tokens == 200
    committed = commit_model_call_receipt_v1(
        store,
        reserved,
        reservation,
        ModelCallReceiptV1(
            invocation_id="call-1",
            provider_receipt_id="provider-1",
            **_model_identity(interpretation_ref),
            input_tokens=70,
            output_tokens=20,
            cost_usd_micros=350,
            interpretation_ref=interpretation_ref,
        ),
        resolver=catalog,
    )

    assert committed.budget_state.reserved == zero
    assert committed.budget_state.used.model_calls == 1
    assert committed.budget_state.used.model_tokens == 90
    assert committed.budget_state.used.cost_usd_micros == 350


def test_model_receipt_rejects_overage_wrong_invocation_and_tampered_ref(
    tmp_path: Path,
) -> None:
    run_id = "model-invalid-receipt"
    catalog = LocalArtifactCatalog(
        LocalArtifactStore(tmp_path / "artifacts").register_run("project", run_id),
        run_id=run_id,
    )
    ref = _put_model_interpretation(catalog, run_id)
    zero = BudgetVectorV2(
        wall_time_ms=0,
        model_calls=0,
        model_tokens=0,
        render_calls=0,
        candidate_attempts=0,
        artifact_bytes=0,
        cost_usd_micros=0,
    )
    state = PngToShaderV2State(
        checkpoint_namespace=build_checkpoint_namespace_v2(run_id),
        project_id="project",
        run_id=run_id,
        run_revision=0,
        phase="initialized",
        evaluation_revision=0,
        measurements_ref=ref,
        visual_interpretation_ref=None,
        request_constraint_set_ref=ref,
        hypothesis_branches=(),
        hypothesis_cursor=0,
        objective_best_id=None,
        candidate_summary_refs=(),
        budget_state=BudgetStateV2(
            policy_hash="c" * 64,
            revision=0,
            limits=_budget(model=True),
            used=zero,
            reserved=zero,
            exhausted_dimensions=(),
        ),
        stop_reason=None,
    )
    store = LocalPngToShaderV2StateStore(tmp_path / "states")
    store.initialize(state)
    reservation = ModelCallReservationV1(
        invocation_id="call-valid",
        **_model_identity(ref),
        max_input_tokens=50,
        max_output_tokens=50,
        max_cost_usd_micros=500,
        max_output_artifact_bytes=ref.size_bytes,
    )
    reserved = reserve_model_call_v1(store, state, reservation)

    def receipt(**updates):
        values = {
            "invocation_id": "call-valid",
            "provider_receipt_id": "provider-valid",
            **_model_identity(ref),
            "input_tokens": 40,
            "output_tokens": 20,
            "cost_usd_micros": 300,
            "interpretation_ref": ref,
        }
        values.update(updates)
        return ModelCallReceiptV1(**values)

    with pytest.raises(ValueError, match="invocation_id"):
        commit_model_call_receipt_v1(
            store,
            reserved,
            reservation,
            receipt(invocation_id="wrong"),
            resolver=catalog,
        )
    with pytest.raises(ValueError, match="request_sha256"):
        commit_model_call_receipt_v1(
            store,
            reserved,
            reservation,
            receipt(request_sha256="4" * 64),
            resolver=catalog,
        )
    with pytest.raises(ValueError, match="token"):
        commit_model_call_receipt_v1(
            store,
            reserved,
            reservation,
            receipt(input_tokens=90, output_tokens=20),
            resolver=catalog,
        )
    with pytest.raises(ValueError, match="cost"):
        commit_model_call_receipt_v1(
            store,
            reserved,
            reservation,
            receipt(cost_usd_micros=501),
            resolver=catalog,
        )
    tampered = ref.__class__(
        artifact_id=ref.artifact_id,
        sha256="f" * 64,
        kind=ref.kind,
        schema_version=ref.schema_version,
        content_type=ref.content_type,
        size_bytes=ref.size_bytes,
    )
    with pytest.raises(ValueError, match="身份"):
        commit_model_call_receipt_v1(
            store,
            reserved,
            reservation,
            receipt(interpretation_ref=tampered),
            resolver=catalog,
        )
    assert store.load_last_confirmed(run_id).budget_state.reserved.model_calls == 1


def test_wall_time_orphan_is_conservatively_charged_after_store_restart(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wall-ledger"
    store = LocalServiceWallTimeLedgerStore(root)
    initial = store.initialize(run_id="orphan-run", policy_hash="d" * 64, limit_ms=50)
    reserved = store.reserve_remaining("orphan-run", expected_revision=initial.revision)
    assert reserved.reserved_ms == 50

    recovered = LocalServiceWallTimeLedgerStore(root).recover_orphan("orphan-run")
    assert recovered.reserved_ms == 0
    assert recovered.used_ms == 50
