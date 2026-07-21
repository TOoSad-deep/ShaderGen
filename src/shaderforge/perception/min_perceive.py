"""面向单主体纯背景图片的轻量确定性感知。."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

import numpy as np
from PIL import Image, UnidentifiedImageError

from shaderforge.scene import (
    Canvas,
    ColorField,
    Feature,
    MinScene,
    Primitive,
    SceneObject,
)

MAX_WORK_SIDE = 256


@dataclass(frozen=True)
class MinPerception:
    """可追踪的测量摘要、目标像素和确定性 scene。."""

    width: int
    height: int
    target_rgb: np.ndarray
    summary: dict[str, Any]
    fallback_scene: MinScene


def _color(values: np.ndarray) -> tuple[float, float, float]:
    clipped = np.clip(values.astype(float), 0.0, 1.0)
    return tuple(float(round(item, 6)) for item in clipped)  # type: ignore[return-value]


def perceive_min_target(image_bytes: bytes) -> MinPerception:
    """解码、缩放并测量单主体；无法分割时使用安全中心先验。."""
    try:
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("无法解码输入图片。") from exc

    original_width, original_height = image.size
    scale = min(1.0, MAX_WORK_SIDE / max(original_width, original_height))
    width = max(16, round(original_width * scale))
    height = max(16, round(original_height * scale))
    if (width, height) != image.size:
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    rgb = np.asarray(image, dtype=np.float32) / 255.0

    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]), axis=0)
    background = np.median(border, axis=0)
    distance = np.linalg.norm(rgb - background, axis=2)
    threshold = max(0.07, float(np.percentile(distance, 70)) * 0.55)
    mask = distance > threshold
    ys, xs = np.nonzero(mask)
    supported = len(xs) >= max(32, width * height // 100)
    if supported:
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
    else:
        x0, x1 = round(width * 0.18), round(width * 0.82)
        y0, y1 = round(height * 0.18), round(height * 0.82)
        mask = np.zeros((height, width), dtype=bool)
        mask[y0 : y1 + 1, x0 : x1 + 1] = True

    min_side = float(min(width, height))
    center_px = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    center = (
        (2.0 * center_px[0] - width) / min_side,
        (height - 2.0 * center_px[1]) / min_side,
    )
    axes = ((x1 - x0 + 1) / min_side, (y1 - y0 + 1) / min_side)
    object_pixels = rgb[mask]
    inner = _color(np.percentile(object_pixels, 82, axis=0))
    outer = _color(np.percentile(object_pixels, 28, axis=0))
    primitive_type = "circle" if abs(axes[0] - axes[1]) < 0.12 else "ellipse"

    scene = MinScene(
        canvas=Canvas(width=width, height=height, background=_color(background)),
        object=SceneObject(
            primitive=Primitive(type=primitive_type, center=center, axes=axes),
            color_field=ColorField(
                model="radial",
                inner=inner,
                outer=outer,
                origin=(-0.35, 0.5),
                scale=1.25,
            ),
            features=(
                Feature(id="rim", type="rim", intensity=0.22, color=inner),
                Feature(
                    id="shadow",
                    type="shadow",
                    center=(center[0] + 0.08, center[1] - axes[1] * 0.92),
                    axes=(axes[0] * 0.68, max(0.05, axes[1] * 0.16)),
                    color=_color(np.asarray(outer) * 0.35),
                    intensity=0.32,
                ),
            ),
        ),
    )
    summary = {
        "source_size": [original_width, original_height],
        "work_size": [width, height],
        "supported_scope": supported,
        "background": list(scene.canvas.background),
        "bbox": [x0, y0, x1, y1],
        "center": list(center),
        "axes": list(axes),
        "primitive": primitive_type,
        "foreground_ratio": round(float(mask.mean()), 6),
    }
    return MinPerception(width, height, rgb, summary, scene)
