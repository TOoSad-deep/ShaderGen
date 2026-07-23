"""固定 7 例 acceptance live A/B 脚本的纯函数测试。"""

from __future__ import annotations

import pytest

from scripts.run_scene_mvp_acceptance_live_ab import (
    acceptance_for,
    build_aggregate,
    evaluate_case_gate,
    evaluate_decision,
    geometry_first_accepts,
    material_roi_regressions,
    strict_total_accepts,
)
from shaderforge.evaluation import MinSceneMetricBreakdown


def _metric(total_loss: float, geometry_mask_loss: float) -> MinSceneMetricBreakdown:
    return MinSceneMetricBreakdown(
        total_loss=total_loss,
        global_mae=0.1,
        foreground_mae=0.1,
        background_mae=0.1,
        geometry_mask_loss=geometry_mask_loss,
        edge_loss=0.1,
        worst_tile_mae=0.1,
        foreground_ratio=0.5,
        background_ratio=0.5,
        effective_weights={},
    )


def test_geometry_first_accepts_prioritizes_geometry() -> None:
    incumbent = _metric(total_loss=0.5, geometry_mask_loss=0.4)
    # geometry 改善但 total 变差：geometry-first 仍接受。
    assert geometry_first_accepts(
        incumbent,
        _metric(total_loss=0.6, geometry_mask_loss=0.3),
    )
    # geometry 相同，total 严格改善才接受。
    assert geometry_first_accepts(
        incumbent,
        _metric(total_loss=0.4, geometry_mask_loss=0.4),
    )
    # geometry 变差，total 再好也拒绝。
    assert not geometry_first_accepts(
        incumbent,
        _metric(total_loss=0.1, geometry_mask_loss=0.5),
    )
    # 两者都相同不构成严格改善。
    assert not geometry_first_accepts(incumbent, incumbent)


def test_strict_total_accepts_only_total_loss() -> None:
    incumbent = _metric(total_loss=0.5, geometry_mask_loss=0.4)
    assert strict_total_accepts(
        incumbent,
        _metric(total_loss=0.4, geometry_mask_loss=0.9),
    )
    assert not strict_total_accepts(
        incumbent,
        _metric(total_loss=0.5, geometry_mask_loss=0.1),
    )
    assert not strict_total_accepts(
        incumbent,
        _metric(total_loss=0.6, geometry_mask_loss=0.1),
    )


def test_acceptance_for_rejects_unknown_mode() -> None:
    assert acceptance_for("geometry_first") is geometry_first_accepts
    assert acceptance_for("strict_total") is strict_total_accepts
    with pytest.raises(ValueError, match="未知 acceptance"):
        acceptance_for("replay_arm_b")  # type: ignore[arg-type]


def test_material_roi_regressions_threshold_is_strict() -> None:
    baseline = {"center": 0.10, "rim": 0.20}
    arm = {"center": 0.11, "rim": 0.25}

    assert material_roi_regressions(baseline, arm, tolerance=0.01) == {
        "rim": pytest.approx(0.05)
    }
    # 恰好等于容差不构成实质回退。
    assert material_roi_regressions(baseline, arm, tolerance=0.05) == {}
    assert material_roi_regressions(baseline, arm, tolerance=0.04) == {
        "rim": pytest.approx(0.05)
    }


def test_material_roi_regressions_validates_inputs() -> None:
    with pytest.raises(ValueError, match="不能为负"):
        material_roi_regressions({"a": 0.1}, {"a": 0.2}, tolerance=-0.1)
    with pytest.raises(ValueError, match="一致"):
        material_roi_regressions({"a": 0.1}, {"b": 0.2}, tolerance=0.01)


def test_build_aggregate_reports_mean_and_median_per_arm() -> None:
    result = build_aggregate(
        {
            "geometry_first": {"case_a": 0.1, "case_b": 0.3, "case_c": 0.2},
            "strict_total": {"case_a": 0.2, "case_b": 0.2, "case_c": 0.2},
        }
    )

    assert result["geometry_first_mean"] == pytest.approx(0.2)
    assert result["geometry_first_median"] == pytest.approx(0.2)
    assert result["strict_total_mean"] == pytest.approx(0.2)
    with pytest.raises(ValueError, match="至少需要一个"):
        build_aggregate({})


def _gate(
    external_delta: float,
    roi_t: dict[str, float] | None = None,
    roi_g: dict[str, float] | None = None,
) -> dict[str, object]:
    return evaluate_case_gate(
        external_delta_t_minus_g=external_delta,
        roi_regressions_t_minus_g=roi_t or {},
        roi_regressions_g_minus_t=roi_g or {},
        roi_tolerance=0.01,
        external_tolerance=0.01,
    )


def test_evaluate_case_gate_checks_external_and_roi_bidirectionally() -> None:
    t_worse = _gate(0.02, roi_t={"upper_color": 0.03})
    assert t_worse["external_objective_material_regression_t_vs_g"] is True
    assert t_worse["external_objective_material_regression_g_vs_t"] is False
    assert t_worse["roi_material_regressions_t_vs_g"] == {"upper_color": 0.03}
    assert t_worse["material_regression_free"] is False

    g_worse = _gate(-0.02, roi_g={"highlight": 0.05})
    assert g_worse["external_objective_material_regression_g_vs_t"] is True
    assert g_worse["external_objective_material_regression_t_vs_g"] is False
    assert g_worse["material_regression_free"] is False

    clean = _gate(0.009)
    assert clean["external_objective_material_regression_t_vs_g"] is False
    assert clean["external_objective_material_regression_g_vs_t"] is False
    assert clean["material_regression_free"] is True
    # 恰好等于容差不构成实质回退。
    boundary = _gate(0.01, roi_t={})
    assert boundary["material_regression_free"] is True


def test_evaluate_case_gate_rejects_negative_tolerance() -> None:
    with pytest.raises(ValueError, match="不能为负"):
        evaluate_case_gate(
            external_delta_t_minus_g=0.0,
            roi_regressions_t_minus_g={},
            roi_regressions_g_minus_t={},
            roi_tolerance=-0.01,
            external_tolerance=0.01,
        )


def _aggregates(
    g_internal: float,
    t_internal: float,
    g_external: float,
    t_external: float,
) -> tuple[dict[str, float], dict[str, float]]:
    internal = {
        "geometry_first_mean": g_internal,
        "geometry_first_median": g_internal,
        "strict_total_mean": t_internal,
        "strict_total_median": t_internal,
    }
    external = {
        "geometry_first_mean": g_external,
        "geometry_first_median": g_external,
        "strict_total_mean": t_external,
        "strict_total_median": t_external,
    }
    return internal, external


def test_evaluate_decision_supports_strict_total() -> None:
    gates = {
        "case_a": _gate(-0.01),
        "case_b": _gate(-0.005, roi_g={"upper_color": 0.02}),
    }
    internal, external = _aggregates(0.03, 0.02, 0.04, 0.03)

    decision = evaluate_decision(
        gates,
        internal_aggregate=internal,
        external_aggregate=external,
        roi_tolerance=0.01,
        external_tolerance=0.01,
    )

    assert decision["outcome"] == "strict_total_supported"
    assert decision["strict_total_supported"] is True
    assert decision["geometry_first_supported"] is False
    assert decision["geometry_first_material_regression_case_ids"] == ["case_b"]
    assert decision["strict_total_material_regression_case_ids"] == []
    assert decision["tolerances"] == {
        "roi_material_regression": 0.01,
        "external_objective_material_regression": 0.01,
    }
    assert decision["internal_total_loss"]["strict_total_not_worse"] is True
    assert decision["internal_total_loss"]["geometry_first_not_worse"] is False
    assert decision["external_objective"]["strict_total_not_worse"] is True
    assert decision["external_objective"]["geometry_first_not_worse"] is False
    assert decision["per_case_external_objective_material_regression"] == {
        "t_vs_g": {"case_a": False, "case_b": False},
        "g_vs_t": {"case_a": False, "case_b": False},
    }
    assert decision["per_case_roi_material_regressions"]["g_vs_t"] == {
        "case_a": {},
        "case_b": {"upper_color": 0.02},
    }


def test_evaluate_decision_supports_geometry_first_symmetrically() -> None:
    gates = {"case_a": _gate(0.02, roi_t={"rim": 0.03})}
    internal, external = _aggregates(0.02, 0.03, 0.02, 0.03)

    decision = evaluate_decision(
        gates,
        internal_aggregate=internal,
        external_aggregate=external,
        roi_tolerance=0.01,
        external_tolerance=0.01,
    )

    assert decision["outcome"] == "geometry_first_supported"
    assert decision["strict_total_material_regression_case_ids"] == ["case_a"]
    assert decision["per_case_roi_material_regressions"]["t_vs_g"] == {
        "case_a": {"rim": 0.03}
    }


def test_evaluate_decision_inconclusive_on_mixed_or_tie() -> None:
    internal, external = _aggregates(0.02, 0.03, 0.03, 0.02)
    mixed = evaluate_decision(
        {"case_a": _gate(0.0)},
        internal_aggregate=internal,
        external_aggregate=external,
        roi_tolerance=0.01,
        external_tolerance=0.01,
    )
    assert mixed["outcome"] == "inconclusive"

    tie_internal, tie_external = _aggregates(0.02, 0.02, 0.03, 0.03)
    tie = evaluate_decision(
        {"case_a": _gate(0.0)},
        internal_aggregate=tie_internal,
        external_aggregate=tie_external,
        roi_tolerance=0.01,
        external_tolerance=0.01,
    )
    # 完全持平时两臂同时满足“不劣且无回退”，按冻结规则如实判 inconclusive。
    assert tie["outcome"] == "inconclusive"

    with pytest.raises(ValueError, match="至少需要一个"):
        evaluate_decision(
            {},
            internal_aggregate=internal,
            external_aggregate=external,
            roi_tolerance=0.01,
            external_tolerance=0.01,
        )
    with pytest.raises(ValueError, match="不能为负"):
        evaluate_decision(
            {"case_a": _gate(0.0)},
            internal_aggregate=internal,
            external_aggregate=external,
            roi_tolerance=-0.01,
            external_tolerance=0.01,
        )


def test_evaluate_decision_fail_closed_on_missing_fields() -> None:
    gates = {"case_a": _gate(0.0)}
    internal, external = _aggregates(0.03, 0.02, 0.04, 0.03)

    incomplete_gate = dict(gates["case_a"])
    del incomplete_gate["roi_material_regressions_t_vs_g"]
    with pytest.raises(ValueError, match="缺少必需字段"):
        evaluate_decision(
            {"case_a": incomplete_gate},
            internal_aggregate=internal,
            external_aggregate=external,
            roi_tolerance=0.01,
            external_tolerance=0.01,
        )

    incomplete_internal = dict(internal)
    del incomplete_internal["strict_total_median"]
    with pytest.raises(ValueError, match="缺少必需字段"):
        evaluate_decision(
            gates,
            internal_aggregate=incomplete_internal,
            external_aggregate=external,
            roi_tolerance=0.01,
            external_tolerance=0.01,
        )

    incomplete_external = dict(external)
    del incomplete_external["geometry_first_mean"]
    with pytest.raises(ValueError, match="缺少必需字段"):
        evaluate_decision(
            gates,
            internal_aggregate=internal,
            external_aggregate=incomplete_external,
            roi_tolerance=0.01,
            external_tolerance=0.01,
        )
