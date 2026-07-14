"""参考图片的确定性像素测量."""

from __future__ import annotations

from collections import deque
from hashlib import sha256
from io import BytesIO

import numpy as np
from PIL import Image, UnidentifiedImageError

from shaderforge.analysis.models import (
    ColorSample,
    EdgeSummary,
    PixelProbe,
    RegionOfInterest,
    TargetMeasurements,
)
from shaderforge.contracts.png_to_shader_v1 import WEBGL1_STATIC_NO_TEXTURE_V1

MAX_IMAGE_PIXELS = 32_000_000


class InvalidTargetImageError(ValueError):
    """表示参考图片无法被安全解码或测量."""


def normalize_target_png(
    image_bytes: bytes,
    *,
    max_long_side: int = WEBGL1_STATIC_NO_TEXTURE_V1.max_long_side,
) -> bytes:
    """把输入图规范为白底 RGB PNG，并在必要时等比缩小."""
    if max_long_side <= 0:
        raise ValueError("max_long_side 必须大于 0。")
    rgb, _original_width, _original_height = _decode_rgb(image_bytes, max_long_side)
    output = BytesIO()
    Image.fromarray(rgb).save(output, format="PNG", compress_level=9)
    return output.getvalue()


def _decode_rgb(image_bytes: bytes, max_long_side: int) -> tuple[np.ndarray, int, int]:
    if not image_bytes:
        raise InvalidTargetImageError("参考图片不能为空。")
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            width, height = source.size
            if width <= 0 or height <= 0:
                raise InvalidTargetImageError("参考图片尺寸无效。")
            if width * height > MAX_IMAGE_PIXELS:
                raise InvalidTargetImageError("参考图片像素数量超过安全上限。")
            rgba = source.convert("RGBA")
            if max(width, height) > max_long_side:
                scale = max_long_side / max(width, height)
                resized = (
                    max(1, round(width * scale)),
                    max(1, round(height * scale)),
                )
                rgba = rgba.resize(resized, Image.Resampling.LANCZOS)
            array = np.asarray(rgba, dtype=np.float32)
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidTargetImageError("无法解码参考图片。") from exc

    alpha = array[..., 3:4] / 255.0
    rgb = array[..., :3] * alpha + 255.0 * (1.0 - alpha)
    return rgb.astype(np.uint8), width, height


def _border_pixels(rgb: np.ndarray) -> np.ndarray:
    return np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]), axis=0)


def _largest_component(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """返回四邻域最大连通区域和像素数."""
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    best: list[tuple[int, int]] = []

    for start_y, start_x in np.argwhere(mask):
        y = int(start_y)
        x = int(start_x)
        if visited[y, x]:
            continue
        visited[y, x] = True
        queue: deque[tuple[int, int]] = deque([(y, x)])
        component: list[tuple[int, int]] = []
        while queue:
            current_y, current_x = queue.popleft()
            component.append((current_y, current_x))
            for next_y, next_x in (
                (current_y - 1, current_x),
                (current_y + 1, current_x),
                (current_y, current_x - 1),
                (current_y, current_x + 1),
            ):
                if (
                    0 <= next_y < height
                    and 0 <= next_x < width
                    and mask[next_y, next_x]
                    and not visited[next_y, next_x]
                ):
                    visited[next_y, next_x] = True
                    queue.append((next_y, next_x))
        if len(component) > len(best):
            best = component

    result = np.zeros_like(mask, dtype=bool)
    if best:
        ys, xs = zip(*best, strict=True)
        result[np.asarray(ys), np.asarray(xs)] = True
    return result, len(best)


def _bbox_uv(mask: np.ndarray) -> tuple[float, float, float, float] | None:
    points = np.argwhere(mask)
    if not len(points):
        return None
    height, width = mask.shape
    min_y, min_x = points.min(axis=0)
    max_y, max_x = points.max(axis=0)
    return (
        float(min_x / width),
        float(1.0 - (max_y + 1) / height),
        float((max_x + 1) / width),
        float(1.0 - min_y / height),
    )


def _palette(
    rgb: np.ndarray, mask: np.ndarray, limit: int = 5
) -> tuple[ColorSample, ...]:
    pixels = rgb[mask] if mask.any() else rgb.reshape(-1, 3)
    quantized = (pixels // 16).astype(np.uint16)
    keys = quantized[:, 0] * 256 + quantized[:, 1] * 16 + quantized[:, 2]
    values, counts = np.unique(keys, return_counts=True)
    order = np.argsort(counts)[::-1][:limit]
    total = int(counts.sum())
    samples: list[ColorSample] = []
    for index in order:
        key = int(values[index])
        red = ((key // 256) % 16) * 16 + 8
        green = ((key // 16) % 16) * 16 + 8
        blue = (key % 16) * 16 + 8
        samples.append(
            ColorSample(
                rgb=(min(red, 255), min(green, 255), min(blue, 255)),
                fraction=float(counts[index] / total),
            )
        )
    return tuple(samples)


def _edge_map(rgb: np.ndarray) -> np.ndarray:
    normalized = rgb.astype(np.float32) / 255.0
    gray = (
        normalized[..., 0] * 0.2126
        + normalized[..., 1] * 0.7152
        + normalized[..., 2] * 0.0722
    )
    delta_x = np.zeros_like(gray)
    delta_y = np.zeros_like(gray)
    delta_x[:, 1:-1] = (gray[:, 2:] - gray[:, :-2]) * 0.5
    delta_y[1:-1, :] = (gray[2:, :] - gray[:-2, :]) * 0.5
    return np.asarray(np.clip(np.hypot(delta_x, delta_y), 0.0, 1.0), dtype=np.float32)


def _sample_rgb(rgb: np.ndarray, uv: tuple[float, float]) -> tuple[int, int, int]:
    height, width, _ = rgb.shape
    x = min(width - 1, max(0, round(uv[0] * (width - 1))))
    row = min(height - 1, max(0, round((1.0 - uv[1]) * (height - 1))))
    value = rgb[row, x]
    return int(value[0]), int(value[1]), int(value[2])


def _representative_pixels(
    rgb: np.ndarray, edges: np.ndarray
) -> tuple[PixelProbe, ...]:
    positions = (
        ("center", (0.50, 0.50), "center_color"),
        ("upper_left", (0.25, 0.75), "quadrant_color"),
        ("upper_right", (0.75, 0.75), "quadrant_color"),
        ("lower_left", (0.25, 0.25), "quadrant_color"),
        ("lower_right", (0.75, 0.25), "quadrant_color"),
        ("top", (0.50, 0.90), "boundary_or_background"),
        ("bottom", (0.50, 0.10), "boundary_or_shadow"),
        ("left", (0.10, 0.50), "boundary_or_background"),
        ("right", (0.90, 0.50), "boundary_or_background"),
    )
    probes = [
        PixelProbe(probe_id=probe_id, uv=uv, rgb=_sample_rgb(rgb, uv), purpose=purpose)
        for probe_id, uv, purpose in positions
    ]
    strongest_row, strongest_x = np.unravel_index(int(np.argmax(edges)), edges.shape)
    strongest_uv = (
        float((strongest_x + 0.5) / edges.shape[1]),
        float(1.0 - (strongest_row + 0.5) / edges.shape[0]),
    )
    probes.append(
        PixelProbe(
            probe_id="strongest_edge",
            uv=strongest_uv,
            rgb=_sample_rgb(rgb, strongest_uv),
            purpose="edge",
        )
    )
    return tuple(probes)


def _rois(
    bbox: tuple[float, float, float, float] | None, confidence: float
) -> tuple[RegionOfInterest, ...]:
    regions = [
        RegionOfInterest(
            region_id="background_border",
            bbox_uv=(0.0, 0.0, 1.0, 0.10),
            purpose="background",
            confidence=1.0,
        ),
        RegionOfInterest(
            region_id="upper_left",
            bbox_uv=(0.10, 0.55, 0.50, 0.95),
            purpose="color",
            confidence=0.7,
        ),
        RegionOfInterest(
            region_id="lower_right",
            bbox_uv=(0.50, 0.05, 0.90, 0.45),
            purpose="color",
            confidence=0.7,
        ),
        RegionOfInterest(
            region_id="protected_center",
            bbox_uv=(0.38, 0.38, 0.62, 0.62),
            purpose="protection",
            confidence=0.8,
        ),
    ]
    if bbox is not None:
        regions.insert(
            0,
            RegionOfInterest(
                region_id="subject",
                bbox_uv=bbox,
                purpose="geometry",
                confidence=confidence,
            ),
        )
    return tuple(regions)


def measure_target(
    image_bytes: bytes,
    *,
    max_long_side: int = WEBGL1_STATIC_NO_TEXTURE_V1.max_long_side,
) -> TargetMeasurements:
    """测量参考图的几何、颜色、边缘和代表区域."""
    if max_long_side <= 0:
        raise ValueError("max_long_side 必须大于 0。")
    rgb, original_width, original_height = _decode_rgb(image_bytes, max_long_side)
    analysis_height, analysis_width, _ = rgb.shape

    border = _border_pixels(rgb).astype(np.float32)
    border_color_float = np.median(border, axis=0)
    border_color = tuple(int(round(value)) for value in border_color_float)
    border_distances = np.linalg.norm((border - border_color_float) / 255.0, axis=1)
    border_uniformity = float(
        np.clip(1.0 - np.percentile(border_distances, 90) / 0.20, 0.0, 1.0)
    )
    threshold = max(18.0 / 255.0, float(np.percentile(border_distances, 95) * 2.5))
    distances = np.linalg.norm(
        (rgb.astype(np.float32) - border_color_float) / 255.0, axis=2
    )
    raw_mask = distances > threshold
    raw_fraction = float(raw_mask.mean())

    if 0.001 <= raw_fraction <= 0.85:
        component_mask, component_size = _largest_component(raw_mask)
    else:
        component_mask = np.zeros_like(raw_mask)
        component_size = 0
    foreground_fraction = float(component_size / raw_mask.size)
    component_coverage = component_size / max(1, int(raw_mask.sum()))
    fraction_factor = float(
        np.clip((foreground_fraction - 0.001) / 0.02, 0.0, 1.0)
        * np.clip((0.85 - foreground_fraction) / 0.15, 0.0, 1.0)
    )
    foreground_confidence = float(
        np.clip(border_uniformity * component_coverage * fraction_factor, 0.0, 1.0)
    )
    bbox = _bbox_uv(component_mask) if foreground_confidence > 0.05 else None

    edges = _edge_map(rgb)
    strongest_row, strongest_x = np.unravel_index(int(np.argmax(edges)), edges.shape)
    edge_summary = EdgeSummary(
        mean_strength=float(edges.mean()),
        p90_strength=float(np.percentile(edges, 90)),
        edge_fraction=float((edges > 0.08).mean()),
        strongest_uv=(
            float((strongest_x + 0.5) / analysis_width),
            float(1.0 - (strongest_row + 0.5) / analysis_height),
        ),
    )

    return TargetMeasurements(
        schema_version=1,
        image_sha256=sha256(image_bytes).hexdigest(),
        image_width=original_width,
        image_height=original_height,
        analysis_width=analysis_width,
        analysis_height=analysis_height,
        border_color_rgb=border_color,  # type: ignore[arg-type]
        border_uniformity=border_uniformity,
        foreground_bbox_uv=bbox,
        foreground_fraction=foreground_fraction,
        foreground_confidence=foreground_confidence,
        palette=_palette(rgb, component_mask),
        representative_pixels=_representative_pixels(rgb, edges),
        edge_summary=edge_summary,
        roi_candidates=_rois(bbox, foreground_confidence),
    )
