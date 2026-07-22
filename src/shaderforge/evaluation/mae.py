"""scene_mvp 优化热路径使用的 RGB MAE 与通用区域 objective。."""

from __future__ import annotations

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
    "evaluate_min_scene",
    "rgb_mae",
]
