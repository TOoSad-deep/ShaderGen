"""Topology and execution-trace tests for the current attempt graph."""

from __future__ import annotations

import asyncio
import threading
from time import perf_counter
from types import SimpleNamespace

import pytest
from langsmith import get_tracing_context

from agent.app.graphs import layerplan_glsl_direct as direct_graph
from agent.app.graphs.layerplan_glsl_direct import (
    DirectGraphContext,
    build_layerplan_glsl_direct_graph,
    run_layerplan_glsl_direct_graph,
)
from agent.app.services.layerplan_glsl_direct import LayerPlanGlslDirectConfig
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
        ),
        receipt_issuer=TEST_ISSUER,
    )


def test_graph_exposes_one_node_per_product_step() -> None:
    topology = build_layerplan_glsl_direct_graph().get_graph()
    assert set(topology.nodes) == PRODUCT_NODES | {"__start__", "__end__"}
    assert len(topology.edges) == 34


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
        "decide_refinement",
        "release_resources",
        "finalize_attempt",
    )


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
        "decide_refinement",
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

    assert [call["role"] for call in gateway.calls] == ["plan", "initial", "refine"]
    assert output["result"].direct_ledger.draw_count == 1
    assert output["completed_nodes"][-4:] == (
        "render_program",
        "decide_refinement",
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
