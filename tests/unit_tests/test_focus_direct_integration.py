"""Focused regression coverage for the LayerPlan Direct graph."""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent.app.contracts.layerplan_glsl_direct import (
    AttemptLedger,
    DirectOptimizationPolicy,
)
from agent.app.graphs.layerplan_glsl_direct import (
    DirectGraphContext,
    run_layerplan_glsl_direct_graph,
)
from agent.app.nodes.layered_direct import uniform_optimization_nodes
from agent.app.nodes.layered_direct.candidate_nodes import select_candidate
from agent.app.nodes.layered_direct.uniform_optimization_nodes import (
    _select_target_components,
)
from agent.app.services.layerplan_glsl_direct import LayerPlanGlslDirectConfig
from shaderforge.evaluation import FocusedRegionMetricsV1, NormalizedUvBBox
from shaderforge.program_spec import NormalizedRegion
from shaderforge.uniform_optimization import (
    FlatTunableComponent,
    UniformOptimizationFocusComponentV1,
    UniformOptimizationFocusV1,
)
from tests.direct_fakes import TEST_ISSUER, FakeRenderer, reference_png
from tests.unit_tests.test_layerplan_glsl_direct_runner import _LayeredFakeGateway


def _context(
    gateway: _LayeredFakeGateway,
    *,
    draw_budget: int = 1,
    uniform_tuning_draw_budget: int = 0,
) -> DirectGraphContext:
    return DirectGraphContext(
        gateway=gateway,
        renderer=FakeRenderer(),
        config=LayerPlanGlslDirectConfig(
            implementation_identity_sha256="d" * 64,
            plan_llm_budget=1,
            direct_author_llm_budget=1,
            compile_budget=1,
            draw_budget=draw_budget,
            refine_budget=0,
            uniform_tuning_draw_budget=uniform_tuning_draw_budget,
        ),
        receipt_issuer=TEST_ISSUER,
    )


def _focus_payload(
    *, target_layer_id: str = "bg", path: str = "u_gain"
) -> dict[str, object]:
    return {
        "target_layer_id": target_layer_id,
        "objective": "color",
        "active_components": [{"path": path, "component_indices": [0]}],
        "region_policy": "layer_region",
    }


def _gateway_with_focus(
    *, target_layer_id: str = "bg", path: str = "u_gain"
) -> _LayeredFakeGateway:
    gateway = _LayeredFakeGateway()
    initial = json.loads(gateway._queues[("initial", False)][0])
    initial["optimization_focus"] = _focus_payload(
        target_layer_id=target_layer_id, path=path
    )
    encoded = json.dumps(initial)
    gateway._queues[("initial", False)] = [encoded]
    gateway._queues[("initial", True)] = [encoded]
    return gateway


@pytest.mark.anyio
async def test_initial_focus_is_trusted_saved_and_scores_bottom_left_roi() -> None:
    gateway = _gateway_with_focus()
    plan = json.loads(gateway._queues["plan"][0])
    plan["layers"][0]["region"] = {
        "x": 0.25,
        "y": 0.75,
        "width": 0.5,
        "height": 0.2,
    }
    gateway._queues["plan"] = [json.dumps(plan)]
    context = _context(gateway)

    output = await run_layerplan_glsl_direct_graph(
        reference_image=reference_png(),
        content_type="image/png",
        instruction="match",
        context=context,
    )

    result = output["result"]
    candidate = result.current_best
    assert candidate is not None
    assert candidate.optimization_focus is not None
    assert candidate.optimization_focus.to_dict() == {
        "schema_version": "uniform_optimization_focus_v1",
        **_focus_payload(),
    }
    assert "optimization_focus" not in candidate.metrics
    assert candidate.metrics["focused_region_metrics"]["uv_bbox"] == pytest.approx(
        {"x": 0.25, "y": 0.75, "width": 0.5, "height": 0.2}
    )
    assert candidate.focused_region_metrics is not None
    assert len(context.renderer.draw_calls) == 1


def _component(layer_id: str, path: str) -> FlatTunableComponent:
    return FlatTunableComponent(
        layer_id=layer_id,
        path=path,
        component_index=0,
        minimum=Decimal("0"),
        maximum=Decimal("1"),
        step=Decimal("0.1"),
        base_value=Decimal("0.5"),
    )


def test_valid_focus_exactly_overrides_automatic_target_heuristic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    focused = _component("background", "u_focus")
    decoy = _component("subject", "u_decoy")
    monkeypatch.setattr(
        uniform_optimization_nodes,
        "validate_uniform_optimization_focus",
        lambda *_args: SimpleNamespace(is_valid=True, components=(focused,)),
    )
    state = {
        "layer_plan": SimpleNamespace(
            layers=(
                SimpleNamespace(
                    layer_id="background",
                    role="background",
                    confidence=1.0,
                    region=NormalizedRegion(0, 0, 1, 1),
                ),
                SimpleNamespace(
                    layer_id="subject",
                    role="subject",
                    confidence=1.0,
                    region=NormalizedRegion(0, 0, 1, 1),
                ),
            )
        ),
        "current_best": SimpleNamespace(
            optimization_focus=object(),
            layered_spec=object(),
            spec=object(),
            residual_summary={"dominant_metric_component": "edge_loss"},
        ),
    }

    assert _select_target_components(cast(Any, state), (focused, decoy)) == (focused,)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("target_layer_id", "path"),
    [("missing", "u_gain"), ("bg", "u_missing")],
)
async def test_invalid_focus_keeps_initial_candidate_and_falls_back_to_heuristic(
    target_layer_id: str,
    path: str,
) -> None:
    gateway = _gateway_with_focus(target_layer_id=target_layer_id, path=path)
    context = _context(gateway)
    output = await run_layerplan_glsl_direct_graph(
        reference_image=reference_png(),
        content_type="image/png",
        instruction="match",
        context=context,
    )
    candidate = output["result"].current_best

    assert candidate is not None
    assert candidate.role == "initial"
    assert candidate.optimization_focus is None
    assert output["result"].direct_ledger.rejected_candidates == 0
    selected = _select_target_components(
        cast(
            Any, {"layer_plan": output["result"].layer_plan, "current_best": candidate}
        ),
        (_component("bg", "u_gain"),),
    )
    assert selected == (_component("bg", "u_gain"),)


@pytest.mark.anyio
async def test_uniform_derived_candidate_inherits_incumbent_focus() -> None:
    gateway = _gateway_with_focus()
    initial = json.loads(gateway._queues[("initial", False)][0])
    initial["layers"][0]["uniform_values"]["u_gain"] = 0.6
    encoded = json.dumps(initial)
    gateway._queues[("initial", False)] = [encoded]
    gateway._queues[("initial", True)] = [encoded]
    context = _context(gateway, draw_budget=3, uniform_tuning_draw_budget=2)
    output = await run_layerplan_glsl_direct_graph(
        reference_image=reference_png(),
        content_type="image/png",
        instruction="match",
        context=context,
    )

    initial = next(
        item for item in output["result"].candidates if item.role == "initial"
    )
    derived = [
        item for item in output["result"].candidates if item.role == "uniform_optimize"
    ]
    assert derived
    assert initial.optimization_focus is not None
    assert all(
        item.optimization_focus == initial.optimization_focus for item in derived
    )


def _focused_metrics(x: float) -> FocusedRegionMetricsV1:
    return FocusedRegionMetricsV1(
        roi_mae=0.0,
        roi_geometry_mask_loss=0.0,
        roi_edge_loss=0.0,
        outside_roi_mae=0.0,
        uv_bbox=NormalizedUvBBox(x=x, y=0.0, width=0.5, height=0.5),
        dilation_radius=2,
        roi_pixel_count=4,
        outside_roi_pixel_count=12,
    )


def _candidate(
    *,
    mae: float,
    loss: float,
    focus: object | None,
    focused_metrics: FocusedRegionMetricsV1 | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        mae=mae,
        loss=loss,
        role="refine",
        patched_layer_id="bg",
        optimization_focus=focus,
        focused_region_metrics=focused_metrics,
        metrics={
            "global_mae": mae,
            "geometry_mask_loss": loss,
            "focused_roi_mae": 0.0,
            "focused_roi_geometry_mask_loss": 0.0,
        },
    )


def _selection_state(
    incumbent: SimpleNamespace, candidate: SimpleNamespace
) -> dict[str, Any]:
    return {
        "pending_candidate": candidate,
        "candidate_role": "refine",
        "direct_ledger": AttemptLedger(),
        "candidates": [incumbent],
        "current_best": incumbent,
        "optimization_policy": DirectOptimizationPolicy(),
        "consecutive_non_improving": 0,
        "previous_refine_feedback": None,
        "completed_nodes": (),
    }


def test_global_selection_ignores_focused_metrics() -> None:
    incumbent = _candidate(mae=0.08, loss=0.12, focus=object())
    candidate = _candidate(mae=0.09, loss=0.10, focus=object())

    update = select_candidate(cast(Any, _selection_state(incumbent, candidate)), None)  # type: ignore[arg-type]

    assert update["candidate_selected"] is False
    assert update["current_best"] is incumbent


def test_rejected_refine_never_replaces_incumbent_focus() -> None:
    incumbent_focus = UniformOptimizationFocusV1(
        target_layer_id="bg",
        objective="color",
        active_components=(UniformOptimizationFocusComponentV1("u_gain", (0,)),),
        region_policy="layer_region",
    )
    incumbent = _candidate(mae=0.08, loss=0.12, focus=incumbent_focus)
    rejected = _candidate(mae=0.09, loss=0.10, focus=object())

    update = select_candidate(cast(Any, _selection_state(incumbent, rejected)), None)  # type: ignore[arg-type]

    assert update["candidate_selected"] is False
    assert update["current_best"].optimization_focus is incumbent_focus


def test_refine_feedback_omits_focused_deltas_for_different_rois() -> None:
    focus = UniformOptimizationFocusV1(
        target_layer_id="bg",
        objective="color",
        active_components=(UniformOptimizationFocusComponentV1("u_gain", (0,)),),
        region_policy="worst_residual_intersection",
    )
    incumbent = _candidate(
        mae=0.08,
        loss=0.12,
        focus=focus,
        focused_metrics=_focused_metrics(0.0),
    )
    rejected = _candidate(
        mae=0.09,
        loss=0.10,
        focus=focus,
        focused_metrics=_focused_metrics(0.5),
    )

    update = select_candidate(
        cast(Any, _selection_state(incumbent, rejected)),
        None,  # type: ignore[arg-type]
    )

    feedback = update["previous_refine_feedback"]
    assert feedback is not None
    assert "geometry_mask_loss" in feedback.metric_deltas
    assert not any(name.startswith("focused_") for name in feedback.metric_deltas)
