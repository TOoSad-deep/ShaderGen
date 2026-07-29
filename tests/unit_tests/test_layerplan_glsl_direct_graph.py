"""Topology and execution-trace tests for the current attempt graph."""

from __future__ import annotations

import asyncio
import threading
from decimal import Decimal
from hashlib import sha256
from time import perf_counter
from types import SimpleNamespace
from typing import Any, cast

import pytest
from langsmith import get_tracing_context

from agent.app.contracts.layerplan_glsl_direct import AttemptLedger
from agent.app.graphs import layerplan_glsl_direct as direct_graph
from agent.app.graphs.layerplan_glsl_direct import (
    DirectGraphContext,
    build_layerplan_glsl_direct_graph,
    run_layerplan_glsl_direct_graph,
)
from agent.app.nodes.layered_direct import uniform_optimization_nodes
from agent.app.nodes.layered_direct.progress_projection import (
    public_uniform_progress_update,
)
from agent.app.nodes.layered_direct.uniform_optimization_nodes import (
    _select_target_components,
)
from agent.app.services.layerplan_glsl_direct import LayerPlanGlslDirectConfig
from shaderforge.program_spec import NormalizedRegion, canonical_json
from shaderforge.uniform_optimization import FlatTunableComponent
from tests.direct_fakes import (
    TEST_ISSUER,
    FakePrepared,
    FakeRenderer,
    reference_png,
)
from tests.unit_tests.test_layerplan_glsl_direct_runner import _LayeredFakeGateway

IMPLEMENTATION_SHA256 = "c" * 64
PRODUCT_NODES = {
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
}


def _context(
    *,
    plan_budget: int = 1,
    gateway: _LayeredFakeGateway | None = None,
    refine_budget: int = 0,
    draw_budget: int | None = None,
    uniform_tuning_draw_budget: int = 0,
) -> DirectGraphContext:
    return DirectGraphContext(
        gateway=gateway or _LayeredFakeGateway(),
        renderer=FakeRenderer(),
        config=LayerPlanGlslDirectConfig(
            implementation_identity_sha256=IMPLEMENTATION_SHA256,
            plan_llm_budget=plan_budget,
            direct_author_llm_budget=1 + refine_budget,
            compile_budget=1,
            draw_budget=draw_budget if draw_budget is not None else 1 + refine_budget,
            refine_budget=refine_budget,
            uniform_tuning_draw_budget=uniform_tuning_draw_budget,
        ),
        receipt_issuer=TEST_ISSUER,
    )


def test_graph_exposes_one_node_per_product_step() -> None:
    topology = build_layerplan_glsl_direct_graph().get_graph()
    assert set(topology.nodes) == PRODUCT_NODES | {"__start__", "__end__"}
    assert len(topology.edges) == 49


def test_uniform_target_layer_does_not_let_full_canvas_background_win() -> None:
    component = lambda layer_id: FlatTunableComponent(  # noqa: E731
        layer_id=layer_id,
        path=f"u_{layer_id}",
        component_index=0,
        minimum=Decimal("0"),
        maximum=Decimal("1"),
        step=Decimal("0.1"),
        base_value=Decimal("0.5"),
    )
    state = {
        "layer_plan": SimpleNamespace(
            layers=(
                SimpleNamespace(
                    layer_id="bg",
                    role="background",
                    confidence=1.0,
                    region=NormalizedRegion(0.0, 0.0, 1.0, 1.0),
                ),
                SimpleNamespace(
                    layer_id="subject",
                    role="subject",
                    confidence=0.9,
                    region=NormalizedRegion(0.2, 0.2, 0.4, 0.4),
                ),
            )
        ),
        "current_best": SimpleNamespace(
            residual_summary={
                "dominant_metric_component": "edge_loss",
                "worst_tiles": [
                    {
                        "uv_bbox": {
                            "x": 0.25,
                            "y": 0.25,
                            "width": 0.25,
                            "height": 0.25,
                        }
                    }
                ],
            }
        ),
    }

    selected = _select_target_components(
        state,  # type: ignore[arg-type]
        (component("bg"), component("subject")),
    )

    assert {item.layer_id for item in selected} == {"subject"}


def test_uniform_candidate_deduplicates_source_and_binding_not_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = SimpleNamespace(
        spec=SimpleNamespace(
            source_sha256="source" * 11,
            binding_sha256="binding" * 9,
            spec_sha256="old-provenance-specific-spec",
        )
    )
    derived_program = SimpleNamespace(
        source_sha256=existing.spec.source_sha256,
        binding_sha256=existing.spec.binding_sha256,
        spec_sha256="new-provenance-specific-spec",
    )
    monkeypatch.setattr(
        uniform_optimization_nodes,
        "apply_uniform_patch",
        lambda _layered, _program, _patch: SimpleNamespace(
            program_spec=derived_program,
            layered_spec=SimpleNamespace(),
        ),
    )
    state = {
        "current_best": SimpleNamespace(
            layered_spec=SimpleNamespace(), spec=SimpleNamespace()
        ),
        "uniform_candidate_patch": object(),
        "next_sequence": 7,
        "direct_ledger": AttemptLedger(),
        "candidates": [existing],
        "events": [],
    }

    update = uniform_optimization_nodes.apply_uniform_candidate(
        state,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )

    assert update["uniform_candidate_failed"] is True
    assert update["events"][-1]["error_code"] == "duplicate_uniform_candidate"
    assert update["direct_ledger"].uniform_tuning_duplicate_count == 1


@pytest.mark.anyio
async def test_happy_path_runs_each_candidate_step_as_a_node() -> None:
    output = await run_layerplan_glsl_direct_graph(
        reference_image=reference_png(),
        content_type="image/png",
        instruction="match",
        context=_context(),
    )

    assert output["result"].status == "ok"
    assert output["completed_nodes"] == (
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
        "release_resources",
        "finalize_attempt",
    )


@pytest.mark.anyio
async def test_uniform_search_reuses_program_and_attests_each_binding() -> None:
    context = _context(
        gateway=_LayeredFakeGateway(initial_gains=(0.6,)),
        draw_budget=3,
        uniform_tuning_draw_budget=2,
    )

    output = await run_layerplan_glsl_direct_graph(
        reference_image=reference_png(),
        content_type="image/png",
        instruction="match",
        context=context,
    )

    result = output["result"]
    assert result.status == "ok"
    assert result.current_best is not None
    assert result.current_best.role == "uniform_optimize"
    assert result.current_best.spec.uniform_values["u_gain"] == pytest.approx(0.59)
    assert result.current_best.spec.derivation_provenance is not None
    assert result.current_best.spec.validation_attestation is not None
    assert result.direct_ledger.compile_count == 1
    assert result.direct_ledger.draw_count == 3
    assert result.direct_ledger.cache_hits == 2
    assert result.direct_ledger.uniform_tuning_draw_count == 2
    assert result.direct_ledger.uniform_tuning_evaluated_count == 2
    assert result.direct_ledger.uniform_tuning_accepted_count == 1
    assert result.uniform_optimization_summary is not None
    assert result.uniform_optimization_summary.evaluated_count == 2
    assert result.uniform_optimization_summary.accepted_count == 1
    assert len(result.uniform_optimization_trace) == 2
    assert (
        result.uniform_optimization_summary.private_trace_sha256
        == sha256(
            canonical_json(list(result.uniform_optimization_trace)).encode("utf-8")
        ).hexdigest()
    )
    assert "path" not in canonical_json(list(result.uniform_optimization_trace))
    assert context.renderer.prepare_calls
    assert len(context.renderer.draw_calls) == 3
    assert "propose_uniform_candidate" in output["completed_nodes"]
    assert "record_uniform_outcome" in output["completed_nodes"]


@pytest.mark.anyio
async def test_uniform_progress_projection_is_incremental_and_redacted() -> None:
    progress: list[tuple[str, str, dict[str, object] | None]] = []
    context = _context(
        gateway=_LayeredFakeGateway(initial_gains=(0.6,)),
        draw_budget=3,
        uniform_tuning_draw_budget=2,
    )

    def capture(
        node_name: str,
        status: str,
        _duration_ms: float | None,
        update: dict[str, object] | None = None,
    ) -> None:
        progress.append((node_name, status, update))

    context.node_progress_callback = capture
    await run_layerplan_glsl_direct_graph(
        reference_image=reference_png(),
        content_type="image/png",
        instruction="match",
        context=context,
    )

    updates = [
        update
        for node_name, status, update in progress
        if node_name in {"decide_uniform_optimization", "record_uniform_outcome"}
        and status == "completed"
        and update is not None
    ]
    assert updates
    assert any(
        update["uniform_optimization"]["candidate_outcome"] == "accepted"
        for update in updates
        if "candidate_outcome" in update["uniform_optimization"]
    )
    assert all(
        set(update) <= {"reason_code", "refinement_stop_reason", "uniform_optimization"}
        for update in updates
    )
    assert all(
        set(update["uniform_optimization"])
        <= {
            "draw_count",
            "draw_budget",
            "evaluated_count",
            "accepted_count",
            "stop_reason",
            "candidate_outcome",
        }
        for update in updates
    )


def test_uniform_progress_projection_allows_global_compile_budget_stop_reason() -> None:
    update = public_uniform_progress_update(
        "decide_uniform_optimization",
        cast(
            Any,
            {
                "direct_ledger": SimpleNamespace(
                    uniform_tuning_draw_count=0,
                    uniform_tuning_evaluated_count=0,
                    uniform_tuning_accepted_count=0,
                ),
                "uniform_tuning_stop_reason": "global_compile_budget_exhausted",
            },
        ),
        {},
        cast(Any, SimpleNamespace(config=SimpleNamespace(uniform_tuning_draw_budget=2))),
    )

    assert update == {
        "reason_code": "global_compile_budget_exhausted",
        "refinement_stop_reason": None,
        "uniform_optimization": {
            "draw_count": 0,
            "draw_budget": 2,
            "evaluated_count": 0,
            "accepted_count": 0,
            "stop_reason": "global_compile_budget_exhausted",
        },
    }


@pytest.mark.anyio
async def test_legacy_three_argument_progress_callback_keeps_uniform_lifecycle() -> (
    None
):
    progress: list[tuple[str, str]] = []
    context = _context(
        gateway=_LayeredFakeGateway(initial_gains=(0.6,)),
        draw_budget=3,
        uniform_tuning_draw_budget=2,
    )
    context.node_progress_callback = lambda node_name, status, _duration_ms: (
        progress.append((node_name, status))
    )

    await run_layerplan_glsl_direct_graph(
        reference_image=reference_png(),
        content_type="image/png",
        instruction="match",
        context=context,
    )

    assert ("decide_uniform_optimization", "completed") in progress
    assert ("record_uniform_outcome", "completed") in progress


@pytest.mark.anyio
async def test_graph_publishes_safe_lifecycle_events_for_each_executed_node() -> None:
    progress: list[tuple[str, str, float | None]] = []
    context = _context()
    context.node_progress_callback = lambda node_name, status, duration_ms: (
        progress.append((node_name, status, duration_ms))
    )

    output = await run_layerplan_glsl_direct_graph(
        reference_image=reference_png(),
        content_type="image/png",
        instruction="match",
        context=context,
    )

    assert [(node_name, status) for node_name, status, _duration_ms in progress] == [
        (node_name, status)
        for node_name in output["completed_nodes"]
        for status in ("running", "completed")
    ]
    assert all(
        duration_ms is None if status == "running" else duration_ms is not None
        for _node_name, status, duration_ms in progress
    )


@pytest.mark.anyio
async def test_node_progress_is_best_effort_and_reports_unhandled_node_failure() -> (
    None
):
    reported: list[tuple[str, str, float | None]] = []
    context = _context()
    context.node_progress_callback = lambda node_name, status, duration_ms: (
        reported.append((node_name, status, duration_ms))
    )

    async def fail(
        _state: object,
        _runtime: object,
    ) -> dict[str, object]:
        raise RuntimeError("private detail")

    observed = direct_graph._with_safe_progress("prepare_reference", fail)
    with pytest.raises(RuntimeError, match="private detail"):
        await observed({}, SimpleNamespace(context=context))

    assert reported[0] == ("prepare_reference", "running", None)
    assert reported[1][:2] == ("prepare_reference", "failed")
    assert reported[1][2] is not None


@pytest.mark.anyio
async def test_node_progress_callback_failure_does_not_affect_the_attempt() -> None:
    context = _context()

    def fail_callback(
        _node_name: str,
        _status: str,
        _duration_ms: float | None,
    ) -> None:
        raise RuntimeError("progress consumer unavailable")

    context.node_progress_callback = fail_callback
    output = await run_layerplan_glsl_direct_graph(
        reference_image=reference_png(),
        content_type="image/png",
        instruction="match",
        context=context,
    )

    assert output["result"].status == "ok"


@pytest.mark.anyio
async def test_progress_wrapper_keeps_sync_nodes_off_the_event_loop() -> None:
    release = threading.Event()

    def blocking_sync_node(
        _state: object,
        _runtime: object,
    ) -> dict[str, object]:
        release.wait(timeout=1)
        return {}

    observed = direct_graph._with_safe_progress(
        "prepare_reference",
        blocking_sync_node,
    )
    asyncio.get_running_loop().call_later(0.02, release.set)
    started_at = perf_counter()

    await observed({}, SimpleNamespace(context=_context()))

    assert perf_counter() - started_at < 0.5


@pytest.mark.anyio
async def test_progress_wrapper_reports_cancellation_as_failed() -> None:
    entered = asyncio.Event()
    block = asyncio.Event()
    reported: list[tuple[str, str]] = []
    context = _context()
    context.node_progress_callback = lambda node_name, status, _duration_ms: (
        reported.append((node_name, status))
    )

    async def cancellable_node(
        _state: object,
        _runtime: object,
    ) -> dict[str, object]:
        entered.set()
        await block.wait()
        return {}

    observed = direct_graph._with_safe_progress(
        "prepare_reference",
        cancellable_node,
    )
    task = asyncio.create_task(observed({}, SimpleNamespace(context=context)))
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert reported == [
        ("prepare_reference", "running"),
        ("prepare_reference", "failed"),
    ]


@pytest.mark.anyio
async def test_plan_failure_short_circuits_to_observable_finalization() -> None:
    context = _context(plan_budget=0)
    output = await run_layerplan_glsl_direct_graph(
        reference_image=reference_png(),
        content_type="image/png",
        instruction="match",
        context=context,
    )

    assert output["result"].failure_code == "layer_plan_generation_failed"
    assert output["completed_nodes"] == (
        "prepare_reference",
        "author_layer_plan",
        "release_resources",
        "finalize_attempt",
    )
    assert context.renderer.prepare_calls == []


@pytest.mark.anyio
async def test_refinement_routes_back_through_candidate_nodes() -> None:
    context = _context(
        gateway=_LayeredFakeGateway(
            initial_gains=(0.9,),
            refine_gains=(0.5,),
        ),
        refine_budget=1,
    )
    output = await run_layerplan_glsl_direct_graph(
        reference_image=reference_png(),
        content_type="image/png",
        instruction="match",
        context=context,
    )

    result = output["result"]
    assert result.current_best is result.candidates[1]
    assert result.direct_ledger.compile_count == 1
    assert result.direct_ledger.draw_count == 2
    assert result.direct_ledger.cache_hits == 1
    assert output["completed_nodes"] == (
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
        "decide_refinement",
        "author_refinement",
        "apply_refinement",
        "compile_candidate",
        "validate_candidate",
        "prepare_program",
        "render_program",
        "verify_receipt",
        "attest_candidate",
        "evaluate_candidate",
        "select_candidate",
        "decide_uniform_optimization",
        "release_resources",
        "finalize_attempt",
    )


@pytest.mark.anyio
async def test_hard_draw_budget_stops_wasted_refinement_calls() -> None:
    gateway = _LayeredFakeGateway(
        initial_gains=(0.9,),
        refine_gains=(0.5, 0.4),
    )
    context = _context(
        gateway=gateway,
        refine_budget=2,
        draw_budget=1,
    )

    output = await run_layerplan_glsl_direct_graph(
        reference_image=reference_png(),
        content_type="image/png",
        instruction="match",
        context=context,
    )

    assert [call["role"] for call in gateway.calls] == ["plan", "initial"]
    assert output["result"].direct_ledger.draw_count == 1
    assert output["result"].refinement_stop_reason == "hard_resource_block"
    assert output["completed_nodes"][-3:] == (
        "decide_uniform_optimization",
        "release_resources",
        "finalize_attempt",
    )


@pytest.mark.anyio
async def test_two_successful_refinements_honor_the_default_budget() -> None:
    gateway = _LayeredFakeGateway(
        initial_gains=(0.9,),
        refine_gains=(0.7, 0.5),
    )
    context = _context(gateway=gateway, refine_budget=2)

    output = await run_layerplan_glsl_direct_graph(
        reference_image=reference_png(),
        content_type="image/png",
        instruction="match",
        context=context,
    )

    assert [call["role"] for call in gateway.calls] == [
        "plan",
        "initial",
        "refine",
        "refine",
    ]
    assert len(output["result"].candidates) == 3
    assert output["result"].current_best is output["result"].candidates[-1]
    assert output["result"].direct_ledger.draw_count == 3


@pytest.mark.anyio
async def test_repeated_worse_patch_stops_before_duplicate_draw() -> None:
    gateway = _LayeredFakeGateway(
        initial_gains=(0.6,),
        refine_gains=(0.9,),
    )
    context = _context(gateway=gateway, refine_budget=2)

    output = await run_layerplan_glsl_direct_graph(
        reference_image=reference_png(),
        content_type="image/png",
        instruction="match",
        context=context,
    )

    result = output["result"]
    assert [call["role"] for call in gateway.calls] == [
        "plan",
        "initial",
        "refine",
        "refine",
    ]
    assert len(result.candidates) == 2
    assert result.direct_ledger.draw_count == 2
    assert result.duplicate_patch_count == 1
    assert result.refinement_stop_reason == "duplicate_patch"
    assert result.safety_failure_codes == ()


@pytest.mark.anyio
async def test_worse_refine_feedback_reaches_one_recovery_attempt() -> None:
    gateway = _LayeredFakeGateway(
        initial_gains=(0.6,),
        refine_gains=(0.9, 0.8, 0.7),
    )
    context = _context(gateway=gateway, refine_budget=3)

    output = await run_layerplan_glsl_direct_graph(
        reference_image=reference_png(),
        content_type="image/png",
        instruction="match",
        context=context,
    )

    refine_calls = [call for call in gateway.calls if call["role"] == "refine"]
    assert len(refine_calls) == 2
    recovery_payload = str(refine_calls[1]["messages"][1].content)
    assert '"outcome":"not_improved"' in recovery_payload
    assert '"loss_delta":' in recovery_payload
    assert output["result"].refinement_stop_reason == "patience_exhausted"
    assert output["result"].non_improving_count == 2


@pytest.mark.anyio
async def test_guarded_entry_disables_tracing_and_cleans_up_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context()

    async def fail_after_prepare(
        _input: object,
        *,
        context: DirectGraphContext,
        **_kwargs: object,
    ) -> object:
        tracing = get_tracing_context()
        assert tracing["enabled"] is False
        assert tracing["parent"] is None
        prepared = FakePrepared(context.renderer, "void main() {}", 64, 64)
        context.program_cache[("prepared",)] = prepared
        raise RuntimeError("unexpected")

    monkeypatch.setattr(direct_graph._attempt_graph, "ainvoke", fail_after_prepare)

    with pytest.raises(RuntimeError, match="unexpected"):
        await run_layerplan_glsl_direct_graph(
            reference_image=reference_png(),
            content_type="image/png",
            instruction="private instruction",
            context=context,
        )

    assert context.program_cache == {}
    assert context.renderer.close_count == 1
