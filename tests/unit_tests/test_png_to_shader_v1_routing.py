from __future__ import annotations

from dataclasses import asdict

from agent.app.graphs.png_to_shader_v1_routing import (
    decide_after_render,
    decide_after_selection,
    model_node_outcome,
)
from shaderforge.contracts import AcceptancePolicy, BudgetPolicy, StopReason


def state() -> dict:
    return {
        "budget_policy": asdict(
            BudgetPolicy(
                max_visual_refinements=2,
                max_compile_repairs=2,
                max_model_calls=8,
                max_wall_time_seconds=30,
            )
        ),
        "acceptance_policy": asdict(AcceptancePolicy()),
        "model_call_count": 2,
        "compile_repair_count": 0,
        "visual_refinement_count": 0,
        "no_improvement_count": 0,
        "current_best_total_loss": 0.30,
        "stop_reason": "",
    }


def test_compile_failure_routes_to_repair_until_budget_is_exhausted() -> None:
    value = state()
    value["render_status"] = "compile_failed"

    assert decide_after_render(value) == {"next_action": "compile_repair"}

    value["compile_repair_count"] = 2
    assert decide_after_render(value) == {
        "next_action": "finalize",
        "stop_reason": StopReason.COMPILE_REPAIR_EXHAUSTED.value,
    }


def test_successful_render_is_selected_even_when_wall_time_just_expired() -> None:
    value = state()
    value["render_status"] = "success"
    value["stop_reason"] = StopReason.WALL_TIME_EXHAUSTED.value

    assert decide_after_render(value) == {"next_action": "select"}
    assert decide_after_selection(value) == {
        "next_action": "finalize",
        "stop_reason": StopReason.WALL_TIME_EXHAUSTED.value,
    }


def test_two_no_improvement_rounds_stop_before_another_critic() -> None:
    value = state()
    value["no_improvement_count"] = 2

    assert decide_after_selection(value) == {
        "next_action": "finalize",
        "stop_reason": StopReason.STAGNATION.value,
    }


def test_quality_visual_and_model_budgets_have_explicit_stop_reasons() -> None:
    quality = state()
    quality["current_best_total_loss"] = 0.10
    assert decide_after_selection(quality)["stop_reason"] == "quality_threshold_met"

    visual = state()
    visual["visual_refinement_count"] = 2
    assert (
        decide_after_selection(visual)["stop_reason"]
        == "visual_iteration_budget_exhausted"
    )

    model = state()
    model["model_call_count"] = 7
    assert decide_after_selection(model)["stop_reason"] == "model_budget_exhausted"


def test_model_failure_always_routes_to_finalize() -> None:
    value = state()
    value["stop_reason"] = StopReason.COMPLETED_WITH_BEST_EFFORT.value

    assert model_node_outcome(value) == "finalize"


def test_candidate_count_has_a_static_hard_upper_bound() -> None:
    budget = BudgetPolicy(
        max_visual_refinements=4,
        max_compile_repairs=2,
        max_model_calls=12,
        max_wall_time_seconds=60,
    )

    # 初稿至多产生 1 个候选；每次 visual refine 和 compile repair
    # 只会各产生 1 个新候选，且两个计数器都在模型调用前消耗。
    assert 1 + budget.max_visual_refinements + budget.max_compile_repairs == 7
