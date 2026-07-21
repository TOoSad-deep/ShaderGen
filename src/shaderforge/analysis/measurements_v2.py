"""TargetMeasurementsV2 的确定性生产与 Artifact 物化。."""

from __future__ import annotations

import math
from collections import deque
from hashlib import sha256
from io import BytesIO
from typing import Literal

import numpy as np
from PIL import Image, UnidentifiedImageError
from pydantic import Field, model_validator

from shaderforge.analysis.measurements import (
    MAX_IMAGE_PIXELS,
    InvalidTargetImageError,
    normalize_target_png,
)
from shaderforge.analysis.models_v2 import (
    BBoxUv,
    GradientEvidence,
    InstanceGeometryV2,
    LabSample,
    MeasuredRelation,
    RadialSegmentInstanceEvidenceV1,
    RadialSegmentRelationEvidenceV1,
    RadialSegmentStructureEvidenceV1,
    RegionStatistics,
    SymmetryEvidence,
    TargetHypothesis,
    TargetMeasurementsV2,
    compute_target_hypothesis_hash,
)
from shaderforge.contracts import FrozenModel, NonEmptyString
from shaderforge.contracts.canonical import canonical_json_bytes
from shaderforge.contracts.png_to_shader_v1 import WEBGL1_STATIC_NO_TEXTURE_V1
from shaderforge.store import ArtifactCatalog, ArtifactRefV2, ArtifactResolver

MEASUREMENTS_V2_PRODUCER_VERSION: Literal["target_measurements_producer_v2_2"] = (
    "target_measurements_producer_v2_2"
)
MEASUREMENTS_V2_UNCERTAINTY_SCHEMA_VERSION: Literal[
    "measurements_v2_uncertainty_v1"
] = "measurements_v2_uncertainty_v1"
MEASUREMENTS_V2_BUNDLE_SCHEMA_VERSION: Literal[
    "target_measurements_v2_artifact_bundle_v2"
] = "target_measurements_v2_artifact_bundle_v2"
INSTANCE_TOPOLOGY_CLASSIFIER_VERSION: Literal["instance_topology_classifier_v2_1"] = (
    "instance_topology_classifier_v2_1"
)
RADIAL_SEGMENT_EVIDENCE_SCHEMA_VERSION: Literal[
    "radial_segment_structure_evidence_v1"
] = "radial_segment_structure_evidence_v1"

_LOW_CONFIDENCE_THRESHOLD = 0.70
_ALPHA_FOREGROUND_THRESHOLD = 32
_MIN_COMPONENT_AREA_RATIO = 0.0005
_MIN_HOLE_AREA_RATIO = 0.0005
_RADIAL_PROFILE_BIN_COUNT = 72
_RADIAL_RING_CV_LIMIT = 0.012
_SUPPORTED_SOURCE_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}


class MeasurementsV2Uncertainty(FrozenModel):
    """记录低置信分割的冻结处置，不把 confidence 直接晋升为事实。."""

    schema_version: Literal["measurements_v2_uncertainty_v1"] = (
        MEASUREMENTS_V2_UNCERTAINTY_SCHEMA_VERSION
    )
    low_confidence: bool
    strategy: Literal[
        "verification_required",
        "alternate_hypothesis_retained",
        "soft_only_manual_review",
    ]
    primary_confidence: float = Field(ge=0.0, le=1.0)
    hard_constraint_policy: Literal["verification_required", "soft_only"]
    reason_codes: tuple[NonEmptyString, ...]
    alternate_hypothesis_ids: tuple[NonEmptyString, ...] = ()

    @model_validator(mode="after")
    def _validate_policy(self) -> MeasurementsV2Uncertainty:
        if not self.reason_codes:
            raise ValueError("uncertainty reason_codes 不能为空。")
        if self.low_confidence:
            if self.hard_constraint_policy != "soft_only":
                raise ValueError("低置信测量只能作为 soft constraint。")
            if self.strategy == "verification_required":
                raise ValueError("低置信测量必须记录替代假设或人工复核策略。")
        elif self.strategy != "verification_required":
            raise ValueError("高置信测量应保持独立验证要求。")
        if self.strategy == "alternate_hypothesis_retained":
            if not self.alternate_hypothesis_ids:
                raise ValueError("替代假设策略必须列出 hypothesis id。")
        elif self.alternate_hypothesis_ids:
            raise ValueError("非替代假设策略不得列出 alternate hypothesis。")
        return self


class HypothesisArtifactSet(FrozenModel):
    """一个结构假设对应的可重放 mask/edge Artifact。."""

    hypothesis_id: NonEmptyString
    subject_mask_ref: ArtifactRefV2
    instance_mask_refs: tuple[ArtifactRefV2, ...]
    edge_ref: ArtifactRefV2
    radial_segment_evidence_ref: ArtifactRefV2 | None = None


class TargetMeasurementsV2ArtifactBundle(FrozenModel):
    """MeasurementsV2 及其全部内容寻址输入、派生证据。."""

    schema_version: Literal["target_measurements_v2_artifact_bundle_v2"] = (
        MEASUREMENTS_V2_BUNDLE_SCHEMA_VERSION
    )
    producer_version: Literal["target_measurements_producer_v2_2"] = (
        MEASUREMENTS_V2_PRODUCER_VERSION
    )
    target_source_ref: ArtifactRefV2
    normalized_reference_ref: ArtifactRefV2
    hypothesis_artifacts: tuple[HypothesisArtifactSet, ...]
    evidence_index_ref: ArtifactRefV2
    measurements_ref: ArtifactRefV2
    measurements: TargetMeasurementsV2
    uncertainty: MeasurementsV2Uncertainty

    @model_validator(mode="after")
    def _validate_bindings(self) -> TargetMeasurementsV2ArtifactBundle:
        if self.target_source_ref.sha256 != self.measurements.target_sha256:
            raise ValueError("source Artifact 与 Measurements target identity 不一致。")
        artifacts = {item.hypothesis_id: item for item in self.hypothesis_artifacts}
        if len(artifacts) != len(self.hypothesis_artifacts):
            raise ValueError("hypothesis_artifacts id 不得重复。")
        if set(artifacts) != {
            item.hypothesis_id for item in self.measurements.target_hypotheses
        }:
            raise ValueError("hypothesis_artifacts 必须完整覆盖 Measurements 假设。")
        for hypothesis in self.measurements.target_hypotheses:
            artifact_set = artifacts[hypothesis.hypothesis_id]
            if artifact_set.subject_mask_ref != hypothesis.subject_mask_ref:
                raise ValueError("subject mask Artifact 绑定不一致。")
            if artifact_set.instance_mask_refs != hypothesis.instance_mask_refs:
                raise ValueError("instance mask Artifact 绑定不一致。")
            if (
                artifact_set.radial_segment_evidence_ref
                != hypothesis.radial_segment_evidence_ref
            ):
                raise ValueError("radial segment evidence Artifact 绑定不一致。")
        if self.measurements.evidence_index_ref != self.evidence_index_ref:
            raise ValueError("evidence index Artifact 绑定不一致。")
        return self


class _MaskDraft(FrozenModel):
    """持久化前的确定性假设草稿。."""

    hypothesis_id: NonEmptyString
    confidence: float = Field(ge=0.0, le=1.0)
    mask: tuple[bool, ...]
    instance_masks: tuple[tuple[bool, ...], ...] = ()
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    topology_hint: Literal["auto", "open", "ring"] = "auto"
    derivation: NonEmptyString = "color_border_distance"


def _source_content_type(source_bytes: bytes) -> str:
    try:
        with Image.open(BytesIO(source_bytes)) as image:
            content_type = _SUPPORTED_SOURCE_TYPES.get(image.format or "")
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidTargetImageError("无法解码 V2 参考图片。") from exc
    if content_type is None:
        raise InvalidTargetImageError("V2 producer 只接受 PNG、JPEG 或 WebP。")
    return content_type


def _decode_normalized(normalized_png: bytes) -> np.ndarray:
    try:
        with Image.open(BytesIO(normalized_png)) as image:
            if image.format != "PNG":
                raise InvalidTargetImageError("规范化参考图必须是 PNG。")
            rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidTargetImageError("无法解码规范化参考图。") from exc
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise InvalidTargetImageError("规范化参考图必须是 RGB。")
    return rgb


def _decode_source_alpha(
    source_bytes: bytes,
    *,
    expected_size: tuple[int, int],
) -> np.ndarray:
    """按 normalization 的缩放规则重放源图 alpha，不从白底 PNG 反推透明度。."""
    try:
        with Image.open(BytesIO(source_bytes)) as source:
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise InvalidTargetImageError("V2 source 图片尺寸无效或超过安全上限。")
            rgba = source.convert("RGBA")
            if rgba.size != expected_size:
                rgba = rgba.resize(expected_size, Image.Resampling.LANCZOS)
            alpha = np.asarray(rgba, dtype=np.uint8)[..., 3]
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidTargetImageError("无法解码 V2 source alpha。") from exc
    if alpha.shape != (expected_size[1], expected_size[0]):
        raise InvalidTargetImageError("V2 source alpha 与规范化尺寸不一致。")
    return alpha


def _has_meaningful_alpha(alpha: np.ndarray) -> bool:
    support = alpha >= _ALPHA_FOREGROUND_THRESHOLD
    return bool(alpha.min() < 250 and support.any() and float(support.mean()) < 0.95)


def _border_pixels(rgb: np.ndarray) -> np.ndarray:
    return np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]), axis=0)


def _segmentation_inputs(rgb: np.ndarray) -> tuple[np.ndarray, float, float]:
    border = _border_pixels(rgb).astype(np.float64)
    background = np.median(border, axis=0)
    border_distances = np.linalg.norm(border - background, axis=1)
    threshold = max(18.0, float(np.percentile(border_distances, 95) * 2.5))
    distances = np.linalg.norm(rgb.astype(np.float64) - background, axis=2)
    border_uniformity = float(
        np.clip(1.0 - np.percentile(border_distances, 90) / 48.0, 0.0, 1.0)
    )
    return distances, threshold, border_uniformity


def _confidence(
    distances: np.ndarray,
    mask: np.ndarray,
    *,
    threshold: float,
    border_uniformity: float,
) -> float:
    if not mask.any():
        return 0.0
    fraction = float(mask.mean())
    fraction_factor = float(
        np.clip((fraction - 0.001) / 0.02, 0.0, 1.0)
        * np.clip((0.95 - fraction) / 0.15, 0.0, 1.0)
    )
    separation = float(
        np.clip(
            (float(np.percentile(distances[mask], 10)) / max(threshold, 1.0) - 1.0)
            / 2.0,
            0.0,
            1.0,
        )
    )
    return float(np.clip(border_uniformity * fraction_factor * separation, 0.0, 1.0))


def _neighbors(y: int, x: int, height: int, width: int) -> tuple[tuple[int, int], ...]:
    values: list[tuple[int, int]] = []
    if y > 0:
        values.append((y - 1, x))
    if y + 1 < height:
        values.append((y + 1, x))
    if x > 0:
        values.append((y, x - 1))
    if x + 1 < width:
        values.append((y, x + 1))
    return tuple(values)


def _components(mask: np.ndarray) -> tuple[np.ndarray, ...]:
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    components: list[np.ndarray] = []
    for raw_y, raw_x in np.argwhere(mask):
        y, x = int(raw_y), int(raw_x)
        if visited[y, x]:
            continue
        visited[y, x] = True
        queue = deque(((y, x),))
        points: list[tuple[int, int]] = []
        while queue:
            current_y, current_x = queue.popleft()
            points.append((current_y, current_x))
            for next_y, next_x in _neighbors(current_y, current_x, height, width):
                if mask[next_y, next_x] and not visited[next_y, next_x]:
                    visited[next_y, next_x] = True
                    queue.append((next_y, next_x))
        component = np.zeros_like(mask, dtype=bool)
        rows, columns = zip(*points, strict=True)
        component[np.asarray(rows), np.asarray(columns)] = True
        components.append(component)
    components.sort(key=lambda item: _component_sort_key(item, width))
    return tuple(components)


def _component_sort_key(mask: np.ndarray, width: int) -> tuple[int, int, int]:
    points = np.argwhere(mask)
    first_y, first_x = points.min(axis=0)
    return int(first_y), int(first_x), -int(mask.sum())


def _hole_count(mask: np.ndarray) -> int:
    height, width = mask.shape
    background = np.logical_not(mask)
    exterior = np.zeros_like(mask, dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    for y, x in np.argwhere(background):
        row, column = int(y), int(x)
        if row in {0, height - 1} or column in {0, width - 1}:
            exterior[row, column] = True
            queue.append((row, column))
    while queue:
        y, x = queue.popleft()
        for next_y, next_x in _neighbors(y, x, height, width):
            if background[next_y, next_x] and not exterior[next_y, next_x]:
                exterior[next_y, next_x] = True
                queue.append((next_y, next_x))
    return len(_components(np.logical_and(background, np.logical_not(exterior))))


def _interior_holes(mask: np.ndarray) -> tuple[np.ndarray, ...]:
    """返回不与画布边界连通的背景区域。."""
    height, width = mask.shape
    background = np.logical_not(mask)
    exterior = np.zeros_like(mask, dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    for raw_y, raw_x in np.argwhere(background):
        y, x = int(raw_y), int(raw_x)
        if y in {0, height - 1} or x in {0, width - 1}:
            exterior[y, x] = True
            queue.append((y, x))
    while queue:
        y, x = queue.popleft()
        for next_y, next_x in _neighbors(y, x, height, width):
            if background[next_y, next_x] and not exterior[next_y, next_x]:
                exterior[next_y, next_x] = True
                queue.append((next_y, next_x))
    return _components(np.logical_and(background, np.logical_not(exterior)))


def _clean_alpha_mask(mask: np.ndarray) -> np.ndarray:
    """移除亚像素碎片并填平微小透明噪点，保留可见的大孔。."""
    minimum_component = max(4, math.ceil(mask.size * _MIN_COMPONENT_AREA_RATIO))
    components = _components(mask)
    kept = [item for item in components if int(item.sum()) >= minimum_component]
    if not kept and components:
        kept = [max(components, key=lambda item: int(item.sum()))]
    if not kept:
        return np.zeros_like(mask, dtype=bool)
    cleaned: np.ndarray = np.asarray(np.logical_or.reduce(kept), dtype=np.bool_)
    maximum_noise_hole = max(4, math.ceil(mask.size * _MIN_HOLE_AREA_RATIO))
    for hole in _interior_holes(cleaned):
        if int(hole.sum()) <= maximum_noise_hole:
            cleaned = np.logical_or(cleaned, hole)
    return cleaned


def _fill_all_holes(mask: np.ndarray) -> np.ndarray:
    """填充全部闭合背景区，作为无 alpha 输入的 solid 竞争假设。."""
    filled = np.array(mask, dtype=bool, copy=True)
    for hole in _interior_holes(mask):
        filled = np.logical_or(filled, hole)
    return np.asarray(filled, dtype=np.bool_)


def _radial_profile(
    mask: np.ndarray,
) -> (
    tuple[
        float,
        float,
        float,
        tuple[float, float],
        tuple[float, float],
        float,
        float,
    ]
    | None
):
    """估计椭圆归一化后的内外径稳定度与角向覆盖率。."""
    points = np.argwhere(mask)
    if len(points) < _RADIAL_PROFILE_BIN_COUNT:
        return None
    min_y, min_x = points.min(axis=0)
    max_y, max_x = points.max(axis=0)
    center_y = float((min_y + max_y + 1) * 0.5)
    center_x = float((min_x + max_x + 1) * 0.5)
    axis_y = max(float((max_y - min_y + 1) * 0.5), 1.0)
    axis_x = max(float((max_x - min_x + 1) * 0.5), 1.0)
    delta_y = (points[:, 0].astype(np.float64) + 0.5 - center_y) / axis_y
    delta_x = (points[:, 1].astype(np.float64) + 0.5 - center_x) / axis_x
    radii = np.hypot(delta_x, delta_y)
    angles = (np.arctan2(delta_y, delta_x) + 2.0 * math.pi) % (2.0 * math.pi)
    bins = np.floor(angles / (2.0 * math.pi) * _RADIAL_PROFILE_BIN_COUNT).astype(
        np.int32
    )
    inner: list[float] = []
    outer: list[float] = []
    for bin_index in range(_RADIAL_PROFILE_BIN_COUNT):
        values = radii[bins == bin_index]
        if len(values):
            inner.append(float(np.percentile(values, 5)))
            outer.append(float(np.percentile(values, 95)))
    coverage = len(outer) / _RADIAL_PROFILE_BIN_COUNT
    if coverage < 0.85 or not inner:
        return None
    inner_mean = float(np.mean(inner))
    outer_mean = float(np.mean(outer))
    if inner_mean <= 0.0 or outer_mean <= inner_mean:
        return None
    inner_cv = float(np.std(inner) / inner_mean)
    outer_cv = float(np.std(outer) / outer_mean)
    return (
        inner_cv,
        outer_cv,
        coverage,
        (center_y, center_x),
        (axis_y, axis_x),
        float(np.median(inner)),
        float(np.median(outer)),
    )


def _is_radial_ring(mask: np.ndarray) -> bool:
    profile = _radial_profile(mask)
    if profile is None:
        return False
    inner_cv, outer_cv, _coverage, _center, _axes, inner, outer = profile
    raster_tolerance = 3.5 / min(mask.shape)
    cv_limit = _RADIAL_RING_CV_LIMIT + raster_tolerance
    return bool(
        inner_cv <= cv_limit
        and outer_cv <= cv_limit
        and inner >= 0.15
        and outer - inner >= 0.04
    )


def _semantic_ring_masks(
    literal_mask: np.ndarray,
) -> tuple[np.ndarray, tuple[np.ndarray, ...]] | None:
    """把角向分段的径向带解释为低置信语义 ring 假设。."""
    components = _components(literal_mask)
    if len(components) < 3 or not _is_radial_ring(literal_mask):
        return None
    profile = _radial_profile(literal_mask)
    if profile is None:  # pragma: no cover - 已由 _is_radial_ring 保证
        return None
    _inner_cv, _outer_cv, _coverage, center, axes, inner, outer = profile
    center_y, center_x = center
    axis_y, axis_x = axes
    rows, columns = np.indices(literal_mask.shape, dtype=np.float64)
    normalized_y = (rows + 0.5 - center_y) / axis_y
    normalized_x = (columns + 0.5 - center_x) / axis_x
    radii = np.hypot(normalized_x, normalized_y)
    # percentile radial closure 可能裁掉抗锯齿后的 raw 极值像素；语义 subject
    # 必须显式包含原始观测，不能用拟合 ring 覆盖/改写 source facts。
    ring_mask = np.logical_or(
        np.logical_and(radii >= inner, radii <= outer),
        literal_mask,
    )
    if _hole_count(ring_mask) != 1 or len(_components(ring_mask)) != 1:
        return None

    component_angles: list[float] = []
    for component in components:
        points = np.argwhere(component).astype(np.float64)
        component_y = float(np.mean(points[:, 0] + 0.5))
        component_x = float(np.mean(points[:, 1] + 0.5))
        component_angles.append(
            float(
                (
                    math.atan2(
                        (component_y - center_y) / axis_y,
                        (component_x - center_x) / axis_x,
                    )
                    + 2.0 * math.pi
                )
                % (2.0 * math.pi)
            )
        )
    pixel_angles = (np.arctan2(normalized_y, normalized_x) + 2.0 * math.pi) % (
        2.0 * math.pi
    )
    distances = np.stack(
        [
            np.abs((pixel_angles - angle + math.pi) % (2.0 * math.pi) - math.pi)
            for angle in component_angles
        ],
        axis=0,
    )
    assignments = np.argmin(distances, axis=0)
    instances = tuple(
        np.logical_and(ring_mask, assignments == index)
        for index in range(len(components))
    )
    if any(not item.any() or len(_components(item)) != 1 for item in instances):
        return None
    if not np.array_equal(np.logical_or.reduce(instances), ring_mask):
        return None
    return ring_mask, instances


def _dominant_color_instance_partition(
    rgb: np.ndarray,
    mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray] | None:
    """用两个大面积、相离色模态生成可重放的连通实例分区。."""
    pixels = rgb[mask]
    if len(pixels) < 32:
        return None
    quantized = (pixels // 16).astype(np.int32)
    keys = quantized[:, 0] * 256 + quantized[:, 1] * 16 + quantized[:, 2]
    values, counts = np.unique(keys, return_counts=True)
    if len(values) < 2:
        return None
    order = sorted(
        range(len(values)),
        key=lambda index: (-int(counts[index]), int(values[index])),
    )
    first_index, second_index = order[:2]
    total = int(counts.sum())
    if (
        int(counts[first_index]) / total < 0.25
        or int(counts[second_index]) / total < 0.25
    ):
        return None
    first_seed = pixels[keys == values[first_index]].astype(np.float64).mean(axis=0)
    second_seed = pixels[keys == values[second_index]].astype(np.float64).mean(axis=0)
    if float(np.linalg.norm(first_seed - second_seed)) < 80.0:
        return None

    all_distances = np.stack(
        (
            np.linalg.norm(rgb.astype(np.float64) - first_seed, axis=2),
            np.linalg.norm(rgb.astype(np.float64) - second_seed, axis=2),
        ),
        axis=0,
    )
    assignment = np.argmin(all_distances, axis=0)
    instances = (
        np.logical_and(mask, assignment == 0),
        np.logical_and(mask, assignment == 1),
    )
    if any(not item.any() or len(_components(item)) != 1 for item in instances):
        return None
    if not np.array_equal(np.logical_or(instances[0], instances[1]), mask):
        return None
    fractions = tuple(float(item.sum() / mask.sum()) for item in instances)
    if any(value < 0.20 or value > 0.80 for value in fractions):
        return None

    subject_points = np.argwhere(mask).astype(np.float64)
    subject_min = subject_points.min(axis=0)
    subject_max = subject_points.max(axis=0)
    subject_diagonal = float(np.linalg.norm(subject_max - subject_min))
    centers = tuple(
        np.argwhere(item).astype(np.float64).mean(axis=0) for item in instances
    )
    if subject_diagonal <= 0.0 or (
        float(np.linalg.norm(centers[0] - centers[1])) / subject_diagonal < 0.20
    ):
        return None
    return instances


def _topology_for_mask(
    mask: np.ndarray, *, hint: str
) -> Literal["solid", "hollow", "ring", "open"]:
    holes = _hole_count(mask)
    if hint == "ring":
        return "ring"
    if hint == "open":
        return "open"
    if holes:
        return "ring" if _is_radial_ring(mask) else "hollow"
    if _is_open_topology(mask):
        return "open"
    return "solid"


def _is_open_topology(mask: np.ndarray) -> bool:
    """冻结 open 判定：中心 aperture 连通外界且主体覆盖至少三个象限。."""
    points = np.argwhere(mask)
    if not len(points) or len(_components(mask)) != 1:
        return False
    min_y, min_x = points.min(axis=0)
    max_y, max_x = points.max(axis=0)
    center_y = int(round((float(min_y) + float(max_y)) * 0.5))
    center_x = int(round((float(min_x) + float(max_x)) * 0.5))
    radius = max(1, min(max_y - min_y + 1, max_x - min_x + 1) // 8)
    center_patch = mask[
        max(0, center_y - radius) : center_y + radius + 1,
        max(0, center_x - radius) : center_x + radius + 1,
    ]
    if center_patch.any():
        return False
    quadrants = (
        mask[min_y : center_y + 1, min_x : center_x + 1],
        mask[min_y : center_y + 1, center_x : max_x + 1],
        mask[center_y : max_y + 1, min_x : center_x + 1],
        mask[center_y : max_y + 1, center_x : max_x + 1],
    )
    return sum(bool(item.any()) for item in quadrants) >= 3


def classify_instance_mask_topology_v2(
    mask: tuple[bool, ...],
    *,
    width: int,
    height: int,
) -> Literal["solid", "hollow", "ring", "open"]:
    """按 producer 同一冻结算法对一张 instance mask 分类."""
    if width <= 0 or height <= 0 or len(mask) != width * height:
        raise ValueError("Instance topology mask 尺寸不一致。")
    array = np.asarray(mask, dtype=bool).reshape((height, width))
    if not array.any():
        raise ValueError("Instance topology mask 不得为空。")
    return _topology_for_mask(array, hint="auto")


def _instance_relation_kind(
    left: np.ndarray,
    right: np.ndarray,
) -> Literal["overlap", "touches", "disjoint"]:
    """从两张 instance mask 像素重测关系.

    instance partition 不会自行推断 contains/subtracts；这两种关系只能
    由更强的显式证据进入后续 Intent。
    """
    if np.logical_and(left, right).any():
        return "overlap"
    adjacent = np.zeros_like(left, dtype=bool)
    adjacent[1:, :] |= left[:-1, :]
    adjacent[:-1, :] |= left[1:, :]
    adjacent[:, 1:] |= left[:, :-1]
    adjacent[:, :-1] |= left[:, 1:]
    return "touches" if np.logical_and(adjacent, right).any() else "disjoint"


def _minimal_angular_interval(
    angles: np.ndarray,
) -> tuple[float, float]:
    """返回跨 2π 稳定的最小 covering arc center/span。."""
    ordered = np.sort(np.asarray(angles, dtype=np.float64) % (2.0 * math.pi))
    if not len(ordered):
        raise InvalidTargetImageError("radial segment 不得为空。")
    if len(ordered) == 1:
        return float(ordered[0]), float(np.finfo(np.float64).eps)
    wrapped = np.concatenate((ordered, ordered[:1] + 2.0 * math.pi))
    gaps = np.diff(wrapped)
    largest_gap_index = int(np.argmax(gaps))
    start = float(ordered[(largest_gap_index + 1) % len(ordered)])
    span = float(2.0 * math.pi - gaps[largest_gap_index])
    if span <= 0.0 or span >= 2.0 * math.pi:
        raise InvalidTargetImageError("radial segment angular span 无效。")
    center = float((start + 0.5 * span) % (2.0 * math.pi))
    return center, span


def _read_exact_ref(resolver: ArtifactResolver, ref: ArtifactRefV2) -> bytes:
    resolved = resolver.resolve(ref.artifact_id)
    if resolved != ref:
        raise ValueError("radial segment ArtifactRef 与 resolver 记录不一致。")
    data = resolver.read_bytes(ref.artifact_id)
    if len(data) != ref.size_bytes or sha256(data).hexdigest() != ref.sha256:
        raise ValueError("radial segment Artifact bytes 完整性失败。")
    return data


def _decode_binary_mask_png(data: bytes) -> np.ndarray:
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG" or image.mode not in {"1", "L"}:
                raise ValueError("radial segment mask 必须是 binary PNG。")
            values = np.asarray(image.convert("L"), dtype=np.uint8)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("radial segment mask PNG 无效。") from exc
    if not values.size or np.any(np.logical_and(values != 0, values != 255)):
        raise ValueError("radial segment mask 只允许 0/255 pixels。")
    result: np.ndarray = np.asarray(values == 255, dtype=np.bool_)
    return result


def _build_radial_segment_structure_evidence(
    *,
    target_sha256: str,
    target_source_ref: ArtifactRefV2,
    raw_subject_mask_ref: ArtifactRefV2,
    semantic_subject_mask_ref: ArtifactRefV2,
    raw_segment_mask_refs: tuple[ArtifactRefV2, ...],
    ownership_mask_refs: tuple[ArtifactRefV2, ...],
    raw_subject_mask: np.ndarray,
    semantic_subject_mask: np.ndarray,
    raw_segment_masks: tuple[np.ndarray, ...],
    ownership_masks: tuple[np.ndarray, ...],
) -> RadialSegmentStructureEvidenceV1:
    """从两套 mask 重建 typed segment evidence；不信任 case metadata。."""
    count = len(raw_segment_masks)
    if count < 3 or len(ownership_masks) != count:
        raise InvalidTargetImageError("radial segment evidence 至少需要三段一一映射。")
    if len(raw_segment_mask_refs) != count or len(ownership_mask_refs) != count:
        raise InvalidTargetImageError("radial segment mask refs 未形成一一映射。")
    shape = semantic_subject_mask.shape
    if raw_subject_mask.shape != shape or any(
        item.shape != shape for item in (*raw_segment_masks, *ownership_masks)
    ):
        raise InvalidTargetImageError("radial segment masks 尺寸不一致。")
    if not np.array_equal(np.logical_or.reduce(raw_segment_masks), raw_subject_mask):
        raise InvalidTargetImageError("raw segment union 必须精确等于 raw subject。")
    if np.logical_and(raw_subject_mask, np.logical_not(semantic_subject_mask)).any():
        raise InvalidTargetImageError("raw segment union 必须位于 semantic subject 内。")
    if not np.array_equal(
        np.logical_or.reduce(ownership_masks), semantic_subject_mask
    ):
        raise InvalidTargetImageError("ownership union 必须精确等于 semantic subject。")
    if any(
        np.logical_and(left, right).any()
        for index, left in enumerate(ownership_masks)
        for right in ownership_masks[index + 1 :]
    ):
        raise InvalidTargetImageError("radial segment ownership masks 必须互斥。")

    profile = _radial_profile(semantic_subject_mask)
    if profile is None:
        raise InvalidTargetImageError("semantic subject 缺少稳定 radial profile。")
    _inner_cv, _outer_cv, _coverage, center, axes, _inner, _outer = profile
    center_y, center_x = center
    axis_y, axis_x = axes
    height, width = shape
    segment_records: list[RadialSegmentInstanceEvidenceV1] = []
    for index, (raw, ownership) in enumerate(
        zip(raw_segment_masks, ownership_masks, strict=True)
    ):
        if not raw.any() or not ownership.any():
            raise InvalidTargetImageError("radial segment raw/ownership mask 不得为空。")
        if len(_components(raw)) != 1 or _hole_count(raw) != 0:
            raise InvalidTargetImageError("raw segment 必须是单连通、零孔 solid。")
        if np.logical_and(raw, np.logical_not(ownership)).any():
            raise InvalidTargetImageError("raw segment 必须落在同 index ownership 内。")
        points = np.argwhere(raw).astype(np.float64)
        normalized_y = (center_y - (points[:, 0] + 0.5)) / axis_y
        normalized_x = (points[:, 1] + 0.5 - center_x) / axis_x
        radii = np.hypot(normalized_x, normalized_y)
        angles = (np.arctan2(normalized_y, normalized_x) + 2.0 * math.pi) % (
            2.0 * math.pi
        )
        angular_center, angular_span = _minimal_angular_interval(angles)
        segment_records.append(
            RadialSegmentInstanceEvidenceV1(
                instance_index=index,
                raw_segment_mask_ref=raw_segment_mask_refs[index],
                ownership_mask_ref=ownership_mask_refs[index],
                radial_center_uv=(
                    float(center_x / width),
                    float(1.0 - center_y / height),
                ),
                radial_axes_uv=(float(axis_x / width), float(axis_y / height)),
                inner_radius_ratio=float(np.min(radii)),
                outer_radius_ratio=float(np.max(radii)),
                angular_center_rad=angular_center,
                angular_span_rad=angular_span,
                raw_pixel_count=int(raw.sum()),
                ownership_pixel_count=int(ownership.sum()),
            )
        )
    relations: list[RadialSegmentRelationEvidenceV1] = []
    for left_index, left in enumerate(raw_segment_masks):
        for right_index, right in enumerate(
            raw_segment_masks[left_index + 1 :], start=left_index + 1
        ):
            if _instance_relation_kind(left, right) != "disjoint":
                raise InvalidTargetImageError("raw radial segments 必须彼此 disjoint。")
            relations.append(
                RadialSegmentRelationEvidenceV1(
                    left_instance_index=left_index,
                    right_instance_index=right_index,
                )
            )
    return RadialSegmentStructureEvidenceV1(
        target_sha256=target_sha256,
        target_source_ref=target_source_ref,
        raw_subject_mask_ref=raw_subject_mask_ref,
        semantic_subject_mask_ref=semantic_subject_mask_ref,
        alpha_foreground_threshold=_ALPHA_FOREGROUND_THRESHOLD,
        radial_profile_bin_count=_RADIAL_PROFILE_BIN_COUNT,
        segments=tuple(segment_records),
        raw_relations=tuple(relations),
    )


def verify_radial_segment_structure_evidence_v1(
    evidence_ref: ArtifactRefV2,
    *,
    resolver: ArtifactResolver,
) -> RadialSegmentStructureEvidenceV1:
    """恢复并从 source/mask bytes 重建 segment evidence，篡改时 fail closed。."""
    if (
        evidence_ref.kind != "radial_segment_structure_evidence"
        or evidence_ref.schema_version != RADIAL_SEGMENT_EVIDENCE_SCHEMA_VERSION
        or evidence_ref.content_type != "application/json"
    ):
        raise ValueError("radial segment evidence ref contract 无效。")
    payload = _read_exact_ref(resolver, evidence_ref)
    try:
        evidence = RadialSegmentStructureEvidenceV1.model_validate_json(
            payload, strict=True
        )
    except ValueError as exc:
        raise ValueError("radial segment evidence payload 无效。") from exc
    if evidence.model_dump_json().encode("utf-8") != payload:
        raise ValueError("radial segment evidence 必须使用规范 Pydantic JSON。")
    source_bytes = _read_exact_ref(resolver, evidence.target_source_ref)
    if sha256(source_bytes).hexdigest() != evidence.target_sha256:
        raise ValueError("radial segment source identity 不一致。")
    raw_subject = _decode_binary_mask_png(
        _read_exact_ref(resolver, evidence.raw_subject_mask_ref)
    )
    semantic_subject = _decode_binary_mask_png(
        _read_exact_ref(resolver, evidence.semantic_subject_mask_ref)
    )
    raw_masks = tuple(
        _decode_binary_mask_png(_read_exact_ref(resolver, item.raw_segment_mask_ref))
        for item in evidence.segments
    )
    ownership_masks = tuple(
        _decode_binary_mask_png(_read_exact_ref(resolver, item.ownership_mask_ref))
        for item in evidence.segments
    )
    if evidence.alpha_foreground_threshold != _ALPHA_FOREGROUND_THRESHOLD:
        raise ValueError("radial segment alpha threshold 版本漂移。")
    if evidence.radial_profile_bin_count != _RADIAL_PROFILE_BIN_COUNT:
        raise ValueError("radial segment radial profile 版本漂移。")
    expected_alpha = _decode_source_alpha(
        source_bytes,
        expected_size=(raw_subject.shape[1], raw_subject.shape[0]),
    )
    if not _has_meaningful_alpha(expected_alpha) or not np.array_equal(
        _clean_alpha_mask(expected_alpha >= _ALPHA_FOREGROUND_THRESHOLD), raw_subject
    ):
        raise ValueError("raw segment subject 不能从 source alpha 重放。")
    rebuilt = _build_radial_segment_structure_evidence(
        target_sha256=evidence.target_sha256,
        target_source_ref=evidence.target_source_ref,
        raw_subject_mask_ref=evidence.raw_subject_mask_ref,
        semantic_subject_mask_ref=evidence.semantic_subject_mask_ref,
        raw_segment_mask_refs=tuple(
            item.raw_segment_mask_ref for item in evidence.segments
        ),
        ownership_mask_refs=tuple(item.ownership_mask_ref for item in evidence.segments),
        raw_subject_mask=raw_subject,
        semantic_subject_mask=semantic_subject,
        raw_segment_masks=raw_masks,
        ownership_masks=ownership_masks,
    )
    if rebuilt != evidence:
        raise ValueError("radial segment evidence 与 source/mask 重建结果不一致。")
    return evidence


def _draft_mask(draft: _MaskDraft) -> np.ndarray:
    return np.asarray(draft.mask, dtype=bool).reshape((draft.height, draft.width))


def _draft_instances(draft: _MaskDraft) -> tuple[np.ndarray, ...]:
    if draft.instance_masks:
        return tuple(
            np.asarray(item, dtype=bool).reshape((draft.height, draft.width))
            for item in draft.instance_masks
        )
    return _components(_draft_mask(draft))


def _bbox(mask: np.ndarray) -> BBoxUv:
    points = np.argwhere(mask)
    if not len(points):
        raise InvalidTargetImageError("V2 subject mask 不能为空。")
    height, width = mask.shape
    min_y, min_x = points.min(axis=0)
    max_y, max_x = points.max(axis=0)
    return BBoxUv(
        min_x=float(min_x / width),
        min_y=float(1.0 - (max_y + 1) / height),
        max_x=float((max_x + 1) / width),
        max_y=float(1.0 - min_y / height),
    )


def _geometry(
    mask: np.ndarray,
) -> tuple[BBoxUv, tuple[float, float], tuple[float, float], float]:
    points = np.argwhere(mask).astype(np.float64)
    height, width = mask.shape
    bbox = _bbox(mask)
    center_x = float(np.mean((points[:, 1] + 0.5) / width))
    center_y = float(np.mean(1.0 - (points[:, 0] + 0.5) / height))
    uv = np.column_stack(
        (
            (points[:, 1] + 0.5) / width,
            1.0 - (points[:, 0] + 0.5) / height,
        )
    )
    if len(uv) > 1:
        covariance = np.cov(uv, rowvar=False, bias=True)
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        order = np.argsort(eigenvalues)[::-1]
        major = eigenvectors[:, order[0]]
        orientation = float(math.atan2(float(major[1]), float(major[0])))
    else:
        orientation = 0.0
    axes = (
        max((bbox.max_x - bbox.min_x) * 0.5, 0.5 / width),
        max((bbox.max_y - bbox.min_y) * 0.5, 0.5 / height),
    )
    return bbox, (center_x, center_y), axes, orientation


def _mask_png(mask: np.ndarray) -> bytes:
    output = BytesIO()
    Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), mode="L").save(
        output,
        format="PNG",
        compress_level=9,
    )
    return output.getvalue()


def _edge_mask(mask: np.ndarray) -> np.ndarray:
    height, width = mask.shape
    result = np.zeros_like(mask, dtype=bool)
    for raw_y, raw_x in np.argwhere(mask):
        y, x = int(raw_y), int(raw_x)
        if any(
            not mask[next_y, next_x]
            for next_y, next_x in _neighbors(y, x, height, width)
        ):
            result[y, x] = True
        elif y in {0, height - 1} or x in {0, width - 1}:
            result[y, x] = True
    return result


def _srgb_to_lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    normalized = np.asarray(rgb, dtype=np.float64) / 255.0
    linear = np.where(
        normalized <= 0.04045,
        normalized / 12.92,
        ((normalized + 0.055) / 1.055) ** 2.4,
    )
    xyz = np.asarray(
        (
            linear[0] * 0.4124564 + linear[1] * 0.3575761 + linear[2] * 0.1804375,
            linear[0] * 0.2126729 + linear[1] * 0.7151522 + linear[2] * 0.0721750,
            linear[0] * 0.0193339 + linear[1] * 0.1191920 + linear[2] * 0.9503041,
        )
    )
    ratios = xyz / np.asarray((0.95047, 1.0, 1.08883))
    delta = 6.0 / 29.0
    transformed = np.where(
        ratios > delta**3,
        np.cbrt(ratios),
        ratios / (3 * delta**2) + 4.0 / 29.0,
    )
    return (
        float(116.0 * transformed[1] - 16.0),
        float(500.0 * (transformed[0] - transformed[1])),
        float(200.0 * (transformed[1] - transformed[2])),
    )


def _palette_lab(
    rgb: np.ndarray, mask: np.ndarray, limit: int = 5
) -> tuple[LabSample, ...]:
    pixels = rgb[mask]
    quantized = (pixels // 32).astype(np.int32)
    keys = quantized[:, 0] * 64 + quantized[:, 1] * 8 + quantized[:, 2]
    values, counts = np.unique(keys, return_counts=True)
    order = sorted(
        range(len(values)), key=lambda index: (-int(counts[index]), int(values[index]))
    )[:limit]
    total = int(counts.sum())
    samples: list[LabSample] = []
    for index in order:
        selected = pixels[keys == values[index]].astype(np.float64)
        mean = selected.mean(axis=0)
        mean_rgb = (float(mean[0]), float(mean[1]), float(mean[2]))
        samples.append(
            LabSample(
                lab=_srgb_to_lab(mean_rgb),
                weight=float(counts[index] / total),
            )
        )
    return tuple(samples)


def _mean_lab(rgb: np.ndarray, mask: np.ndarray) -> tuple[float, float, float]:
    mean = rgb[mask].astype(np.float64).mean(axis=0)
    mean_rgb = (float(mean[0]), float(mean[1]), float(mean[2]))
    return _srgb_to_lab(mean_rgb)


def _symmetry_score(mask: np.ndarray, transformed: np.ndarray) -> float:
    union = np.logical_or(mask, transformed)
    if not union.any():
        return 1.0
    mismatch = np.logical_xor(mask, transformed)
    return float(np.clip(1.0 - mismatch.sum() / union.sum(), 0.0, 1.0))


def _symmetry(mask: np.ndarray) -> SymmetryEvidence:
    return SymmetryEvidence(
        horizontal=_symmetry_score(mask, np.fliplr(mask)),
        vertical=_symmetry_score(mask, np.flipud(mask)),
        radial=_symmetry_score(mask, np.flip(mask, axis=(0, 1))),
    )


def _gradient_evidence(
    rgb: np.ndarray,
    regions: tuple[tuple[str, np.ndarray], ...],
) -> tuple[GradientEvidence, ...]:
    normalized = rgb.astype(np.float64) / 255.0
    gray = (
        normalized[..., 0] * 0.2126
        + normalized[..., 1] * 0.7152
        + normalized[..., 2] * 0.0722
    )
    delta_y, delta_x = np.gradient(gray)
    result: list[GradientEvidence] = []
    for region_id, mask in regions:
        mean_x = float(np.extract(mask, delta_x).mean())
        mean_y = float(np.extract(mask, delta_y).mean())
        magnitude = math.hypot(mean_x, mean_y)
        direction = (
            (0.0, 0.0)
            if magnitude <= 1e-12
            else (mean_x / magnitude, -mean_y / magnitude)
        )
        result.append(
            GradientEvidence(
                region_id=region_id,
                direction_uv=direction,
                strength=float(np.clip(magnitude * 4.0, 0.0, 1.0)),
            )
        )
    return tuple(result)


def _artifact_projection(ref: ArtifactRefV2) -> dict[str, object]:
    return {
        "artifact_id": ref.artifact_id,
        "sha256": ref.sha256,
        "kind": ref.kind,
        "schema_version": ref.schema_version,
        "content_type": ref.content_type,
        "size_bytes": ref.size_bytes,
    }


def _put(
    catalog: ArtifactCatalog,
    *,
    run_id: str,
    kind: str,
    schema_version: str,
    content_type: str,
    data: bytes,
) -> ArtifactRefV2:
    return catalog.put(
        run_id=run_id,
        kind=kind,
        schema_version=schema_version,
        content_type=content_type,
        data=data,
    )


def measure_target_v2(
    source_bytes: bytes,
    *,
    catalog: ArtifactCatalog,
    run_id: str,
    max_long_side: int = WEBGL1_STATIC_NO_TEXTURE_V1.max_long_side,
) -> TargetMeasurementsV2ArtifactBundle:
    """测量真实输入并把 MeasurementsV2 全证据写入内容寻址 Catalog。."""
    if not isinstance(source_bytes, bytes) or not source_bytes:
        raise InvalidTargetImageError("V2 source_bytes 必须是非空 bytes。")
    if not run_id.strip():
        raise ValueError("run_id 不能为空。")
    content_type = _source_content_type(source_bytes)
    normalized_png = normalize_target_png(source_bytes, max_long_side=max_long_side)
    rgb = _decode_normalized(normalized_png)
    height, width, _ = rgb.shape
    alpha = _decode_source_alpha(source_bytes, expected_size=(width, height))
    source_ref = _put(
        catalog,
        run_id=run_id,
        kind="target_source",
        schema_version="target_source_v1",
        content_type=content_type,
        data=source_bytes,
    )
    target_sha256 = sha256(source_bytes).hexdigest()
    normalized_ref = _put(
        catalog,
        run_id=run_id,
        kind="normalized_reference",
        schema_version="normalized_target_png_v1",
        content_type="image/png",
        data=normalized_png,
    )

    distances, threshold, border_uniformity = _segmentation_inputs(rgb)
    alpha_evidence = _has_meaningful_alpha(alpha)
    primary_derivation = "source_alpha_threshold_and_noise_filter"
    if alpha_evidence:
        primary_mask = _clean_alpha_mask(alpha >= _ALPHA_FOREGROUND_THRESHOLD)
        alpha_border = (
            _border_pixels(alpha[..., np.newaxis]).reshape(-1).astype(np.float64)
        )
        border_clear = float(
            np.clip(1.0 - np.percentile(alpha_border, 95) / 255.0, 0.0, 1.0)
        )
        alpha_opacity = float(np.percentile(alpha[primary_mask], 75) / 255.0)
        primary_confidence = float(
            np.clip(0.72 + 0.18 * border_clear + 0.10 * alpha_opacity, 0.0, 0.99)
        )
    else:
        primary_derivation = "normalized_rgb_border_distance"
        primary_mask = distances > threshold
        primary_confidence = _confidence(
            distances,
            primary_mask,
            threshold=threshold,
            border_uniformity=border_uniformity,
        )
    if not primary_mask.any() or float(primary_mask.mean()) >= 0.95:
        raise InvalidTargetImageError("无法从背景中分离有效 V2 subject mask。")
    semantic_ring = _semantic_ring_masks(primary_mask) if alpha_evidence else None
    rgb_holes = _hole_count(primary_mask) if not alpha_evidence else 0
    filled_solid_mask = _fill_all_holes(primary_mask) if rgb_holes > 0 else None
    color_partition = (
        _dominant_color_instance_partition(rgb, primary_mask)
        if not alpha_evidence and rgb_holes == 0
        else None
    )
    primary_topology_hint: Literal["auto", "open", "ring"] = "auto"
    if semantic_ring is not None:
        primary_topology_hint = "open"
        primary_confidence = min(primary_confidence, 0.68)
    if filled_solid_mask is not None or color_partition is not None:
        primary_confidence = min(primary_confidence, 0.68)
    drafts = [
        _MaskDraft(
            hypothesis_id="hypothesis_primary",
            confidence=primary_confidence,
            mask=tuple(bool(item) for item in primary_mask.flat),
            width=width,
            height=height,
            topology_hint=primary_topology_hint,
            derivation=primary_derivation,
        )
    ]
    if semantic_ring is not None:
        semantic_mask, semantic_instances = semantic_ring
        drafts.append(
            _MaskDraft(
                hypothesis_id="hypothesis_semantic_radial_ring",
                confidence=min(primary_confidence, 0.65),
                mask=tuple(bool(item) for item in semantic_mask.flat),
                instance_masks=tuple(
                    tuple(bool(item) for item in instance.flat)
                    for instance in semantic_instances
                ),
                width=width,
                height=height,
                topology_hint="ring",
                derivation="alpha_components_radial_ring_closure",
            )
        )
    if filled_solid_mask is not None:
        drafts.append(
            _MaskDraft(
                hypothesis_id="hypothesis_rgb_holes_filled_solid",
                confidence=min(primary_confidence, 0.65),
                mask=tuple(bool(item) for item in filled_solid_mask.flat),
                width=width,
                height=height,
                derivation="opaque_rgb_closed_holes_filled_solid",
            )
        )
    if color_partition is not None:
        drafts.append(
            _MaskDraft(
                hypothesis_id="hypothesis_dominant_color_instances",
                confidence=min(primary_confidence, 0.65),
                mask=tuple(bool(item) for item in primary_mask.flat),
                instance_masks=tuple(
                    tuple(bool(item) for item in instance.flat)
                    for instance in color_partition
                ),
                width=width,
                height=height,
                derivation="dominant_color_connected_instance_partition",
            )
        )
    low_confidence = primary_confidence < _LOW_CONFIDENCE_THRESHOLD
    alternate_retained = len(drafts) > 1
    if low_confidence and not alpha_evidence:
        alternate_threshold = max(8.0, threshold * 0.65)
        alternate_mask = distances > alternate_threshold
        if (
            alternate_mask.any()
            and float(alternate_mask.mean()) < 0.95
            and not np.array_equal(alternate_mask, primary_mask)
        ):
            alternate_retained = True
            drafts.append(
                _MaskDraft(
                    hypothesis_id="hypothesis_low_threshold",
                    confidence=min(
                        primary_confidence,
                        _confidence(
                            distances,
                            alternate_mask,
                            threshold=alternate_threshold,
                            border_uniformity=border_uniformity,
                        ),
                    ),
                    mask=tuple(bool(item) for item in alternate_mask.flat),
                    width=width,
                    height=height,
                    derivation="normalized_rgb_border_distance_low_threshold",
                )
            )

    uncertainty = MeasurementsV2Uncertainty(
        low_confidence=low_confidence,
        strategy=(
            "alternate_hypothesis_retained"
            if alternate_retained
            else "soft_only_manual_review"
            if low_confidence
            else "verification_required"
        ),
        primary_confidence=primary_confidence,
        hard_constraint_policy="soft_only"
        if low_confidence
        else "verification_required",
        reason_codes=(
            (
                "semantic_radial_ring_requires_verification",
                "alpha_literal_and_radial_closure_hypotheses_retained",
            )
            if semantic_ring is not None
            else (
                "opaque_rgb_hole_topology_ambiguous",
                "filled_solid_alternate_retained",
            )
            if filled_solid_mask is not None
            else (
                "dominant_color_instances_require_verification",
                "connected_color_partition_alternate_retained",
            )
            if color_partition is not None
            else ("foreground_separation_low", "alternate_threshold_retained")
            if alternate_retained
            else ("foreground_separation_low", "manual_or_model_segmentation_required")
            if low_confidence
            else (
                "deterministic_segmentation_confident_independent_verification_required",
            )
        ),
        alternate_hypothesis_ids=tuple(item.hypothesis_id for item in drafts[1:])
        if alternate_retained
        else (),
    )

    artifact_sets: list[HypothesisArtifactSet] = []
    mask_arrays: list[np.ndarray] = []
    edge_refs: list[ArtifactRefV2] = []
    for draft in drafts:
        mask = _draft_mask(draft)
        instances = _draft_instances(draft)
        subject_ref = _put(
            catalog,
            run_id=run_id,
            kind="subject_mask",
            schema_version="binary_mask_v1",
            content_type="image/png",
            data=_mask_png(mask),
        )
        instance_refs = tuple(
            _put(
                catalog,
                run_id=run_id,
                kind="instance_mask",
                schema_version="binary_mask_v1",
                content_type="image/png",
                data=_mask_png(component),
            )
            for component in instances
        )
        edge_ref = _put(
            catalog,
            run_id=run_id,
            kind="target_edge_mask",
            schema_version="binary_edge_mask_v1",
            content_type="image/png",
            data=_mask_png(_edge_mask(mask)),
        )
        artifact_sets.append(
            HypothesisArtifactSet(
                hypothesis_id=draft.hypothesis_id,
                subject_mask_ref=subject_ref,
                instance_mask_refs=instance_refs,
                edge_ref=edge_ref,
            )
        )
        mask_arrays.append(mask)
        edge_refs.append(edge_ref)

    if semantic_ring is not None:
        primary_index = next(
            index
            for index, draft in enumerate(drafts)
            if draft.hypothesis_id == "hypothesis_primary"
        )
        semantic_index = next(
            index
            for index, draft in enumerate(drafts)
            if draft.hypothesis_id == "hypothesis_semantic_radial_ring"
        )
        primary_artifacts = artifact_sets[primary_index]
        semantic_artifacts = artifact_sets[semantic_index]
        segment_evidence = _build_radial_segment_structure_evidence(
            target_sha256=target_sha256,
            target_source_ref=source_ref,
            raw_subject_mask_ref=primary_artifacts.subject_mask_ref,
            semantic_subject_mask_ref=semantic_artifacts.subject_mask_ref,
            raw_segment_mask_refs=primary_artifacts.instance_mask_refs,
            ownership_mask_refs=semantic_artifacts.instance_mask_refs,
            raw_subject_mask=mask_arrays[primary_index],
            semantic_subject_mask=mask_arrays[semantic_index],
            raw_segment_masks=_draft_instances(drafts[primary_index]),
            ownership_masks=_draft_instances(drafts[semantic_index]),
        )
        segment_ref = _put(
            catalog,
            run_id=run_id,
            kind="radial_segment_structure_evidence",
            schema_version=RADIAL_SEGMENT_EVIDENCE_SCHEMA_VERSION,
            content_type="application/json",
            data=segment_evidence.model_dump_json().encode("utf-8"),
        )
        artifact_sets[semantic_index] = semantic_artifacts.model_copy(
            update={"radial_segment_evidence_ref": segment_ref}
        )

    evidence_index_payload = {
        "schema_version": "target_evidence_index_v1",
        "producer_version": MEASUREMENTS_V2_PRODUCER_VERSION,
        "target_source": _artifact_projection(source_ref),
        "normalized_reference": _artifact_projection(normalized_ref),
        "segmentation": {
            "connectivity": 4,
            "primary_threshold_rgb_distance": threshold,
            "alpha_evidence_used": alpha_evidence,
            "alpha_foreground_threshold": _ALPHA_FOREGROUND_THRESHOLD,
            "minimum_component_area_ratio": _MIN_COMPONENT_AREA_RATIO,
            "maximum_noise_hole_area_ratio": _MIN_HOLE_AREA_RATIO,
            "low_confidence_threshold": _LOW_CONFIDENCE_THRESHOLD,
            "uncertainty": uncertainty,
        },
        "hypotheses": [
            {
                "hypothesis_id": item.hypothesis_id,
                "derivation": draft.derivation,
                "topology_hint": draft.topology_hint,
                "instance_relation_source": "deterministic_pairwise_mask_pixels_v2",
                "subject_mask": _artifact_projection(item.subject_mask_ref),
                "instance_masks": [
                    _artifact_projection(ref) for ref in item.instance_mask_refs
                ],
                "instance_geometry_source": (
                    "deterministic_remeasurement_from_instance_mask_pixels_v2"
                ),
                "radial_segment_evidence": (
                    None
                    if item.radial_segment_evidence_ref is None
                    else _artifact_projection(item.radial_segment_evidence_ref)
                ),
                "edge": _artifact_projection(item.edge_ref),
            }
            for draft, item in zip(drafts, artifact_sets, strict=True)
        ],
        "hypothesis_neutral_statistics": {
            "region_basis": "source_visible_alpha"
            if alpha_evidence
            else "full_normalized_image",
            "region_ids_are_not_hypothesis_subject_or_instance_ids": True,
        },
    }
    evidence_index_ref = _put(
        catalog,
        run_id=run_id,
        kind="target_evidence_index",
        schema_version="target_evidence_index_v1",
        content_type="application/json",
        data=canonical_json_bytes(evidence_index_payload),
    )

    hypotheses: list[TargetHypothesis] = []
    for draft, mask, artifact_set in zip(
        drafts, mask_arrays, artifact_sets, strict=True
    ):
        instances = _draft_instances(draft)
        bbox, center, axes, orientation = _geometry(mask)
        holes = _hole_count(mask)
        topology = _topology_for_mask(mask, hint=draft.topology_hint)
        relations = tuple(
            MeasuredRelation(
                relation_id=(
                    f"{draft.hypothesis_id}:"
                    f"{_instance_relation_kind(instances[left_index], instances[right_index])}:"
                    f"{left_index}:{right_index}"
                ),
                kind=_instance_relation_kind(
                    instances[left_index], instances[right_index]
                ),
                subject_ref=f"instance_{left_index:04d}",
                object_ref=f"instance_{right_index:04d}",
                confidence=draft.confidence,
                evidence_refs=(
                    artifact_set.instance_mask_refs[left_index],
                    artifact_set.instance_mask_refs[right_index],
                ),
            )
            for left_index in range(len(instances))
            for right_index in range(left_index + 1, len(instances))
        )
        raw = TargetHypothesis(
            hypothesis_id=draft.hypothesis_id,
            hypothesis_hash="0" * 64,
            subject_mask_ref=artifact_set.subject_mask_ref,
            instance_mask_refs=artifact_set.instance_mask_refs,
            instance_geometries=tuple(
                InstanceGeometryV2(
                    instance_index=index,
                    mask_ref=artifact_set.instance_mask_refs[index],
                    bbox_uv=_geometry(instance)[0],
                    center_uv=_geometry(instance)[1],
                    area_ratio=float(instance.mean()),
                    axes_uv=_geometry(instance)[2],
                    orientation_rad=_geometry(instance)[3],
                    fill_topology=classify_instance_mask_topology_v2(
                        tuple(bool(item) for item in instance.flat),
                        width=width,
                        height=height,
                    ),
                    component_count=len(_components(instance)),
                    hole_count=_hole_count(instance),
                )
                for index, instance in enumerate(instances)
            ),
            confidence=draft.confidence,
            bbox_uv=bbox,
            center_uv=center,
            area_ratio=float(mask.mean()),
            axes_uv=axes,
            orientation_rad=orientation,
            fill_topology=topology,
            component_count=len(_components(mask)),
            instance_count=len(instances),
            hole_count=holes,
            relations=relations,
            radial_segment_evidence_ref=artifact_set.radial_segment_evidence_ref,
            evidence_refs=(
                evidence_index_ref,
                artifact_set.edge_ref,
                *((artifact_set.radial_segment_evidence_ref,)
                  if artifact_set.radial_segment_evidence_ref is not None
                  else ()),
            ),
        )
        hypotheses.append(
            raw.model_copy(
                update={
                    "hypothesis_hash": compute_target_hypothesis_hash(
                        target_sha256,
                        raw,
                    )
                }
            )
        )

    statistics_mask = (
        alpha >= _ALPHA_FOREGROUND_THRESHOLD
        if alpha_evidence
        else np.ones((height, width), dtype=bool)
    )
    statistics_region_id = (
        "source_visible_alpha" if alpha_evidence else "full_normalized_image"
    )
    region_masks = ((statistics_region_id, statistics_mask),)
    region_statistics = tuple(
        RegionStatistics(
            region_id=region_id,
            bbox_uv=_bbox(mask),
            area_ratio=float(mask.mean()),
            mean_lab=_mean_lab(rgb, mask),
        )
        for region_id, mask in region_masks
    )
    symmetry = _symmetry(statistics_mask)
    measurements = TargetMeasurementsV2(
        target_sha256=target_sha256,
        image_size=(width, height),
        target_hypotheses=tuple(hypotheses),
        palette_lab=_palette_lab(rgb, statistics_mask),
        region_statistics=region_statistics,
        symmetry=symmetry,
        radiality=symmetry.radial,
        gradient_evidence=_gradient_evidence(rgb, region_masks),
        edge_refs=tuple(edge_refs),
        evidence_index_ref=evidence_index_ref,
    )
    measurements_bytes = measurements.model_dump_json().encode("utf-8")
    measurements_ref = _put(
        catalog,
        run_id=run_id,
        kind="target_measurements",
        schema_version="target_measurements_v2_2",
        content_type="application/json",
        data=measurements_bytes,
    )
    return TargetMeasurementsV2ArtifactBundle(
        target_source_ref=source_ref,
        normalized_reference_ref=normalized_ref,
        hypothesis_artifacts=tuple(artifact_sets),
        evidence_index_ref=evidence_index_ref,
        measurements_ref=measurements_ref,
        measurements=measurements,
        uncertainty=uncertainty,
    )


__all__ = [
    "INSTANCE_TOPOLOGY_CLASSIFIER_VERSION",
    "MEASUREMENTS_V2_BUNDLE_SCHEMA_VERSION",
    "MEASUREMENTS_V2_PRODUCER_VERSION",
    "MEASUREMENTS_V2_UNCERTAINTY_SCHEMA_VERSION",
    "RADIAL_SEGMENT_EVIDENCE_SCHEMA_VERSION",
    "HypothesisArtifactSet",
    "MeasurementsV2Uncertainty",
    "TargetMeasurementsV2ArtifactBundle",
    "classify_instance_mask_topology_v2",
    "measure_target_v2",
    "verify_radial_segment_structure_evidence_v1",
]
