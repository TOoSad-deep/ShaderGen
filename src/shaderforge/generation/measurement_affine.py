"""从确定性图片测量构造无贴图 affine seed Shader."""

from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from typing import Any, Literal

import numpy as np
from PIL import Image, UnidentifiedImageError

from shaderforge.analysis import TargetMeasurements

MEASUREMENT_AFFINE_SEED_VERSION = "measurement_affine_seed_v1"
NORMALIZED_REFERENCE_CONTRACT = "normalized_rgb_png_v1"
MIN_FOREGROUND_CONFIDENCE = 0.5
MIN_FIT_PIXELS = 64
MAX_FIT_CONDITION_NUMBER = 1_000_000.0

SeedStrategy = Literal["foreground_affine_plane", "palette_solid_ellipse"]


@dataclass(frozen=True)
class MeasurementSeedProvenance:
    """描述 deterministic seed 的输入绑定、策略和稳定输出身份."""

    schema_version: int
    generator_version: str
    input_contract: str
    strategy: SeedStrategy
    reference_sha256: str
    measurements_sha256: str
    glsl_sha256: str
    glsl_chars: int
    fit_pixel_count: int
    fit_rmse: float | None
    coefficients: tuple[tuple[float, float, float], ...]
    fallback_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        """返回可持久化并参与 benchmark 审计的普通字典."""
        return {
            "schema_version": self.schema_version,
            "generator_version": self.generator_version,
            "input_contract": self.input_contract,
            "strategy": self.strategy,
            "reference_sha256": self.reference_sha256,
            "measurements_sha256": self.measurements_sha256,
            "glsl_sha256": self.glsl_sha256,
            "glsl_chars": self.glsl_chars,
            "fit_pixel_count": self.fit_pixel_count,
            "fit_rmse": self.fit_rmse,
            "coefficients": [list(row) for row in self.coefficients],
            "fallback_reason": self.fallback_reason,
        }


@dataclass(frozen=True)
class MeasurementAffineSeed:
    """一份可直接进入 Validator/Renderer 的 deterministic seed."""

    glsl: str
    provenance: MeasurementSeedProvenance


def _measurements_sha256(measurements: TargetMeasurements) -> str:
    encoded = json.dumps(
        measurements.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _decode_normalized_reference(
    reference_image: bytes,
    measurements: TargetMeasurements,
) -> np.ndarray:
    """解码并验证 normalized RGB PNG 与测量结果强绑定."""
    if not reference_image:
        raise ValueError("normalized reference 不能为空。")
    digest = sha256(reference_image).hexdigest()
    if digest != measurements.image_sha256:
        raise ValueError("normalized reference 与 TargetMeasurements hash 不一致。")
    try:
        with Image.open(BytesIO(reference_image)) as image:
            if image.format != "PNG" or image.mode != "RGB":
                raise ValueError("normalized reference 必须是 RGB PNG。")
            if image.size != (
                measurements.analysis_width,
                measurements.analysis_height,
            ):
                raise ValueError(
                    "normalized reference 尺寸与 TargetMeasurements 不一致。"
                )
            rgb = np.asarray(image, dtype=np.float64)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("无法解码 normalized reference PNG。") from exc
    return rgb


def _border_pixels(rgb: np.ndarray) -> np.ndarray:
    return np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]), axis=0)


def _largest_component(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """返回四邻域最大连通 foreground component."""
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    best: list[tuple[int, int]] = []
    for start_y, start_x in np.argwhere(mask):
        y, x = int(start_y), int(start_x)
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


def _foreground_component(
    rgb: np.ndarray,
    measurements: TargetMeasurements,
) -> tuple[np.ndarray, int]:
    background = np.asarray(measurements.border_color_rgb, dtype=np.float64)
    border_distances = np.linalg.norm(
        (_border_pixels(rgb) - background) / 255.0,
        axis=1,
    )
    threshold = max(
        18.0 / 255.0,
        float(np.percentile(border_distances, 95) * 2.5),
    )
    distances = np.linalg.norm((rgb - background) / 255.0, axis=2)
    raw_mask = distances > threshold
    raw_fraction = float(raw_mask.mean())
    if not 0.001 <= raw_fraction <= 0.85:
        return np.zeros_like(raw_mask), 0
    return _largest_component(raw_mask)


def _fit_affine_plane(
    rgb: np.ndarray,
    component: np.ndarray,
    bbox: tuple[float, float, float, float],
) -> tuple[tuple[tuple[float, float, float], ...], float] | None:
    rows, columns = np.nonzero(component)
    if len(rows) < MIN_FIT_PIXELS:
        return None
    height, width, _channels = rgb.shape
    min_x, min_y, max_x, max_y = bbox
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    half_width = (max_x - min_x) * 0.5
    half_height = (max_y - min_y) * 0.5
    if half_width <= 0.0 or half_height <= 0.0:
        return None

    uv_x = (columns.astype(np.float64) + 0.5) / width
    uv_y = 1.0 - (rows.astype(np.float64) + 0.5) / height
    local_x = (uv_x - center_x) / half_width
    local_y = (uv_y - center_y) / half_height
    design = np.column_stack((np.ones_like(local_x), local_x, local_y))
    target = rgb[rows, columns] / 255.0
    try:
        coefficients, _residuals, rank, singular_values = np.linalg.lstsq(
            design,
            target,
            rcond=None,
        )
    except np.linalg.LinAlgError:
        return None
    if rank != 3 or len(singular_values) != 3 or singular_values[-1] <= 0.0:
        return None
    condition = float(singular_values[0] / singular_values[-1])
    if not math.isfinite(condition) or condition > MAX_FIT_CONDITION_NUMBER:
        return None
    prediction = design @ coefficients
    fit_rmse = float(np.sqrt(np.mean(np.square(prediction - target))))
    if not math.isfinite(fit_rmse) or not np.all(np.isfinite(coefficients)):
        return None
    rounded_rows = tuple(
        (
            _rounded_float(row[0]),
            _rounded_float(row[1]),
            _rounded_float(row[2]),
        )
        for row in coefficients
    )
    rounded = (rounded_rows[0], rounded_rows[1], rounded_rows[2])
    return rounded, round(fit_rmse, 12)


def _rounded_float(value: float) -> float:
    rounded = round(float(value), 8)
    return 0.0 if rounded == -0.0 else rounded


def _fallback_color(
    measurements: TargetMeasurements,
) -> tuple[float, float, float]:
    if measurements.palette:
        color = measurements.palette[0].rgb
        return (
            _rounded_float(color[0] / 255.0),
            _rounded_float(color[1] / 255.0),
            _rounded_float(color[2] / 255.0),
        )
    background = tuple(channel / 255.0 for channel in measurements.border_color_rgb)
    return (
        _rounded_float(1.0 - background[0]),
        _rounded_float(1.0 - background[1]),
        _rounded_float(1.0 - background[2]),
    )


def _format_float(value: float) -> str:
    normalized = 0.0 if abs(value) < 0.000000005 else value
    return f"{normalized:.8f}"


def _vec3(value: tuple[float, float, float]) -> str:
    return "vec3(" + ", ".join(_format_float(item) for item in value) + ")"


def _build_glsl(
    measurements: TargetMeasurements,
    coefficients: tuple[tuple[float, float, float], ...],
    strategy: SeedStrategy,
) -> str:
    bbox = measurements.foreground_bbox_uv or (0.20, 0.20, 0.80, 0.80)
    center_x = (bbox[0] + bbox[2]) * 0.5
    center_y = (bbox[1] + bbox[3]) * 0.5
    half_width = max(0.02, (bbox[2] - bbox[0]) * 0.5)
    half_height = max(0.02, (bbox[3] - bbox[1]) * 0.5)
    background_rgb = measurements.border_color_rgb
    background = (
        _rounded_float(background_rgb[0] / 255.0),
        _rounded_float(background_rgb[1] / 255.0),
        _rounded_float(background_rgb[2] / 255.0),
    )
    intercept, slope_x, slope_y = coefficients
    return f"""precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;

// shaderforge_generator: {MEASUREMENT_AFFINE_SEED_VERSION}
// strategy: {strategy}
void main() {{
    vec2 center = vec2({_format_float(center_x)}, {_format_float(center_y)});
    vec2 half_size = vec2({_format_float(half_width)}, {_format_float(half_height)});
    vec2 local = (v_uv - center) / half_size;
    float distance_to_ellipse = length(local) - 1.0;
    float pixel = 2.0 / max(min(u_resolution.x, u_resolution.y), 1.0);
    float aa = pixel / max(min(half_size.x, half_size.y), 0.01);
    float mask = 1.0 - smoothstep(-aa, aa, distance_to_ellipse);
    vec3 background = {_vec3(background)};
    vec3 foreground = clamp(
        {_vec3(intercept)}
        + {_vec3(slope_x)} * local.x
        + {_vec3(slope_y)} * local.y,
        0.0,
        1.0
    );
    gl_FragColor = vec4(mix(background, foreground, mask), 1.0);
}}
"""


def build_measurement_affine_seed(
    reference_image: bytes,
    measurements: TargetMeasurements,
) -> MeasurementAffineSeed:
    """用 normalized reference 和确定性测量生成 affine/solid ellipse seed."""
    rgb = _decode_normalized_reference(reference_image, measurements)
    strategy: SeedStrategy = "palette_solid_ellipse"
    fallback_reason: str | None = None
    fit_pixel_count = 0
    fit_rmse: float | None = None
    coefficients: tuple[tuple[float, float, float], ...]

    if measurements.foreground_confidence < MIN_FOREGROUND_CONFIDENCE:
        fallback_reason = "foreground_low_confidence"
    elif measurements.foreground_bbox_uv is None:
        fallback_reason = "foreground_bbox_unavailable"
    else:
        component, fit_pixel_count = _foreground_component(rgb, measurements)
        fitted = _fit_affine_plane(
            rgb,
            component,
            measurements.foreground_bbox_uv,
        )
        if fitted is None:
            fallback_reason = "affine_fit_unavailable"
        else:
            coefficients, fit_rmse = fitted
            strategy = "foreground_affine_plane"

    if strategy == "palette_solid_ellipse":
        coefficients = (
            _fallback_color(measurements),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
        )
    glsl = _build_glsl(measurements, coefficients, strategy)
    provenance = MeasurementSeedProvenance(
        schema_version=1,
        generator_version=MEASUREMENT_AFFINE_SEED_VERSION,
        input_contract=NORMALIZED_REFERENCE_CONTRACT,
        strategy=strategy,
        reference_sha256=sha256(reference_image).hexdigest(),
        measurements_sha256=_measurements_sha256(measurements),
        glsl_sha256=sha256(glsl.encode("utf-8")).hexdigest(),
        glsl_chars=len(glsl),
        fit_pixel_count=fit_pixel_count,
        fit_rmse=fit_rmse,
        coefficients=coefficients,
        fallback_reason=fallback_reason,
    )
    return MeasurementAffineSeed(glsl=glsl, provenance=provenance)
