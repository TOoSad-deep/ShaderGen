"""scene_mvp 优化热路径使用的 RGB MAE 与通用区域 objective。."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import numpy as np
from PIL import Image, UnidentifiedImageError

MIN_SCENE_METRIC_VERSION = "min_scene_composite_v3"
MIN_SCENE_METRIC_WEIGHTS = {
    "global_mae": 0.20,
    "foreground_mae": 0.25,
    "background_mae": 0.15,
    "geometry_mask_loss": 0.15,
    "edge_loss": 0.10,
    "worst_tile_mae": 0.15,
}
MIN_SCENE_TILE_GRID = 4
MIN_SCENE_WORST_TILE_COUNT = 2


def decode_rgb(image_bytes: bytes) -> np.ndarray:
    """把图片解码为 `[0, 1]` float32 RGB。."""
    try:
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("无法解码用于 MAE 的图片。") from exc
    return np.asarray(image, dtype=np.float32) / 255.0


def rgb_mae(reference: np.ndarray, rendered: np.ndarray) -> float:
    """计算相同尺寸 RGB 的全局 MAE，尺寸不符时 fail closed。."""
    if (
        reference.shape != rendered.shape
        or reference.ndim != 3
        or reference.shape[2] != 3
    ):
        raise ValueError("MAE 两侧必须是相同尺寸的 RGB 图片。")
    return float(
        np.mean(np.abs(reference.astype(np.float32) - rendered.astype(np.float32)))
    )


@dataclass(frozen=True)
class MinSceneMetricBreakdown:
    """扩展 scene 的通用区域损失。."""

    total_loss: float
    global_mae: float
    foreground_mae: float
    background_mae: float
    geometry_mask_loss: float
    edge_loss: float
    worst_tile_mae: float
    foreground_ratio: float
    background_ratio: float
    effective_weights: dict[str, float]
    metric_version: str = MIN_SCENE_METRIC_VERSION

    def to_dict(self) -> dict[str, Any]:
        """返回可进入 Artifact/API 的稳定 JSON 结构。."""
        return {
            "metric_version": self.metric_version,
            "total_loss": self.total_loss,
            "global_mae": self.global_mae,
            "foreground_mae": self.foreground_mae,
            "background_mae": self.background_mae,
            "geometry_mask_loss": self.geometry_mask_loss,
            "edge_loss": self.edge_loss,
            "worst_tile_mae": self.worst_tile_mae,
            "foreground_ratio": self.foreground_ratio,
            "background_ratio": self.background_ratio,
            "tile_grid": MIN_SCENE_TILE_GRID,
            "worst_tile_count": MIN_SCENE_WORST_TILE_COUNT,
            "effective_weights": self.effective_weights,
        }


def _dilate(mask: np.ndarray, radius: int = 2) -> np.ndarray:
    result = mask.copy()
    for _ in range(radius):
        expanded = result.copy()
        expanded[1:, :] |= result[:-1, :]
        expanded[:-1, :] |= result[1:, :]
        expanded[:, 1:] |= result[:, :-1]
        expanded[:, :-1] |= result[:, 1:]
        result = expanded
    return result


def _edge_strength(rgb: np.ndarray) -> np.ndarray:
    luminance = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
    dx = np.zeros_like(luminance)
    dy = np.zeros_like(luminance)
    dx[:, 1:] = np.abs(luminance[:, 1:] - luminance[:, :-1])
    dy[1:, :] = np.abs(luminance[1:, :] - luminance[:-1, :])
    return np.maximum(dx, dy)


def _worst_tile_mae(delta: np.ndarray) -> float:
    rows = np.array_split(np.arange(delta.shape[0]), MIN_SCENE_TILE_GRID)
    columns = np.array_split(np.arange(delta.shape[1]), MIN_SCENE_TILE_GRID)
    losses = [
        float(np.mean(delta[np.ix_(row, column)])) for row in rows for column in columns
    ]
    count = min(MIN_SCENE_WORST_TILE_COUNT, len(losses))
    return float(np.mean(sorted(losses, reverse=True)[:count]))


def summarize_spatial_residual(
    reference: np.ndarray,
    rendered: np.ndarray,
) -> dict[str, Any]:
    """返回固定 4x4 网格中误差最大的两个 tile 及有符号偏差.

    所有 bias 均使用 ``rendered - reference``：正值表示候选过亮或对应
    RGB 通道过高，负值表示候选过暗或对应通道过低。该摘要只增加诊断
    事实，不参与复合 loss 计算。
    """
    rgb_mae(reference, rendered)
    if (
        reference.shape[0] < MIN_SCENE_TILE_GRID
        or reference.shape[1] < MIN_SCENE_TILE_GRID
    ):
        raise ValueError("空间残差摘要要求图片宽高至少为 4。")
    reference_rgb = reference.astype(np.float32)
    rendered_rgb = rendered.astype(np.float32)
    signed_rgb = rendered_rgb - reference_rgb
    absolute_delta = np.mean(np.abs(signed_rgb), axis=2)
    luminance_weights = np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)
    signed_luminance = np.sum(signed_rgb * luminance_weights, axis=2)
    rows = np.array_split(np.arange(reference.shape[0]), MIN_SCENE_TILE_GRID)
    columns = np.array_split(np.arange(reference.shape[1]), MIN_SCENE_TILE_GRID)
    tiles: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        for column_index, column in enumerate(columns):
            index = np.ix_(row, column)
            tile_rgb_bias = np.mean(signed_rgb[index], axis=(0, 1))
            x0 = float(column[0] / reference.shape[1])
            x1 = float((column[-1] + 1) / reference.shape[1])
            y0 = float(1.0 - (row[-1] + 1) / reference.shape[0])
            y1 = float(1.0 - row[0] / reference.shape[0])
            tiles.append(
                {
                    "row": row_index,
                    "column": column_index,
                    "uv_bbox": {
                        "x": x0,
                        "y": y0,
                        "width": x1 - x0,
                        "height": y1 - y0,
                    },
                    "mae": float(np.mean(absolute_delta[index])),
                    "signed_luminance_bias": float(np.mean(signed_luminance[index])),
                    "signed_rgb_bias": [float(value) for value in tile_rgb_bias],
                }
            )
    tiles.sort(key=lambda item: (-float(item["mae"]), item["row"], item["column"]))
    return {
        "residual_version": "spatial_residual_v2",
        "coordinate_system": "webgl_uv_bottom_left",
        "source_row_origin": "image_top",
        "tile_grid": MIN_SCENE_TILE_GRID,
        "worst_tile_count": MIN_SCENE_WORST_TILE_COUNT,
        "bias_convention": "rendered_minus_reference",
        "worst_tiles": tiles[:MIN_SCENE_WORST_TILE_COUNT],
    }


def dominant_metric_component(
    metric: MinSceneMetricBreakdown | Mapping[str, Any],
) -> str:
    """按有效权重后的 loss 贡献返回主导指标，平局使用固定指标顺序。."""
    payload = (
        metric.to_dict() if isinstance(metric, MinSceneMetricBreakdown) else metric
    )
    weights_value = payload.get("effective_weights", MIN_SCENE_METRIC_WEIGHTS)
    weights = (
        weights_value
        if isinstance(weights_value, Mapping)
        else MIN_SCENE_METRIC_WEIGHTS
    )
    contributions: list[tuple[float, int, str]] = []
    for index, name in enumerate(MIN_SCENE_METRIC_WEIGHTS):
        value = payload.get(name)
        weight = weights.get(name, MIN_SCENE_METRIC_WEIGHTS[name])
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not np.isfinite(value)
            or not np.isfinite(weight)
        ):
            continue
        contributions.append((float(value) * float(weight), -index, name))
    if not contributions:
        raise ValueError("无法从指标摘要确定主导 loss 分量。")
    return max(contributions)[2]


def evaluate_min_scene(
    reference: np.ndarray,
    rendered: np.ndarray,
    background: tuple[float, float, float] | list[float],
) -> MinSceneMetricBreakdown:
    """评分整图、主体、保护背景、几何、边缘与固定网格最坏区域。."""
    global_mae = rgb_mae(reference, rendered)
    background_rgb = np.asarray(background, dtype=np.float32)
    if background_rgb.shape != (3,):
        raise ValueError("scene_mvp 背景颜色必须是 RGB 三元组。")
    reference_rgb = reference.astype(np.float32)
    rendered_rgb = rendered.astype(np.float32)
    delta = np.mean(np.abs(reference_rgb - rendered_rgb), axis=2)
    reference_distance = np.max(np.abs(reference_rgb - background_rgb), axis=2)
    rendered_distance = np.max(np.abs(rendered_rgb - background_rgb), axis=2)
    detected_foreground = reference_distance > 0.05
    candidate_foreground = rendered_distance > 0.05
    minimum_pixels = max(16, reference.shape[0] * reference.shape[1] // 100)
    supported = int(np.count_nonzero(detected_foreground)) >= minimum_pixels
    foreground = (
        detected_foreground if supported else np.ones(reference.shape[:2], dtype=bool)
    )
    protected_background = (
        ~_dilate(detected_foreground, 2)
        if supported
        else np.zeros_like(detected_foreground)
    )

    foreground_mae = float(np.mean(delta[foreground]))
    background_mae = (
        float(np.mean(delta[protected_background]))
        if np.any(protected_background)
        else global_mae
    )
    if supported:
        intersection = int(np.count_nonzero(detected_foreground & candidate_foreground))
        union = int(np.count_nonzero(detected_foreground | candidate_foreground))
        geometry_mask_loss = 1.0 - float(intersection / max(1, union))
        edge_loss = float(
            np.mean(
                np.abs(_edge_strength(reference_rgb) - _edge_strength(rendered_rgb))
            )
        )
    else:
        geometry_mask_loss = 0.0
        edge_loss = global_mae
    worst_tile_mae = _worst_tile_mae(delta)

    values = {
        "global_mae": global_mae,
        "foreground_mae": foreground_mae,
        "background_mae": background_mae,
        "geometry_mask_loss": geometry_mask_loss,
        "edge_loss": edge_loss,
        "worst_tile_mae": worst_tile_mae,
    }
    active = set(values)
    if not supported:
        active -= {"background_mae", "geometry_mask_loss"}
    denominator = sum(MIN_SCENE_METRIC_WEIGHTS[name] for name in active)
    effective_weights = {
        name: (MIN_SCENE_METRIC_WEIGHTS[name] / denominator if name in active else 0.0)
        for name in values
    }
    total_loss = sum(values[name] * effective_weights[name] for name in values)
    pixel_count = float(reference.shape[0] * reference.shape[1])
    return MinSceneMetricBreakdown(
        total_loss=float(total_loss),
        global_mae=global_mae,
        foreground_mae=foreground_mae,
        background_mae=background_mae,
        geometry_mask_loss=geometry_mask_loss,
        edge_loss=edge_loss,
        worst_tile_mae=worst_tile_mae,
        foreground_ratio=float(np.count_nonzero(detected_foreground) / pixel_count),
        background_ratio=float(np.count_nonzero(protected_background) / pixel_count),
        effective_weights=effective_weights,
    )


__all__ = [
    "MIN_SCENE_METRIC_VERSION",
    "MIN_SCENE_METRIC_WEIGHTS",
    "MinSceneMetricBreakdown",
    "decode_rgb",
    "dominant_metric_component",
    "evaluate_min_scene",
    "rgb_mae",
    "summarize_spatial_residual",
]
