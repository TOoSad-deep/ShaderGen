"""PNG-to-Shader V2.3 的纯确定性、有界路由规则。"""
# ruff: noqa: D401, D415

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _get(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _stopped(state: Mapping[str, Any] | object) -> bool:
    return bool(_get(state, "stop_reason"))


def _branches(state: Mapping[str, Any] | object) -> tuple[object, ...]:
    return tuple(_get(state, "hypothesis_branches", ()))


def _current_branch(state: Mapping[str, Any] | object) -> object | None:
    branches = _branches(state)
    cursor = int(_get(state, "hypothesis_cursor", 0))
    return branches[cursor] if cursor < len(branches) else None


def _has_budget(state: Mapping[str, Any] | object, dimension: str) -> bool:
    budget = _get(state, "budget_state")
    if budget is None:
        return False
    limits = _get(budget, "limits")
    used = _get(budget, "used")
    reserved = _get(budget, "reserved")
    return int(_get(used, dimension, 0)) + int(_get(reserved, dimension, 0)) < int(
        _get(limits, dimension, 0)
    )


def route_after_initialize(state: Mapping[str, Any] | object) -> str:
    """按最后确认 phase/ref 恢复，不从 START 盲目重跑。"""
    phase = str(_get(state, "phase", "initialized"))
    if phase == "finalized":
        return "end"
    # phase 是最后确认的副作用边界；旧 active refs 只能辅助同 phase 恢复，
    # 绝不能越过 phase 把 materialized/selected 状态倒退到 evaluate/materialize。
    if phase == "selecting":
        if _get(state, "objective_best_ref") is not None:
            return "promote"
        if int(_get(state, "hypothesis_cursor", 0)) >= len(_branches(state)):
            return "cross_select"
        return "select_hypothesis"
    if phase == "evaluating":
        if (
            len(tuple(_get(state, "active_evaluation_refs", ()))) == 5
            and _get(state, "active_rendered_structure_verification_ref") is not None
        ):
            return "materialize"
        if _get(state, "active_render_repeatability_ref") is not None:
            return "evaluate"
    if phase == "rendering":
        if _get(state, "active_render_repeatability_ref") is not None:
            return "evaluate"
        if (
            _get(state, "active_compilation_ref") is not None
            and _get(state, "active_diagnostic_compilation_ref") is not None
        ):
            return "render"
    if phase == "compiling":
        if _get(state, "active_compilation_ref") is not None:
            return "render"
        if _get(state, "active_genome_ref") is not None:
            return "compile"
    if phase in {"compiling", "rendering", "evaluating"}:
        branch = _current_branch(state)
        if branch is None:
            return "next_hypothesis"
        cursor = int(_get(branch, "seed_cursor", 0))
        return (
            "next_seed"
            if cursor < len(tuple(_get(branch, "seed_refs", ())))
            else "next_hypothesis"
        )
    if phase == "seeding" and _get(state, "active_genome_ref") is not None:
        return "prepare_candidate"
    if phase in {"seeding", "intent_built"}:
        return "dequeue_hypothesis"
    if phase == "interpreted":
        return "build_intents"
    if phase == "measured":
        return "analyze"
    return "prepare"


def route_after_measurement(state: Mapping[str, Any] | object) -> str:
    """测量恢复成功才进入视觉解释。"""
    return "finalize" if _stopped(state) else "interpret"


def route_after_interpretation(state: Mapping[str, Any] | object) -> str:
    """解释失败或模型预算失败时 fail-closed。"""
    if _stopped(state):
        return "finalize"
    return "build_intents" if _get(state, "visual_interpretation_ref") else "finalize"


def route_after_intent_build(state: Mapping[str, Any] | object) -> str:
    """只有至少一个可行 Intent hypothesis 才进入 hypothesis loop。"""
    if _stopped(state):
        return "finalize"
    return "dequeue_hypothesis" if _branches(state) else "finalize"


def route_after_hypothesis(state: Mapping[str, Any] | object) -> str:
    """显式区分新 hypothesis、已有 seeds 与跨 hypothesis 选择。"""
    if _stopped(state):
        return "finalize"
    branch = _current_branch(state)
    if branch is None:
        return "cross_select"
    if not tuple(_get(branch, "seed_refs", ())):
        return "plan_seeds"
    return "dequeue_seed"


def route_after_seed_planning(state: Mapping[str, Any] | object) -> str:
    """Diversity gate 不通过即终止当前 run。"""
    if _stopped(state):
        return "finalize"
    branch = _current_branch(state)
    if branch is None or not tuple(_get(branch, "seed_refs", ())):
        return "finalize"
    return "dequeue_seed"


def route_after_strategy(state: Mapping[str, Any] | object) -> str:
    """Strategy 失败不得继续提出 SeedPlan。"""
    return "finalize" if _stopped(state) else "propose_seeds"


def route_after_seed_proposal(state: Mapping[str, Any] | object) -> str:
    """只有已物化 SeedPlan set 才进入确定性展开。"""
    return "finalize" if _stopped(state) else "expand_seeds"


def route_after_seed(state: Mapping[str, Any] | object) -> str:
    """Seed dequeue 后检查候选预算，并显式切换 hypothesis。"""
    if _stopped(state):
        return "finalize"
    if _get(state, "active_genome_ref") is not None:
        return (
            "prepare_candidate"
            if _has_budget(state, "candidate_attempts")
            else "finalize"
        )
    return "next_hypothesis"


def route_after_candidate_preparation(state: Mapping[str, Any] | object) -> str:
    """Candidate reservation/commit 完成后才允许 Compiler。"""
    return "finalize" if _stopped(state) else "compile"


def _route_seed_failure(state: Mapping[str, Any] | object, success_field: str) -> str:
    if _stopped(state):
        return "finalize"
    if _get(state, success_field) is not None:
        return "success"
    branch = _current_branch(state)
    if branch is None:
        return "next_hypothesis"
    cursor = int(_get(branch, "seed_cursor", 0))
    return (
        "next_seed"
        if cursor < len(tuple(_get(branch, "seed_refs", ())))
        else "next_hypothesis"
    )


def route_after_compile(state: Mapping[str, Any] | object) -> str:
    """Compiler defect fail-run；普通非法 seed 转下一 seed/hypothesis。"""
    result = _route_seed_failure(state, "active_compilation_ref")
    if result == "success":
        return "render" if _has_budget(state, "render_calls") else "finalize"
    return result


def route_after_render(state: Mapping[str, Any] | object) -> str:
    """完整 plan 才进入评估；否则沿同一节点执行有界 self-loop。"""
    if _stopped(state):
        return "finalize"
    if _get(state, "active_render_repeatability_ref") is not None:
        return "evaluate"
    if (
        _get(state, "active_render_plan_ref") is not None
        and _get(state, "active_render_progress_ref") is not None
        and _get(state, "active_compilation_ref") is not None
    ):
        return "render"
    branch = _current_branch(state)
    if branch is None:
        return "next_hypothesis"
    cursor = int(_get(branch, "seed_cursor", 0))
    return (
        "next_seed"
        if cursor < len(tuple(_get(branch, "seed_refs", ())))
        else "next_hypothesis"
    )


def route_after_evaluation(state: Mapping[str, Any] | object) -> str:
    """只有 typed evaluation Artifact 才可物化 Candidate。"""
    if _stopped(state):
        return "finalize"
    if (
        len(tuple(_get(state, "active_evaluation_refs", ()))) == 5
        and _get(state, "active_rendered_structure_verification_ref") is not None
    ):
        return "materialize"
    branch = _current_branch(state)
    if branch is None:
        return "next_hypothesis"
    cursor = int(_get(branch, "seed_cursor", 0))
    return (
        "next_seed"
        if cursor < len(tuple(_get(branch, "seed_refs", ())))
        else "next_hypothesis"
    )


def route_after_materialization(state: Mapping[str, Any] | object) -> str:
    """Typed closure 成功才选择；普通 closure 拒绝继续有界 loop。"""
    if _stopped(state):
        return "finalize"
    summaries = tuple(_get(state, "candidate_summary_refs", ()))
    if summaries and _get(summaries[-1], "kind") == "candidate_record":
        return "select"
    branch = _current_branch(state)
    if branch is None:
        return "next_hypothesis"
    cursor = int(_get(branch, "seed_cursor", 0))
    return (
        "next_seed"
        if cursor < len(tuple(_get(branch, "seed_refs", ())))
        else "next_hypothesis"
    )


def route_after_candidate_selection(state: Mapping[str, Any] | object) -> str:
    """候选选择后继续 seed loop，或进入下一 hypothesis。"""
    if _stopped(state):
        return "finalize"
    branch = _current_branch(state)
    if branch is None:
        return "next_hypothesis"
    cursor = int(_get(branch, "seed_cursor", 0))
    return (
        "next_seed"
        if cursor < len(tuple(_get(branch, "seed_refs", ())))
        else "next_hypothesis"
    )


def route_after_cross_selection(state: Mapping[str, Any] | object) -> str:
    """只有存在 objective best 时才进入可选 promotion。"""
    if _stopped(state) and _get(state, "objective_best_ref") is None:
        return "finalize"
    return "promote" if _get(state, "objective_best_ref") is not None else "finalize"


__all__ = [
    "route_after_candidate_selection",
    "route_after_candidate_preparation",
    "route_after_compile",
    "route_after_cross_selection",
    "route_after_evaluation",
    "route_after_hypothesis",
    "route_after_initialize",
    "route_after_intent_build",
    "route_after_interpretation",
    "route_after_measurement",
    "route_after_materialization",
    "route_after_render",
    "route_after_seed",
    "route_after_seed_planning",
    "route_after_seed_proposal",
    "route_after_strategy",
]
