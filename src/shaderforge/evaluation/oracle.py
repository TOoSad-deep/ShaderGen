"""参考图与 WebGL 渲染图的 Basic Oracle."""

from __future__ import annotations

import math
from io import BytesIO

import numpy as np
from PIL import Image, UnidentifiedImageError

from shaderforge.analysis import RegionOfInterest, TargetMeasurements, measure_target
from shaderforge.evaluation.models import MetricWeights, ScoreBreakdownV1

METRIC_VERSION = "basic_oracle_v1"


class ImageSizeMismatchError(ValueError):
    """表示参考图和候选图尺寸不一致."""


def _decode_rgb(image_bytes: bytes) -> np.ndarray:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            rgba = np.asarray(image.convert("RGBA"), dtype=np.float64)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("无法解码评分图片。") from exc
    alpha = rgba[..., 3:4] / 255.0
    return (rgba[..., :3] * alpha + 255.0 * (1.0 - alpha)) / 255.0


def _edge_map(rgb: np.ndarray) -> np.ndarray:
    gray = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
    padded = np.pad(gray, 1, mode="edge")
    gx = (
        -padded[:-2, :-2]
        + padded[:-2, 2:]
        - 2.0 * padded[1:-1, :-2]
        + 2.0 * padded[1:-1, 2:]
        - padded[2:, :-2]
        + padded[2:, 2:]
    )
    gy = (
        -padded[:-2, :-2]
        - 2.0 * padded[:-2, 1:-1]
        - padded[:-2, 2:]
        + padded[2:, :-2]
        + 2.0 * padded[2:, 1:-1]
        + padded[2:, 2:]
    )
    return np.clip(np.hypot(gx, gy) / 4.0, 0.0, 1.0)


def _bbox_geometry_loss(
    reference: tuple[float, float, float, float] | None,
    candidate: tuple[float, float, float, float] | None,
) -> float | None:
    if reference is None or candidate is None:
        return None
    ref = np.asarray(reference, dtype=np.float64)
    cand = np.asarray(candidate, dtype=np.float64)
    edge_loss = float(np.abs(ref - cand).mean())
    ref_center = ((ref[0] + ref[2]) * 0.5, (ref[1] + ref[3]) * 0.5)
    cand_center = ((cand[0] + cand[2]) * 0.5, (cand[1] + cand[3]) * 0.5)
    center_loss = math.dist(ref_center, cand_center) / math.sqrt(2.0)
    ref_area = max(0.0, (ref[2] - ref[0]) * (ref[3] - ref[1]))
    cand_area = max(0.0, (cand[2] - cand[0]) * (cand[3] - cand[1]))
    area_loss = abs(ref_area - cand_area)
    return float(np.clip((edge_loss + center_loss + area_loss) / 3.0, 0.0, 1.0))


def _sample_rgb(rgb: np.ndarray, uv: tuple[float, float]) -> np.ndarray:
    height, width, _ = rgb.shape
    x = min(width - 1, max(0, round(uv[0] * (width - 1))))
    row = min(height - 1, max(0, round((1.0 - uv[1]) * (height - 1))))
    return np.asarray(rgb[row, x], dtype=np.float64)


def _roi_slices(
    bbox_uv: tuple[float, float, float, float], width: int, height: int
) -> tuple[slice, slice]:
    min_x, min_y, max_x, max_y = bbox_uv
    x0 = min(width - 1, max(0, math.floor(min_x * width)))
    x1 = min(width, max(x0 + 1, math.ceil(max_x * width)))
    row0 = min(height - 1, max(0, math.floor((1.0 - max_y) * height)))
    row1 = min(height, max(row0 + 1, math.ceil((1.0 - min_y) * height)))
    return slice(row0, row1), slice(x0, x1)


def _rmse(reference: np.ndarray, candidate: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(reference - candidate))))


def _effective_weights(
    configured: MetricWeights,
    *,
    geometry_available: bool,
    geometry_confidence: float,
    probes_available: bool,
    rois_available: bool,
) -> dict[str, float]:
    weights = {
        "global_rmse": configured.global_rmse,
        "global_mae": configured.global_mae,
        "edge": configured.edge,
        "geometry": configured.geometry * geometry_confidence
        if geometry_available
        else 0.0,
        "representative_pixels": configured.representative_pixels
        if probes_available
        else 0.0,
        "roi": configured.roi if rois_available else 0.0,
    }
    total = sum(weights.values())
    return {name: value / total for name, value in weights.items()}


def evaluate_render(
    reference_image: bytes,
    candidate_image: bytes,
    *,
    measurements: TargetMeasurements | None = None,
    regions: tuple[RegionOfInterest, ...] | None = None,
    weights: MetricWeights = MetricWeights(),
) -> ScoreBreakdownV1:
    """计算候选相对参考图的全局和局部 V1 损失."""
    reference = _decode_rgb(reference_image)
    candidate = _decode_rgb(candidate_image)
    if reference.shape != candidate.shape:
        raise ImageSizeMismatchError(
            f"参考图尺寸 {reference.shape[1]}x{reference.shape[0]} 与候选图尺寸 "
            f"{candidate.shape[1]}x{candidate.shape[0]} 不一致。"
        )
    target = measurements or measure_target(reference_image)
    candidate_measurements = measure_target(candidate_image)
    selected_regions = regions if regions is not None else target.roi_candidates

    global_rmse = _rmse(reference, candidate)
    global_mae = float(np.mean(np.abs(reference - candidate)))
    edge_loss = float(np.mean(np.abs(_edge_map(reference) - _edge_map(candidate))))
    geometry_loss = _bbox_geometry_loss(
        target.foreground_bbox_uv,
        candidate_measurements.foreground_bbox_uv,
    )

    probe_losses = [
        float(
            np.linalg.norm(
                np.asarray(probe.rgb, dtype=np.float64) / 255.0
                - _sample_rgb(candidate, probe.uv)
            )
            / math.sqrt(3.0)
        )
        for probe in target.representative_pixels
    ]
    representative_pixel_loss = float(np.mean(probe_losses)) if probe_losses else 0.0

    height, width, _ = reference.shape
    roi_losses: list[tuple[str, float]] = []
    protected_losses: list[tuple[str, float]] = []
    for region in selected_regions:
        rows, columns = _roi_slices(region.bbox_uv, width, height)
        loss = _rmse(reference[rows, columns], candidate[rows, columns])
        roi_losses.append((region.region_id, loss))
        if region.purpose == "protection":
            protected_losses.append((region.region_id, loss))

    diagnostics: list[str] = []
    if target.foreground_confidence < 0.5:
        diagnostics.append("foreground_mask_low_confidence")
    if geometry_loss is None:
        diagnostics.append("geometry_loss_unavailable")
    if not probe_losses:
        diagnostics.append("representative_pixels_unavailable")
    if not roi_losses:
        diagnostics.append("roi_losses_unavailable")

    effective = _effective_weights(
        weights,
        geometry_available=geometry_loss is not None,
        geometry_confidence=target.foreground_confidence,
        probes_available=bool(probe_losses),
        rois_available=bool(roi_losses),
    )
    mean_roi_loss = float(np.mean([loss for _, loss in roi_losses])) if roi_losses else 0.0
    total_loss = (
        effective["global_rmse"] * global_rmse
        + effective["global_mae"] * global_mae
        + effective["edge"] * edge_loss
        + effective["geometry"] * (geometry_loss or 0.0)
        + effective["representative_pixels"] * representative_pixel_loss
        + effective["roi"] * mean_roi_loss
    )

    return ScoreBreakdownV1(
        metric_version=METRIC_VERSION,
        total_loss=float(total_loss),
        global_rmse=global_rmse,
        global_mae=global_mae,
        edge_loss=edge_loss,
        geometry_loss=geometry_loss,
        representative_pixel_loss=representative_pixel_loss,
        roi_losses=tuple(roi_losses),
        protected_region_losses=tuple(protected_losses),
        effective_weights=tuple(effective.items()),
        diagnostics=tuple(diagnostics),
    )


def max_protected_regression(
    previous: ScoreBreakdownV1, candidate: ScoreBreakdownV1
) -> float:
    """返回候选相对上一最佳保护区域的最大 loss 增量."""
    previous_losses = previous.protected_region_loss_map
    candidate_losses = candidate.protected_region_loss_map
    shared = previous_losses.keys() & candidate_losses.keys()
    if not shared:
        return 0.0
    return max(0.0, max(candidate_losses[key] - previous_losses[key] for key in shared))
