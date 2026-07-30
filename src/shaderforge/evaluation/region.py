"""Deterministic local ROI metrics for WebGL beauty renders."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from shaderforge.evaluation.mae import _dilate, _edge_strength, rgb_mae

FOCUSED_REGION_METRICS_VERSION = "focused_region_metrics_v1"
_FOREGROUND_THRESHOLD = 0.05


@dataclass(frozen=True)
class NormalizedUvBBox:
    """A clipped WebGL UV bounding box, with its origin at bottom left."""

    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> dict[str, float]:
        """Return the stable JSON representation."""
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class FocusedRegionMetricsV1:
    """Local image-quality facts for one normalized WebGL UV region."""

    roi_mae: float
    roi_geometry_mask_loss: float
    roi_edge_loss: float
    outside_roi_mae: float
    uv_bbox: NormalizedUvBBox
    dilation_radius: int
    roi_pixel_count: int
    outside_roi_pixel_count: int
    metric_version: str = FOCUSED_REGION_METRICS_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Return the stable JSON representation."""
        return {
            "metric_version": self.metric_version,
            "roi_mae": self.roi_mae,
            "roi_geometry_mask_loss": self.roi_geometry_mask_loss,
            "roi_edge_loss": self.roi_edge_loss,
            "outside_roi_mae": self.outside_roi_mae,
            "uv_bbox": self.uv_bbox.to_dict(),
            "dilation_radius": self.dilation_radius,
            "roi_pixel_count": self.roi_pixel_count,
            "outside_roi_pixel_count": self.outside_roi_pixel_count,
        }


def _finite_coordinate(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"ROI {name} 必须是有限数值。")
    coordinate = float(value)
    if not np.isfinite(coordinate):
        raise ValueError(f"ROI {name} 必须是有限数值。")
    return coordinate


def normalize_uv_bbox(uv_bbox: Mapping[str, object] | object) -> NormalizedUvBBox:
    """Normalize and clip a UV box to ``[0, 1]`` without changing its origin."""
    if isinstance(uv_bbox, Mapping):
        try:
            values = {name: uv_bbox[name] for name in ("x", "y", "width", "height")}
        except KeyError as exc:
            raise ValueError("ROI uv_bbox 必须包含 x、y、width、height。") from exc
    else:
        try:
            values = {
                name: getattr(uv_bbox, name) for name in ("x", "y", "width", "height")
            }
        except AttributeError as exc:
            raise ValueError("ROI uv_bbox 必须包含 x、y、width、height。") from exc
    x = _finite_coordinate(values["x"], "x")
    y = _finite_coordinate(values["y"], "y")
    width = _finite_coordinate(values["width"], "width")
    height = _finite_coordinate(values["height"], "height")
    if width < 0.0 or height < 0.0:
        raise ValueError("ROI uv_bbox 的 width 和 height 不能为负数。")
    x0, x1 = np.clip((x, x + width), 0.0, 1.0)
    y0, y1 = np.clip((y, y + height), 0.0, 1.0)
    return NormalizedUvBBox(
        x=float(x0), y=float(y0), width=float(x1 - x0), height=float(y1 - y0)
    )


def _validate_dilation_radius(dilation_radius: int) -> int:
    if isinstance(dilation_radius, bool) or not isinstance(dilation_radius, int):
        raise ValueError("ROI dilation_radius 必须是非负整数。")
    if dilation_radius < 0:
        raise ValueError("ROI dilation_radius 必须是非负整数。")
    return dilation_radius


def _roi_mask(shape: tuple[int, int], bbox: NormalizedUvBBox) -> np.ndarray:
    """Map bottom-left UV to image-top rows, matching spatial_residual_v2."""
    height, width = shape
    # Images remain in their decoded image-top ordering.  Only the UV interval is
    # converted: high WebGL y selects low image row numbers; no image Y flip occurs.
    mask = np.zeros((height, width), dtype=bool)
    if bbox.width == 0.0 or bbox.height == 0.0:
        return mask
    # Remove only floating point representation noise at integer pixel borders;
    # the right/top intervals remain exclusive.
    epsilon = 1e-9
    x0 = int(np.floor(bbox.x * width + epsilon))
    x1 = int(np.ceil((bbox.x + bbox.width) * width - epsilon))
    row0 = int(np.floor((1.0 - (bbox.y + bbox.height)) * height + epsilon))
    row1 = int(np.ceil((1.0 - bbox.y) * height - epsilon))
    if x1 > x0 and row1 > row0:
        mask[row0:row1, x0:x1] = True
    return mask


def _background_rgb(
    background: tuple[float, float, float] | list[float],
) -> np.ndarray:
    """Validate the scene background used by the global evaluator."""
    background_rgb = np.asarray(background, dtype=np.float32)
    if background_rgb.shape != (3,) or not np.all(np.isfinite(background_rgb)):
        raise ValueError("focused ROI 背景颜色必须是有限 RGB 三元组。")
    return background_rgb


def _geometry_mask(rgb: np.ndarray, background_rgb: np.ndarray) -> np.ndarray:
    """Use the same background-distance foreground convention as scene metrics."""
    return np.max(np.abs(rgb - background_rgb), axis=2) > _FOREGROUND_THRESHOLD


def evaluate_focused_region(
    reference: np.ndarray,
    rendered: np.ndarray,
    uv_bbox: Mapping[str, object] | object,
    *,
    background: tuple[float, float, float] | list[float],
    dilation_radius: int = 2,
) -> FocusedRegionMetricsV1:
    """Score a clipped WebGL UV ROI and its protected outside region.

    The supplied images are normal image-top ``(height, width, RGB)`` arrays,
    while ``uv_bbox`` is bottom-left WebGL UV, exactly as in
    ``spatial_residual_v2``. ``background`` follows the global scene metric's
    foreground convention. Dilation expands the ROI by a fixed number of image
    pixels before every local metric is calculated.
    """
    rgb_mae(reference, rendered)
    bbox = normalize_uv_bbox(uv_bbox)
    radius = _validate_dilation_radius(dilation_radius)
    background_rgb = _background_rgb(background)
    reference_rgb = reference.astype(np.float32)
    rendered_rgb = rendered.astype(np.float32)
    roi = _roi_mask(reference.shape[:2], bbox)
    if radius:
        roi = _dilate(roi, radius)
    outside = ~roi
    delta = np.mean(np.abs(reference_rgb - rendered_rgb), axis=2)

    if np.any(roi):
        roi_mae = float(np.mean(delta[roi]))
        reference_mask = _geometry_mask(reference_rgb, background_rgb)
        rendered_mask = _geometry_mask(rendered_rgb, background_rgb)
        intersection = int(np.count_nonzero(reference_mask[roi] & rendered_mask[roi]))
        union = int(np.count_nonzero(reference_mask[roi] | rendered_mask[roi]))
        geometry_loss = 1.0 - float(intersection / union) if union else 0.0
        edge_loss = float(
            np.mean(
                np.abs(_edge_strength(reference_rgb) - _edge_strength(rendered_rgb))[
                    roi
                ]
            )
        )
    else:
        roi_mae = geometry_loss = edge_loss = 0.0
    outside_mae = float(np.mean(delta[outside])) if np.any(outside) else 0.0
    return FocusedRegionMetricsV1(
        roi_mae=roi_mae,
        roi_geometry_mask_loss=geometry_loss,
        roi_edge_loss=edge_loss,
        outside_roi_mae=outside_mae,
        uv_bbox=bbox,
        dilation_radius=radius,
        roi_pixel_count=int(np.count_nonzero(roi)),
        outside_roi_pixel_count=int(np.count_nonzero(outside)),
    )


__all__ = [
    "FOCUSED_REGION_METRICS_VERSION",
    "FocusedRegionMetricsV1",
    "NormalizedUvBBox",
    "evaluate_focused_region",
    "normalize_uv_bbox",
]
