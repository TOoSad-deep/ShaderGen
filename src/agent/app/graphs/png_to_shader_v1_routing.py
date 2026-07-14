"""PNG 转无贴图 Shader V1 的纯确定性路由规则."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shaderforge.contracts import AcceptancePolicy, BudgetPolicy, StopReason


def _budget(state: Mapping[str, Any]) -> BudgetPolicy:
    value = state["budget_policy"]
    if isinstance(value, BudgetPolicy):
        return value
    return BudgetPolicy(**dict(value))


def _acceptance(state: Mapping[str, Any]) -> AcceptancePolicy:
    value = state["acceptance_policy"]
    if isinstance(value, AcceptancePolicy):
        return value
    return AcceptancePolicy(**dict(value))


def model_node_outcome(state: Mapping[str, Any]) -> str:
    """把有界模型 Node 的成功/降级结果映射为 Graph 分支."""
    return "finalize" if state.get("stop_reason") else "continue"


def decide_after_render(state: Mapping[str, Any]) -> dict[str, str]:
    """根据 compile 事实和剩余预算决定 select、repair 或 finalize."""
    if state.get("render_status") == "success":
        return {"next_action": "select"}
    if state.get("cancelled", False):
        return {
            "next_action": "finalize",
            "stop_reason": StopReason.CANCELLED.value,
        }
    if reason := state.get("stop_reason"):
        return {"next_action": "finalize", "stop_reason": str(reason)}

    budget = _budget(state)
    if int(state.get("model_call_count", 0)) >= budget.max_model_calls:
        return {
            "next_action": "finalize",
            "stop_reason": StopReason.MODEL_BUDGET_EXHAUSTED.value,
        }
    if int(state.get("compile_repair_count", 0)) >= budget.max_compile_repairs:
        return {
            "next_action": "finalize",
            "stop_reason": StopReason.COMPILE_REPAIR_EXHAUSTED.value,
        }
    return {"next_action": "compile_repair"}


def decide_after_selection(state: Mapping[str, Any]) -> dict[str, str]:
    """在一次有效评分后按固定优先级决定 critic 或停止."""
    if state.get("cancelled", False):
        return {
            "next_action": "finalize",
            "stop_reason": StopReason.CANCELLED.value,
        }
    if reason := state.get("stop_reason"):
        return {"next_action": "finalize", "stop_reason": str(reason)}

    budget = _budget(state)
    acceptance = _acceptance(state)
    model_calls = int(state.get("model_call_count", 0))
    if model_calls >= budget.max_model_calls:
        return {
            "next_action": "finalize",
            "stop_reason": StopReason.MODEL_BUDGET_EXHAUSTED.value,
        }
    if float(state["current_best_total_loss"]) <= acceptance.quality_threshold:
        return {
            "next_action": "finalize",
            "stop_reason": StopReason.QUALITY_THRESHOLD_MET.value,
        }
    if int(state.get("no_improvement_count", 0)) >= acceptance.stagnation_rounds:
        return {
            "next_action": "finalize",
            "stop_reason": StopReason.STAGNATION.value,
        }
    if int(state.get("visual_refinement_count", 0)) >= budget.max_visual_refinements:
        return {
            "next_action": "finalize",
            "stop_reason": StopReason.VISUAL_ITERATION_BUDGET_EXHAUSTED.value,
        }
    if budget.max_model_calls - model_calls < 2:
        return {
            "next_action": "finalize",
            "stop_reason": StopReason.MODEL_BUDGET_EXHAUSTED.value,
        }
    return {"next_action": "visual_critic"}


def route_next_action(state: Mapping[str, Any]) -> str:
    """返回决定节点写入的受限动作名."""
    action = str(state.get("next_action", ""))
    if action not in {"select", "compile_repair", "visual_critic", "finalize"}:
        raise ValueError(f"未知 PNG-to-Shader 路由动作：{action}。")
    return action
