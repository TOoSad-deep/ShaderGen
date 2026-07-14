"""M5 benchmark 聚合与发布门禁判断."""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from typing import Any

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


def _rate(
    case_results: Sequence[Mapping[str, Any]], predicate: CasePredicate
) -> float:
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


def _review_summary(
    human_review: Mapping[str, Any] | None,
    assignments: Mapping[str, Any] | None,
) -> tuple[int, float | None]:
    if human_review is None or assignments is None:
        return 0, None
    assignment_items = assignments.get("items")
    review_items = human_review.get("items")
    if not isinstance(assignment_items, list) or not isinstance(review_items, list):
        return 0, None
    role_by_case: dict[str, dict[str, str]] = {}
    for raw in assignment_items:
        if not isinstance(raw, Mapping):
            continue
        role_by_case[str(raw.get("case_id", ""))] = {
            "A": str(raw.get("a_role", "")),
            "B": str(raw.get("b_role", "")),
        }
    valid = 0
    final_preferences = 0
    seen: set[str] = set()
    for raw in review_items:
        if not isinstance(raw, Mapping):
            continue
        case_id = str(raw.get("case_id", ""))
        choice = str(raw.get("choice", "")).upper()
        if case_id in seen or case_id not in role_by_case or choice not in {"A", "B", "TIE"}:
            continue
        seen.add(case_id)
        valid += 1
        if choice in {"A", "B"} and role_by_case[case_id].get(choice) == "final":
            final_preferences += 1
    return valid, (final_preferences / valid if valid else None)


def evaluate_quality_gate(
    case_results: Sequence[Mapping[str, Any]],
    policy: QualityGatePolicy,
    *,
    human_review: Mapping[str, Any] | None = None,
    assignments: Mapping[str, Any] | None = None,
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
            bbox_error is not None
            and bbox_error <= policy.pink_gel_max_bbox_error_uv,
            bbox_error,
            f"<= {policy.pink_gel_max_bbox_error_uv:.6f}",
            (() if bbox_error is not None and bbox_error <= policy.pink_gel_max_bbox_error_uv else ("pink_gel",)),
        )
    )
    global_rmse = _number(pink.get("global_rmse"))
    checks.append(
        GateCheck(
            "pink_gel_global_color",
            global_rmse is not None
            and global_rmse <= policy.pink_gel_max_global_rmse,
            global_rmse,
            f"<= {policy.pink_gel_max_global_rmse:.6f}",
            (() if global_rmse is not None and global_rmse <= policy.pink_gel_max_global_rmse else ("pink_gel",)),
        )
    )
    roi_losses = pink.get("key_roi_losses")
    roi_map = roi_losses if isinstance(roi_losses, Mapping) else {}
    roi_failures = tuple(
        region_id
        for region_id, limit in policy.pink_gel_max_key_roi_losses
        if _number(roi_map.get(region_id)) is None
        or float(roi_map[region_id]) > limit
    )
    checks.append(
        GateCheck(
            "pink_gel_key_rois",
            not roi_failures,
            {key: _number(roi_map.get(key)) for key, _ in policy.pink_gel_max_key_roi_losses},
            str(policy.pink_gel_key_roi_limit_map),
            tuple(f"pink_gel:{region_id}" for region_id in roi_failures),
        )
    )

    review_count, final_preference_rate = _review_summary(human_review, assignments)
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
