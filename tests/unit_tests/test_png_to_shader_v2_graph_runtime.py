from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.app.graphs.png_to_shader_v2_builder import build_png_to_shader_v2_graph
from agent.app.graphs.png_to_shader_v2_routing import (
    route_after_compile,
    route_after_cross_selection,
    route_after_evaluation,
    route_after_initialize,
    route_after_render,
)
from agent.app.nodes.png_to_shader_v2 import (
    PNG_TO_SHADER_V2_NODE_IDS,
    PngToShaderV2NodeRuntime,
    build_png_to_shader_v2_node_callables,
    recover_reserved_budget_v2,
)
from agent.app.nodes.png_to_shader_v2.integrations.node_lab.executor import (
    _fixture_interpretation,
)
from agent.app.states.png_to_shader_v2_state import (
    BudgetVectorV2,
    HypothesisBranchStateV2,
)
from agent.app.states.png_to_shader_v2_state_store import (
    LocalPngToShaderV2StateStore,
)
from shaderforge.evaluation import (
    EFFECT_GENOME_EXPANDER_V2_CAPABILITY,
    EFFECT_GENOME_EXPANDER_V2_CAPABILITY_SHA256,
    GeneratorAdmissionDecision,
    PromotionSinkResultV1,
    RenderProgressV2,
    RuntimeAdmissionRejected,
    TargetStructureFacts,
    assess_target_structure_capability,
    load_attempt_evidence,
    load_render_model,
    materialize_runtime_target_structure_artifacts,
)
from shaderforge.rendering import CompileResult, RenderResult
from shaderforge.store import LocalArtifactCatalog, LocalArtifactStore
from shaderforge.validation import validate_shader
from tests.fixtures.png_to_shader_v2_contracts import make_state
from tests.unit_tests.test_candidate_artifact_recovery import _png_bytes
from tests.unit_tests.test_runtime_target_structure_verifier import _build_evidence
from tests.unit_tests.test_v2_typed_candidate_semantics import (
    RUN_ID as TYPED_RUN_ID,
)
from tests.unit_tests.test_v2_typed_candidate_semantics import (
    _typed_candidate,
)

ROOT = Path(__file__).resolve().parents[2]


class _RecoverableRecordingSink:
    def __init__(self, sunk: list[object]) -> None:
        self.sunk = sunk
        self.results: dict[str, PromotionSinkResultV1] = {}

    def execute(self, operation, _state, trusted_input):
        self.sunk.append(trusted_input)
        result = PromotionSinkResultV1(
            operation_id=operation.operation_id,
            status="completed",
            external_receipt_id=f"receipt-{operation.operation_id[:16]}",
            external_receipt_sha256="c" * 64,
            reason_code="recorded_once",
        )
        self.results[operation.operation_id] = result
        return result

    def recover(self, operation_id):
        return self.results.get(
            operation_id,
            PromotionSinkResultV1(
                operation_id=operation_id,
                status="not_executed",
                reason_code="operation_absent",
            ),
        )


class _WriteThenRaiseSink(_RecoverableRecordingSink):
    def execute(self, operation, state, trusted_input):
        super().execute(operation, state, trusted_input)
        raise ValueError("sink wrote then transport failed")


class _UnknownRecoverySink(_RecoverableRecordingSink):
    def recover(self, operation_id):
        return PromotionSinkResultV1(
            operation_id=operation_id,
            status="unknown",
            reason_code="sink_cannot_prove_outcome",
        )


class _SuccessfulRenderer:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def render(self, fragment_source: str, width: int, height: int):
        self.calls.append(fragment_source)
        validation = validate_shader(fragment_source)
        return RenderResult(
            success=True,
            image_bytes=_png_bytes(),
            width=width,
            height=height,
            compile=CompileResult(
                success=True,
                vertex_log="",
                fragment_log="",
                link_log="",
                draw_error=None,
                static_validation=validation,
            ),
            console_errors=(),
            metadata=SimpleNamespace(
                renderer_version="fixture-renderer-v2.4",
                browser_version="fixture-browser-v2.4",
                gl_version="WebGL 1 fixture",
                glsl_version="WebGL GLSL ES 1.00 fixture",
                gl_vendor="fixture-vendor",
                gl_renderer="fixture-device",
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


def _runtime(tmp_path: Path, *, provider=_fixture_interpretation):
    state = make_state().model_copy(
        update={
            "project_id": "project-v2-graph",
            "run_id": "run-v2-graph",
            "checkpoint_namespace": "png-to-shader-v2.4:run-v2-graph",
        }
    )
    store = LocalPngToShaderV2StateStore(tmp_path / "state")
    store.initialize(state)
    run = LocalArtifactStore(tmp_path / "artifacts").start_run(
        state.project_id, state.run_id
    )
    catalog = LocalArtifactCatalog(run, run_id=state.run_id)
    runtime = PngToShaderV2NodeRuntime(
        catalog_factory=lambda _state: catalog,
        interpretation_provider=provider,
        state_store=store,
    )
    return state, store, runtime


def test_v2_builder_registry_mermaid_and_langgraph_registration_are_closed(
    tmp_path: Path,
) -> None:
    runtime = PngToShaderV2NodeRuntime(
        catalog_factory=lambda _state: (_ for _ in ()).throw(AssertionError())
    )
    callables = build_png_to_shader_v2_node_callables(runtime)
    graph = build_png_to_shader_v2_graph(runtime).get_graph()
    mermaid = (ROOT / "src/agent/app/graphs/ARCHITECTURE.md").read_text()
    langgraph = json.loads((ROOT / "langgraph.json").read_text())

    assert tuple(callables) == PNG_TO_SHADER_V2_NODE_IDS
    assert set(PNG_TO_SHADER_V2_NODE_IDS).issubset(graph.nodes)
    assert all(node_id in mermaid for node_id in PNG_TO_SHADER_V2_NODE_IDS)
    assert tuple(langgraph["graphs"]) == ("png_to_shader_v1",)


def test_strict_graph_invoke_preserves_artifact_ref_instances(tmp_path: Path) -> None:
    state = make_state().model_copy(update={"phase": "finalized"})
    catalog = LocalArtifactCatalog(
        LocalArtifactStore(tmp_path / "artifacts").start_run(
            state.project_id, state.run_id
        ),
        run_id=state.run_id,
    )
    graph = build_png_to_shader_v2_graph(
        PngToShaderV2NodeRuntime(catalog_factory=lambda _state: catalog)
    )

    output = graph.invoke(state)

    assert output["phase"] == "finalized"
    assert output["measurements_ref"] == state.measurements_ref


def test_model_and_artifact_budget_are_reserved_before_side_effect_and_committed(
    tmp_path: Path,
) -> None:
    seen_reserved = False

    def provider(state, catalog):
        nonlocal seen_reserved
        confirmed = store.load_last_confirmed(state.run_id)
        seen_reserved = confirmed.budget_state.reserved.model_calls == 1
        return _fixture_interpretation(state, catalog)

    state, store, runtime = _runtime(tmp_path, provider=provider)
    output = build_png_to_shader_v2_node_callables(runtime)["analyze_visual_layers_v2"](
        state
    )
    confirmed = store.load_last_confirmed(state.run_id)

    assert seen_reserved
    assert output["visual_interpretation_ref"] is not None
    assert confirmed.budget_state.used.model_calls == 1
    assert confirmed.budget_state.used.artifact_bytes > 0
    assert confirmed.budget_state.reserved == BudgetVectorV2(
        wall_time_ms=0,
        model_calls=0,
        model_tokens=0,
        render_calls=0,
        candidate_attempts=0,
        artifact_bytes=0,
        cost_usd_micros=0,
    )


def test_crash_keeps_reservation_and_explicit_recovery_charges_it(
    tmp_path: Path,
) -> None:
    def crash(_state, _catalog):
        raise RuntimeError("simulated crash")

    state, store, runtime = _runtime(tmp_path, provider=crash)
    node = build_png_to_shader_v2_node_callables(runtime)["analyze_visual_layers_v2"]
    with pytest.raises(RuntimeError, match="simulated crash"):
        node(state)
    reserved = store.load_last_confirmed(state.run_id)
    assert reserved.budget_state.reserved.model_calls == 1

    recovered = recover_reserved_budget_v2(runtime, reserved)
    assert recovered.budget_state.reserved.model_calls == 0
    assert recovered.budget_state.used.model_calls == 1


def test_same_revision_budget_limit_tamper_is_rejected_before_provider(
    tmp_path: Path,
) -> None:
    called = False

    def provider(_state, _catalog):
        nonlocal called
        called = True
        raise AssertionError

    state, _store, runtime = _runtime(tmp_path, provider=provider)
    enlarged = state.budget_state.model_copy(
        update={
            "limits": state.budget_state.limits.model_copy(
                update={"model_calls": state.budget_state.limits.model_calls + 1}
            )
        }
    )
    tampered = state.model_copy(update={"budget_state": enlarged})

    with pytest.raises(RuntimeError, match="篡改|最后确认"):
        build_png_to_shader_v2_node_callables(runtime)["analyze_visual_layers_v2"](
            tampered
        )
    assert not called


def test_failure_routes_keep_compiler_defect_fatal_and_ordinary_seed_bounded() -> None:
    state = make_state().model_dump(mode="python")
    branch = {
        "seed_refs": (
            state["measurements_ref"],
            state["request_constraint_set_ref"],
        ),
        "seed_cursor": 1,
    }
    state["hypothesis_branches"] = (branch,)
    state["active_compilation_ref"] = None

    assert route_after_compile(state) == "next_seed"
    state["stop_reason"] = "compiler_defect:emitted_glsl_invalid"
    assert route_after_compile(state) == "finalize"
    state["stop_reason"] = None
    state["active_render_ref"] = None
    assert route_after_render(state) == "next_seed"
    state["active_evaluation_ref"] = None
    assert route_after_evaluation(state) == "next_seed"
    state["objective_best_ref"] = None
    assert route_after_cross_selection(state) == "finalize"


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"phase": "initialized"}, "prepare"),
        ({"phase": "measured"}, "analyze"),
        ({"phase": "interpreted"}, "build_intents"),
        ({"phase": "intent_built"}, "dequeue_hypothesis"),
        ({"phase": "finalized"}, "end"),
    ],
)
def test_initialize_routes_from_last_confirmed_phase(updates, expected) -> None:
    state = make_state().model_dump(mode="python")
    state.update(updates)
    assert route_after_initialize(state) == expected


def test_initialize_recovers_confirmed_phase_without_downgrade(tmp_path: Path) -> None:
    state, store, runtime = _runtime(tmp_path)
    measured = store.compare_and_swap_run(
        state.run_id,
        expected_run_revision=state.run_revision,
        changes={"phase": "measured"},
    )
    output = build_png_to_shader_v2_node_callables(runtime)["initialize_run_v2"](
        measured
    )

    assert output["phase"] == "measured"
    assert route_after_initialize(output) == "analyze"


def test_materialized_candidate_resume_routes_to_selection_not_rematerialization() -> (
    None
):
    state = make_state().model_dump(mode="python")
    state.update(
        {
            "phase": "selecting",
            "active_evaluation_ref": None,
            "hypothesis_branches": ({"seed_refs": (), "seed_cursor": 0},),
            "candidate_summary_refs": ({"kind": "candidate_record"},),
        }
    )
    assert route_after_initialize(state) == "select_hypothesis"


@pytest.mark.parametrize(
    ("phase", "refs", "expected"),
    [
        ("measured", {"active_evaluation_ref": object()}, "analyze"),
        ("interpreted", {"active_render_ref": object()}, "build_intents"),
        ("seeding", {"active_genome_ref": object()}, "prepare_candidate"),
        ("compiling", {"active_genome_ref": object()}, "compile"),
        ("compiling", {"active_compilation_ref": object()}, "render"),
        (
            "rendering",
                {
                    "active_compilation_ref": object(),
                    "active_diagnostic_compilation_ref": object(),
                },
                "render",
            ),
            (
                "rendering",
                {"active_render_repeatability_ref": object()},
                "evaluate",
            ),
            (
                "evaluating",
                {
                    "active_evaluation_refs": (object(),) * 5,
                    "active_rendered_structure_verification_ref": object(),
                },
                "materialize",
            ),
        (
            "selecting",
            {
                "active_evaluation_ref": object(),
                "candidate_summary_refs": ({"kind": "candidate_record"},),
            },
            "select_hypothesis",
        ),
        ("selecting", {"objective_best_ref": object()}, "promote"),
    ],
)
def test_initialize_resume_is_phase_first_at_every_side_effect_boundary(
    phase: str, refs: dict[str, object], expected: str
) -> None:
    state = make_state().model_dump(mode="python")
    state.update(
        {
            "phase": phase,
            "hypothesis_branches": (
                {"seed_refs": (state["measurements_ref"],), "seed_cursor": 0},
            ),
            **refs,
        }
    )
    assert route_after_initialize(state) == expected


def test_default_admission_skips_and_explicit_enable_requires_dependencies(
    tmp_path: Path,
) -> None:
    original = make_state()
    state = original.model_copy(
        update={"objective_best_ref": original.measurements_ref}
    )
    catalog = LocalArtifactCatalog(
        LocalArtifactStore(tmp_path / "artifacts").start_run(
            state.project_id, state.run_id
        ),
        run_id=state.run_id,
    )
    skipped = build_png_to_shader_v2_node_callables(
        PngToShaderV2NodeRuntime(catalog_factory=lambda _state: catalog)
    )["promote_or_skip_v2"](state)
    blocked = build_png_to_shader_v2_node_callables(
        PngToShaderV2NodeRuntime(
            catalog_factory=lambda _state: catalog,
            production_admission_enabled=True,
        )
    )["promote_or_skip_v2"](state)

    assert skipped["phase"] == "finalized"
    assert skipped["stop_reason"] == "completed_with_objective_best"
    assert blocked["stop_reason"] == "production_admission_dependencies_missing"


@pytest.mark.parametrize("status", ["unknown", "unsupported"])
def test_promotion_calls_sealed_decision_and_never_sinks_non_admitted(
    tmp_path: Path, monkeypatch, status: str
) -> None:
    original = make_state()
    state = original.model_copy(
        update={"objective_best_ref": original.measurements_ref}
    )
    catalog = LocalArtifactCatalog(
        LocalArtifactStore(tmp_path / "artifacts").start_run(
            state.project_id, state.run_id
        ),
        run_id=state.run_id,
    )
    trusted = object()
    seen: list[object] = []
    sunk: list[object] = []
    candidate = SimpleNamespace(
        candidate=SimpleNamespace(
            candidate_id="candidate-v2-test",
            glsl_ref=SimpleNamespace(sha256="a" * 64, artifact_id="glsl-v2"),
            render_refs=(SimpleNamespace(sha256="b" * 64, artifact_id="render-v2"),),
            provenance_ref=SimpleNamespace(artifact_id="provenance-v2"),
        ),
        provenance=SimpleNamespace(
            origin="deterministic",
            generator_version="effect_genome_expander_v2",
        ),
    )
    monkeypatch.setattr(
        "agent.app.nodes.png_to_shader_v2.runtime.load_trusted_runtime_selector_input",
        lambda *_args, **_kwargs: trusted,
    )
    monkeypatch.setattr(
        "agent.app.nodes.png_to_shader_v2.runtime.load_typed_candidate_artifacts",
        lambda *_args, **_kwargs: candidate,
    )

    def decide(**kwargs):
        seen.append(kwargs["trusted_input"])
        return GeneratorAdmissionDecision(
            status=status,
            reason_codes=("strict_test",),
            policy_version="measurement_seed_admission_v1",
        )

    monkeypatch.setattr(
        "agent.app.nodes.png_to_shader_v2.runtime.decide_trusted_runtime_admission",
        decide,
    )
    runtime = PngToShaderV2NodeRuntime(
        catalog_factory=lambda _state: catalog,
        production_admission_enabled=True,
        structure_envelope_provider=lambda *_args: original.measurements_ref,
        promotion_sink=_RecoverableRecordingSink(sunk),
    )
    output = build_png_to_shader_v2_node_callables(runtime)["promote_or_skip_v2"](state)

    assert seen == [trusted]
    assert sunk == []
    assert output["phase"] == "finalized"
    assert output["stop_reason"].startswith(f"runtime_admission_not_admitted:{status}:")


def test_promotion_sinks_only_after_admitted_decision(
    tmp_path: Path, monkeypatch
) -> None:
    original = make_state()
    state = original.model_copy(
        update={"objective_best_ref": original.measurements_ref}
    )
    catalog = LocalArtifactCatalog(
        LocalArtifactStore(tmp_path / "artifacts").start_run(
            state.project_id, state.run_id
        ),
        run_id=state.run_id,
    )
    trusted = object()
    sunk: list[object] = []
    candidate = SimpleNamespace(
        candidate=SimpleNamespace(
            candidate_id="candidate-v2-test",
            glsl_ref=SimpleNamespace(sha256="a" * 64, artifact_id="glsl-v2"),
            render_refs=(SimpleNamespace(sha256="b" * 64, artifact_id="render-v2"),),
            provenance_ref=SimpleNamespace(artifact_id="provenance-v2"),
        ),
        provenance=SimpleNamespace(
            origin="deterministic",
            generator_version="effect_genome_expander_v2",
        ),
    )
    monkeypatch.setattr(
        "agent.app.nodes.png_to_shader_v2.runtime.load_trusted_runtime_selector_input",
        lambda *_args, **_kwargs: trusted,
    )
    monkeypatch.setattr(
        "agent.app.nodes.png_to_shader_v2.runtime.load_typed_candidate_artifacts",
        lambda *_args, **_kwargs: candidate,
    )
    monkeypatch.setattr(
        "agent.app.nodes.png_to_shader_v2.runtime.decide_trusted_runtime_admission",
        lambda **_kwargs: GeneratorAdmissionDecision(
            status="admitted",
            reason_codes=("labels_within_generator_capability",),
            policy_version="measurement_seed_admission_v1",
        ),
    )
    runtime = PngToShaderV2NodeRuntime(
        catalog_factory=lambda _state: catalog,
        production_admission_enabled=True,
        structure_envelope_provider=lambda *_args: original.measurements_ref,
        promotion_sink=_RecoverableRecordingSink(sunk),
    )
    output = build_png_to_shader_v2_node_callables(runtime)["promote_or_skip_v2"](state)

    assert sunk == [trusted]
    assert output["phase"] == "finalized"
    assert output["stop_reason"] == "completed_with_objective_best"


def _real_admitted_promotion_inputs(tmp_path: Path):
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    structure = materialize_runtime_target_structure_artifacts(
        catalog=catalog,
        run_id=TYPED_RUN_ID,
        evidence=evidence,
    )
    candidate = _typed_candidate(catalog, intent_source_ref=evidence.intent_ref)
    state = make_state().model_copy(
        update={
            "project_id": "project-v2",
            "run_id": TYPED_RUN_ID,
            "checkpoint_namespace": f"png-to-shader-v2.4:{TYPED_RUN_ID}",
            "objective_best_ref": candidate.candidate_ref,
        }
    )
    return catalog, structure, state


@pytest.mark.parametrize(
    "crash_point",
    [
        "promotion.after_outbox_before_sink",
        "promotion.after_sink_before_receipt",
    ],
)
def test_promotion_outbox_recovers_crash_without_duplicate_execute(
    tmp_path: Path, crash_point: str
) -> None:
    catalog, structure, state = _real_admitted_promotion_inputs(tmp_path)
    store = LocalPngToShaderV2StateStore(tmp_path / "promotion-state")
    store.initialize(state)
    sunk: list[object] = []
    sink = _RecoverableRecordingSink(sunk)
    crashed = False

    def inject(point: str) -> None:
        nonlocal crashed
        if point == crash_point and not crashed:
            crashed = True
            raise RuntimeError(f"crash:{point}")

    runtime = PngToShaderV2NodeRuntime(
        catalog_factory=lambda _state: catalog,
        state_store=store,
        production_admission_enabled=True,
        structure_envelope_provider=lambda *_args: structure.envelope_ref,
        promotion_sink=sink,
        fault_injector=inject,
    )
    node = build_png_to_shader_v2_node_callables(runtime)["promote_or_skip_v2"]
    with pytest.raises(RuntimeError, match="crash:promotion"):
        node(state)
    checkpoint = store.load_last_confirmed(state.run_id)
    assert checkpoint.promotion_operation_ref is not None
    assert checkpoint.promotion_receipt_ref is None

    node(checkpoint)
    completed = store.load_last_confirmed(state.run_id)
    assert completed.promotion_receipt_ref is not None
    assert completed.stop_reason == "completed_with_objective_best"
    assert len(sunk) == 1

    node(completed)
    assert len(sunk) == 1


def test_promotion_sink_write_then_raise_is_recovered_not_replayed(
    tmp_path: Path,
) -> None:
    catalog, structure, state = _real_admitted_promotion_inputs(tmp_path)
    store = LocalPngToShaderV2StateStore(tmp_path / "promotion-state")
    store.initialize(state)
    sunk: list[object] = []
    sink = _WriteThenRaiseSink(sunk)
    runtime = PngToShaderV2NodeRuntime(
        catalog_factory=lambda _state: catalog,
        state_store=store,
        production_admission_enabled=True,
        structure_envelope_provider=lambda *_args: structure.envelope_ref,
        promotion_sink=sink,
    )
    node = build_png_to_shader_v2_node_callables(runtime)["promote_or_skip_v2"]
    with pytest.raises(RuntimeError, match="必须通过 operation_id recover"):
        node(state)

    node(store.load_last_confirmed(state.run_id))
    completed = store.load_last_confirmed(state.run_id)
    assert completed.promotion_receipt_ref is not None
    assert len(sunk) == 1


def test_promotion_unknown_recovery_fails_closed_without_replay(
    tmp_path: Path,
) -> None:
    catalog, structure, state = _real_admitted_promotion_inputs(tmp_path)
    store = LocalPngToShaderV2StateStore(tmp_path / "promotion-state")
    store.initialize(state)
    sunk: list[object] = []
    sink = _UnknownRecoverySink(sunk)
    runtime = PngToShaderV2NodeRuntime(
        catalog_factory=lambda _state: catalog,
        state_store=store,
        production_admission_enabled=True,
        structure_envelope_provider=lambda *_args: structure.envelope_ref,
        promotion_sink=sink,
        fault_injector=lambda point: (
            (_ for _ in ()).throw(RuntimeError("crash before sink"))
            if point == "promotion.after_outbox_before_sink"
            else None
        ),
    )
    node = build_png_to_shader_v2_node_callables(runtime)["promote_or_skip_v2"]
    with pytest.raises(RuntimeError, match="crash before sink"):
        node(state)

    safe_runtime = replace(runtime, fault_injector=lambda _point: None)
    build_png_to_shader_v2_node_callables(safe_runtime)["promote_or_skip_v2"](
        store.load_last_confirmed(state.run_id)
    )
    completed = store.load_last_confirmed(state.run_id)
    assert completed.promotion_receipt_ref is None
    assert completed.stop_reason == (
        "promotion_outcome_unknown_fail_closed:sink_cannot_prove_outcome"
    )
    assert sunk == []


def test_promotion_outbox_ref_tamper_never_reaches_sink(tmp_path: Path) -> None:
    catalog, structure, state = _real_admitted_promotion_inputs(tmp_path)
    store = LocalPngToShaderV2StateStore(tmp_path / "promotion-state")
    store.initialize(state)
    sink = _RecoverableRecordingSink([])
    runtime = PngToShaderV2NodeRuntime(
        catalog_factory=lambda _state: catalog,
        state_store=store,
        production_admission_enabled=True,
        structure_envelope_provider=lambda *_args: structure.envelope_ref,
        promotion_sink=sink,
        fault_injector=lambda point: (
            (_ for _ in ()).throw(RuntimeError("stop after outbox"))
            if point == "promotion.after_outbox_before_sink"
            else None
        ),
    )
    with pytest.raises(RuntimeError, match="stop after outbox"):
        build_png_to_shader_v2_node_callables(runtime)["promote_or_skip_v2"](state)
    checkpoint = store.load_last_confirmed(state.run_id)
    assert checkpoint.promotion_operation_ref is not None
    tampered = checkpoint.model_copy(
        update={
            "promotion_operation_ref": replace(
                checkpoint.promotion_operation_ref, sha256="f" * 64
            )
        }
    )
    isolated_runtime = replace(
        runtime,
        state_store=None,
        fault_injector=lambda _point: None,
    )

    output = build_png_to_shader_v2_node_callables(isolated_runtime)[
        "promote_or_skip_v2"
    ](tampered)
    assert output["stop_reason"] == "runtime_admission_failed"
    assert sink.sunk == []


def test_effect_genome_expander_capability_is_frozen_at_proven_boundary() -> None:
    assessment = assess_target_structure_capability(
        TargetStructureFacts(
            topology="solid",
            instance_count=1,
            hole_count=0,
            required_layers=(
                "shadow",
                "base_fill",
                "color_lobe",
                "haze",
                "rim",
                "outline",
                "highlight",
                "detail",
                "glow",
            ),
        ),
        origin="deterministic",
        generator_version="effect_genome_expander_v2",
    )

    assert assessment.status == "supported"
    assert assessment.reason_codes == ("labels_within_generator_capability",)
    assert EFFECT_GENOME_EXPANDER_V2_CAPABILITY.declaration_sha256 == (
        EFFECT_GENOME_EXPANDER_V2_CAPABILITY_SHA256
    )
    assert EFFECT_GENOME_EXPANDER_V2_CAPABILITY_SHA256 == (
        "8177827bbedd0d346634683a9894fd896780444f5544c7d21760500c6f4cc696"
    )


@pytest.mark.parametrize(
    ("target", "dimension", "reason"),
    (
        (
            TargetStructureFacts(
                topology="open",
                instance_count=1,
                hole_count=0,
                required_layers=("base_fill",),
            ),
            "topology_status",
            "unsupported_topology",
        ),
        (
            TargetStructureFacts(
                topology="solid",
                instance_count=2,
                hole_count=0,
                required_layers=("base_fill",),
            ),
            "instance_count_status",
            "instance_count_exceeds_generator_capability",
        ),
        (
            TargetStructureFacts(
                topology="ring",
                instance_count=1,
                hole_count=1,
                required_layers=("base_fill",),
            ),
            "hole_count_status",
            "hole_count_exceeds_generator_capability",
        ),
        (
            TargetStructureFacts(
                topology="solid",
                instance_count=1,
                hole_count=0,
                required_layers=("background", "base_fill"),
            ),
            "required_layers_status",
            "required_layers_exceed_generator_capability",
        ),
    ),
)
def test_effect_genome_expander_capability_rejects_each_unproven_dimension(
    target: TargetStructureFacts,
    dimension: str,
    reason: str,
) -> None:
    assessment = assess_target_structure_capability(
        target,
        origin="deterministic",
        generator_version="effect_genome_expander_v2",
    )

    assert assessment.status == "unsupported"
    assert getattr(assessment, dimension) == "unsupported"
    assert reason in assessment.reason_codes


def test_real_supported_candidate_reaches_sink_only_with_explicit_admission(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    structure = materialize_runtime_target_structure_artifacts(
        catalog=catalog,
        run_id=TYPED_RUN_ID,
        evidence=evidence,
    )
    candidate = _typed_candidate(catalog, intent_source_ref=evidence.intent_ref)
    state = make_state().model_copy(
        update={
            "project_id": "project-v2",
            "run_id": TYPED_RUN_ID,
            "checkpoint_namespace": f"png-to-shader-v2.4:{TYPED_RUN_ID}",
            "objective_best_ref": candidate.candidate_ref,
        }
    )
    sunk: list[object] = []
    default_output = build_png_to_shader_v2_node_callables(
        PngToShaderV2NodeRuntime(
            catalog_factory=lambda _state: catalog,
            structure_envelope_provider=lambda *_args: structure.envelope_ref,
            promotion_sink=_RecoverableRecordingSink(sunk),
        )
    )["promote_or_skip_v2"](state)

    assert default_output["stop_reason"] == "completed_with_objective_best"
    assert sunk == []

    enabled_output = build_png_to_shader_v2_node_callables(
        PngToShaderV2NodeRuntime(
            catalog_factory=lambda _state: catalog,
            production_admission_enabled=True,
            structure_envelope_provider=lambda *_args: structure.envelope_ref,
            promotion_sink=_RecoverableRecordingSink(sunk),
        )
    )["promote_or_skip_v2"](state)

    assert enabled_output["stop_reason"] == "completed_with_objective_best"
    assert len(sunk) == 1


def test_promotion_identity_mismatch_never_reaches_sink(
    tmp_path: Path, monkeypatch
) -> None:
    original = make_state()
    state = original.model_copy(
        update={"objective_best_ref": original.measurements_ref}
    )
    catalog = LocalArtifactCatalog(
        LocalArtifactStore(tmp_path / "artifacts").start_run(
            state.project_id, state.run_id
        ),
        run_id=state.run_id,
    )
    sunk: list[object] = []

    def reject(*_args, **_kwargs):
        raise RuntimeAdmissionRejected("runtime_candidate_target_identity_mismatch")

    monkeypatch.setattr(
        "agent.app.nodes.png_to_shader_v2.runtime.load_trusted_runtime_selector_input",
        reject,
    )
    runtime = PngToShaderV2NodeRuntime(
        catalog_factory=lambda _state: catalog,
        production_admission_enabled=True,
        structure_envelope_provider=lambda *_args: original.measurements_ref,
        promotion_sink=_RecoverableRecordingSink(sunk),
    )

    output = build_png_to_shader_v2_node_callables(runtime)["promote_or_skip_v2"](state)

    assert sunk == []
    assert output["phase"] == "finalized"
    assert output["stop_reason"] == (
        "runtime_admission_rejected:runtime_candidate_target_identity_mismatch"
    )


def test_model_budget_exhaustion_happens_before_provider(tmp_path: Path) -> None:
    called = False

    def provider(_state, _catalog):
        nonlocal called
        called = True
        raise AssertionError

    state, _store, runtime = _runtime(tmp_path, provider=provider)
    exhausted_budget = state.budget_state.model_copy(
        update={
            "used": state.budget_state.used.model_copy(
                update={"model_calls": state.budget_state.limits.model_calls}
            ),
            "exhausted_dimensions": ("model_calls",),
        }
    )
    output = build_png_to_shader_v2_node_callables(
        PngToShaderV2NodeRuntime(
            catalog_factory=runtime.catalog_factory,
            interpretation_provider=provider,
        )
    )["analyze_visual_layers_v2"](
        state.model_copy(update={"budget_state": exhausted_budget})
    )
    assert output["stop_reason"] == "model_budget_exhausted"
    assert not called


@pytest.mark.parametrize("tamper", ["semantic_hash", "target_binding"])
def test_compile_recomputes_attempt_identity_and_rejects_state_ref_tamper(
    tmp_path: Path, tamper: str
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    candidate = _typed_candidate(catalog, intent_source_ref=evidence.intent_ref)
    base = make_state()
    branch = HypothesisBranchStateV2(
        target_hypothesis_id=candidate.intent.target_hypothesis_id,
        target_hypothesis_hash=candidate.intent.target_hypothesis_hash,
        intent_ref=candidate.candidate.intent_ref,
        strategy_ref=None,
        seed_refs=(candidate.candidate.genome_ref,),
        seed_cursor=1,
        hypothesis_best_id=None,
        status="running",
    )
    state = base.model_copy(
        update={
            "run_id": TYPED_RUN_ID,
            "checkpoint_namespace": f"png-to-shader-v2.4:{TYPED_RUN_ID}",
            "phase": "seeding",
            "hypothesis_branches": (branch,),
            "active_seed_ref": candidate.candidate.genome_ref,
            "active_genome_ref": candidate.candidate.genome_ref,
        }
    )
    runtime = PngToShaderV2NodeRuntime(catalog_factory=lambda _state: catalog)
    nodes = build_png_to_shader_v2_node_callables(runtime)
    prepared = state.model_validate(
        nodes["prepare_candidate_attempt_v2"](state), strict=True
    )
    if tamper == "semantic_hash":
        tampered = prepared.model_copy(update={"active_semantic_genome_hash": "f" * 64})
    else:
        wrong_branch = branch.model_copy(update={"target_hypothesis_hash": "e" * 64})
        tampered = prepared.model_copy(update={"hypothesis_branches": (wrong_branch,)})

    with pytest.raises(ValueError, match="identity"):
        nodes["compile_genome_v2"](tampered)


def test_render_crash_after_evidence_before_budget_commit_recovers_same_slot(
    tmp_path: Path,
) -> None:
    catalog, evidence = _build_evidence(
        tmp_path,
        topology="solid",
        hole_count=0,
        mask_has_hole=False,
    )
    candidate = _typed_candidate(catalog, intent_source_ref=evidence.intent_ref)
    base = make_state()
    branch = HypothesisBranchStateV2(
        target_hypothesis_id=candidate.intent.target_hypothesis_id,
        target_hypothesis_hash=candidate.intent.target_hypothesis_hash,
        intent_ref=candidate.candidate.intent_ref,
        strategy_ref=None,
        seed_refs=(candidate.candidate.genome_ref,),
        seed_cursor=1,
        hypothesis_best_id=None,
        status="running",
    )
    state = base.model_copy(
        update={
            "project_id": "project-v2",
            "run_id": TYPED_RUN_ID,
            "checkpoint_namespace": f"png-to-shader-v2.4:{TYPED_RUN_ID}",
            "phase": "seeding",
            "hypothesis_branches": (branch,),
            "active_seed_ref": candidate.candidate.genome_ref,
            "active_genome_ref": candidate.candidate.genome_ref,
        }
    )
    store = LocalPngToShaderV2StateStore(tmp_path / "render-state")
    store.initialize(state)
    calls: list[str] = []
    crashed = False

    def inject(point: str) -> None:
        nonlocal crashed
        if point == "render.after_evidence_before_budget_commit" and not crashed:
            crashed = True
            raise RuntimeError("render evidence crash")

    runtime = PngToShaderV2NodeRuntime(
        catalog_factory=lambda _state: catalog,
        state_store=store,
        renderer_factory=lambda _state: _SuccessfulRenderer(calls),
        fault_injector=inject,
    )
    nodes = build_png_to_shader_v2_node_callables(runtime)
    prepared = state.model_validate(
        nodes["prepare_candidate_attempt_v2"](state), strict=True
    )
    compiled = state.model_validate(nodes["compile_genome_v2"](prepared), strict=True)
    try:
        asyncio.run(nodes["render_candidate_v2"](compiled))
    except RuntimeError as exc:
        assert "render evidence crash" in str(exc)
    else:
        assert crashed

    orphan = store.load_last_confirmed(state.run_id)
    assert orphan.active_render_call_ordinal == 1
    assert orphan.budget_state.reserved.render_calls == 1
    assert len(calls) == 1
    first_evidence = tuple(
        load_attempt_evidence(ref, resolver=catalog, run_id=state.run_id)
        for ref in orphan.active_attempt_evidence_refs
    )
    assert tuple(item.outcome for item in first_evidence) == ("success",)

    initialized = state.model_validate(nodes["initialize_run_v2"](orphan), strict=True)
    rendered = state.model_validate(
        asyncio.run(nodes["render_candidate_v2"](initialized)), strict=True
    )
    assert len(calls) == 2
    assert rendered.active_render_progress_ref is not None
    progress = load_render_model(
        rendered.active_render_progress_ref,
        resolver=catalog,
        run_id=state.run_id,
    )
    assert isinstance(progress, RenderProgressV2)
    assert progress.completed_logical_requests == 2
    assert rendered.active_render_call_ordinal is None
    assert rendered.budget_state.reserved.render_calls == 0
    assert rendered.budget_state.used.render_calls == 2
    final_evidence = tuple(
        load_attempt_evidence(ref, resolver=catalog, run_id=state.run_id)
        for ref in rendered.active_attempt_evidence_refs
    )
    assert tuple(item.call_ordinal for item in final_evidence) == (1, 1)
    assert tuple(item.outcome for item in final_evidence) == ("success", "success")


def test_all_seed_failure_cross_selection_is_no_valid_candidate(tmp_path: Path) -> None:
    state = make_state()
    catalog = LocalArtifactCatalog(
        LocalArtifactStore(tmp_path / "artifacts").start_run(
            state.project_id, state.run_id
        ),
        run_id=state.run_id,
    )
    output = build_png_to_shader_v2_node_callables(
        PngToShaderV2NodeRuntime(catalog_factory=lambda _state: catalog)
    )["select_cross_hypothesis_best_v2"](state)
    assert output["stop_reason"] == "no_valid_candidate"
