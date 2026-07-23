"""在固定 7 例上执行 scene_mvp scorer 的无模型方向一致性校准。."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from PIL import Image, ImageDraw

if TYPE_CHECKING or __package__:
    from scripts.run_scene_mvp_run_diagnostics import (
        _render_scene as render_scene,
    )
    from scripts.run_scene_mvp_run_diagnostics import (
        _run_geometry_local_search as run_geometry_local_search,
    )
    from scripts.run_scene_mvp_run_diagnostics import foreground_membership
else:
    from run_scene_mvp_run_diagnostics import (
        _render_scene as render_scene,
    )
    from run_scene_mvp_run_diagnostics import (
        _run_geometry_local_search as run_geometry_local_search,
    )
    from run_scene_mvp_run_diagnostics import foreground_membership
from shaderforge.analysis import RegionOfInterest
from shaderforge.benchmark import load_benchmark_suite
from shaderforge.evaluation import decode_rgb, evaluate_render
from shaderforge.generation import materialize_min_shader
from shaderforge.perception import perceive_min_target
from shaderforge.rendering import PlaywrightWebGL1Renderer
from shaderforge.scene import MinScene

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/png_to_shader_v1/manifest.yaml"
DEFAULT_BASELINE = (
    ROOT / "benchmarks/png_to_shader_v1/scene_mvp_fixed_template_v3_baseline.json"
)
SCHEMA_VERSION = "scene_mvp_scorer_calibration_v1"
GEOMETRY_THRESHOLD = 0.05
SUBJECT_INTERIOR_DISTANCE = 0.90
SUBJECT_EDGE_DISTANCE = 1.10
DIRECTION_EPSILON = 1.0e-6
MATERIAL_PIXEL_REGRESSION = 0.005


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _pixel_coordinates(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    unit = float(min(width, height))
    x = (2.0 * (np.arange(width, dtype=np.float32) + 0.5) - width) / unit
    y = (height - 2.0 * (np.arange(height, dtype=np.float32) + 0.5)) / unit
    return np.meshgrid(x, y)


def primitive_normalized_distance(scene: MinScene) -> np.ndarray:
    """返回每个像素到 fallback 主体椭圆中心的归一化距离。."""
    width = scene.canvas.width
    height = scene.canvas.height
    center_x, center_y = scene.object.primitive.center
    axis_x, axis_y = scene.object.primitive.axes
    if width <= 0 or height <= 0 or axis_x <= 0.0 or axis_y <= 0.0:
        raise ValueError("Scene 尺寸和主体 axes 必须为正。")
    grid_x, grid_y = _pixel_coordinates(width, height)
    return np.sqrt(
        ((grid_x - center_x) / axis_x) ** 2
        + ((grid_y - center_y) / axis_y) ** 2
    )


def _masked_mae(delta: np.ndarray, mask: np.ndarray) -> float | None:
    if delta.shape != mask.shape or delta.ndim != 2:
        raise ValueError("区域 MAE 要求相同尺寸的二维 delta/mask。")
    return float(np.mean(delta[mask])) if np.any(mask) else None


def _masked_iou_loss(
    reference_mask: np.ndarray,
    candidate_mask: np.ndarray,
) -> float:
    if reference_mask.shape != candidate_mask.shape or reference_mask.ndim != 2:
        raise ValueError("区域 IoU 要求相同尺寸的二维 mask。")
    reference_bool = reference_mask.astype(bool)
    candidate_bool = candidate_mask.astype(bool)
    union = int(np.count_nonzero(reference_bool | candidate_bool))
    if union == 0:
        return 0.0
    intersection = int(np.count_nonzero(reference_bool & candidate_bool))
    return 1.0 - float(intersection / union)


def semantic_region_breakdown(
    reference: np.ndarray,
    rendered: np.ndarray,
    scene: MinScene,
    metric_background: Sequence[float],
) -> dict[str, float | None]:
    """按主体内部、边缘和主体外效果拆分像素及 mask 误差.

    主体区域使用确定性感知 fallback 的解析椭圆作为代理，不宣称它是
    reference 的人工真值。外部效果只统计 reference/candidate 中距背景
    超过生产阈值、且位于解析主体边缘之外的像素。
    """
    if reference.shape != rendered.shape or reference.ndim != 3:
        raise ValueError("语义区域诊断要求相同尺寸 RGB 图片。")
    if reference.shape[:2] != (scene.canvas.height, scene.canvas.width):
        raise ValueError("语义区域诊断图片尺寸必须与 Scene 一致。")
    delta = np.mean(
        np.abs(reference.astype(np.float32) - rendered.astype(np.float32)),
        axis=2,
    )
    distance = primitive_normalized_distance(scene)
    interior = distance <= SUBJECT_INTERIOR_DISTANCE
    edge_band = (distance > SUBJECT_INTERIOR_DISTANCE) & (
        distance <= SUBJECT_EDGE_DISTANCE
    )
    outside = distance > SUBJECT_EDGE_DISTANCE
    reference_foreground = foreground_membership(
        reference,
        metric_background,
        threshold=GEOMETRY_THRESHOLD,
    )
    candidate_foreground = foreground_membership(
        rendered,
        metric_background,
        threshold=GEOMETRY_THRESHOLD,
    )
    reference_exterior_effect = reference_foreground & outside
    candidate_exterior_effect = candidate_foreground & outside
    protected_background = ~reference_foreground & outside
    subject_support = ~outside
    pixel_count = float(reference.shape[0] * reference.shape[1])
    return {
        "subject_interior_mae": _masked_mae(delta, interior),
        "subject_edge_band_mae": _masked_mae(delta, edge_band),
        "reference_exterior_effect_mae": _masked_mae(
            delta, reference_exterior_effect
        ),
        "protected_background_mae": _masked_mae(delta, protected_background),
        "subject_foreground_iou_loss": _masked_iou_loss(
            reference_foreground & subject_support,
            candidate_foreground & subject_support,
        ),
        "exterior_effect_iou_loss": _masked_iou_loss(
            reference_exterior_effect,
            candidate_exterior_effect,
        ),
        "reference_exterior_effect_ratio": float(
            np.count_nonzero(reference_exterior_effect) / pixel_count
        ),
        "candidate_exterior_effect_ratio": float(
            np.count_nonzero(candidate_exterior_effect) / pixel_count
        ),
    }


def _numeric_delta(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    fields: Sequence[str],
) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for field in fields:
        before_value = before.get(field)
        after_value = after.get(field)
        if isinstance(before_value, (int, float)) and isinstance(
            after_value, (int, float)
        ):
            result[field] = float(after_value) - float(before_value)
        else:
            result[field] = None
    return result


def classify_direction_conflict(
    baseline_metrics: Mapping[str, Any],
    optimized_metrics: Mapping[str, Any],
    baseline_semantic: Mapping[str, Any],
    optimized_semantic: Mapping[str, Any],
    roi_deltas: Mapping[str, float],
    *,
    epsilon: float = DIRECTION_EPSILON,
    material_pixel_regression: float = MATERIAL_PIXEL_REGRESSION,
    material_roi_regression: float = 0.01,
) -> dict[str, Any]:
    """判断 composite 改善是否伴随像素/关键区域代理恶化。."""
    metric_deltas = _numeric_delta(
        baseline_metrics,
        optimized_metrics,
        (
            "total_loss",
            "global_mae",
            "foreground_mae",
            "background_mae",
            "geometry_mask_loss",
            "edge_loss",
            "worst_tile_mae",
        ),
    )
    semantic_deltas = _numeric_delta(
        baseline_semantic,
        optimized_semantic,
        (
            "subject_interior_mae",
            "subject_edge_band_mae",
            "reference_exterior_effect_mae",
            "protected_background_mae",
            "subject_foreground_iou_loss",
            "exterior_effect_iou_loss",
        ),
    )
    worsened_visual_proxies: list[str] = []
    for name in (
        "global_mae",
        "foreground_mae",
        "edge_loss",
        "worst_tile_mae",
    ):
        delta = metric_deltas[name]
        if delta is not None and delta > epsilon:
            worsened_visual_proxies.append(name)
    for name in (
        "subject_interior_mae",
        "subject_edge_band_mae",
        "reference_exterior_effect_mae",
        "protected_background_mae",
        "exterior_effect_iou_loss",
    ):
        delta = semantic_deltas[name]
        if delta is not None and delta > epsilon:
            worsened_visual_proxies.append(name)
    worsened_rois = sorted(name for name, delta in roi_deltas.items() if delta > epsilon)
    worsened_visual_proxies.extend(f"roi:{name}" for name in worsened_rois)
    materially_worsened_visual_proxies: list[str] = []
    for name in (
        "global_mae",
        "foreground_mae",
        "edge_loss",
        "worst_tile_mae",
    ):
        delta = metric_deltas[name]
        if delta is not None and delta > material_pixel_regression:
            materially_worsened_visual_proxies.append(name)
    for name in (
        "subject_interior_mae",
        "subject_edge_band_mae",
        "reference_exterior_effect_mae",
        "protected_background_mae",
    ):
        delta = semantic_deltas[name]
        if delta is not None and delta > material_pixel_regression:
            materially_worsened_visual_proxies.append(name)
    materially_worsened_visual_proxies.extend(
        f"roi:{name}"
        for name, delta in sorted(roi_deltas.items())
        if delta > material_roi_regression
    )
    total_delta = metric_deltas["total_loss"]
    composite_improved = total_delta is not None and total_delta < -epsilon
    geometry_delta = metric_deltas["geometry_mask_loss"]
    return {
        "metric_deltas": metric_deltas,
        "semantic_deltas": semantic_deltas,
        "roi_deltas": dict(sorted(roi_deltas.items())),
        "composite_improved": composite_improved,
        "geometry_improved": (
            geometry_delta is not None and geometry_delta < -epsilon
        ),
        "worsened_visual_proxies": worsened_visual_proxies,
        "worsened_roi_ids": worsened_rois,
        "materially_worsened_visual_proxies": (
            materially_worsened_visual_proxies
        ),
        "objective_direction_conflict": bool(
            composite_improved and worsened_visual_proxies
        ),
        "material_objective_direction_conflict": bool(
            composite_improved and materially_worsened_visual_proxies
        ),
    }


def _regions(case: Any) -> tuple[RegionOfInterest, ...]:
    return tuple(
        RegionOfInterest(
            region_id=roi.region_id,
            bbox_uv=roi.bbox_uv,
            purpose=roi.purpose,
            confidence=1.0,
        )
        for roi in case.key_rois
    )


def _contact_sheet(
    rows: Sequence[tuple[str, bytes, bytes, bytes]],
) -> bytes:
    if not rows:
        raise ValueError("contact sheet 至少需要一行。")
    decoded: list[tuple[str, Image.Image, Image.Image, Image.Image]] = []
    for case_id, reference_bytes, baseline_bytes, optimized_bytes in rows:
        images: list[Image.Image] = []
        for payload in (reference_bytes, baseline_bytes, optimized_bytes):
            with Image.open(BytesIO(payload)) as opened_image:
                images.append(opened_image.convert("RGB").copy())
        decoded.append((case_id, images[0], images[1], images[2]))
    cell_width = max(
        cell_image.width for _, *images in decoded for cell_image in images
    )
    cell_height = max(
        cell_image.height for _, *images in decoded for cell_image in images
    )
    header_height = 24
    label_height = 20
    sheet = Image.new(
        "RGB",
        (
            cell_width * 3,
            header_height + len(decoded) * (cell_height + label_height),
        ),
        (248, 248, 248),
    )
    draw = ImageDraw.Draw(sheet)
    for column, label in enumerate(("reference", "fallback", "geometry-first")):
        draw.text((column * cell_width + 6, 5), label, fill=(20, 20, 20))
    for row_index, (
        case_id,
        reference_image,
        baseline_image,
        optimized_image,
    ) in enumerate(decoded):
        row_top = header_height + row_index * (cell_height + label_height)
        draw.text((6, row_top + 2), case_id, fill=(20, 20, 20))
        image_top = row_top + label_height
        for column, paste_image in enumerate(
            (reference_image, baseline_image, optimized_image)
        ):
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


async def run_calibration(
    manifest_path: Path,
    baseline_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """在 baseline 声明的 7 个支持案例上运行 geometry 方向校准。."""
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
    sheet_rows: list[tuple[str, bytes, bytes, bytes]] = []
    physical_draw_count = 0

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
            baseline_render = await render_scene(
                prepared,
                scene,
                reference=perception.target_rgb,
                metric_background=scene.canvas.background,
                capture_png=True,
            )
            search_result, optimized_render = await run_geometry_local_search(
                prepared,
                baseline_render,
                reference=perception.target_rgb,
                metric_background=scene.canvas.background,
            )
            physical_draw_count += prepared.render_count
            await prepared.close()
            if (
                baseline_render.image_bytes is None
                or optimized_render.image_bytes is None
            ):
                raise RuntimeError(f"{case_id} 诊断渲染没有返回 PNG。")

            baseline_rgb = decode_rgb(baseline_render.image_bytes)
            optimized_rgb = decode_rgb(optimized_render.image_bytes)
            baseline_semantic = semantic_region_breakdown(
                perception.target_rgb,
                baseline_rgb,
                scene,
                scene.canvas.background,
            )
            optimized_semantic = semantic_region_breakdown(
                perception.target_rgb,
                optimized_rgb,
                optimized_render.scene,
                scene.canvas.background,
            )
            regions = _regions(case)
            baseline_external = evaluate_render(
                reference_bytes,
                baseline_render.image_bytes,
                regions=regions,
            )
            optimized_external = evaluate_render(
                reference_bytes,
                optimized_render.image_bytes,
                regions=regions,
            )
            roi_deltas = {
                name: optimized_external.roi_loss_map[name] - value
                for name, value in baseline_external.roi_loss_map.items()
            }
            direction = classify_direction_conflict(
                baseline_render.metric.to_dict(),
                optimized_render.metric.to_dict(),
                baseline_semantic,
                optimized_semantic,
                roi_deltas,
                material_roi_regression=float(
                    baseline["max_roi_loss_regression"]
                ),
            )
            case_results.append(
                {
                    "case_id": case_id,
                    "reference_sha256": case.image_sha256,
                    "expected_foreground_bbox_uv": list(
                        case.expected_foreground_bbox_uv
                    ),
                    "roi_purposes": {
                        roi.region_id: roi.purpose for roi in case.key_rois
                    },
                    "perception_summary": perception.summary,
                    "fallback_scene": scene.model_dump(mode="json"),
                    "geometry_optimized_scene": optimized_render.scene.model_dump(
                        mode="json"
                    ),
                    "baseline_metrics": baseline_render.metric.to_dict(),
                    "geometry_optimized_metrics": (
                        optimized_render.metric.to_dict()
                    ),
                    "baseline_semantic_regions": baseline_semantic,
                    "geometry_optimized_semantic_regions": optimized_semantic,
                    "baseline_external_objective": baseline_external.to_dict(),
                    "geometry_optimized_external_objective": (
                        optimized_external.to_dict()
                    ),
                    "direction_analysis": direction,
                    "search": search_result,
                    "review_artifacts": {
                        "reference": f"renders/{case_id}-reference.png",
                        "fallback": f"renders/{case_id}-fallback.png",
                        "geometry_first": (
                            f"renders/{case_id}-geometry-first.png"
                        ),
                    },
                }
            )
            artifacts.extend(
                (
                    (f"{case_id}-reference.png", reference_bytes),
                    (f"{case_id}-fallback.png", baseline_render.image_bytes),
                    (
                        f"{case_id}-geometry-first.png",
                        optimized_render.image_bytes,
                    ),
                )
            )
            sheet_rows.append(
                (
                    case_id,
                    reference_bytes,
                    baseline_render.image_bytes,
                    optimized_render.image_bytes,
                )
            )

    conflict_case_ids = [
        item["case_id"]
        for item in case_results
        if item["direction_analysis"]["objective_direction_conflict"]
    ]
    composite_improved_case_ids = [
        item["case_id"]
        for item in case_results
        if item["direction_analysis"]["composite_improved"]
    ]
    geometry_improved_case_ids = [
        item["case_id"]
        for item in case_results
        if item["direction_analysis"]["geometry_improved"]
    ]
    material_conflict_case_ids = [
        item["case_id"]
        for item in case_results
        if item["direction_analysis"]["material_objective_direction_conflict"]
    ]
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "experiment_type": "offline_no_model_fixed_7_scorer_calibration",
        "inputs": {
            "manifest": str(manifest_path.resolve()),
            "manifest_sha256": _sha256(manifest_path),
            "baseline": str(baseline_path.resolve()),
            "baseline_sha256": _sha256(baseline_path),
            "supported_case_ids": list(supported),
        },
        "fixed_contract": {
            "geometry_threshold": GEOMETRY_THRESHOLD,
            "subject_interior_distance": SUBJECT_INTERIOR_DISTANCE,
            "subject_edge_distance": SUBJECT_EDGE_DISTANCE,
            "search_scope": "base_then_existing_shadow",
            "search_objective": "geometry_mask_loss_then_total_loss",
            "logical_draw_budget_per_case": 64,
            "direction_epsilon": DIRECTION_EPSILON,
            "material_pixel_regression": MATERIAL_PIXEL_REGRESSION,
            "material_roi_regression": baseline["max_roi_loss_regression"],
            "model_call_count": 0,
        },
        "summary": {
            "case_count": len(case_results),
            "physical_renderer_draw_count": physical_draw_count,
            "geometry_improved_case_count": len(geometry_improved_case_ids),
            "geometry_improved_case_ids": geometry_improved_case_ids,
            "composite_improved_case_count": len(composite_improved_case_ids),
            "composite_improved_case_ids": composite_improved_case_ids,
            "objective_direction_conflict_case_count": len(conflict_case_ids),
            "objective_direction_conflict_case_ids": conflict_case_ids,
            "material_objective_direction_conflict_case_count": len(
                material_conflict_case_ids
            ),
            "material_objective_direction_conflict_case_ids": (
                material_conflict_case_ids
            ),
            "human_preference_status": "pending_manual_review",
        },
        "cases": case_results,
        "review_artifacts": {
            "contact_sheet": "renders/contact-sheet.png",
            "column_order": ["reference", "fallback", "geometry-first"],
        },
        "notes": [
            "主体 interior/edge/exterior 使用 fallback 解析椭圆作为诊断代理，不是人工真值。",
            "direction conflict 只表示 composite 改善同时至少一个像素、语义区域或关键 ROI 代理恶化。",
            "自动代理不能替代人工偏好；contact sheet 必须独立审阅。",
            "本实验不修改生产 scorer、权重、Prompt、预算或停止目标。",
        ],
    }
    artifacts.append(("contact-sheet.png", _contact_sheet(sheet_rows)))
    _write_results(output_dir, report, artifacts)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="在固定 7 例上执行 scene_mvp scorer 无模型方向校准。"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    """解析参数、执行校准并输出单行摘要。."""
    args = _parse_args()
    report = asyncio.run(
        run_calibration(args.manifest, args.baseline, args.output_dir)
    )
    summary = report["summary"]
    print(  # noqa: T201
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "case_count": summary["case_count"],
                "physical_renderer_draw_count": summary[
                    "physical_renderer_draw_count"
                ],
                "objective_direction_conflict_case_count": summary[
                    "objective_direction_conflict_case_count"
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
