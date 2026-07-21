"""M5 benchmark 聚合与发布门禁判断."""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from shaderforge.benchmark.models import (
    GateCheck,
    GateStatus,
    QualityGatePolicy,
    QualityGateReport,
)


def _section(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    section = value.get(key)
    return section if isinstance(section, Mapping) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


CasePredicate = Callable[[Mapping[str, Any]], bool]


@dataclass(frozen=True)
class _HumanReviewSummary:
    review_count: int
    final_preference_rate: float | None
    final_win_count: int
    initial_win_count: int
    tie_count: int


def _rate(case_results: Sequence[Mapping[str, Any]], predicate: CasePredicate) -> float:
    if not case_results:
        return 0.0
    return sum(1 for case in case_results if predicate(case)) / len(case_results)


def _failed_ids(
    case_results: Sequence[Mapping[str, Any]], predicate: CasePredicate
) -> tuple[str, ...]:
    return tuple(
        str(case.get("case_id", "unknown"))
        for case in case_results
        if not predicate(case)
    )


def decode_human_preferences(
    human_review: Mapping[str, Any],
    assignments: Mapping[str, Any],
    *,
    case_results: Sequence[Mapping[str, Any]],
    expected_suite_run_id: str | None,
) -> dict[str, Literal["initial", "final", "tie"]]:
    """严格复用发布门禁语义，把匿名 A/B 选择解码为候选角色."""
    expected_case_ids = tuple(str(case.get("case_id", "")) for case in case_results)
    if any(not case_id for case_id in expected_case_ids):
        raise ValueError("benchmark case_id 不能为空。")
    if len(set(expected_case_ids)) != len(expected_case_ids):
        raise ValueError("benchmark case_id 不得重复。")
    expected_case_set = set(expected_case_ids)

    assignment_schema = assignments.get("schema_version")
    if type(assignment_schema) is not int or assignment_schema != 1:
        raise ValueError("assignments.schema_version 必须为 1。")
    assignment_suite_run_id = assignments.get("suite_run_id")
    if (
        not isinstance(assignment_suite_run_id, str)
        or not assignment_suite_run_id.strip()
    ):
        raise ValueError("assignments.suite_run_id 不能为空。")
    if (
        expected_suite_run_id is not None
        and assignment_suite_run_id != expected_suite_run_id
    ):
        raise ValueError("assignments.suite_run_id 与当前 suite run 不一致。")
    assignment_items = assignments.get("items")
    if not isinstance(assignment_items, list):
        raise ValueError("assignments.items 必须为数组。")

    expected_paths: dict[str, tuple[str, str]] = {}
    for case in case_results:
        case_id = str(case.get("case_id", ""))
        ai_on = _section(case, "ai_on")
        initial_path = ai_on.get("initial_render_path")
        final_path = ai_on.get("final_render_path")
        if (
            isinstance(initial_path, str)
            and initial_path
            and isinstance(final_path, str)
            and final_path
        ):
            expected_paths[case_id] = (initial_path, final_path)

    role_by_case: dict[str, dict[str, str]] = {}
    for raw in assignment_items:
        if not isinstance(raw, Mapping):
            raise ValueError("assignments.items 的每一项都必须是对象。")
        assignment_case_id = raw.get("case_id")
        if not isinstance(assignment_case_id, str) or not assignment_case_id:
            raise ValueError("assignments case_id 不能为空。")
        case_id = assignment_case_id
        if case_id in role_by_case:
            raise ValueError(f"assignments case_id 重复：{case_id}")
        a_role = raw.get("a_role")
        b_role = raw.get("b_role")
        if {a_role, b_role} != {"initial", "final"}:
            raise ValueError(f"assignments A/B 角色非法：{case_id}")
        initial_path = raw.get("initial_render_path")
        final_path = raw.get("final_render_path")
        if not isinstance(initial_path, str) or not initial_path:
            raise ValueError(f"assignments initial_render_path 缺失：{case_id}")
        if not isinstance(final_path, str) or not final_path:
            raise ValueError(f"assignments final_render_path 缺失：{case_id}")
        expected = expected_paths.get(case_id)
        if expected is not None and (initial_path, final_path) != expected:
            raise ValueError(f"assignments render path 与 case 证据不一致：{case_id}")
        role_by_case[case_id] = {"A": str(a_role), "B": str(b_role)}
    assignment_case_set = set(role_by_case)
    if assignment_case_set != expected_case_set:
        missing = sorted(expected_case_set - assignment_case_set)
        extra = sorted(assignment_case_set - expected_case_set)
        raise ValueError(
            f"assignments case 集合不一致：missing={missing}, extra={extra}"
        )

    review_schema = human_review.get("schema_version")
    if type(review_schema) is not int or review_schema != 1:
        raise ValueError("human review schema_version 必须为 1。")
    review_suite_run_id = human_review.get("suite_run_id")
    if not isinstance(review_suite_run_id, str) or not review_suite_run_id.strip():
        raise ValueError("human review suite_run_id 不能为空。")
    if review_suite_run_id != assignment_suite_run_id:
        raise ValueError("human review suite_run_id 与 assignments 不一致。")
    reviewer = human_review.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("human review reviewer 不能为空。")
    review_items = human_review.get("items")
    if not isinstance(review_items, list):
        raise ValueError("human review items 必须为数组。")

    choices_by_case: dict[str, str] = {}
    for raw in review_items:
        if not isinstance(raw, Mapping):
            raise ValueError("human review items 的每一项都必须是对象。")
        review_case_id = raw.get("case_id")
        if not isinstance(review_case_id, str) or not review_case_id:
            raise ValueError("human review case_id 不能为空。")
        case_id = review_case_id
        if case_id in choices_by_case:
            raise ValueError(f"human review case_id 重复：{case_id}")
        choice = raw.get("choice")
        if choice not in {"A", "B", "TIE"}:
            raise ValueError(f"human review choice 非法：{case_id}")
        choices_by_case[case_id] = str(choice)
    review_case_set = set(choices_by_case)
    if review_case_set != expected_case_set:
        missing = sorted(expected_case_set - review_case_set)
        extra = sorted(review_case_set - expected_case_set)
        raise ValueError(
            f"human review case 集合不一致：missing={missing}, extra={extra}"
        )

    preferences: dict[str, Literal["initial", "final", "tie"]] = {}
    for case_id in expected_case_ids:
        choice = choices_by_case[case_id]
        if choice == "TIE":
            preferences[case_id] = "tie"
        elif role_by_case[case_id].get(choice) == "final":
            preferences[case_id] = "final"
        else:
            preferences[case_id] = "initial"
    return preferences


def _review_summary(
    human_review: Mapping[str, Any] | None,
    assignments: Mapping[str, Any] | None,
    *,
    case_results: Sequence[Mapping[str, Any]],
    expected_suite_run_id: str | None,
) -> _HumanReviewSummary:
    """严格校验盲评证据，并返回不会静默吞错的聚合结果."""
    empty_summary = _HumanReviewSummary(0, None, 0, 0, 0)
    if assignments is None:
        if human_review is not None:
            raise ValueError("人工盲评存在，但 assignments 证据缺失。")
        return empty_summary
    if human_review is None:
        return empty_summary
    preferences = decode_human_preferences(
        human_review,
        assignments,
        case_results=case_results,
        expected_suite_run_id=expected_suite_run_id,
    )
    final_win_count = sum(value == "final" for value in preferences.values())
    initial_win_count = sum(value == "initial" for value in preferences.values())
    tie_count = sum(value == "tie" for value in preferences.values())
    review_count = len(preferences)
    return _HumanReviewSummary(
        review_count=review_count,
        final_preference_rate=final_win_count / review_count,
        final_win_count=final_win_count,
        initial_win_count=initial_win_count,
        tie_count=tie_count,
    )


def evaluate_quality_gate(
    case_results: Sequence[Mapping[str, Any]],
    policy: QualityGatePolicy,
    *,
    human_review: Mapping[str, Any] | None = None,
    assignments: Mapping[str, Any] | None = None,
    expected_suite_run_id: str | None = None,
    bit_identical_case_ids: Sequence[str] | None = None,
) -> QualityGateReport:
    """以运行前冻结的 policy 评估完整 benchmark，不动态移动阈值."""
    cases = tuple(case_results)
    checks: list[GateCheck] = []

    def add_rate_check(
        check_id: str,
        section: str,
        field: str,
        threshold: float,
    ) -> None:
        def predicate(case: Mapping[str, Any]) -> bool:
            return bool(_section(case, section).get(field, False))

        actual = _rate(cases, predicate)
        checks.append(
            GateCheck(
                check_id,
                actual >= threshold,
                actual,
                f">= {threshold:.3f}",
                _failed_ids(cases, predicate),
            )
        )

    checks.append(
        GateCheck(
            "case_count",
            len(cases) == policy.required_case_count,
            len(cases),
            f"== {policy.required_case_count}",
        )
    )
    add_rate_check(
        "ai_off_compile_rate",
        "ai_off",
        "compile_passed",
        policy.min_ai_off_compile_rate,
    )
    add_rate_check(
        "ai_off_static_pass_rate",
        "ai_off",
        "static_passed",
        policy.min_ai_off_static_pass_rate,
    )
    add_rate_check(
        "final_compile_rate",
        "ai_on",
        "final_compile_passed",
        policy.min_final_compile_rate,
    )
    add_rate_check(
        "final_static_pass_rate",
        "ai_on",
        "final_static_passed",
        policy.min_final_static_pass_rate,
    )

    def improved(case: Mapping[str, Any]) -> bool:
        ai_on = _section(case, "ai_on")
        initial = _number(ai_on.get("initial_total_loss"))
        final = _number(ai_on.get("final_total_loss"))
        return bool(
            initial is not None
            and final is not None
            and initial - final >= policy.min_total_improvement
        )

    improvement_rate = _rate(cases, improved)
    checks.append(
        GateCheck(
            "metric_improvement_rate",
            improvement_rate >= policy.min_improvement_rate,
            improvement_rate,
            f">= {policy.min_improvement_rate:.3f}",
            _failed_ids(cases, improved),
        )
    )

    mismatch_ids = tuple(
        str(case.get("case_id", "unknown"))
        for case in cases
        if not bool(_section(case, "ai_on").get("final_matches_current_best", False))
    )
    checks.append(
        GateCheck(
            "final_is_current_best",
            len(mismatch_ids) <= policy.max_final_current_best_mismatches,
            len(mismatch_ids),
            f"<= {policy.max_final_current_best_mismatches}",
            mismatch_ids,
        )
    )
    non_monotonic_ids = tuple(
        str(case.get("case_id", "unknown"))
        for case in cases
        if not bool(_section(case, "ai_on").get("best_updates_monotonic", False))
    )
    checks.append(
        GateCheck(
            "current_best_monotonic",
            len(non_monotonic_ids) <= policy.max_non_monotonic_runs,
            len(non_monotonic_ids),
            f"<= {policy.max_non_monotonic_runs}",
            non_monotonic_ids,
        )
    )

    def trace_predicate(case: Mapping[str, Any]) -> bool:
        return bool(_section(case, "ai_on").get("traceability_passed", False))

    traceability_rate = _rate(cases, trace_predicate)
    checks.append(
        GateCheck(
            "traceability_rate",
            traceability_rate >= policy.min_traceability_rate,
            traceability_rate,
            f">= {policy.min_traceability_rate:.3f}",
            _failed_ids(cases, trace_predicate),
        )
    )

    pink_case = next(
        (case for case in cases if str(case.get("case_id")) == "pink_gel"),
        None,
    )
    pink = _section(pink_case or {}, "ai_on")
    bbox_error = _number(pink.get("bbox_max_error_uv"))
    checks.append(
        GateCheck(
            "pink_gel_bbox",
            bbox_error is not None and bbox_error <= policy.pink_gel_max_bbox_error_uv,
            bbox_error,
            f"<= {policy.pink_gel_max_bbox_error_uv:.6f}",
            (
                ()
                if bbox_error is not None
                and bbox_error <= policy.pink_gel_max_bbox_error_uv
                else ("pink_gel",)
            ),
        )
    )
    global_rmse = _number(pink.get("global_rmse"))
    checks.append(
        GateCheck(
            "pink_gel_global_color",
            global_rmse is not None and global_rmse <= policy.pink_gel_max_global_rmse,
            global_rmse,
            f"<= {policy.pink_gel_max_global_rmse:.6f}",
            (
                ()
                if global_rmse is not None
                and global_rmse <= policy.pink_gel_max_global_rmse
                else ("pink_gel",)
            ),
        )
    )
    roi_losses = pink.get("key_roi_losses")
    roi_map = roi_losses if isinstance(roi_losses, Mapping) else {}
    roi_failures = tuple(
        region_id
        for region_id, limit in policy.pink_gel_max_key_roi_losses
        if _number(roi_map.get(region_id)) is None or float(roi_map[region_id]) > limit
    )
    checks.append(
        GateCheck(
            "pink_gel_key_rois",
            not roi_failures,
            {
                key: _number(roi_map.get(key))
                for key, _ in policy.pink_gel_max_key_roi_losses
            },
            str(policy.pink_gel_key_roi_limit_map),
            tuple(f"pink_gel:{region_id}" for region_id in roi_failures),
        )
    )

    review_summary = _review_summary(
        human_review,
        assignments,
        case_results=cases,
        expected_suite_run_id=expected_suite_run_id,
    )
    review_count = review_summary.review_count
    final_preference_rate = review_summary.final_preference_rate
    case_ids = tuple(str(case.get("case_id", "")) for case in cases)
    if bit_identical_case_ids is None:
        identical_ids: tuple[str, ...] = ()
        distinct_pair_count: int | None = None
    else:
        raw_identical_ids = tuple(bit_identical_case_ids)
        if len(set(raw_identical_ids)) != len(raw_identical_ids):
            raise ValueError("bit_identical_case_ids 不得重复。")
        unknown_ids = sorted(set(raw_identical_ids) - set(case_ids))
        if unknown_ids:
            raise ValueError(f"bit_identical_case_ids 含未知 case：{unknown_ids}")
        identical_set = set(raw_identical_ids)
        identical_ids = tuple(
            case_id for case_id in case_ids if case_id in identical_set
        )
        distinct_pair_count = len(cases) - len(identical_ids)
    human_complete = review_count >= policy.required_human_review_count
    checks.append(
        GateCheck(
            "human_blind_review_count",
            human_complete,
            review_count,
            f">= {policy.required_human_review_count}",
        )
    )
    preference_passed = bool(
        human_complete
        and final_preference_rate is not None
        and final_preference_rate >= policy.min_human_final_preference_rate
    )
    checks.append(
        GateCheck(
            "human_final_preference_rate",
            preference_passed,
            final_preference_rate,
            f">= {policy.min_human_final_preference_rate:.3f}",
        )
    )

    quality_checks = checks[:-2]
    if any(not check.passed for check in quality_checks):
        status: GateStatus = "failed"
    elif not human_complete:
        status = "pending_human_review"
    elif not preference_passed:
        status = "failed"
    else:
        status = "passed"

    ai_on_sections = [_section(case, "ai_on") for case in cases]
    call_counts = [
        number
        for section in ai_on_sections
        if (number := _number(section.get("model_call_count"))) is not None
    ]
    elapsed_values = [
        number
        for section in ai_on_sections
        if (number := _number(section.get("elapsed_seconds"))) is not None
    ]
    best_updates = [
        number
        for section in ai_on_sections
        if (number := _number(section.get("best_update_count"))) is not None
    ]
    summary = {
        "case_count": len(cases),
        "improved_case_count": sum(1 for case in cases if improved(case)),
        "improvement_rate": improvement_rate,
        "average_model_calls": statistics.fmean(call_counts) if call_counts else None,
        "average_elapsed_seconds": (
            statistics.fmean(elapsed_values) if elapsed_values else None
        ),
        "average_best_updates": (
            statistics.fmean(best_updates) if best_updates else None
        ),
        "human_review_count": review_count,
        "human_final_preference_rate": final_preference_rate,
        "final_win_count": review_summary.final_win_count,
        "initial_win_count": review_summary.initial_win_count,
        "tie_count": review_summary.tie_count,
        "distinct_pair_count": distinct_pair_count,
        "bit_identical_case_ids": list(identical_ids),
        "failed_case_ids": [
            str(case.get("case_id", "unknown"))
            for case in cases
            if not bool(_section(case, "ai_on").get("success", False))
        ],
    }
    return QualityGateReport(
        policy_id=policy.policy_id,
        status=status,
        checks=tuple(checks),
        summary=summary,
    )
