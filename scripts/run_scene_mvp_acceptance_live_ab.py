"""固定 7 例 acceptance live 单因素直接 A/B（无模型离线诊断）.

Arm G 使用既有 geometry-first 字典序 acceptance（先 `geometry_mask_loss`
后 `total_loss`）；Arm T 使用 strict total-loss acceptance。两臂从完全
相同的初始 fallback scene 与同一次 fallback 渲染出发，使用相同候选
生成器、参数范围、阶段顺序与每 case 候选/draw 预算，但各自基于本臂
current incumbent 实时生成和评估候选；禁止把一臂候选流 offline replay
给另一臂，轨迹分叉是 acceptance 的预期因果后果。判定门槛在
``FIXED_GATE`` 中预先冻结，看到结果后不得调整。本实验不修改生产
scorer、Prompt、Graph、预算、目标或 ``current_best`` 代码，不是
D058/D059 冻结 benchmark，也不能使 F09 passing。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import platform
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from statistics import mean, median
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
from PIL import Image, ImageDraw

if TYPE_CHECKING or __package__:
    from scripts.run_scene_mvp_run_diagnostics import (
        _raw_rgb_array as raw_rgb_array,
    )
    from scripts.run_scene_mvp_run_diagnostics import (
        interleave_proposal_directions,
    )
    from scripts.run_scene_mvp_scorer_calibration import _regions as case_regions
    from scripts.run_scene_mvp_tile_guard_ab import (
        max_tile_regression,
        tile_mae_grid,
    )
else:
    from run_scene_mvp_run_diagnostics import (
        _raw_rgb_array as raw_rgb_array,
    )
    from run_scene_mvp_run_diagnostics import (
        interleave_proposal_directions,
    )
    from run_scene_mvp_scorer_calibration import _regions as case_regions
    from run_scene_mvp_tile_guard_ab import (
        max_tile_regression,
        tile_mae_grid,
    )
from shaderforge.benchmark import load_benchmark_suite
from shaderforge.evaluation import (
    MinSceneMetricBreakdown,
    evaluate_min_scene,
    evaluate_render,
)
from shaderforge.generation import materialize_min_shader
from shaderforge.optimization import (
    OptimizationStage,
    propose_min_scene_candidates,
    rebase_candidate_proposal,
)
from shaderforge.perception import perceive_min_target
from shaderforge.rendering import (
    PlaywrightWebGL1Renderer,
    PreparedWebGL1Renderer,
)
from shaderforge.scene import MinScene

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/png_to_shader_v1/manifest.yaml"
DEFAULT_BASELINE = (
    ROOT / "benchmarks/png_to_shader_v1/scene_mvp_fixed_template_v3_baseline.json"
)
SCHEMA_VERSION = "scene_mvp_acceptance_live_ab_v2"
STAGE_DRAW_BUDGET = 32
TILE_GRIDS = (4, 8)
METRIC_COMPONENTS = (
    "global_mae",
    "foreground_mae",
    "background_mae",
    "geometry_mask_loss",
    "edge_loss",
    "worst_tile_mae",
)
# 仅用于事后评价的发布阻塞 ROI，不进入任何 acceptance。
WATCH_ROIS = {
    "ellipse_gradient": ("upper_color",),
    "arc_highlight_orb": ("highlight_upper_left",),
}
# 预先冻结的判定门槛：看到结果后不得调整。
FIXED_GATE: dict[str, Any] = {
    # 逐 case/ROI 实质回退容差，与冻结 baseline max_roi_loss_regression 一致。
    "material_roi_regression_tolerance": 0.01,
    # 逐 case 外部 objective 实质回退容差，与 ROI 容差取同一冻结量级。
    "material_external_objective_regression_tolerance": 0.01,
    # 判定规则（自然语言冻结）：若 strict-total 相对 geometry-first 在
    # aggregate 内部 total loss 与外部 objective 上同时不劣，且逐 case 的
    # 外部 objective 与 ROI 都不引入超过容差的实质回退，则证据支持
    # strict-total；geometry-first 对称判定；两臂各有 aggregate 优劣或
    # 互有/均有实质回退时如实报告为 inconclusive。
    "decision_rule": (
        "strict_total_supported_if_aggregate_not_worse_and_no_material_regression;"
        "geometry_first_supported_symmetrically;otherwise_inconclusive"
    ),
    # 人工看片只是工程分析限制，不构成独立人工盲评。
    "human_review_limitation": (
        "contact sheet 代理看片只是工程分析，不是独立人工偏好票"
    ),
}

AcceptanceMode = Literal["geometry_first", "strict_total"]
ARM_ORDER: tuple[AcceptanceMode, ...] = ("geometry_first", "strict_total")
ARM_LABELS: dict[AcceptanceMode, str] = {
    "geometry_first": "G:geometry-first",
    "strict_total": "T:strict-total",
}


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def geometry_first_accepts(
    incumbent: MinSceneMetricBreakdown,
    candidate: MinSceneMetricBreakdown,
) -> bool:
    """Arm G acceptance：`(geometry_mask_loss, total_loss)` 字典序严格改善."""
    return (candidate.geometry_mask_loss, candidate.total_loss) < (
        incumbent.geometry_mask_loss,
        incumbent.total_loss,
    )


def strict_total_accepts(
    incumbent: MinSceneMetricBreakdown,
    candidate: MinSceneMetricBreakdown,
) -> bool:
    """Arm T acceptance：仅 `total_loss` 严格改善."""
    return candidate.total_loss < incumbent.total_loss


AcceptanceFn = Callable[[MinSceneMetricBreakdown, MinSceneMetricBreakdown], bool]


def acceptance_for(mode: AcceptanceMode) -> AcceptanceFn:
    """按臂返回 acceptance 谓词，未知臂 fail closed."""
    if mode == "geometry_first":
        return geometry_first_accepts
    if mode == "strict_total":
        return strict_total_accepts
    raise ValueError(f"未知 acceptance 臂：{mode}")


def material_roi_regressions(
    baseline_roi: Mapping[str, float],
    arm_roi: Mapping[str, float],
    *,
    tolerance: float,
) -> dict[str, float]:
    """返回超过冻结容差的 ROI 回退（``arm - baseline > tolerance``）."""
    if tolerance < 0.0:
        raise ValueError("实质回退容差不能为负。")
    if set(baseline_roi) != set(arm_roi):
        raise ValueError("ROI 集合必须一致。")
    return {
        name: float(arm_roi[name] - baseline_roi[name])
        for name in sorted(baseline_roi)
        if arm_roi[name] - baseline_roi[name] > tolerance
    }


def _component_dict(metric: MinSceneMetricBreakdown) -> dict[str, float]:
    result = {"total_loss": metric.total_loss}
    for name in METRIC_COMPONENTS:
        result[name] = float(getattr(metric, name))
    return result


def _metric_delta(
    before: MinSceneMetricBreakdown,
    after: MinSceneMetricBreakdown,
) -> dict[str, float]:
    result = {"total_loss": after.total_loss - before.total_loss}
    for name in METRIC_COMPONENTS:
        result[name] = float(getattr(after, name) - getattr(before, name))
    return result


def build_aggregate(
    per_arm_case_metrics: Mapping[str, Mapping[str, float]],
) -> dict[str, float]:
    """按臂聚合内部 total loss 或外部 objective 的均值/中位数."""
    if not per_arm_case_metrics:
        raise ValueError("aggregate 至少需要一个案例。")
    result: dict[str, float] = {}
    for arm, case_values in per_arm_case_metrics.items():
        values = [float(value) for value in case_values.values()]
        result[f"{arm}_mean"] = float(mean(values))
        result[f"{arm}_median"] = float(median(values))
    return result


def evaluate_case_gate(
    *,
    external_delta_t_minus_g: float,
    roi_regressions_t_minus_g: Mapping[str, float],
    roi_regressions_g_minus_t: Mapping[str, float],
    roi_tolerance: float,
    external_tolerance: float,
) -> dict[str, Any]:
    """逐 case 机器可读 gate：外部 objective 与 ROI 的 0.01 实质回退检查.

    回退按两臂差值双向判定：``t_vs_g`` 表示 strict-total 相对
    geometry-first 更差超过容差，``g_vs_t`` 为反方向。容差参数只接受
    显式传入，调用方必须使用 ``FIXED_GATE`` 的冻结值。
    """
    if roi_tolerance < 0.0 or external_tolerance < 0.0:
        raise ValueError("实质回退容差不能为负。")
    external_t_vs_g = external_delta_t_minus_g > external_tolerance
    external_g_vs_t = external_delta_t_minus_g < -external_tolerance
    roi_t_vs_g = dict(sorted(roi_regressions_t_minus_g.items()))
    roi_g_vs_t = dict(sorted(roi_regressions_g_minus_t.items()))
    return {
        "external_objective_delta_t_minus_g": float(external_delta_t_minus_g),
        "external_objective_material_regression_t_vs_g": external_t_vs_g,
        "external_objective_material_regression_g_vs_t": external_g_vs_t,
        "external_objective_regression_tolerance": external_tolerance,
        "roi_material_regressions_t_vs_g": roi_t_vs_g,
        "roi_material_regressions_g_vs_t": roi_g_vs_t,
        "roi_regression_tolerance": roi_tolerance,
        "material_regression_free": not (
            external_t_vs_g or external_g_vs_t or roi_t_vs_g or roi_g_vs_t
        ),
    }


_DECISION_GATE_FIELDS = (
    "external_objective_material_regression_t_vs_g",
    "external_objective_material_regression_g_vs_t",
    "roi_material_regressions_t_vs_g",
    "roi_material_regressions_g_vs_t",
    "material_regression_free",
)
_AGGREGATE_FIELDS = (
    "strict_total_mean",
    "strict_total_median",
    "geometry_first_mean",
    "geometry_first_median",
)


def _require_fields(
    payload: Mapping[str, Any],
    fields: tuple[str, ...],
    *,
    owner: str,
) -> None:
    """显式 fail closed：缺少必需字段时抛 ValueError，不依赖 KeyError."""
    missing = [field for field in fields if field not in payload]
    if missing:
        raise ValueError(f"{owner} 缺少必需字段：{missing}")


def evaluate_decision(
    case_gates: Mapping[str, Mapping[str, Any]],
    *,
    internal_aggregate: Mapping[str, float],
    external_aggregate: Mapping[str, float],
    roi_tolerance: float,
    external_tolerance: float,
) -> dict[str, Any]:
    """按 ``FIXED_GATE`` 的双向冻结规则汇总机器可读 decision.

    strict-total 成立条件：内部与外部 aggregate 的均值/中位数都不劣于
    geometry-first，且没有任何逐 case 的 t_vs_g 实质回退；geometry-first
    对称。两臂同时成立（完全持平）或同时不成立（互有优劣/回退）时判为
    inconclusive，不做倾向性解读。输入缺少任何必需字段时显式 fail closed。
    """
    if not case_gates:
        raise ValueError("decision 至少需要一个案例 gate。")
    if roi_tolerance < 0.0 or external_tolerance < 0.0:
        raise ValueError("实质回退容差不能为负。")
    for case_id, gate in case_gates.items():
        _require_fields(gate, _DECISION_GATE_FIELDS, owner=f"case gate {case_id}")
    _require_fields(internal_aggregate, _AGGREGATE_FIELDS, owner="internal_aggregate")
    _require_fields(external_aggregate, _AGGREGATE_FIELDS, owner="external_aggregate")
    t_material = any(
        gate["external_objective_material_regression_t_vs_g"]
        or bool(gate["roi_material_regressions_t_vs_g"])
        for gate in case_gates.values()
    )
    g_material = any(
        gate["external_objective_material_regression_g_vs_t"]
        or bool(gate["roi_material_regressions_g_vs_t"])
        for gate in case_gates.values()
    )
    t_internal_ok = (
        internal_aggregate["strict_total_mean"]
        <= internal_aggregate["geometry_first_mean"]
        and internal_aggregate["strict_total_median"]
        <= internal_aggregate["geometry_first_median"]
    )
    t_external_ok = (
        external_aggregate["strict_total_mean"]
        <= external_aggregate["geometry_first_mean"]
        and external_aggregate["strict_total_median"]
        <= external_aggregate["geometry_first_median"]
    )
    strict_supported = t_internal_ok and t_external_ok and not t_material
    # geometry-first 对称条件：两项 aggregate 不劣于 strict-total，且没有
    # 任何逐 case 的 g_vs_t 实质回退。
    g_internal_ok = (
        internal_aggregate["geometry_first_mean"]
        <= internal_aggregate["strict_total_mean"]
        and internal_aggregate["geometry_first_median"]
        <= internal_aggregate["strict_total_median"]
    )
    g_external_ok = (
        external_aggregate["geometry_first_mean"]
        <= external_aggregate["strict_total_mean"]
        and external_aggregate["geometry_first_median"]
        <= external_aggregate["strict_total_median"]
    )
    geometry_supported = g_internal_ok and g_external_ok and not g_material
    if strict_supported and not geometry_supported:
        outcome = "strict_total_supported"
    elif geometry_supported and not strict_supported:
        outcome = "geometry_first_supported"
    else:
        outcome = "inconclusive"
    return {
        "outcome": outcome,
        "rule": (
            "aggregate_mean_median_not_worse_and_no_material_regression;"
            "symmetric_both_directions"
        ),
        "tolerances": {
            "roi_material_regression": roi_tolerance,
            "external_objective_material_regression": external_tolerance,
        },
        "internal_total_loss": {
            "aggregate": dict(internal_aggregate),
            "strict_total_not_worse": t_internal_ok,
            "geometry_first_not_worse": g_internal_ok,
        },
        "external_objective": {
            "aggregate": dict(external_aggregate),
            "strict_total_not_worse": t_external_ok,
            "geometry_first_not_worse": g_external_ok,
        },
        "per_case_external_objective_material_regression": {
            "t_vs_g": {
                case_id: bool(gate["external_objective_material_regression_t_vs_g"])
                for case_id, gate in sorted(case_gates.items())
            },
            "g_vs_t": {
                case_id: bool(gate["external_objective_material_regression_g_vs_t"])
                for case_id, gate in sorted(case_gates.items())
            },
        },
        "per_case_roi_material_regressions": {
            "t_vs_g": {
                case_id: gate["roi_material_regressions_t_vs_g"]
                for case_id, gate in sorted(case_gates.items())
            },
            "g_vs_t": {
                case_id: gate["roi_material_regressions_g_vs_t"]
                for case_id, gate in sorted(case_gates.items())
            },
        },
        "strict_total_supported": strict_supported,
        "geometry_first_supported": geometry_supported,
        "strict_total_material_regression_case_ids": sorted(
            case_id
            for case_id, gate in case_gates.items()
            if gate["external_objective_material_regression_t_vs_g"]
            or gate["roi_material_regressions_t_vs_g"]
        ),
        "geometry_first_material_regression_case_ids": sorted(
            case_id
            for case_id, gate in case_gates.items()
            if gate["external_objective_material_regression_g_vs_t"]
            or gate["roi_material_regressions_g_vs_t"]
        ),
        "material_regression_free_case_count": sum(
            1 for gate in case_gates.values() if gate["material_regression_free"]
        ),
    }


@dataclass(frozen=True)
class _EvaluatedCandidate:
    """一次已评估候选：轨迹、指标、像素与 PNG."""

    stage: str
    parameter: str
    direction: str
    before: float
    after: float
    metric: MinSceneMetricBreakdown
    rgb: np.ndarray
    image_bytes: bytes
    accepted: bool


@dataclass(frozen=True)
class _ArmRunResult:
    """单臂 live 搜索结果."""

    mode: AcceptanceMode
    candidates: tuple[_EvaluatedCandidate, ...]
    stage_candidate_counts: dict[str, int]
    final_metric: MinSceneMetricBreakdown
    final_rgb: np.ndarray
    final_image_bytes: bytes
    final_candidate_index: int | None


async def _render_with_arrays(
    prepared: PreparedWebGL1Renderer,
    scene: MinScene,
    *,
    reference: np.ndarray,
    metric_background: tuple[float, float, float],
) -> tuple[MinSceneMetricBreakdown, np.ndarray, bytes]:
    """执行一次真实 Renderer draw，返回指标、float RGB 与 PNG."""
    materialized = materialize_min_shader(scene)
    result = await prepared.render_uniforms(
        materialized.uniform_values,
        capture_png=True,
    )
    if not result.success or result.rgb_bytes is None or result.image_bytes is None:
        raise RuntimeError(result.draw_error or "acceptance_live_ab_renderer_failed")
    rgb = raw_rgb_array(
        result.rgb_bytes,
        scene.canvas.width,
        scene.canvas.height,
    )
    return (
        evaluate_min_scene(reference, rgb, metric_background),
        rgb,
        result.image_bytes,
    )


async def _run_live_arm(
    prepared: PreparedWebGL1Renderer,
    start_scene: MinScene,
    fallback_metric: MinSceneMetricBreakdown,
    fallback_rgb: np.ndarray,
    fallback_image_bytes: bytes,
    *,
    reference: np.ndarray,
    metric_background: tuple[float, float, float],
    mode: AcceptanceMode,
) -> _ArmRunResult:
    """基于本臂 incumbent 实时生成/评估候选的 live 局部搜索.

    stage 顺序、方向交错、提案参数与每 stage 32 次 draw 预算在两臂间完全
    一致；唯一变量是 acceptance 谓词。fallback 快照由调用方渲染一次并
    共享给两臂，保证完全相同的初始状态。
    """
    accepts = acceptance_for(mode)
    best_scene = start_scene
    best_metric = fallback_metric
    best_rgb = fallback_rgb
    best_png = fallback_image_bytes
    candidates: list[_EvaluatedCandidate] = []
    stage_candidate_counts: dict[str, int] = {}
    final_candidate_index: int | None = None
    stages: tuple[tuple[OptimizationStage, str | None], ...] = (
        ("base", None),
        (
            "feature",
            next(
                (
                    feature.id
                    for feature in start_scene.object.features
                    if feature.type == "shadow"
                ),
                None,
            ),
        ),
    )
    for stage_name, feature_id in stages:
        if stage_name == "feature" and feature_id is None:
            continue
        remaining = STAGE_DRAW_BUDGET
        while remaining > 0:
            proposals = propose_min_scene_candidates(
                best_scene,
                stage=stage_name,
                feature_id=feature_id,
                remaining_draw_budget=min(STAGE_DRAW_BUDGET, remaining),
                batch_size=min(STAGE_DRAW_BUDGET, remaining),
            )
            ordered = interleave_proposal_directions(proposals)
            if not ordered:
                break
            evaluated_this_round = 0
            for planned in ordered:
                if remaining <= 0:
                    break
                rebased = rebase_candidate_proposal(best_scene, planned)
                if rebased is None:
                    continue
                metric, rgb, png = await _render_with_arrays(
                    prepared,
                    rebased.scene,
                    reference=reference,
                    metric_background=metric_background,
                )
                remaining -= 1
                evaluated_this_round += 1
                stage_candidate_counts[stage_name] = (
                    stage_candidate_counts.get(stage_name, 0) + 1
                )
                accepted = accepts(best_metric, metric)
                if accepted:
                    final_candidate_index = len(candidates)
                candidates.append(
                    _EvaluatedCandidate(
                        stage=stage_name,
                        parameter=rebased.parameter.path,
                        direction=rebased.direction,
                        before=float(rebased.before),
                        after=float(rebased.after),
                        metric=metric,
                        rgb=rgb,
                        image_bytes=png,
                        accepted=accepted,
                    )
                )
                if accepted:
                    best_scene = rebased.scene
                    best_metric = metric
                    best_rgb = rgb
                    best_png = png
            if evaluated_this_round == 0:
                break
    return _ArmRunResult(
        mode=mode,
        candidates=tuple(candidates),
        stage_candidate_counts=stage_candidate_counts,
        final_metric=best_metric,
        final_rgb=best_rgb,
        final_image_bytes=best_png,
        final_candidate_index=final_candidate_index,
    )


def _labeled_contact_sheet(
    column_labels: Sequence[str],
    rows: Sequence[tuple[str, Sequence[bytes]]],
) -> bytes:
    """生成多列 PNG contact sheet；首行为列标签，每行首格下方为行标签."""
    if not column_labels:
        raise ValueError("contact sheet 至少需要一列。")
    if not rows:
        raise ValueError("contact sheet 至少需要一行。")
    decoded: list[tuple[str, list[Image.Image]]] = []
    for row_label, payloads in rows:
        if len(payloads) != len(column_labels):
            raise ValueError("contact sheet 每行的图片数必须等于列数。")
        images: list[Image.Image] = []
        for payload in payloads:
            with Image.open(BytesIO(payload)) as opened_image:
                images.append(opened_image.convert("RGB").copy())
        decoded.append((row_label, images))
    cell_width = max(image.width for _, images in decoded for image in images)
    cell_height = max(image.height for _, images in decoded for image in images)
    header_height = 24
    label_height = 20
    sheet = Image.new(
        "RGB",
        (
            cell_width * len(column_labels),
            header_height + len(decoded) * (cell_height + label_height),
        ),
        (248, 248, 248),
    )
    draw = ImageDraw.Draw(sheet)
    for column, label in enumerate(column_labels):
        draw.text((column * cell_width + 6, 5), label, fill=(20, 20, 20))
    for row_index, (row_label, images) in enumerate(decoded):
        row_top = header_height + row_index * (cell_height + label_height)
        draw.text((6, row_top + 2), row_label, fill=(20, 20, 20))
        image_top = row_top + label_height
        for column, paste_image in enumerate(images):
            sheet.paste(paste_image, (column * cell_width, image_top))
    output = BytesIO()
    sheet.save(output, format="PNG")
    return output.getvalue()


def _write_results(
    output_dir: Path,
    report: Mapping[str, Any],
    artifacts: Sequence[tuple[str, bytes]],
) -> None:
    if output_dir.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output_dir}")
    render_dir = output_dir / "renders"
    render_dir.mkdir(parents=True)
    for filename, payload in artifacts:
        (render_dir / filename).write_bytes(payload)
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _candidate_trace(arm: _ArmRunResult) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "stage": item.stage,
            "parameter": item.parameter,
            "direction": item.direction,
            "before": item.before,
            "after": item.after,
            "total_loss": item.metric.total_loss,
            "geometry_mask_loss": item.metric.geometry_mask_loss,
            "accepted": item.accepted,
        }
        for index, item in enumerate(arm.candidates)
    ]


def _tile_summary(
    reference_rgb: np.ndarray,
    baseline_rgb: np.ndarray,
    final_rgb: np.ndarray,
) -> dict[str, Any]:
    regression = max_tile_regression(reference_rgb, baseline_rgb, final_rgb)
    grids: dict[str, Any] = {}
    for grid in TILE_GRIDS:
        baseline_mae = tile_mae_grid(reference_rgb, baseline_rgb, grid)
        final_mae = tile_mae_grid(reference_rgb, final_rgb, grid)
        delta = final_mae - baseline_mae
        grids[str(grid)] = {
            "max_regression": float(np.max(delta)),
            "max_improvement": float(np.min(delta)),
        }
    return {
        "grids": grids,
        "max_tile_regression_vs_fallback": regression.to_dict(),
    }


async def run_acceptance_live_ab(
    manifest_path: Path,
    baseline_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """在冻结 7 例上执行 acceptance live 单因素直接 A/B."""
    if output_dir.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output_dir}")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    supported = baseline.get("supported_cases")
    if not isinstance(supported, dict) or len(supported) != 7:
        raise ValueError("冻结 baseline 必须声明恰好 7 个 supported_cases。")
    suite = load_benchmark_suite(manifest_path)
    case_by_id = {case.case_id: case for case in suite.cases}
    missing = sorted(set(supported) - set(case_by_id))
    if missing:
        raise ValueError(f"manifest 缺少冻结支持案例：{missing}")
    tolerance = float(baseline["max_roi_loss_regression"])
    if tolerance != FIXED_GATE["material_roi_regression_tolerance"]:
        raise ValueError("冻结门槛与 baseline max_roi_loss_regression 不一致。")
    external_tolerance = float(
        FIXED_GATE["material_external_objective_regression_tolerance"]
    )

    case_results: list[dict[str, Any]] = []
    case_gates: dict[str, dict[str, Any]] = {}
    artifacts: list[tuple[str, bytes]] = []
    physical_draw_count = 0
    arm_physical_draws: dict[str, int] = {mode: 0 for mode in ARM_ORDER}
    column_labels = ["reference", "fallback"] + [ARM_LABELS[mode] for mode in ARM_ORDER]

    async with PlaywrightWebGL1Renderer() as renderer:
        for case_id in supported:
            case = case_by_id[case_id]
            reference_bytes = case.image_path.read_bytes()
            perception = perceive_min_target(reference_bytes)
            scene = perception.fallback_scene
            materialized = materialize_min_shader(scene)
            prepared = await renderer.prepare(
                materialized.webgl1_source,
                scene.canvas.width,
                scene.canvas.height,
                materialized.uniform_schema,
            )
            reference_rgb = perception.target_rgb
            # fallback 渲染一次，两臂共享完全相同的初始快照。
            fallback_metric, fallback_rgb, fallback_png = await _render_with_arrays(
                prepared,
                scene,
                reference=reference_rgb,
                metric_background=scene.canvas.background,
            )
            arm_results: dict[AcceptanceMode, _ArmRunResult] = {}
            for mode in ARM_ORDER:
                draws_before = prepared.render_count
                arm_results[mode] = await _run_live_arm(
                    prepared,
                    scene,
                    fallback_metric,
                    fallback_rgb,
                    fallback_png,
                    reference=reference_rgb,
                    metric_background=scene.canvas.background,
                    mode=mode,
                )
                arm_physical_draws[mode] += prepared.render_count - draws_before
            physical_draw_count += prepared.render_count
            await prepared.close()

            regions = case_regions(case)
            fallback_external = evaluate_render(
                reference_bytes,
                fallback_png,
                regions=regions,
            )
            arm_externals = {
                mode: evaluate_render(
                    reference_bytes,
                    arm_results[mode].final_image_bytes,
                    regions=regions,
                )
                for mode in ARM_ORDER
            }

            arms_payload: dict[str, Any] = {}
            for mode in ARM_ORDER:
                arm = arm_results[mode]
                external = arm_externals[mode]
                roi_deltas_vs_fallback = {
                    name: float(external.roi_loss_map[name] - value)
                    for name, value in fallback_external.roi_loss_map.items()
                }
                arms_payload[mode] = {
                    "logical_candidate_draw_count": len(arm.candidates),
                    "stage_candidate_counts": dict(
                        sorted(arm.stage_candidate_counts.items())
                    ),
                    "accepted_count": sum(
                        1 for item in arm.candidates if item.accepted
                    ),
                    "accepted_sequence": [
                        {
                            "index": index,
                            "stage": item.stage,
                            "parameter": item.parameter,
                            "direction": item.direction,
                            "total_loss": item.metric.total_loss,
                            "geometry_mask_loss": item.metric.geometry_mask_loss,
                        }
                        for index, item in enumerate(arm.candidates)
                        if item.accepted
                    ],
                    "candidate_trace": _candidate_trace(arm),
                    "final_candidate_index": arm.final_candidate_index,
                    "final_metrics": _component_dict(arm.final_metric),
                    "external_objective": external.to_dict(),
                    "roi_deltas_vs_fallback": roi_deltas_vs_fallback,
                    "material_roi_regressions_vs_fallback": (
                        material_roi_regressions(
                            fallback_external.roi_loss_map,
                            external.roi_loss_map,
                            tolerance=tolerance,
                        )
                    ),
                    "tile_summary_vs_fallback": _tile_summary(
                        reference_rgb,
                        fallback_rgb,
                        arm.final_rgb,
                    ),
                }

            g_metric = arm_results["geometry_first"].final_metric
            t_metric = arm_results["strict_total"].final_metric
            g_external = arm_externals["geometry_first"]
            t_external = arm_externals["strict_total"]
            t_minus_g_roi = {
                name: float(
                    t_external.roi_loss_map[name] - g_external.roi_loss_map[name]
                )
                for name in g_external.roi_loss_map
            }
            material_t_minus_g = material_roi_regressions(
                g_external.roi_loss_map,
                t_external.roi_loss_map,
                tolerance=tolerance,
            )
            material_g_minus_t = material_roi_regressions(
                t_external.roi_loss_map,
                g_external.roi_loss_map,
                tolerance=tolerance,
            )
            external_delta_t_minus_g = float(
                t_external.total_loss - g_external.total_loss
            )
            comparison = {
                "component_deltas_t_minus_g": _metric_delta(g_metric, t_metric),
                "external_objective_delta_t_minus_g": external_delta_t_minus_g,
                "roi_deltas_t_minus_g": t_minus_g_roi,
                "material_roi_regressions_t_minus_g": material_t_minus_g,
                "material_roi_regressions_g_minus_t": material_g_minus_t,
                "max_tile_regression_t_vs_g": max_tile_regression(
                    reference_rgb,
                    arm_results["geometry_first"].final_rgb,
                    arm_results["strict_total"].final_rgb,
                ).to_dict(),
                "watch_roi_deltas_t_minus_g": {
                    roi_id: t_minus_g_roi[roi_id]
                    for roi_id in WATCH_ROIS.get(case_id, ())
                },
            }
            gate = evaluate_case_gate(
                external_delta_t_minus_g=external_delta_t_minus_g,
                roi_regressions_t_minus_g=material_t_minus_g,
                roi_regressions_g_minus_t=material_g_minus_t,
                roi_tolerance=tolerance,
                external_tolerance=external_tolerance,
            )
            case_gates[case_id] = gate
            case_results.append(
                {
                    "case_id": case_id,
                    "reference_sha256": case.image_sha256,
                    "roi_purposes": {
                        roi.region_id: roi.purpose for roi in case.key_rois
                    },
                    "fallback_metrics": _component_dict(fallback_metric),
                    "fallback_external_objective": fallback_external.to_dict(),
                    "arms": arms_payload,
                    "comparison": comparison,
                    "gate": gate,
                    "review_artifacts": {
                        "reference": f"renders/{case_id}-reference.png",
                        "fallback": f"renders/{case_id}-fallback.png",
                        "arm_geometry_first": (
                            f"renders/{case_id}-arm-geometry-first.png"
                        ),
                        "arm_strict_total": (f"renders/{case_id}-arm-strict-total.png"),
                        "contact_sheet": (f"renders/{case_id}-contact-sheet.png"),
                    },
                }
            )
            artifacts.extend(
                (
                    (f"{case_id}-reference.png", reference_bytes),
                    (f"{case_id}-fallback.png", fallback_png),
                    (
                        f"{case_id}-arm-geometry-first.png",
                        arm_results["geometry_first"].final_image_bytes,
                    ),
                    (
                        f"{case_id}-arm-strict-total.png",
                        arm_results["strict_total"].final_image_bytes,
                    ),
                )
            )
            artifacts.append(
                (
                    f"{case_id}-contact-sheet.png",
                    _labeled_contact_sheet(
                        column_labels,
                        (
                            (
                                case_id,
                                (
                                    reference_bytes,
                                    fallback_png,
                                    arm_results["geometry_first"].final_image_bytes,
                                    arm_results["strict_total"].final_image_bytes,
                                ),
                            ),
                        ),
                    ),
                )
            )

    internal_aggregate = build_aggregate(
        {
            mode: {
                item["case_id"]: item["arms"][mode]["final_metrics"]["total_loss"]
                for item in case_results
            }
            for mode in ARM_ORDER
        }
    )
    external_aggregate = build_aggregate(
        {
            mode: {
                item["case_id"]: item["arms"][mode]["external_objective"]["total_loss"]
                for item in case_results
            }
            for mode in ARM_ORDER
        }
    )
    material_regression_summary = {
        mode: {
            item["case_id"]: item["arms"][mode]["material_roi_regressions_vs_fallback"]
            for item in case_results
            if item["arms"][mode]["material_roi_regressions_vs_fallback"]
        }
        for mode in ARM_ORDER
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "experiment_type": "offline_no_model_fixed_7_acceptance_live_ab",
        "inputs": {
            "manifest": str(manifest_path.resolve()),
            "manifest_sha256": _sha256(manifest_path),
            "baseline": str(baseline_path.resolve()),
            "baseline_sha256": _sha256(baseline_path),
            "supported_case_ids": list(supported),
        },
        "environment": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "numpy_version": np.__version__,
            "pillow_version": importlib.metadata.version("pillow"),
            "rng_seed": (
                "not_applicable_deterministic：候选生成无 RNG，"
                "Renderer 每次 draw 完整上传 typed uniform，无跨 draw 状态"
            ),
            "arm_execution_order": list(ARM_ORDER),
        },
        "fixed_contract": {
            "search_scope": "base_then_existing_shadow",
            "stage_draw_budget": STAGE_DRAW_BUDGET,
            "candidate_generation": "shared_generator_per_arm_live",
            "acceptance_arm_g": "geometry_mask_loss_then_total_loss_lexicographic",
            "acceptance_arm_t": "strict_total_loss_improvement",
            "shared_fallback_snapshot": (
                "fallback 每例渲染一次，两臂从完全相同的初始 scene 与渲染快照出发"
            ),
            "tile_grids": list(TILE_GRIDS),
            "roi_usage": "post_hoc_evaluation_only",
            "fixed_gate": FIXED_GATE,
            "model_call_count": 0,
            "experiment_classification": (
                "independent_no_model_diagnostic：不是 D058/D059 冻结 benchmark，"
                "不能使 F09 passing"
            ),
        },
        "summary": {
            "case_count": len(case_results),
            "physical_renderer_draw_count": physical_draw_count,
            "arm_physical_draw_counts": {
                **arm_physical_draws,
                "fallback_shared": physical_draw_count
                - sum(arm_physical_draws.values()),
            },
            "model_call_count": 0,
            "internal_total_loss_aggregate": internal_aggregate,
            "external_objective_aggregate": external_aggregate,
            "material_roi_regressions_vs_fallback": (material_regression_summary),
            "decision": evaluate_decision(
                case_gates,
                internal_aggregate=internal_aggregate,
                external_aggregate=external_aggregate,
                roi_tolerance=tolerance,
                external_tolerance=external_tolerance,
            ),
            "human_preference_status": "pending_manual_review",
        },
        "cases": case_results,
        "notes": [
            "两臂各自基于本臂 incumbent 实时生成/评估候选，轨迹分叉是 acceptance 的预期因果后果；未做任何 offline replay。",
            "判定门槛预先冻结：逐 case 外部 objective 与 ROI 实质回退容差均为 0.01（ROI 容差与冻结 baseline 一致），aggregate 双向判定，看到结果后未调整；逐 case gate 与整体 decision 均为机器可读字段。",
            "benchmark ROI 与 tile 摘要只用于事后评价，不进入任何 acceptance。",
            "contact sheet 代理看片只是工程分析，不构成独立人工盲评；本实验不是 D058/D059 冻结 benchmark，不能使 F09 passing。",
            "本实验不修改生产 scorer、权重、Prompt、Graph、预算、目标或 current_best 代码。",
        ],
    }
    _write_results(output_dir, report, artifacts)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="固定 7 例 acceptance live 单因素直接 A/B（无模型）。"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    """解析参数、执行 A/B 并输出单行摘要."""
    args = _parse_args()
    report = asyncio.run(
        run_acceptance_live_ab(args.manifest, args.baseline, args.output_dir)
    )
    summary = report["summary"]
    print(  # noqa: T201
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "case_count": summary["case_count"],
                "physical_renderer_draw_count": summary["physical_renderer_draw_count"],
                "model_call_count": summary["model_call_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
