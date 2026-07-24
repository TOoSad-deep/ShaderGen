"""面向单主体纯背景图片的轻量确定性感知。."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any, Literal

import numpy as np
from PIL import Image, UnidentifiedImageError

from shaderforge.dsl import ShaderDocument, adapt_min_scene_to_shader_graph
from shaderforge.scene import (
    Canvas,
    Feature,
    LinearColorField,
    MinScene,
    Primitive,
    RadialColorField,
    SceneObject,
    SolidColorField,
)

MAX_WORK_SIDE = 256


@dataclass(frozen=True)
class MinPerception:
    """可追踪的测量摘要、目标像素、确定性 scene 与 ShaderGraph fallback 文档。."""

    width: int
    height: int
    target_rgb: np.ndarray
    summary: dict[str, Any]
    fallback_scene: MinScene
    fallback_document: ShaderDocument


def _color(values: np.ndarray) -> tuple[float, float, float]:
    clipped = np.clip(values.astype(float), 0.0, 1.0)
    return tuple(float(round(item, 6)) for item in clipped)  # type: ignore[return-value]


def _erode_mask(mask: np.ndarray) -> np.ndarray:
    """仅用四邻域去掉一像素边缘，避免基础颜色场拟合吞掉 rim。."""
    eroded = mask.copy()
    eroded[1:, :] &= mask[:-1, :]
    eroded[:-1, :] &= mask[1:, :]
    eroded[:, 1:] &= mask[:, :-1]
    eroded[:, :-1] &= mask[:, 1:]
    return eroded if np.count_nonzero(eroded) >= 16 else mask


def _fit_endpoints(
    t: np.ndarray, colors: np.ndarray
) -> tuple[np.ndarray, np.ndarray, float]:
    """对固定空间坐标 t 拟合两端 RGB，并返回像素 MAE。."""
    design = np.stack((1.0 - t, t), axis=1)
    endpoints, *_ = np.linalg.lstsq(design, colors, rcond=None)
    endpoints = np.clip(endpoints, 0.0, 1.0)
    predicted = design @ endpoints
    return endpoints[0], endpoints[1], float(np.mean(np.abs(colors - predicted)))


def _fit_color_field(
    rgb: np.ndarray,
    mask: np.ndarray,
    center: tuple[float, float],
    axes: tuple[float, float],
) -> tuple[SolidColorField | RadialColorField | LinearColorField, dict[str, float]]:
    """在主体内部用同一 MAE 比较 solid/radial/linear 三个固定模型。."""
    interior = _erode_mask(mask)
    ys, xs = np.nonzero(interior)
    colors = rgb[ys, xs]
    height, width = mask.shape
    unit = float(min(width, height))
    px = (2.0 * xs.astype(np.float32) + 1.0 - width) / unit
    py = (height - (2.0 * ys.astype(np.float32) + 1.0)) / unit
    q = np.stack(((px - center[0]) / axes[0], (py - center[1]) / axes[1]), axis=1)

    mean = np.clip(np.mean(colors, axis=0), 0.0, 1.0)
    scores = {"solid": float(np.mean(np.abs(colors - mean)))}
    best_field: SolidColorField | RadialColorField | LinearColorField = SolidColorField(
        model="solid", color=_color(mean)
    )
    best_score = scores["solid"]

    best_radial: RadialColorField | None = None
    radial_score = float("inf")
    for origin_x, origin_y in ((0.0, 0.0), (-0.35, 0.5), (0.35, 0.5), (-0.35, -0.5)):
        distance = np.linalg.norm(q - np.asarray((origin_x, origin_y)), axis=1)
        for scale in (0.75, 1.0, 1.25, 1.5):
            inner, outer, score = _fit_endpoints(
                np.clip(distance / scale, 0.0, 1.0), colors
            )
            if score < radial_score:
                radial_score = score
                best_radial = RadialColorField(
                    model="radial",
                    inner=_color(inner),
                    outer=_color(outer),
                    origin=(origin_x, origin_y),
                    scale=scale,
                )
    scores["radial"] = radial_score
    if best_radial is not None and radial_score + 1.0e-4 < best_score:
        best_field, best_score = best_radial, radial_score

    best_linear: LinearColorField | None = None
    linear_score = float("inf")
    for direction in (
        (1.0, 0.0),
        (0.0, 1.0),
        (0.707107, 0.707107),
        (0.707107, -0.707107),
    ):
        projection = q @ np.asarray(direction, dtype=np.float32)
        low, high = np.quantile(projection, (0.02, 0.98))
        scale = max(0.05, float(high - low))
        offset = float(-low / scale)
        start, end, score = _fit_endpoints(
            np.clip(projection / scale + offset, 0.0, 1.0), colors
        )
        if score < linear_score:
            linear_score = score
            best_linear = LinearColorField(
                model="linear",
                start=_color(start),
                end=_color(end),
                direction=direction,
                offset=max(-2.0, min(3.0, offset)),
                scale=min(4.0, scale),
            )
    scores["linear"] = linear_score
    if best_linear is not None and linear_score + 1.0e-4 < best_score:
        best_field = best_linear
    return best_field, {name: round(value, 8) for name, value in scores.items()}


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
    primitive_type: Literal["circle", "ellipse"] = (
        "circle" if abs(axes[0] - axes[1]) < 0.12 else "ellipse"
    )
    if primitive_type == "circle":
        radius = (axes[0] + axes[1]) / 2.0
        axes = (radius, radius)
    color_field, fit_scores = _fit_color_field(rgb, mask, center, axes)
    object_pixels = rgb[mask]
    light_color = _color(np.percentile(object_pixels, 82, axis=0))
    dark_color = _color(np.percentile(object_pixels, 28, axis=0))

    scene = MinScene(
        canvas=Canvas(width=width, height=height, background=_color(background)),
        object=SceneObject(
            primitive=Primitive(type=primitive_type, center=center, axes=axes),
            color_field=color_field,
            features=(
                Feature(id="rim", type="rim", intensity=0.22, color=light_color),
                Feature(
                    id="shadow",
                    type="shadow",
                    center=(center[0] + 0.08, center[1] - axes[1] * 0.92),
                    axes=(axes[0] * 0.68, max(0.05, axes[1] * 0.16)),
                    color=_color(np.asarray(dark_color) * 0.35),
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
        "color_field_model": color_field.model,
        "color_field_fit_mae": fit_scores,
    }
    # ShaderGraph 产品的确定性 fallback 在感知阶段直接产出，热路径不再
    # 依赖 MinScene 中间表示；legacy Builder 仍可使用 fallback_scene。
    document = adapt_min_scene_to_shader_graph(scene)
    return MinPerception(width, height, rgb, summary, scene, document)
