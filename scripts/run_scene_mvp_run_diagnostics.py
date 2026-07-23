"""对一个已完成 scene_mvp run 执行无模型 geometry 与 maturity 诊断实验。."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import numpy as np

from shaderforge.evaluation import (
    MinSceneMetricBreakdown,
    evaluate_min_scene,
)
from shaderforge.generation import materialize_min_shader
from shaderforge.optimization import (
    CandidateProposal,
    OptimizationStage,
    propose_min_scene_candidates,
    rebase_candidate_proposal,
)
from shaderforge.perception import perceive_min_target
from shaderforge.rendering import (
    PlaywrightWebGL1Renderer,
    PreparedWebGL1Renderer,
)
from shaderforge.scene import Feature, MinScene

SCHEMA_VERSION = "scene_mvp_run_diagnostics_v1"
CURRENT_LOCAL_DRAW_BUDGET = 11
EXTENDED_LOCAL_DRAW_BUDGET = 31
GEOMETRY_THRESHOLD = 0.05
SOFT_GEOMETRY_LOW = 0.03
SOFT_GEOMETRY_HIGH = 0.10

StrategyName = Literal[
    "current_fixed_12",
    "interleaved_12",
    "current_fixed_32",
    "interleaved_32",
]


@dataclass(frozen=True)
class EvaluatedScene:
    """保存一次真实 Renderer draw 的 Scene、指标和可选 PNG。."""

    scene: MinScene
    metric: MinSceneMetricBreakdown
    image_bytes: bytes | None = None


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _raw_rgb_array(rgb_bytes: bytes, width: int, height: int) -> np.ndarray:
    expected = width * height * 3
    if len(rgb_bytes) != expected:
        raise ValueError(f"Renderer RGB 字节数错误：{len(rgb_bytes)}!={expected}。")
    return (
        np.frombuffer(rgb_bytes, dtype=np.uint8)
        .reshape((height, width, 3))
        .astype(np.float32)
        / 255.0
    )


def foreground_membership(
    rgb: np.ndarray,
    background: Sequence[float],
    *,
    threshold: float,
) -> np.ndarray:
    """按当前 evaluator 的 max-channel 距背景语义返回硬前景掩码。."""
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("foreground membership 要求 RGB 图片。")
    background_rgb = np.asarray(background, dtype=np.float32)
    if background_rgb.shape != (3,):
        raise ValueError("background 必须是 RGB 三元组。")
    if not math.isfinite(threshold) or threshold <= 0.0:
        raise ValueError("threshold 必须是有限正数。")
    distance = np.max(np.abs(rgb.astype(np.float32) - background_rgb), axis=2)
    return distance > threshold


def geometry_mask_loss(reference_mask: np.ndarray, candidate_mask: np.ndarray) -> float:
    """计算两个相同尺寸 bool mask 的 `1-IoU`。."""
    if reference_mask.shape != candidate_mask.shape or reference_mask.ndim != 2:
        raise ValueError("geometry mask 必须是相同尺寸的二维数组。")
    reference_bool = reference_mask.astype(bool)
    candidate_bool = candidate_mask.astype(bool)
    intersection = int(np.count_nonzero(reference_bool & candidate_bool))
    union = int(np.count_nonzero(reference_bool | candidate_bool))
    return 1.0 - float(intersection / max(1, union))


def soft_geometry_loss(
    reference: np.ndarray,
    rendered: np.ndarray,
    background: Sequence[float],
    *,
    low: float = SOFT_GEOMETRY_LOW,
    high: float = SOFT_GEOMETRY_HIGH,
) -> float:
    """以线性软隶属实验性重算 `1-soft-IoU`，不改变生产 scorer。."""
    if reference.shape != rendered.shape or reference.ndim != 3:
        raise ValueError("soft geometry 要求相同尺寸 RGB 图片。")
    if not math.isfinite(low) or not math.isfinite(high) or low < 0.0 or high <= low:
        raise ValueError("soft geometry 阈值必须满足 0<=low<high。")
    background_rgb = np.asarray(background, dtype=np.float32)
    reference_distance = np.max(
        np.abs(reference.astype(np.float32) - background_rgb), axis=2
    )
    rendered_distance = np.max(
        np.abs(rendered.astype(np.float32) - background_rgb), axis=2
    )
    reference_membership = np.clip(
        (reference_distance - low) / (high - low), 0.0, 1.0
    )
    rendered_membership = np.clip(
        (rendered_distance - low) / (high - low), 0.0, 1.0
    )
    intersection = float(np.sum(np.minimum(reference_membership, rendered_membership)))
    union = float(np.sum(np.maximum(reference_membership, rendered_membership)))
    return 1.0 - intersection / max(1.0e-12, union)


def _pixel_coordinates(width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    unit = float(min(width, height))
    x = (2.0 * (np.arange(width, dtype=np.float32) + 0.5) - width) / unit
    y = (height - 2.0 * (np.arange(height, dtype=np.float32) + 0.5)) / unit
    return np.meshgrid(x, y)


def circle_mask(
    width: int,
    height: int,
    *,
    center: tuple[float, float],
    radius: float,
) -> np.ndarray:
    """返回与 Scene 坐标一致的理想硬圆 mask。."""
    if width <= 0 or height <= 0 or radius <= 0.0:
        raise ValueError("circle mask 的尺寸和 radius 必须为正。")
    grid_x, grid_y = _pixel_coordinates(width, height)
    distance = np.sqrt(
        ((grid_x - center[0]) / radius) ** 2
        + ((grid_y - center[1]) / radius) ** 2
    )
    return distance < 1.0


def bounded_best_circle(
    reference_mask: np.ndarray,
    *,
    center: tuple[float, float],
    radius: float,
    center_offsets: Sequence[float],
    radius_offsets: Sequence[float],
) -> dict[str, Any]:
    """在显式有界网格内寻找理想硬圆的最低 geometry loss。."""
    height, width = reference_mask.shape
    best: dict[str, Any] | None = None
    evaluated = 0
    for offset_x in center_offsets:
        for offset_y in center_offsets:
            for radius_offset in radius_offsets:
                candidate_radius = radius + radius_offset
                if candidate_radius <= 0.0:
                    continue
                candidate_center = (center[0] + offset_x, center[1] + offset_y)
                loss = geometry_mask_loss(
                    reference_mask,
                    circle_mask(
                        width,
                        height,
                        center=candidate_center,
                        radius=candidate_radius,
                    ),
                )
                evaluated += 1
                record = {
                    "center": [float(value) for value in candidate_center],
                    "radius": float(candidate_radius),
                    "geometry_mask_loss": loss,
                }
                if best is None or loss < float(best["geometry_mask_loss"]):
                    best = record
    if best is None:
        raise ValueError("bounded circle search 没有合法候选。")
    return {
        "evaluated": evaluated,
        "center_offsets": [float(value) for value in center_offsets],
        "radius_offsets": [float(value) for value in radius_offsets],
        "best": best,
    }


def interleave_proposal_directions(
    proposals: Sequence[CandidateProposal],
) -> tuple[CandidateProposal, ...]:
    """把生产顺序的 decrease-all/increase-all 改为每字段双向交替。."""
    path_order: list[str] = []
    by_path: dict[str, dict[str, CandidateProposal]] = {}
    for proposal in proposals:
        path = proposal.parameter.path
        if path not in by_path:
            path_order.append(path)
            by_path[path] = {}
        by_path[path][proposal.direction] = proposal
    ordered: list[CandidateProposal] = []
    for path in path_order:
        for direction in ("decrease", "increase"):
            matching_proposal = by_path[path].get(direction)
            if matching_proposal is not None:
                ordered.append(matching_proposal)
    return tuple(ordered)


def _feature_fixture(
    scene: MinScene,
    *,
    fixture_name: str,
    axes: tuple[float, float],
    intensity: float,
) -> MinScene:
    feature = Feature(
        id=f"diagnostic_{fixture_name}",
        type="gaussian_lobe",
        center=(-0.44, 0.79),
        axes=axes,
        color=(1.0, 0.98, 0.99),
        intensity=intensity,
    )
    return scene.model_copy(
        update={
            "object": scene.object.model_copy(
                update={"features": (*scene.object.features, feature)}
            )
        }
    )


async def _render_scene(
    prepared: PreparedWebGL1Renderer,
    scene: MinScene,
    *,
    reference: np.ndarray,
    metric_background: tuple[float, float, float],
    capture_png: bool = False,
) -> EvaluatedScene:
    materialized = materialize_min_shader(scene)
    result = await prepared.render_uniforms(
        materialized.uniform_values,
        capture_png=capture_png,
    )
    if not result.success or result.rgb_bytes is None:
        raise RuntimeError(result.draw_error or "diagnostic_renderer_failed")
    rendered = _raw_rgb_array(
        result.rgb_bytes,
        scene.canvas.width,
        scene.canvas.height,
    )
    return EvaluatedScene(
        scene=scene,
        metric=evaluate_min_scene(reference, rendered, metric_background),
        image_bytes=result.image_bytes,
    )


def _strategy_settings(
    name: StrategyName,
) -> tuple[int, bool]:
    if name == "current_fixed_12":
        return CURRENT_LOCAL_DRAW_BUDGET, False
    if name == "interleaved_12":
        return CURRENT_LOCAL_DRAW_BUDGET, True
    if name == "current_fixed_32":
        return EXTENDED_LOCAL_DRAW_BUDGET, False
    return EXTENDED_LOCAL_DRAW_BUDGET, True


async def _run_maturity_strategy(
    prepared: PreparedWebGL1Renderer,
    raw: EvaluatedScene,
    *,
    strategy: StrategyName,
    feature_id: str,
    reference: np.ndarray,
    metric_background: tuple[float, float, float],
) -> tuple[dict[str, Any], EvaluatedScene]:
    local_budget, interleaved = _strategy_settings(strategy)
    best = raw
    remaining = local_budget
    accepted_steps: list[dict[str, Any]] = []
    round_index = 0

    while remaining > 0:
        round_index += 1
        proposals = propose_min_scene_candidates(
            best.scene,
            stage="feature",
            feature_id=feature_id,
            remaining_draw_budget=16,
            batch_size=16,
        )
        ordered = (
            interleave_proposal_directions(proposals) if interleaved else proposals
        )
        if not ordered:
            break
        evaluated_this_round = 0
        for planned in ordered:
            if remaining <= 0:
                break
            rebased = rebase_candidate_proposal(best.scene, planned)
            if rebased is None:
                continue
            candidate = await _render_scene(
                prepared,
                rebased.scene,
                reference=reference,
                metric_background=metric_background,
            )
            remaining -= 1
            evaluated_this_round += 1
            if candidate.metric.total_loss < best.metric.total_loss:
                best = candidate
                accepted_steps.append(
                    {
                        "round": round_index,
                        "parameter": rebased.parameter.path,
                        "direction": rebased.direction,
                        "before": rebased.before,
                        "after": rebased.after,
                        "loss": candidate.metric.total_loss,
                    }
                )
        if evaluated_this_round == 0:
            break

    final = await _render_scene(
        prepared,
        best.scene,
        reference=reference,
        metric_background=metric_background,
        capture_png=True,
    )
    local_draws = local_budget - remaining
    return (
        {
            "strategy": strategy,
            "raw_loss": raw.metric.total_loss,
            "matured_loss": final.metric.total_loss,
            "loss_delta": final.metric.total_loss - raw.metric.total_loss,
            "local_draw_count": local_draws,
            "total_candidate_draw_count": 1 + local_draws,
            "accepted_step_count": len(accepted_steps),
            "accepted_steps": accepted_steps,
            "final_metrics": final.metric.to_dict(),
            "final_feature": next(
                feature.model_dump(mode="json")
                for feature in final.scene.object.features
                if feature.id == feature_id
            ),
        },
        final,
    )


async def _run_geometry_local_search(
    prepared: PreparedWebGL1Renderer,
    start: EvaluatedScene,
    *,
    reference: np.ndarray,
    metric_background: tuple[float, float, float],
) -> tuple[dict[str, Any], EvaluatedScene]:
    """在真实模板上对 base 与现有 shadow 各做 32 次局部 geometry 搜索。."""
    best = start
    accepted_steps: list[dict[str, Any]] = []
    stages: tuple[tuple[OptimizationStage, str | None], ...] = (
        ("base", None),
        (
            "feature",
            next(
                (
                    feature.id
                    for feature in start.scene.object.features
                    if feature.type == "shadow"
                ),
                None,
            ),
        ),
    )
    logical_draw_count = 0
    for stage_name, feature_id in stages:
        if stage_name == "feature" and feature_id is None:
            continue
        remaining = 32
        round_index = 0
        while remaining > 0:
            round_index += 1
            proposals = propose_min_scene_candidates(
                best.scene,
                stage=stage_name,
                feature_id=feature_id,
                remaining_draw_budget=min(32, remaining),
                batch_size=min(32, remaining),
            )
            ordered = interleave_proposal_directions(proposals)
            if not ordered:
                break
            evaluated_this_round = 0
            for planned in ordered:
                if remaining <= 0:
                    break
                rebased = rebase_candidate_proposal(best.scene, planned)
                if rebased is None:
                    continue
                candidate = await _render_scene(
                    prepared,
                    rebased.scene,
                    reference=reference,
                    metric_background=metric_background,
                )
                remaining -= 1
                logical_draw_count += 1
                evaluated_this_round += 1
                candidate_key = (
                    candidate.metric.geometry_mask_loss,
                    candidate.metric.total_loss,
                )
                best_key = (best.metric.geometry_mask_loss, best.metric.total_loss)
                if candidate_key < best_key:
                    best = candidate
                    accepted_steps.append(
                        {
                            "stage": stage_name,
                            "round": round_index,
                            "parameter": rebased.parameter.path,
                            "direction": rebased.direction,
                            "before": rebased.before,
                            "after": rebased.after,
                            "geometry_mask_loss": (
                                candidate.metric.geometry_mask_loss
                            ),
                            "total_loss": candidate.metric.total_loss,
                        }
                    )
            if evaluated_this_round == 0:
                break
    final = await _render_scene(
        prepared,
        best.scene,
        reference=reference,
        metric_background=metric_background,
        capture_png=True,
    )
    return (
        {
            "scope": "base_then_existing_shadow",
            "objective": "geometry_mask_loss_then_total_loss",
            "logical_draw_count": logical_draw_count,
            "initial_metrics": start.metric.to_dict(),
            "best_metrics": final.metric.to_dict(),
            "accepted_step_count": len(accepted_steps),
            "accepted_steps": accepted_steps,
            "best_scene": final.scene.model_dump(mode="json"),
        },
        final,
    )


def _geometry_probe(
    reference: np.ndarray,
    rendered: np.ndarray,
    metric_background: Sequence[float],
    scene: MinScene,
) -> dict[str, Any]:
    threshold_losses: dict[str, float] = {}
    for threshold in (0.03, 0.05, 0.07, 0.10):
        threshold_losses[f"{threshold:.2f}"] = geometry_mask_loss(
            foreground_membership(
                reference,
                metric_background,
                threshold=threshold,
            ),
            foreground_membership(
                rendered,
                metric_background,
                threshold=threshold,
            ),
        )
    reference_mask = foreground_membership(
        reference,
        metric_background,
        threshold=GEOMETRY_THRESHOLD,
    )
    rendered_mask = foreground_membership(
        rendered,
        metric_background,
        threshold=GEOMETRY_THRESHOLD,
    )
    radius = 0.5 * sum(scene.object.primitive.axes)
    fitted_circle = circle_mask(
        scene.canvas.width,
        scene.canvas.height,
        center=scene.object.primitive.center,
        radius=radius,
    )
    reference_pixels = max(1, int(np.count_nonzero(reference_mask)))
    outside_circle = reference_mask & ~fitted_circle
    false_negative = reference_mask & ~rendered_mask
    false_positive = rendered_mask & ~reference_mask
    offsets = tuple(round(value, 9) for value in np.arange(-0.08, 0.081, 0.02))
    radius_offsets = tuple(
        round(value, 9) for value in np.arange(-0.12, 0.121, 0.02)
    )
    return {
        "production_threshold": GEOMETRY_THRESHOLD,
        "hard_threshold_losses": threshold_losses,
        "soft_geometry": {
            "low": SOFT_GEOMETRY_LOW,
            "high": SOFT_GEOMETRY_HIGH,
            "loss": soft_geometry_loss(
                reference,
                rendered,
                metric_background,
            ),
        },
        "reference_foreground_ratio": float(np.mean(reference_mask)),
        "rendered_foreground_ratio": float(np.mean(rendered_mask)),
        "reference_foreground_outside_final_circle_ratio": float(
            np.count_nonzero(outside_circle) / reference_pixels
        ),
        "false_negative_image_ratio": float(np.mean(false_negative)),
        "false_positive_image_ratio": float(np.mean(false_positive)),
        "bounded_ideal_circle_search": bounded_best_circle(
            reference_mask,
            center=scene.object.primitive.center,
            radius=radius,
            center_offsets=offsets,
            radius_offsets=radius_offsets,
        ),
    }


def _write_results(
    output_dir: Path,
    report: dict[str, Any],
    renders: Iterable[tuple[str, bytes]],
) -> None:
    if output_dir.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output_dir}")
    render_items = tuple(renders)
    output_dir.mkdir(parents=True)
    render_dir = output_dir / "renders"
    render_dir.mkdir()
    for filename, image_bytes in render_items:
        (render_dir / filename).write_bytes(image_bytes)
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def run_diagnostics(run_dir: Path, output_dir: Path) -> dict[str, Any]:
    """执行固定 run 的两组无模型诊断并写入独立结果目录。."""
    manifest_path = run_dir / "final/manifest.json"
    metrics_path = run_dir / "final/metrics.json"
    reference_path = run_dir / "input/reference.png"
    final_render_path = run_dir / "final/render.png"
    required = (manifest_path, metrics_path, reference_path, final_render_path)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"run 产物不完整：{missing}")
    if output_dir.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    scene = MinScene.model_validate(manifest["scene"])
    perception = perceive_min_target(reference_path.read_bytes())
    reference = perception.target_rgb
    metric_background = perception.fallback_scene.canvas.background

    materialized = materialize_min_shader(scene)
    maturity_results: list[dict[str, Any]] = []
    render_artifacts: list[tuple[str, bytes]] = []

    async with PlaywrightWebGL1Renderer() as renderer:
        prepared = await renderer.prepare(
            materialized.webgl1_source,
            scene.canvas.width,
            scene.canvas.height,
            materialized.uniform_schema,
        )
        reproduced = await _render_scene(
            prepared,
            scene,
            reference=reference,
            metric_background=metric_background,
            capture_png=True,
        )
        if not math.isclose(
            reproduced.metric.total_loss,
            float(recorded_metrics["total_loss"]),
            abs_tol=1.0e-9,
        ):
            raise RuntimeError(
                "final scene 复算 loss 与记录不一致："
                f"{reproduced.metric.total_loss}!={recorded_metrics['total_loss']}"
            )
        if reproduced.image_bytes is None:
            raise RuntimeError("final scene 复算未返回 PNG。")
        render_artifacts.append(("reproduced-final.png", reproduced.image_bytes))
        final_rgb_result = await prepared.render_uniforms(
            materialized.uniform_values,
            capture_png=False,
        )
        if not final_rgb_result.success or final_rgb_result.rgb_bytes is None:
            raise RuntimeError(
                final_rgb_result.draw_error or "final_rgb_diagnostic_render_failed"
            )
        rendered_rgb = _raw_rgb_array(
            final_rgb_result.rgb_bytes,
            scene.canvas.width,
            scene.canvas.height,
        )
        geometry = _geometry_probe(
            reference,
            rendered_rgb,
            metric_background,
            scene,
        )
        actual_geometry_search, actual_geometry_final = (
            await _run_geometry_local_search(
                prepared,
                reproduced,
                reference=reference,
                metric_background=metric_background,
            )
        )
        geometry["bounded_actual_template_search"] = actual_geometry_search
        if actual_geometry_final.image_bytes is None:
            raise RuntimeError("actual template geometry search 未返回 PNG。")
        render_artifacts.append(
            (
                "geometry-bounded-actual-template.png",
                actual_geometry_final.image_bytes,
            )
        )

        fixtures = (
            (
                "underfit_top_left",
                _feature_fixture(
                    scene,
                    fixture_name="underfit_top_left",
                    axes=(0.20, 0.05),
                    intensity=0.60,
                ),
            ),
            (
                "overfit_top_left",
                _feature_fixture(
                    scene,
                    fixture_name="overfit_top_left",
                    axes=(0.35, 0.15),
                    intensity=1.20,
                ),
            ),
        )
        for fixture_name, fixture_scene in fixtures:
            feature_id = f"diagnostic_{fixture_name}"
            raw = await _render_scene(
                prepared,
                fixture_scene,
                reference=reference,
                metric_background=metric_background,
            )
            strategies: list[dict[str, Any]] = []
            for strategy in (
                "current_fixed_12",
                "interleaved_12",
                "current_fixed_32",
                "interleaved_32",
            ):
                result, final = await _run_maturity_strategy(
                    prepared,
                    raw,
                    strategy=strategy,
                    feature_id=feature_id,
                    reference=reference,
                    metric_background=metric_background,
                )
                strategies.append(result)
                if final.image_bytes is None:
                    raise RuntimeError(f"{fixture_name}/{strategy} 未返回 PNG。")
                render_artifacts.append(
                    (f"{fixture_name}-{strategy}.png", final.image_bytes)
                )
            maturity_results.append(
                {
                    "fixture": fixture_name,
                    "raw_feature": next(
                        feature.model_dump(mode="json")
                        for feature in fixture_scene.object.features
                        if feature.id == feature_id
                    ),
                    "anchor_loss": reproduced.metric.total_loss,
                    "raw_loss": raw.metric.total_loss,
                    "strategies": strategies,
                }
            )
        physical_render_count = prepared.render_count
        await prepared.close()

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "experiment_type": "offline_no_model",
        "run_id": manifest["run_id"],
        "project_id": manifest["project_id"],
        "run_classification": manifest["run_classification"],
        "experiment_id": manifest["experiment_id"],
        "config_fingerprint": manifest["config_fingerprint"],
        "inputs": {
            "run_dir": str(run_dir.resolve()),
            "manifest_sha256": _sha256(manifest_path),
            "metrics_sha256": _sha256(metrics_path),
            "reference_sha256": _sha256(reference_path),
            "final_render_sha256": _sha256(final_render_path),
        },
        "fixed_contract": {
            "scene_schema_version": scene.schema_version,
            "template_version": manifest["template_version"],
            "metric_version": recorded_metrics["metric_version"],
            "metric_background": [float(value) for value in metric_background],
            "production_target_loss": recorded_metrics["target_loss"],
            "production_target_mae": recorded_metrics["target_mae"],
        },
        "baseline": {
            "recorded_metrics": recorded_metrics,
            "reproduced_metrics": reproduced.metric.to_dict(),
        },
        "geometry_probe": geometry,
        "maturity_probe": maturity_results,
        "physical_renderer_draw_count": physical_render_count,
        "notes": [
            "bounded ideal circle search 是显式小网格诊断，不是全局最优证明。",
            "soft geometry 只用于离线消融，不改变生产 scorer。",
            "maturity fixture 是固定合成 Patch，不是本 run 被拒 Patch 的精确重放。",
        ],
    }
    _write_results(output_dir, report, render_artifacts)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="对已完成 scene_mvp run 执行无模型 geometry/maturity 诊断。"
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    """解析 CLI 参数并运行诊断。."""
    args = _parse_args()
    report = asyncio.run(run_diagnostics(args.run_dir, args.output_dir))
    print(  # noqa: T201
        json.dumps(
            {
                "run_id": report["run_id"],
                "output_dir": str(args.output_dir.resolve()),
                "physical_renderer_draw_count": report[
                    "physical_renderer_draw_count"
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
