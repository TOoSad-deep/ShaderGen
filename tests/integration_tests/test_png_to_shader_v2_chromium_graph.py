from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest
from PIL import Image, ImageDraw

from agent.app.benchmarks.v2_rendered_gate_collector import (
    V2_3RenderedCaseCollectionIdentity,
    collect_v2_3_verified_rendered_case,
)
from agent.app.services.png_to_shader_v2 import (
    FixtureIntentInputFactory,
    FixtureIntentInputsV1,
    FixtureRendererFactory,
    PngToShaderV2RequestMetadata,
    PngToShaderV2ServiceConfig,
    create_png_to_shader_v2_development_service,
)
from agent.app.states.png_to_shader_v2_state import (
    BudgetVectorV2,
    PngToShaderV2State,
)
from agent.app.states.png_to_shader_v2_state_store import (
    LocalPngToShaderV2StateStore,
)
from shaderforge.analysis import TargetMeasurementsV2ArtifactBundle
from shaderforge.benchmark import V2_3ActualChromiumReplayRunner
from shaderforge.benchmark.v2_3_rendered_structure_gate import (
    build_v2_3_rendered_threshold_policy,
)
from shaderforge.contracts import REQUIRED_LAYER_ORDER
from shaderforge.evaluation import load_typed_candidate_artifacts
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
from shaderforge.rendering import PlaywrightWebGL1Renderer, RenderResult
from shaderforge.store import (
    ArtifactCatalog,
    LocalArtifactCatalog,
    LocalArtifactStore,
)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_VALID_SHADER = """precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;
void main() {
    vec3 color = vec3(v_uv, 0.5);
    gl_FragColor = vec4(color, 1.0);
}
"""


def _solid_source_png() -> bytes:
    image = Image.new("RGBA", (64, 64), (255, 255, 255, 0))
    ImageDraw.Draw(image).ellipse((12, 12, 52, 52), fill=(220, 70, 90, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _fixture_intent_inputs(
    bundle: TargetMeasurementsV2ArtifactBundle,
    _catalog: ArtifactCatalog,
) -> FixtureIntentInputsV1:
    evidence = bundle.measurements_ref
    interpretation = VisualInterpretationV2(
        summary="真实测量绑定的离线单层椭圆 fixture。",
        layer_hypotheses=(
            LayerHypothesis(
                layer_id="chromium-base",
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
                candidate_id="chromium-ellipse",
                primitive_id="ellipse_sdf",
                layer_id="chromium-base",
                confidence=1.0,
                evidence_refs=(evidence,),
            ),
        ),
        strategy_hypotheses=(
            StrategyHypothesis(
                strategy_id="chromium-minimal",
                template_ids=("geometry.ellipse_sdf.v0",),
                required_layer_ids=("chromium-base",),
                complexity="low",
                confidence=1.0,
                evidence_refs=(evidence,),
            ),
        ),
        evidence_refs=(evidence,),
    )
    constraints = build_request_constraint_set(
        constraint_set_id="chromium-graph-constraints",
        target_sha256=bundle.measurements.target_sha256,
        request_revision=0,
        constraints=(
            Constraint(
                constraint_id="chromium-render-contract",
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
        request_constraint_set=constraints,
        visual_interpretation=interpretation,
        intent_context=context,
    )


def _budget() -> BudgetVectorV2:
    return BudgetVectorV2(
        wall_time_ms=60_000,
        model_calls=0,
        model_tokens=0,
        render_calls=24,
        candidate_attempts=3,
        artifact_bytes=16_000_000,
        cost_usd_micros=0,
    )


class _ObservedChromiumRenderer:
    def __init__(self) -> None:
        # V2.4 的 physical replay 只由 Graph progress/budget 控制。
        self.delegate = PlaywrightWebGL1Renderer(replay_on_worker_failure=0)
        self.results: list[RenderResult] = []
        self.close_completed = False

    async def render(
        self,
        fragment_source: str,
        width: int,
        height: int,
    ) -> RenderResult:
        result = await self.delegate.render(fragment_source, width, height)
        self.results.append(result)
        return result

    async def close(self) -> None:
        await self.delegate.close()
        self.close_completed = True


class _ObservedChromiumFactory:
    def __init__(self) -> None:
        self.sessions: list[_ObservedChromiumRenderer] = []

    def __call__(
        self,
        _state: PngToShaderV2State,
        _normalized_reference_png: bytes,
    ) -> _ObservedChromiumRenderer:
        renderer = _ObservedChromiumRenderer()
        self.sessions.append(renderer)
        return renderer


@pytest.mark.anyio
async def test_v2_development_only_graph_closes_candidates_with_real_chromium(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 source 经 production V2 Graph 编译、链接、绘制并形成闭合 Candidate。"""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    artifact_root = tmp_path / "artifacts"
    state_root = tmp_path / "states"
    renderer_factory = _ObservedChromiumFactory()
    service = create_png_to_shader_v2_development_service(
        artifact_root=artifact_root,
        state_root=state_root,
        fixture_input_factory=cast(
            FixtureIntentInputFactory,
            _fixture_intent_inputs,
        ),
        renderer_factory=cast(FixtureRendererFactory, renderer_factory),
    )
    source = _solid_source_png()
    config = PngToShaderV2ServiceConfig(budget_limits=_budget())

    result = await service.invoke(
        project_id="v2-chromium-project",
        run_id="v2-chromium-graph-run",
        source_bytes=source,
        request_metadata=PngToShaderV2RequestMetadata(
            request_id="v2-chromium-request",
            expected_source_sha256=sha256(source).hexdigest(),
            source_label="generated solid ellipse integration fixture",
            source_license="test-only generated fixture",
        ),
        config=config,
    )

    state = result.final_state
    assert state.phase == "finalized"
    assert state.stop_reason == "completed_with_objective_best"
    assert state.objective_best_id is not None
    assert state.objective_best_ref is not None
    assert state.objective_best_ref in state.candidate_summary_refs
    assert len(state.hypothesis_branches) == 1
    assert state.hypothesis_branches[0].status == "completed"
    assert state.hypothesis_branches[0].hypothesis_best_id is not None
    assert config.production_admission_enabled is False
    assert state.budget_state.used.model_calls == 0
    assert state.budget_state.used.model_tokens == 0
    assert state.budget_state.used.cost_usd_micros == 0
    assert state.budget_state.used.render_calls == 24
    assert state.budget_state.used.candidate_attempts == 3
    assert state.budget_state.reserved.render_calls == 0
    assert state.budget_state.reserved.candidate_attempts == 0

    catalog = LocalArtifactCatalog(
        LocalArtifactStore(artifact_root).resolve_run(state.run_id),
        run_id=state.run_id,
    )
    candidate_refs = tuple(
        ref for ref in state.candidate_summary_refs if ref.kind == "candidate_record"
    )
    assert len(candidate_refs) == 3
    assert not tuple(
        ref
        for ref in state.candidate_summary_refs
        if ref.kind == "candidate_attempt_record"
    )

    persisted_render_hashes: list[str] = []
    for candidate_ref in candidate_refs:
        candidate = load_typed_candidate_artifacts(
            candidate_ref,
            resolver=catalog,
            run_id=state.run_id,
        )
        subject = candidate.intent.objects[0]
        assert subject.topology == "solid"
        assert subject.component_count == 1
        assert subject.instance_count == 1
        assert subject.hole_count == 0
        assert candidate.constraint_evaluation.hard_constraints_passed is True
        assert len(candidate.candidate.render_refs) == 5
        assert tuple(
            item.profile for item in candidate.render_plan.items[:5]
        ) == ("beauty_full_v1",) * 5
        assert len(candidate.render_plan.items) == 8
        assert candidate.render_progress.completed_logical_requests == 8
        for render_ref in candidate.candidate.render_refs:
            png = catalog.read_bytes(render_ref.artifact_id)
            assert png.startswith(_PNG_SIGNATURE)
            assert sha256(png).hexdigest() == render_ref.sha256
            assert render_ref.sha256 != sha256(source).hexdigest()
            with Image.open(BytesIO(png)) as image:
                image.load()
                assert image.format == "PNG"
                assert image.size == (64, 64)
        persisted_render_hashes.extend(
            outcome.render_ref.sha256
            for outcome in candidate.render_progress.outcomes
            if outcome.render_ref is not None
        )

    policy = build_v2_3_rendered_threshold_policy()
    async with V2_3ActualChromiumReplayRunner() as replay_runner:
        collection = await collect_v2_3_verified_rendered_case(
            state_store=LocalPngToShaderV2StateStore(state_root),
            run_id=state.run_id,
            resolver=catalog,
            identity=V2_3RenderedCaseCollectionIdentity(
                manifest_id="chromium-integration-manifest",
                dataset_version="chromium-integration-v1",
                manifest_sha256="a" * 64,
                taxonomy_sha256="b" * 64,
                config_sha256="c" * 64,
                threshold_policy_hash=policy.policy_hash,
                input_intent_outcomes_sha256="d" * 64,
                input_compiler_outcomes_sha256="e" * 64,
                split="development",
                case_id="chromium-collector-case",
                source_image_sha256=sha256(source).hexdigest(),
                expected_hypothesis_count=1,
            ),
            replay_runner=replay_runner,
        )
    outcome = collection.capability.outcome
    assert outcome.success
    assert outcome.selected_candidate_ref == state.objective_best_ref
    assert outcome.all_candidate_refs == candidate_refs
    assert len(outcome.actual_replay_receipt_hashes) == 3
    assert tuple(item.record_hash for item in collection.receipts) == (
        outcome.actual_replay_receipt_hashes
    )
    assert outcome.actual_replay_receipts_root is not None
    assert outcome.beauty_capture_count == 15
    assert outcome.diagnostic_render_count == 9
    assert outcome.physical_render_call_count == 24
    assert all(
        receipt.actual_environment_hash == outcome.renderer_environment_hash
        for receipt in collection.receipts
    )
    assert all(
        item.persisted_renderer_environment_hash
        == outcome.persisted_renderer_environment_hash
        for receipt in collection.receipts
        for item in receipt.item_receipts
    )
    assert all(receipt.model_dump_json() for receipt in collection.receipts)

    async with V2_3ActualChromiumReplayRunner() as replay_runner:
        # 模拟 replay worker 在 collector 进入逐 Candidate 执行前失效。
        await replay_runner.__aexit__()
        failed_collection = await collect_v2_3_verified_rendered_case(
            state_store=LocalPngToShaderV2StateStore(state_root),
            run_id=state.run_id,
            resolver=catalog,
            identity=V2_3RenderedCaseCollectionIdentity(
                manifest_id="chromium-integration-manifest",
                dataset_version="chromium-integration-v1",
                manifest_sha256="a" * 64,
                taxonomy_sha256="b" * 64,
                config_sha256="c" * 64,
                threshold_policy_hash=policy.policy_hash,
                input_intent_outcomes_sha256="d" * 64,
                input_compiler_outcomes_sha256="e" * 64,
                split="development",
                case_id="chromium-collector-case",
                source_image_sha256=sha256(source).hexdigest(),
                expected_hypothesis_count=1,
            ),
            replay_runner=replay_runner,
        )
    failed_outcome = failed_collection.capability.outcome
    assert not failed_outcome.success
    assert failed_outcome.all_candidate_refs == candidate_refs
    assert failed_collection.receipts == ()
    assert failed_outcome.failure_codes == (
        "strict_collection_failed:replay_candidates:RuntimeError",
    )

    assert len(renderer_factory.sessions) == 24
    assert all(session.close_completed for session in renderer_factory.sessions)
    observed_results = [
        render
        for session in renderer_factory.sessions
        for render in session.results
    ]
    assert len(observed_results) == 24
    assert all(render.success and render.compile.success for render in observed_results)
    assert all(render.image_bytes is not None for render in observed_results)
    assert all(render.metadata is not None for render in observed_results)
    assert all(
        render.metadata is not None and "WebGL" in render.metadata.gl_version
        for render in observed_results
    )
    assert all(
        render.metadata is not None
        and render.metadata.webgl_context_kind == "webgl1"
        and render.metadata.canvas_alpha is False
        and render.metadata.canvas_antialias is False
        and render.metadata.canvas_depth is False
        and render.metadata.canvas_stencil is False
        and render.metadata.premultiplied_alpha is False
        and render.metadata.preserve_drawing_buffer is True
        and render.metadata.canvas_clear_color_rgba == (1.0, 1.0, 1.0, 1.0)
        for render in observed_results
    )
    assert sorted(persisted_render_hashes) == sorted(
        render.image_sha256 for render in observed_results if render.image_sha256
    )


@pytest.mark.anyio
async def test_development_only_graph_chromium_a_red_invalid_b_green_has_no_stale_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一真实 worker 的失败帧必须为空，后续成功帧不得来自陈旧 canvas。"""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    shader_a_red = _VALID_SHADER.replace(
        "vec3 color = vec3(v_uv, 0.5);",
        "vec3 color = vec3(1.0, 0.0, 0.0);",
    )
    syntax_invalid = shader_a_red.replace(
        "vec3 color = vec3(1.0, 0.0, 0.0);",
        "vec3 color = ;",
    )
    shader_b_green = _VALID_SHADER.replace(
        "vec3 color = vec3(v_uv, 0.5);",
        "vec3 color = vec3(0.0, 1.0, 0.0);",
    )
    renderer = PlaywrightWebGL1Renderer()
    try:
        first = await renderer.render(shader_a_red, 64, 64)
        failed = await renderer.render(syntax_invalid, 64, 64)
        recovered = await renderer.render(shader_b_green, 64, 64)
    finally:
        await renderer.close()

    assert first.success and first.image_bytes is not None
    assert not failed.success
    assert failed.image_bytes is None
    assert failed.image_sha256 is None
    assert failed.compile.fragment_log
    assert recovered.success
    assert recovered.image_bytes != first.image_bytes
    assert recovered.image_sha256 != first.image_sha256
    assert renderer._page is None  # noqa: SLF001
    assert renderer._browser is None  # noqa: SLF001
    assert renderer._playwright is None  # noqa: SLF001
