"""固定 7 例多尺度 tile no-regression guard 的无模型离线 A/B 实验.

Arm A 复用当前“total_loss 严格改善即接受”；Arm B 在 Arm A 实跑的同一批
geometry-first 候选流上离线重放，只有 total_loss 严格改善且 4x4 与 8x8
全部非重叠 tile 的 reference RGB MAE 最大回退不超过显式容差时才接受。
guard 容差在 ``GUARD_TOLERANCES`` 中预先声明，benchmark ROI 不进入 guard
输入，只用于事后评价。本脚本不修改生产 scorer、Prompt、Graph、预算或目标。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
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
    from scripts.run_scene_mvp_scorer_calibration import (
        semantic_region_breakdown,
    )
else:
    from run_scene_mvp_run_diagnostics import (
        _raw_rgb_array as raw_rgb_array,
    )
    from run_scene_mvp_run_diagnostics import (
        interleave_proposal_directions,
    )
    from run_scene_mvp_scorer_calibration import _regions as case_regions
    from run_scene_mvp_scorer_calibration import (
        semantic_region_breakdown,
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
SCHEMA_VERSION = "scene_mvp_tile_guard_ab_v1"
STAGE_DRAW_BUDGET = 32
TILE_GRIDS = (4, 8)
# 预先声明的 guard 容差 sweep；看到结果后不得再调整。
GUARD_TOLERANCES = (0.0, 0.001, 0.0025, 0.005, 0.01)
METRIC_COMPONENTS = (
    "global_mae",
    "foreground_mae",
    "background_mae",
    "geometry_mask_loss",
    "edge_loss",
    "worst_tile_mae",
)
# 仅用于事后评价的发布阻塞 ROI，绝不进入 guard 输入。
WATCH_ROIS = {
    "ellipse_gradient": ("upper_color",),
    "arc_highlight_orb": ("highlight_upper_left",),
}
# 必须保留明确整体改善的对照案例。
PRESERVE_CASES = ("solid_circle", "color_lobes")


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _tolerance_key(tolerance: float) -> str:
    return f"{tolerance:.4f}"


def _tolerance_label(tolerance: float) -> str:
    return f"t={tolerance:g}"


def tile_mae_grid(
    reference: np.ndarray,
    rendered: np.ndarray,
    grid: int,
) -> np.ndarray:
    """返回 ``grid x grid`` 非重叠 tile 对 reference 的 RGB MAE 矩阵."""
    if (
        reference.shape != rendered.shape
        or reference.ndim != 3
        or reference.shape[2] != 3
    ):
        raise ValueError("tile MAE 要求相同尺寸的 RGB 图片。")
    if not isinstance(grid, int) or isinstance(grid, bool) or grid <= 0:
        raise ValueError("tile grid 必须是正整数。")
    if reference.shape[0] < grid or reference.shape[1] < grid:
        raise ValueError("图片宽高必须不小于 tile grid。")
    delta = np.mean(
        np.abs(reference.astype(np.float32) - rendered.astype(np.float32)),
        axis=2,
    )
    rows = np.array_split(np.arange(delta.shape[0]), grid)
    columns = np.array_split(np.arange(delta.shape[1]), grid)
    result = np.zeros((grid, grid), dtype=np.float64)
    for row_index, row in enumerate(rows):
        for column_index, column in enumerate(columns):
            result[row_index, column_index] = float(np.mean(delta[np.ix_(row, column)]))
    return result


@dataclass(frozen=True)
class TileRegression:
    """一次 guard 评价中的最大 tile 回退及其位置."""

    value: float
    grid: int
    row: int
    column: int

    def to_dict(self) -> dict[str, Any]:
        """返回稳定的 JSON 结构."""
        return {
            "value": self.value,
            "grid": self.grid,
            "row": self.row,
            "column": self.column,
        }


def max_tile_regression(
    reference: np.ndarray,
    incumbent: np.ndarray,
    candidate: np.ndarray,
    *,
    grids: Sequence[int] = TILE_GRIDS,
) -> TileRegression:
    """返回 candidate 相对 incumbent 在所有 tile 上的最大 MAE 回退.

    回退定义为 ``tile_mae(candidate) - tile_mae(incumbent)``，两侧都对
    reference 计算；负值表示该 tile 改善。平局时取较小 grid、较小
    row、较小 column。
    """
    if not grids:
        raise ValueError("tile guard 至少需要一个 grid。")
    best: TileRegression | None = None
    for grid in grids:
        incumbent_mae = tile_mae_grid(reference, incumbent, grid)
        candidate_mae = tile_mae_grid(reference, candidate, grid)
        regression = candidate_mae - incumbent_mae
        for row in range(grid):
            for column in range(grid):
                value = float(regression[row, column])
                if best is None or value > best.value:
                    best = TileRegression(
                        value=value,
                        grid=grid,
                        row=row,
                        column=column,
                    )
    if best is None:
        raise ValueError("tile guard 没有可评价的 tile。")
    return best


def guard_accepts(
    *,
    incumbent_total_loss: float,
    candidate_total_loss: float,
    regression: TileRegression,
    tolerance: float,
) -> bool:
    """Arm B acceptance：total_loss 严格改善且最大 tile 回退不超过容差."""
    if tolerance < 0.0:
        raise ValueError("guard 容差不能为负。")
    return candidate_total_loss < incumbent_total_loss and regression.value <= tolerance


@dataclass(frozen=True)
class GuardCandidate:
    """候选流中一次已评估候选的 guard 重放输入."""

    total_loss: float
    rgb: np.ndarray
    accepted_by_a: bool
    label: str


@dataclass(frozen=True)
class ArmStep:
    """Arm B 重放中单个候选的处理结果."""

    index: int
    accepted: bool
    reason: Literal["accepted", "total_loss_not_improved", "tile_guard_rejected"]
    regression: TileRegression


@dataclass(frozen=True)
class GuardArmResult:
    """一个容差臂的离线重放结果."""

    tolerance: float
    steps: tuple[ArmStep, ...]
    accepted_count: int
    rejected_total_loss_count: int
    rejected_tile_guard_count: int
    a_accepted_guard_rejected_count: int
    final_index: int | None
    max_blocked_regression: TileRegression | None
    final_regression_vs_baseline: TileRegression


def replay_guard_arm(
    reference: np.ndarray,
    baseline_rgb: np.ndarray,
    baseline_total_loss: float,
    candidates: Sequence[GuardCandidate],
    tolerance: float,
    *,
    grids: Sequence[int] = TILE_GRIDS,
) -> GuardArmResult:
    """在同一批候选流上按给定容差离线重放 Arm B acceptance.

    候选顺序与 Arm A 实跑时完全一致；只有 acceptance 谓词不同。
    重放不重新渲染、不重新生成候选，因此 draw 预算与 Arm A 相同。
    """
    best_rgb = baseline_rgb
    best_total = baseline_total_loss
    final_index: int | None = None
    steps: list[ArmStep] = []
    accepted_count = 0
    rejected_total_loss_count = 0
    rejected_tile_guard_count = 0
    a_accepted_guard_rejected_count = 0
    max_blocked: TileRegression | None = None
    for index, candidate in enumerate(candidates):
        regression = max_tile_regression(
            reference,
            best_rgb,
            candidate.rgb,
            grids=grids,
        )
        if guard_accepts(
            incumbent_total_loss=best_total,
            candidate_total_loss=candidate.total_loss,
            regression=regression,
            tolerance=tolerance,
        ):
            steps.append(
                ArmStep(
                    index=index,
                    accepted=True,
                    reason="accepted",
                    regression=regression,
                )
            )
            accepted_count += 1
        elif candidate.total_loss >= best_total:
            steps.append(
                ArmStep(
                    index=index,
                    accepted=False,
                    reason="total_loss_not_improved",
                    regression=regression,
                )
            )
            rejected_total_loss_count += 1
            continue
        else:
            steps.append(
                ArmStep(
                    index=index,
                    accepted=False,
                    reason="tile_guard_rejected",
                    regression=regression,
                )
            )
            rejected_tile_guard_count += 1
            if candidate.accepted_by_a:
                a_accepted_guard_rejected_count += 1
            if max_blocked is None or regression.value > max_blocked.value:
                max_blocked = regression
            continue
        best_rgb = candidate.rgb
        best_total = candidate.total_loss
        final_index = index
    return GuardArmResult(
        tolerance=tolerance,
        steps=tuple(steps),
        accepted_count=accepted_count,
        rejected_total_loss_count=rejected_total_loss_count,
        rejected_tile_guard_count=rejected_tile_guard_count,
        a_accepted_guard_rejected_count=a_accepted_guard_rejected_count,
        final_index=final_index,
        max_blocked_regression=max_blocked,
        final_regression_vs_baseline=max_tile_regression(
            reference,
            baseline_rgb,
            best_rgb,
            grids=grids,
        ),
    )


@dataclass(frozen=True)
class _EvaluatedCandidate:
    """实跑阶段记录的候选：guard 输入、指标与 PNG."""

    stage: str
    parameter: str
    direction: str
    metric: MinSceneMetricBreakdown
    rgb: np.ndarray
    image_bytes: bytes
    regression_vs_a_incumbent: TileRegression
    accepted_by_a: bool


@dataclass(frozen=True)
class _ArmAStreamResult:
    """单个案例的 fallback 快照、完整候选流与 Arm A 终态."""

    fallback_metric: MinSceneMetricBreakdown
    fallback_rgb: np.ndarray
    fallback_image_bytes: bytes
    candidates: tuple[_EvaluatedCandidate, ...]
    arm_a_final_metric: MinSceneMetricBreakdown
    arm_a_final_image_bytes: bytes
    arm_a_final_index: int | None


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
        raise RuntimeError(result.draw_error or "tile_guard_ab_renderer_failed")
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


async def _run_arm_a_stream(
    prepared: PreparedWebGL1Renderer,
    start_scene: MinScene,
    *,
    reference: np.ndarray,
    metric_background: tuple[float, float, float],
) -> _ArmAStreamResult:
    """实跑 geometry-first 候选流，Arm A 按 total_loss 严格改善接受.

    stage 顺序、方向交错、候选生成参数与每 stage 32 次 draw 预算与既有
    geometry 局部搜索完全一致；唯一变化是 acceptance 只看 total_loss。
    每个候选额外记录相对当前 Arm A incumbent 的最大 tile 回退。
    """
    best_scene = start_scene
    best_metric, best_rgb, best_png = await _render_with_arrays(
        prepared,
        start_scene,
        reference=reference,
        metric_background=metric_background,
    )
    fallback_metric = best_metric
    fallback_rgb = best_rgb
    fallback_png = best_png
    candidates: list[_EvaluatedCandidate] = []
    arm_a_final_index: int | None = None
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
                regression = max_tile_regression(reference, best_rgb, rgb)
                accepted = metric.total_loss < best_metric.total_loss
                if accepted:
                    arm_a_final_index = len(candidates)
                candidates.append(
                    _EvaluatedCandidate(
                        stage=stage_name,
                        parameter=rebased.parameter.path,
                        direction=rebased.direction,
                        metric=metric,
                        rgb=rgb,
                        image_bytes=png,
                        regression_vs_a_incumbent=regression,
                        accepted_by_a=accepted,
                    )
                )
                if accepted:
                    best_scene = rebased.scene
                    best_metric = metric
                    best_rgb = rgb
                    best_png = png
            if evaluated_this_round == 0:
                break
    return _ArmAStreamResult(
        fallback_metric=fallback_metric,
        fallback_rgb=fallback_rgb,
        fallback_image_bytes=fallback_png,
        candidates=tuple(candidates),
        arm_a_final_metric=best_metric,
        arm_a_final_image_bytes=best_png,
        arm_a_final_index=arm_a_final_index,
    )


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


def _summarize_thresholds(case_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """按容差聚合保护/误拒结论，全部为事后评价，不回灌 guard."""
    per_tolerance: dict[str, Any] = {}
    for tolerance in GUARD_TOLERANCES:
        key = _tolerance_key(tolerance)
        total_loss_deltas_vs_a: dict[str, float] = {}
        watch_roi_deltas_vs_a: dict[str, dict[str, float]] = {}
        external_deltas_vs_a: dict[str, float] = {}
        guard_rejected_total = 0
        a_accepted_guard_rejected_total = 0
        for case_result in case_results:
            case_id = str(case_result["case_id"])
            arm = case_result["arms_b"][key]
            comparison = arm["comparison_vs_arm_a"]
            total_loss_deltas_vs_a[case_id] = comparison["component_deltas"][
                "total_loss"
            ]
            external_deltas_vs_a[case_id] = comparison["external_objective_delta"]
            guard_rejected_total += arm["rejected_tile_guard_count"]
            a_accepted_guard_rejected_total += arm["a_accepted_guard_rejected_count"]
            watched = WATCH_ROIS.get(case_id, ())
            watch_roi_deltas_vs_a[case_id] = {
                roi_id: comparison["roi_deltas"][roi_id] for roi_id in watched
            }
        per_tolerance[key] = {
            "tolerance": tolerance,
            "guard_rejected_candidate_count": guard_rejected_total,
            "a_accepted_guard_rejected_count": a_accepted_guard_rejected_total,
            "total_loss_delta_vs_arm_a": total_loss_deltas_vs_a,
            "external_objective_delta_vs_arm_a": external_deltas_vs_a,
            "watch_roi_delta_vs_arm_a": watch_roi_deltas_vs_a,
            "preserve_case_total_loss_delta_vs_arm_a": {
                case_id: total_loss_deltas_vs_a[case_id] for case_id in PRESERVE_CASES
            },
        }
    return per_tolerance


async def run_tile_guard_ab(
    manifest_path: Path,
    baseline_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """在冻结 7 例上执行 tile no-regression guard 的无模型 A/B."""
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

    case_results: list[dict[str, Any]] = []
    artifacts: list[tuple[str, bytes]] = []
    physical_draw_count = 0
    column_labels = ["reference", "fallback", "A:total"] + [
        f"B:{_tolerance_label(tolerance)}" for tolerance in GUARD_TOLERANCES
    ]

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
            stream = await _run_arm_a_stream(
                prepared,
                scene,
                reference=perception.target_rgb,
                metric_background=scene.canvas.background,
            )
            physical_draw_count += prepared.render_count
            await prepared.close()

            reference_rgb = perception.target_rgb
            fallback_semantic = semantic_region_breakdown(
                reference_rgb,
                stream.fallback_rgb,
                scene,
                scene.canvas.background,
            )
            regions = case_regions(case)
            baseline_external = evaluate_render(
                reference_bytes,
                stream.fallback_image_bytes,
                regions=regions,
            )
            arm_a_external = evaluate_render(
                reference_bytes,
                stream.arm_a_final_image_bytes,
                regions=regions,
            )
            guard_candidates = [
                GuardCandidate(
                    total_loss=item.metric.total_loss,
                    rgb=item.rgb,
                    accepted_by_a=item.accepted_by_a,
                    label=f"{item.stage}:{item.parameter}:{item.direction}",
                )
                for item in stream.candidates
            ]

            arm_results: dict[str, Any] = {}
            arm_final_pngs: dict[float, bytes] = {}
            for tolerance in GUARD_TOLERANCES:
                replay = replay_guard_arm(
                    reference_rgb,
                    stream.fallback_rgb,
                    stream.fallback_metric.total_loss,
                    guard_candidates,
                    tolerance,
                )
                if replay.final_index is not None:
                    final_metric = stream.candidates[replay.final_index].metric
                    final_png = stream.candidates[replay.final_index].image_bytes
                else:
                    final_metric = stream.fallback_metric
                    final_png = stream.fallback_image_bytes
                arm_final_pngs[tolerance] = final_png
                external = evaluate_render(
                    reference_bytes,
                    final_png,
                    regions=regions,
                )
                arm_results[_tolerance_key(tolerance)] = {
                    "tolerance": tolerance,
                    "accepted_count": replay.accepted_count,
                    "rejected_total_loss_count": (replay.rejected_total_loss_count),
                    "rejected_tile_guard_count": (replay.rejected_tile_guard_count),
                    "a_accepted_guard_rejected_count": (
                        replay.a_accepted_guard_rejected_count
                    ),
                    "final_candidate_index": replay.final_index,
                    "final_metrics": _component_dict(final_metric),
                    "external_objective": external.to_dict(),
                    "max_blocked_tile_regression": (
                        replay.max_blocked_regression.to_dict()
                        if replay.max_blocked_regression is not None
                        else None
                    ),
                    "final_tile_regression_vs_baseline": (
                        replay.final_regression_vs_baseline.to_dict()
                    ),
                    "comparison_vs_arm_a": {
                        "component_deltas": _metric_delta(
                            stream.arm_a_final_metric,
                            final_metric,
                        ),
                        "external_objective_delta": (
                            external.total_loss - arm_a_external.total_loss
                        ),
                        "roi_deltas": {
                            name: external.roi_loss_map[name]
                            - arm_a_external.roi_loss_map[name]
                            for name in arm_a_external.roi_loss_map
                        },
                    },
                    "comparison_vs_baseline": {
                        "component_deltas": _metric_delta(
                            stream.fallback_metric,
                            final_metric,
                        ),
                        "external_objective_delta": (
                            external.total_loss - baseline_external.total_loss
                        ),
                        "roi_deltas": {
                            name: external.roi_loss_map[name]
                            - baseline_external.roi_loss_map[name]
                            for name in baseline_external.roi_loss_map
                        },
                    },
                }

            max_accepted = max(
                (
                    item.regression_vs_a_incumbent
                    for item in stream.candidates
                    if item.accepted_by_a
                ),
                key=lambda item: item.value,
                default=None,
            )
            case_results.append(
                {
                    "case_id": case_id,
                    "reference_sha256": case.image_sha256,
                    "roi_purposes": {
                        roi.region_id: roi.purpose for roi in case.key_rois
                    },
                    "fallback_metrics": _component_dict(stream.fallback_metric),
                    "fallback_semantic_regions": fallback_semantic,
                    "fallback_external_objective": baseline_external.to_dict(),
                    "stream": {
                        "evaluated_candidate_count": len(stream.candidates),
                        "accepted_by_a_count": sum(
                            1 for item in stream.candidates if item.accepted_by_a
                        ),
                        "candidates": [
                            {
                                "index": index,
                                "stage": item.stage,
                                "parameter": item.parameter,
                                "direction": item.direction,
                                "total_loss": item.metric.total_loss,
                                "geometry_mask_loss": (item.metric.geometry_mask_loss),
                                "regression_vs_a_incumbent": (
                                    item.regression_vs_a_incumbent.to_dict()
                                ),
                                "accepted_by_a": item.accepted_by_a,
                            }
                            for index, item in enumerate(stream.candidates)
                        ],
                    },
                    "arm_a": {
                        "final_candidate_index": stream.arm_a_final_index,
                        "final_metrics": _component_dict(stream.arm_a_final_metric),
                        "external_objective": arm_a_external.to_dict(),
                        "max_accepted_tile_regression": (
                            max_accepted.to_dict() if max_accepted is not None else None
                        ),
                    },
                    "arms_b": arm_results,
                    "review_artifacts": {
                        "reference": f"renders/{case_id}-reference.png",
                        "fallback": f"renders/{case_id}-fallback.png",
                        "arm_a": f"renders/{case_id}-arm-a.png",
                        "contact_sheet": (f"renders/{case_id}-contact-sheet.png"),
                    },
                }
            )
            artifacts.extend(
                (
                    (f"{case_id}-reference.png", reference_bytes),
                    (f"{case_id}-fallback.png", stream.fallback_image_bytes),
                    (f"{case_id}-arm-a.png", stream.arm_a_final_image_bytes),
                )
            )
            column_payloads: list[bytes] = [
                reference_bytes,
                stream.fallback_image_bytes,
                stream.arm_a_final_image_bytes,
            ]
            for tolerance in GUARD_TOLERANCES:
                key = _tolerance_key(tolerance).replace(".", "p")
                final_png = arm_final_pngs[tolerance]
                artifacts.append((f"{case_id}-arm-b-{key}.png", final_png))
                column_payloads.append(final_png)
            artifacts.append(
                (
                    f"{case_id}-contact-sheet.png",
                    _labeled_contact_sheet(
                        column_labels,
                        ((case_id, tuple(column_payloads)),),
                    ),
                )
            )

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "experiment_type": "offline_no_model_fixed_7_tile_guard_ab",
        "inputs": {
            "manifest": str(manifest_path.resolve()),
            "manifest_sha256": _sha256(manifest_path),
            "baseline": str(baseline_path.resolve()),
            "baseline_sha256": _sha256(baseline_path),
            "supported_case_ids": list(supported),
        },
        "fixed_contract": {
            "search_scope": "base_then_existing_shadow",
            "stage_draw_budget": STAGE_DRAW_BUDGET,
            "candidate_generation": "geometry_first_interleaved",
            "acceptance_arm_a": "strict_total_loss_improvement",
            "acceptance_arm_b": (
                "strict_total_loss_improvement_and_max_tile_regression_within_tolerance"
            ),
            "tile_grids": list(TILE_GRIDS),
            "guard_tolerances": list(GUARD_TOLERANCES),
            "roi_usage": "post_hoc_evaluation_only",
            "replay_note": (
                "Arm B 各容差臂在 Arm A 实跑的同一批候选流上离线重放，"
                "不重新渲染、不重新生成候选；live guard 搜索属于生产接入前的后续工作。"
            ),
            "model_call_count": 0,
        },
        "summary": {
            "case_count": len(case_results),
            "physical_renderer_draw_count": physical_draw_count,
            "model_call_count": 0,
            "per_tolerance": _summarize_thresholds(case_results),
            "human_preference_status": "pending_manual_review",
        },
        "cases": case_results,
        "notes": [
            "guard 输入只有 4x4 与 8x8 非重叠 tile 对 reference 的 RGB MAE，benchmark ROI 只用于事后评价。",
            "容差 sweep 预先声明为 0、0.001、0.0025、0.005、0.01，看到结果后不再调整。",
            "Arm B 离线重放与生产 live guard 搜索的差异：重放沿用 Arm A 轨迹生成的候选，生产接入前需要单独的 live 验证。",
            "自动代理不能替代人工偏好；contact sheet 必须独立审阅。",
            "本实验不修改生产 scorer、权重、Prompt、预算或停止目标。",
        ],
    }
    _write_results(output_dir, report, artifacts)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="固定 7 例多尺度 tile no-regression guard 无模型 A/B。"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    """解析参数、执行 A/B 并输出单行摘要."""
    args = _parse_args()
    report = asyncio.run(
        run_tile_guard_ab(args.manifest, args.baseline, args.output_dir)
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
