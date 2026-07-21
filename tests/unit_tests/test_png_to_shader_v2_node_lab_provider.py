from __future__ import annotations

import asyncio
import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from agent.app.lab.models import (
    LabRunCreateRequest,
    NodeLabError,
    StepExecutionRequest,
)
from agent.app.nodes.png_to_shader_v2.integrations.node_lab import (
    V2_INTERPRETATION_FIXTURE_ID,
    build_png_to_shader_v2_registry,
    create_png_to_shader_v2_node_provider,
)
from agent.app.nodes.png_to_shader_v2.integrations.node_lab.catalog import (
    NodeLabArtifactCatalogV2,
)
from agent.app.nodes.png_to_shader_v2.runtime import PNG_TO_SHADER_V2_NODE_IDS
from agent.app.services.node_lab import create_node_lab_application
from shaderforge.evaluation import INTENT_ARTIFACT_KIND
from shaderforge.intent import (
    RequestConstraintSet,
    build_intent_build_context,
    compute_constraint_set_hash,
    parse_visual_interpretation_v2,
)
from shaderforge.rendering import CompileResult, RendererMetadata, RenderResult
from shaderforge.store import ArtifactRefV2
from shaderforge.validation import validate_shader
from tests.fixtures.png_to_shader_v2_contracts import (
    make_constraint_set,
    make_state,
    make_target_measurements,
)


def _ref(descriptor: object, *, kind: str, schema_version: str) -> ArtifactRefV2:
    return ArtifactRefV2(
        artifact_id=descriptor.artifact_id,
        sha256=descriptor.sha256,
        kind=kind,
        schema_version=schema_version,
        content_type=descriptor.content_type,
        size_bytes=descriptor.size_bytes,
    )


def _application(tmp_path: Path, **provider_options: object):
    def context_provider(*_args: object) -> object:
        raise AssertionError("当前测试不应调用 Intent context provider。")

    def reference_provider(*_args: object) -> ArtifactRefV2:
        raise AssertionError("当前测试不应调用 Basic Oracle reference provider。")

    provider_options.setdefault("intent_context_provider", context_provider)
    provider_options.setdefault("reference_artifact_provider", reference_provider)
    provider = create_png_to_shader_v2_node_provider(**provider_options)
    return create_node_lab_application(
        root=tmp_path / "node-lab-v2",
        node_provider=provider,
    )


def _run_and_state(application: object) -> tuple[object, dict[str, object]]:
    run = application.create_run(
        LabRunCreateRequest(project_id="project-v2", initial_state={})
    )
    measurements = application.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="target_measurements",
        content_type="application/json",
        data=b"{}",
    )
    constraints = application.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="request_constraint_set",
        content_type="application/json",
        data=b"{}",
    )
    state = make_state().model_copy(
        update={
            "project_id": "project-v2",
            "run_id": "run-v2-node-lab",
            "checkpoint_namespace": "png-to-shader-v2.4:run-v2-node-lab",
            "measurements_ref": _ref(
                measurements,
                kind="target_measurements",
                schema_version="target_measurements_v2_2",
            ),
            "request_constraint_set_ref": _ref(
                constraints,
                kind="request_constraint_set",
                schema_version="request_constraint_set_v1",
            ),
        }
    )
    return run, state.model_dump(mode="json")


class _DeterministicRenderer:
    def __init__(self) -> None:
        self.closed = False

    async def render(
        self, fragment_source: str, width: int, height: int
    ) -> RenderResult:
        validation = validate_shader(fragment_source)
        image = Image.new("RGB", (width, height), (0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse(
            (
                round(width * 0.2),
                round(height * 0.2),
                round(width * 0.8) - 1,
                round(height * 0.8) - 1,
            ),
            fill=(255, 255, 255),
        )
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return RenderResult(
            success=validation.valid,
            image_bytes=buffer.getvalue() if validation.valid else None,
            width=width,
            height=height,
            compile=CompileResult(
                success=validation.valid,
                vertex_log="",
                fragment_log="",
                link_log="",
                draw_error=None,
                static_validation=validation,
            ),
            console_errors=(),
            metadata=RendererMetadata(
                renderer_version="node-lab-v2-fixture",
                browser_version="fixture",
                gl_version="WebGL 1.0",
                glsl_version="WebGL GLSL ES 1.00",
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
        self.closed = True


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (192, 192), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _contract_only_constraints() -> RequestConstraintSet:
    original = make_constraint_set()
    draft = RequestConstraintSet(
        constraint_set_id="node-lab-v2-contract-only",
        constraint_set_hash="0" * 64,
        target_sha256=original.target_sha256,
        request_revision=original.request_revision,
        constraints=(original.constraints[0],),
        conflicts=(),
        evidence_refs=(),
    )
    return draft.model_copy(
        update={"constraint_set_hash": compute_constraint_set_hash(draft)}
    )


def test_node_lab_catalog_reports_stable_deduplicated_run_snapshot(
    tmp_path: Path,
) -> None:
    application = _application(tmp_path)
    run = application.create_run(
        LabRunCreateRequest(project_id="project-v2", initial_state={})
    )
    first_descriptor = application.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="first",
        content_type="application/octet-stream",
        data=b"first",
    )
    second_descriptor = application.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="second",
        content_type="application/octet-stream",
        data=b"second-payload",
    )
    first = _ref(first_descriptor, kind="first", schema_version="first_v1")
    second = _ref(second_descriptor, kind="second", schema_version="second_v1")
    catalog = NodeLabArtifactCatalogV2(
        application,
        lab_run_id=run.lab_run_id,
        run_id="run-v2-node-lab-catalog",
        refs=(second, first, first),
    )

    initial_snapshot = catalog.list_refs()

    assert initial_snapshot == tuple(
        sorted((first, second), key=lambda item: item.artifact_id)
    )
    assert catalog.total_size_bytes() == first.size_bytes + second.size_bytes

    third = catalog.put(
        run_id="run-v2-node-lab-catalog",
        kind="third",
        schema_version="third_v1",
        content_type="application/octet-stream",
        data=b"third",
    )

    assert catalog.list_refs() == tuple(
        sorted((first, second, third), key=lambda item: item.artifact_id)
    )
    assert catalog.total_size_bytes() == sum(
        item.size_bytes for item in (first, second, third)
    )
    assert initial_snapshot == tuple(
        sorted((first, second), key=lambda item: item.artifact_id)
    )


def _full_chain_application_state(tmp_path: Path):
    references: dict[str, ArtifactRefV2] = {}
    renderers: list[_DeterministicRenderer] = []
    png = _png_bytes()

    def context_provider(state, *_args):
        return build_intent_build_context(
            contract_id="webgl1_static_no_texture_v1",
            primitive_catalog_sha256="a" * 64,
            template_catalog_sha256="b" * 64,
            allowed_primitive_ids=("ellipse_sdf",),
            allowed_template_ids=("geometry.ellipse_sdf.v0",),
            allowed_interpretation_evidence_refs=(state.measurements_ref,),
        )

    def reference_provider(state, _resolver):
        return references[state.run_id]

    def renderer_factory():
        renderer = _DeterministicRenderer()
        renderers.append(renderer)
        return renderer

    application = _application(
        tmp_path,
        intent_context_provider=context_provider,
        reference_artifact_provider=reference_provider,
        renderer_factory=renderer_factory,
    )
    run = application.create_run(
        LabRunCreateRequest(project_id="project-v2", initial_state={})
    )
    measurements = make_target_measurements()
    constraints = _contract_only_constraints()
    measurements_descriptor = application.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="target_measurements",
        content_type="application/json",
        data=measurements.model_dump_json().encode("utf-8"),
    )
    constraint_descriptor = application.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="request_constraint_set",
        content_type="application/json",
        data=constraints.model_dump_json().encode("utf-8"),
    )
    reference_descriptor = application.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="normalized_reference_png",
        content_type="image/png",
        data=png,
    )
    run_id = "run-v2-node-lab-chain"
    references[run_id] = _ref(
        reference_descriptor,
        kind="normalized_reference",
        schema_version="normalized_reference_v1",
    )
    state = make_state().model_copy(
        update={
            "project_id": "project-v2",
            "run_id": run_id,
            "checkpoint_namespace": f"png-to-shader-v2.4:{run_id}",
            "measurements_ref": _ref(
                measurements_descriptor,
                kind="target_measurements",
                schema_version="target_measurements_v2_2",
            ),
            "request_constraint_set_ref": _ref(
                constraint_descriptor,
                kind="request_constraint_set",
                schema_version="request_constraint_set_v1",
            ),
        }
    )
    return application, run, state.model_dump(mode="json"), renderers


def test_v2_provider_descriptors_exactly_match_production_nodes() -> None:
    descriptors = build_png_to_shader_v2_registry().describe_nodes()

    assert tuple(item.node_id for item in descriptors) == PNG_TO_SHADER_V2_NODE_IDS
    assert len(descriptors) == 22
    assert {item.pipeline_id for item in descriptors} == {"png_to_shader_v2"}
    analyze = next(
        item for item in descriptors if item.node_id == "analyze_visual_layers_v2"
    )
    assert analyze.execution_modes == ["fixture", "mock", "real"]
    assert analyze.requires_model is True
    assert analyze.default_fixture_ids == [V2_INTERPRETATION_FIXTURE_ID]
    assert (
        next(
            item for item in descriptors if item.node_id == "render_candidate_v2"
        ).requires_browser
        is True
    )
    assert all(item.implementation_status == "available" for item in descriptors)


def test_v2_fixture_calls_production_node_and_artifactizes_output(
    tmp_path: Path,
) -> None:
    application = _application(tmp_path)
    run, state = _run_and_state(application)

    response = asyncio.run(
        application.execute_step(
            StepExecutionRequest(
                lab_run_id=run.lab_run_id,
                node_id="analyze_visual_layers_v2",
                execution_mode="fixture",
                fixture_id=V2_INTERPRETATION_FIXTURE_ID,
                inputs=state,
            )
        )
    )

    assert response.execution_status == "completed"
    assert response.outcome == "success"
    assert response.pipeline_id == "png_to_shader_v2"
    assert response.provenance["execution_source"] == "production_node"
    assert response.provenance["model_boundary"] == "fixture"
    assert response.usage == {"model_call_count": 0, "browser_launch_count": 0}
    visual_ref = response.output["visual_interpretation_ref"]
    assert visual_ref["kind"] == "visual_interpretation"
    assert visual_ref["schema_version"] == "visual_interpretation_v2_1"
    assert len(response.artifacts) == 1
    descriptor, payload = application.read_artifact(
        run.lab_run_id, visual_ref["artifact_id"]
    )
    assert descriptor.sha256 == visual_ref["sha256"]
    assert parse_visual_interpretation_v2(payload.decode("utf-8")).summary.startswith(
        "Node Lab V2"
    )


def test_v2_mock_is_strict_and_still_calls_production_node(tmp_path: Path) -> None:
    application = _application(tmp_path)
    run, state = _run_and_state(application)
    fixture = asyncio.run(
        application.execute_step(
            StepExecutionRequest(
                lab_run_id=run.lab_run_id,
                node_id="analyze_visual_layers_v2",
                execution_mode="fixture",
                inputs=state,
            )
        )
    )
    visual_ref = fixture.output["visual_interpretation_ref"]
    _descriptor, payload = application.read_artifact(
        run.lab_run_id, visual_ref["artifact_id"]
    )
    mock = application.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="mock_visual_interpretation",
        content_type="application/json",
        data=payload,
    )

    response = asyncio.run(
        application.execute_step(
            StepExecutionRequest(
                lab_run_id=run.lab_run_id,
                node_id="analyze_visual_layers_v2",
                execution_mode="mock",
                mock_response_artifact_id=mock.artifact_id,
                inputs=state,
            )
        )
    )
    assert response.execution_status == "completed"
    assert response.provenance["execution_source"] == "production_node"
    assert response.provenance["model_boundary"] == "mock"
    assert response.usage["model_call_count"] == 0

    invalid = application.upload_artifact(
        lab_run_id=run.lab_run_id,
        kind="mock_visual_interpretation",
        content_type="application/json",
        data=b'{"schema_version":"visual_interpretation_v2_1"}',
    )
    failed = asyncio.run(
        application.execute_step(
            StepExecutionRequest(
                lab_run_id=run.lab_run_id,
                node_id="analyze_visual_layers_v2",
                execution_mode="mock",
                mock_response_artifact_id=invalid.artifact_id,
                inputs=state,
            )
        )
    )
    assert failed.execution_status == "failed"
    assert failed.diagnostics["error"]["code"] == "mock_response_invalid"


def test_v2_real_model_requires_both_explicit_switches_before_step_creation(
    tmp_path: Path,
) -> None:
    disabled = _application(tmp_path / "disabled")
    run, state = _run_and_state(disabled)
    before = disabled.list_step_ids(run.lab_run_id)
    with pytest.raises(NodeLabError, match="durable Service") as blocked:
        asyncio.run(
            disabled.execute_step(
                StepExecutionRequest(
                    lab_run_id=run.lab_run_id,
                    node_id="analyze_visual_layers_v2",
                    execution_mode="real",
                    allow_model_call=True,
                    inputs=state,
                )
            )
        )
    assert blocked.value.code == "real_model_requires_durable_service"
    assert disabled.list_step_ids(run.lab_run_id) == before

    provider_calls = 0

    def real_provider(state_value: object, catalog: object) -> ArtifactRefV2:
        nonlocal provider_calls
        provider_calls += 1
        fixture_application = enabled
        descriptor, payload = fixture_application.read_artifact(
            enabled_run.lab_run_id, real_payload.artifact_id
        )
        assert descriptor.lab_run_id == enabled_run.lab_run_id
        interpretation = parse_visual_interpretation_v2(payload.decode("utf-8"))
        return catalog.put(
            run_id=state_value.run_id,
            kind="visual_interpretation",
            schema_version="visual_interpretation_v2_1",
            content_type="application/json",
            data=interpretation.model_dump_json().encode("utf-8"),
        )

    enabled = _application(
        tmp_path / "enabled",
        real_model_enabled=True,
        real_interpretation_provider=real_provider,
    )
    enabled_run, enabled_state = _run_and_state(enabled)
    seed = asyncio.run(
        enabled.execute_step(
            StepExecutionRequest(
                lab_run_id=enabled_run.lab_run_id,
                node_id="analyze_visual_layers_v2",
                execution_mode="fixture",
                inputs=enabled_state,
            )
        )
    )
    _seed_descriptor, seed_payload = enabled.read_artifact(
        enabled_run.lab_run_id,
        seed.output["visual_interpretation_ref"]["artifact_id"],
    )
    real_payload = enabled.upload_artifact(
        lab_run_id=enabled_run.lab_run_id,
        kind="real_model_fixture",
        content_type="application/json",
        data=seed_payload,
    )

    with pytest.raises(NodeLabError, match="durable Service"):
        asyncio.run(
            enabled.execute_step(
                StepExecutionRequest(
                    lab_run_id=enabled_run.lab_run_id,
                    node_id="analyze_visual_layers_v2",
                    execution_mode="real",
                    allow_model_call=False,
                    inputs=enabled_state,
                )
            )
        )
    assert provider_calls == 0

    with pytest.raises(NodeLabError, match="durable Service") as durable_blocked:
        asyncio.run(enabled.execute_step(
            StepExecutionRequest(
                lab_run_id=enabled_run.lab_run_id,
                node_id="analyze_visual_layers_v2",
                execution_mode="real",
                allow_model_call=True,
                inputs=enabled_state,
            )
        ))
    assert durable_blocked.value.code == "real_model_requires_durable_service"
    assert provider_calls == 0


def test_v2_project_commit_fails_before_artifact_side_effect(tmp_path: Path) -> None:
    application = _application(tmp_path)
    run, state = _run_and_state(application)
    artifact_count = len(application.list_artifacts(run.lab_run_id))

    with pytest.raises(NodeLabError) as blocked:
        asyncio.run(
            application.execute_step(
                StepExecutionRequest(
                    lab_run_id=run.lab_run_id,
                    node_id="analyze_visual_layers_v2",
                    execution_mode="fixture",
                    effect_mode="project_commit",
                    inputs=state,
                )
            )
        )
    assert blocked.value.code == "effect_not_allowed"
    assert len(application.list_artifacts(run.lab_run_id)) == artifact_count
    assert application.list_step_ids(run.lab_run_id) == ()


def test_v2_node_lab_runs_analyze_then_production_intent_builder(
    tmp_path: Path,
) -> None:
    application, run, state, _renderers = _full_chain_application_state(tmp_path)
    analyze = asyncio.run(
        application.execute_step(
            StepExecutionRequest(
                lab_run_id=run.lab_run_id,
                node_id="analyze_visual_layers_v2",
                execution_mode="fixture",
                inputs=state,
            )
        )
    )
    assert analyze.execution_status == "completed"
    assert analyze.output["stop_reason"] is None

    built = asyncio.run(
        application.execute_step(
            StepExecutionRequest(
                lab_run_id=run.lab_run_id,
                node_id="build_intent_variants_v2",
                execution_mode="deterministic",
                base_step_id=analyze.step_id,
            )
        )
    )
    assert built.execution_status == "completed"
    assert built.output["stop_reason"] is None
    assert built.output["phase"] == "intent_built"
    assert len(built.output["hypothesis_branches"]) == 1
    assert (
        built.output["hypothesis_branches"][0]["intent_ref"]["kind"]
        == INTENT_ARTIFACT_KIND
    )


def test_v2_node_lab_runs_compile_render_evaluate_materialize_chain(
    tmp_path: Path,
) -> None:
    application, run, state, renderers = _full_chain_application_state(tmp_path)
    setup_steps = (
        ("analyze_visual_layers_v2", "fixture"),
        ("build_intent_variants_v2", "deterministic"),
        ("dequeue_hypothesis_v2", "deterministic"),
        ("plan_strategy_v2", "deterministic"),
        ("propose_seed_plans_v2", "deterministic"),
        ("expand_validate_seeds_v2", "deterministic"),
        ("dequeue_seed_v2", "deterministic"),
        ("prepare_candidate_attempt_v2", "deterministic"),
        ("compile_genome_v2", "deterministic"),
    )
    previous_step_id = None
    response = None
    for index, (node_id, mode) in enumerate(setup_steps):
        response = asyncio.run(
            application.execute_step(
                StepExecutionRequest(
                    lab_run_id=run.lab_run_id,
                    node_id=node_id,
                    execution_mode=mode,
                    base_step_id=previous_step_id,
                    inputs=state if index == 0 else {},
                )
            )
        )
        assert response.execution_status == "completed", (
            node_id,
            response.diagnostics,
        )
        assert response.output["stop_reason"] is None, (node_id, response.output)
        previous_step_id = response.step_id

    assert response is not None
    render_step_ids: list[str] = []
    for render_index in range(32):
        response = asyncio.run(
            application.execute_step(
                StepExecutionRequest(
                    lab_run_id=run.lab_run_id,
                    node_id="render_candidate_v2",
                    execution_mode="deterministic",
                    base_step_id=previous_step_id,
                )
            )
        )
        assert response.execution_status == "completed", (
            render_index,
            response.diagnostics,
        )
        assert response.output["stop_reason"] is None, response.output
        render_step_ids.append(response.step_id)
        previous_step_id = response.step_id
        if response.output["active_render_repeatability_ref"] is not None:
            break
    else:
        pytest.fail("V2.4 render suite 未在 32 次有界 physical calls 内完成。")

    plan_ref = response.output["active_render_plan_ref"]
    _plan_descriptor, plan_payload = application.read_artifact(
        run.lab_run_id, plan_ref["artifact_id"]
    )
    plan = json.loads(plan_payload)
    # 每次 physical call 单独 checkpoint；最后一次无浏览器 step 只封闭
    # repeatability evidence，不能把它误计为额外 Renderer 调用。
    assert len(render_step_ids) == len(plan["items"]) + 1
    assert [item["profile"] for item in plan["items"][:5]] == [
        "beauty_full_v1"
    ] * 5
    assert all(
        item["profile"] != "beauty_full_v1" for item in plan["items"][5:]
    )

    for node_id in (
        "evaluate_structure_and_basic_score_v2",
        "materialize_immutable_candidate_v2",
    ):
        response = asyncio.run(
            application.execute_step(
                StepExecutionRequest(
                    lab_run_id=run.lab_run_id,
                    node_id=node_id,
                    execution_mode="deterministic",
                    base_step_id=previous_step_id,
                )
            )
        )
        assert response.execution_status == "completed", (
            node_id,
            response.diagnostics,
        )
        assert response.output["stop_reason"] is None, (node_id, response.output)
        previous_step_id = response.step_id

    assert response.output["phase"] == "selecting"
    assert any(
        ref["kind"] == "candidate_record"
        for ref in response.output["candidate_summary_refs"]
    )
    assert len(renderers) == len(plan["items"])
    assert all(renderer.closed for renderer in renderers)
    assert [
        application.get_step(run.lab_run_id, step_id).usage[
            "browser_launch_count"
        ]
        for step_id in render_step_ids
    ] == [1] * len(plan["items"]) + [0]
