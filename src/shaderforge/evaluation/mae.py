"""最小优化热路径使用的 RGB MAE。."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

import numpy as np
from PIL import Image, UnidentifiedImageError


def decode_rgb(image_bytes: bytes) -> np.ndarray:
    """把图片解码为 `[0, 1]` float32 RGB。."""
    try:
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("无法解码用于 MAE 的图片。") from exc
    return np.asarray(image, dtype=np.float32) / 255.0


def rgb_mae(reference: np.ndarray, rendered: np.ndarray) -> float:
    """计算相同尺寸 RGB 的全局 MAE，尺寸不符时 fail closed。."""
    if reference.shape != rendered.shape or reference.ndim != 3 or reference.shape[2] != 3:
        raise ValueError("MAE 两侧必须是相同尺寸的 RGB 图片。")
    return float(np.mean(np.abs(reference.astype(np.float32) - rendered.astype(np.float32))))


@dataclass(frozen=True)
class MinSceneMetricBreakdown:
    """最小 scene 的整图与局部确定性损失。."""

    total_loss: float
    global_mae: float
    foreground_mae: float
    highlight_mae: float
    shadow_mae: float
    foreground_ratio: float
    highlight_ratio: float
    shadow_ratio: float
    metric_version: str = "min_scene_composite_v2"

    def to_dict(self) -> dict[str, Any]:
        """返回可直接写入 Artifact/API 的稳定结构。."""
        return {
            "metric_version": self.metric_version,
            "total_loss": self.total_loss,
            "global_mae": self.global_mae,
            "foreground_mae": self.foreground_mae,
            "highlight_mae": self.highlight_mae,
            "shadow_mae": self.shadow_mae,
            "foreground_ratio": self.foreground_ratio,
            "highlight_ratio": self.highlight_ratio,
            "shadow_ratio": self.shadow_ratio,
            "effective_weights": {
                "global_mae": 0.35,
                "foreground_mae": 0.35,
                "highlight_mae": 0.15,
                "shadow_mae": 0.15,
            },
        }


def _masked_mae(delta: np.ndarray, mask: np.ndarray, fallback: np.ndarray) -> float:
    selected = mask if np.any(mask) else fallback
    return float(np.mean(delta[selected]))


def evaluate_min_scene(
    reference: np.ndarray,
    rendered: np.ndarray,
    background: tuple[float, float, float] | list[float],
) -> MinSceneMetricBreakdown:
    """评分整图、前景及前景内亮/暗区域，降低背景面积对主体误差的稀释。."""
    global_mae = rgb_mae(reference, rendered)
    background_rgb = np.asarray(background, dtype=np.float32)
    if background_rgb.shape != (3,):
        raise ValueError("scene_mvp 背景颜色必须是 RGB 三元组。")
    reference_rgb = reference.astype(np.float32)
    rendered_rgb = rendered.astype(np.float32)
    delta = np.mean(np.abs(reference_rgb - rendered_rgb), axis=2)
    distance = np.max(np.abs(reference_rgb - background_rgb), axis=2)
    detected_foreground = distance > 0.05
    minimum_pixels = max(16, reference.shape[0] * reference.shape[1] // 100)
    foreground = (
        detected_foreground
        if int(np.count_nonzero(detected_foreground)) >= minimum_pixels
        else np.ones(reference.shape[:2], dtype=bool)
    )
    luminance = (
        reference_rgb[..., 0] * 0.2126
        + reference_rgb[..., 1] * 0.7152
        + reference_rgb[..., 2] * 0.0722
    )
    foreground_luminance = luminance[foreground]
    low, high = np.quantile(foreground_luminance, (0.2, 0.8))
    highlight = foreground & (luminance >= high)
    shadow = foreground & (luminance <= low)
    foreground_mae = _masked_mae(delta, foreground, foreground)
    highlight_mae = _masked_mae(delta, highlight, foreground)
    shadow_mae = _masked_mae(delta, shadow, foreground)
    total_loss = (
        global_mae * 0.35
        + foreground_mae * 0.35
        + highlight_mae * 0.15
        + shadow_mae * 0.15
    )
    pixel_count = float(reference.shape[0] * reference.shape[1])
    return MinSceneMetricBreakdown(
        total_loss=float(total_loss),
        global_mae=global_mae,
        foreground_mae=foreground_mae,
        highlight_mae=highlight_mae,
        shadow_mae=shadow_mae,
        foreground_ratio=float(np.count_nonzero(detected_foreground) / pixel_count),
        highlight_ratio=float(np.count_nonzero(highlight) / pixel_count),
        shadow_ratio=float(np.count_nonzero(shadow) / pixel_count),
    )


__all__ = [
    "MinSceneMetricBreakdown",
    "decode_rgb",
    "evaluate_min_scene",
    "rgb_mae",
]
