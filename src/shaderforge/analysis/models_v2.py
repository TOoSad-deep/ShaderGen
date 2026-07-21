"""V2 目标测量与多假设的冻结契约。."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

from pydantic import Field, model_validator

from shaderforge.contracts import (
    FiniteFloat,
    FrozenModel,
    NonEmptyString,
    Sha256Hex,
    canonical_sha256,
)
from shaderforge.store import ArtifactRefV2

HYPOTHESIS_HASH_VERSION = "target_hypothesis_hash_v3"
HYPOTHESIS_CONFIDENCE_QUANTIZATION = Decimal("0.000001")


class BBoxUv(FrozenModel):
    """bottom-left UV 坐标系中的闭包围框。."""

    min_x: FiniteFloat = Field(ge=0.0, le=1.0)
    min_y: FiniteFloat = Field(ge=0.0, le=1.0)
    max_x: FiniteFloat = Field(ge=0.0, le=1.0)
    max_y: FiniteFloat = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_extent(self) -> BBoxUv:
        if self.min_x >= self.max_x or self.min_y >= self.max_y:
            raise ValueError("BBoxUv 必须满足 min < max。")
        return self


class MeasuredRelation(FrozenModel):
    """测量得到的对象或实例关系。."""

    relation_id: NonEmptyString
    kind: Literal["overlap", "contains", "subtracts", "touches", "disjoint"]
    subject_ref: NonEmptyString
    object_ref: NonEmptyString
    confidence: FiniteFloat = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[ArtifactRefV2, ...] = ()


class LabSample(FrozenModel):
    """带权 CIE Lab 调色板样本。."""

    lab: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    weight: FiniteFloat = Field(gt=0.0, le=1.0)


class RegionStatistics(FrozenModel):
    """确定性图像区域统计。."""

    region_id: NonEmptyString
    bbox_uv: BBoxUv
    area_ratio: FiniteFloat = Field(ge=0.0, le=1.0)
    mean_lab: tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class SymmetryEvidence(FrozenModel):
    """水平、垂直和中心对称证据。."""

    horizontal: FiniteFloat = Field(ge=0.0, le=1.0)
    vertical: FiniteFloat = Field(ge=0.0, le=1.0)
    radial: FiniteFloat = Field(ge=0.0, le=1.0)


class GradientEvidence(FrozenModel):
    """一个区域内的确定性梯度证据。."""

    region_id: NonEmptyString
    direction_uv: tuple[FiniteFloat, FiniteFloat]
    strength: FiniteFloat = Field(ge=0.0, le=1.0)


class InstanceGeometryV2(FrozenModel):
    """由 instance mask 像素确定性重测的逐实例几何。."""

    schema_version: Literal["instance_geometry_v2"] = "instance_geometry_v2"
    instance_index: int = Field(ge=0)
    mask_ref: ArtifactRefV2
    bbox_uv: BBoxUv
    center_uv: tuple[FiniteFloat, FiniteFloat]
    area_ratio: FiniteFloat = Field(gt=0.0, le=1.0)
    axes_uv: tuple[FiniteFloat, FiniteFloat]
    orientation_rad: FiniteFloat
    fill_topology: Literal["solid", "hollow", "ring", "open"]
    component_count: int = Field(ge=1)
    hole_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_geometry(self) -> InstanceGeometryV2:
        if any(not 0.0 <= value <= 1.0 for value in self.center_uv):
            raise ValueError("InstanceGeometry center_uv 必须位于 0 到 1。")
        if any(value <= 0.0 for value in self.axes_uv):
            raise ValueError("InstanceGeometry axes_uv 必须大于 0。")
        if self.fill_topology in {"ring", "hollow"} and self.hole_count < 1:
            raise ValueError("InstanceGeometry ring/hollow 必须至少有一个 hole。")
        if self.fill_topology in {"solid", "open"} and self.hole_count != 0:
            raise ValueError("InstanceGeometry solid/open 的 hole_count 必须为 0。")
        return self


class RadialSegmentInstanceEvidenceV1(FrozenModel):
    """一个原始可见 segment 与语义 ownership 分区之间的冻结映射。."""

    schema_version: Literal["radial_segment_instance_evidence_v1"] = (
        "radial_segment_instance_evidence_v1"
    )
    instance_index: int = Field(ge=0)
    raw_segment_mask_ref: ArtifactRefV2
    ownership_mask_ref: ArtifactRefV2
    radial_center_uv: tuple[FiniteFloat, FiniteFloat]
    radial_axes_uv: tuple[FiniteFloat, FiniteFloat]
    inner_radius_ratio: FiniteFloat = Field(gt=0.0)
    outer_radius_ratio: FiniteFloat = Field(gt=0.0)
    angular_center_rad: FiniteFloat = Field(ge=0.0, lt=6.283185307179586)
    angular_span_rad: FiniteFloat = Field(gt=0.0, lt=6.283185307179586)
    raw_pixel_count: int = Field(gt=0)
    ownership_pixel_count: int = Field(gt=0)
    raw_component_count: Literal[1] = 1
    raw_hole_count: Literal[0] = 0
    raw_fill_topology: Literal["solid"] = "solid"
    raw_is_subset_of_ownership: Literal[True] = True

    @model_validator(mode="after")
    def _validate_radial_frame(self) -> RadialSegmentInstanceEvidenceV1:
        if any(not 0.0 <= value <= 1.0 for value in self.radial_center_uv):
            raise ValueError("segment radial_center_uv 必须位于 0 到 1。")
        if any(value <= 0.0 for value in self.radial_axes_uv):
            raise ValueError("segment radial_axes_uv 必须大于 0。")
        if self.inner_radius_ratio >= self.outer_radius_ratio:
            raise ValueError("segment inner radius 必须小于 outer radius。")
        if self.raw_pixel_count > self.ownership_pixel_count:
            raise ValueError("segment raw pixels 不得多于 ownership pixels。")
        return self


class RadialSegmentRelationEvidenceV1(FrozenModel):
    """原始 segment mask 的完整 pair relation 事实。."""

    schema_version: Literal["radial_segment_relation_evidence_v1"] = (
        "radial_segment_relation_evidence_v1"
    )
    left_instance_index: int = Field(ge=0)
    right_instance_index: int = Field(ge=0)
    kind: Literal["disjoint"] = "disjoint"
    intersection_pixel_count: Literal[0] = 0
    boundary_touch_pixel_count: Literal[0] = 0

    @model_validator(mode="after")
    def _validate_pair(self) -> RadialSegmentRelationEvidenceV1:
        if self.left_instance_index >= self.right_instance_index:
            raise ValueError("segment relation pair 必须按 index 严格升序。")
        return self


class RadialSegmentStructureEvidenceV1(FrozenModel):
    """从 source alpha 原始段到闭合语义 ring ownership 的可重放证据。."""

    schema_version: Literal["radial_segment_structure_evidence_v1"] = (
        "radial_segment_structure_evidence_v1"
    )
    derivation_version: Literal["radial_segment_derivation_v1"] = (
        "radial_segment_derivation_v1"
    )
    target_sha256: Sha256Hex
    target_source_ref: ArtifactRefV2
    raw_subject_mask_ref: ArtifactRefV2
    semantic_subject_mask_ref: ArtifactRefV2
    alpha_foreground_threshold: int = Field(ge=0, le=255)
    radial_profile_bin_count: int = Field(ge=8)
    segments: tuple[RadialSegmentInstanceEvidenceV1, ...] = Field(min_length=3)
    raw_relations: tuple[RadialSegmentRelationEvidenceV1, ...]
    raw_union_is_subset_of_semantic_subject: Literal[True] = True
    ownership_union_equals_semantic_subject: Literal[True] = True

    @model_validator(mode="after")
    def _validate_closure(self) -> RadialSegmentStructureEvidenceV1:
        if (
            self.target_source_ref.kind != "target_source"
            or self.target_source_ref.schema_version != "target_source_v1"
            or self.target_source_ref.content_type
            not in {"image/png", "image/jpeg", "image/webp"}
        ):
            raise ValueError("radial segment target source ref contract 无效。")
        for name, ref, expected_kind in (
            ("raw subject", self.raw_subject_mask_ref, "subject_mask"),
            ("semantic subject", self.semantic_subject_mask_ref, "subject_mask"),
            *(
                (
                    f"raw segment {item.instance_index}",
                    item.raw_segment_mask_ref,
                    "instance_mask",
                )
                for item in self.segments
            ),
            *(
                (
                    f"ownership {item.instance_index}",
                    item.ownership_mask_ref,
                    "instance_mask",
                )
                for item in self.segments
            ),
        ):
            if (
                ref.kind != expected_kind
                or ref.schema_version != "binary_mask_v1"
                or ref.content_type != "image/png"
            ):
                raise ValueError(f"radial segment {name} ref contract 无效。")
        indexes = tuple(item.instance_index for item in self.segments)
        if indexes != tuple(range(len(self.segments))):
            raise ValueError("radial segments 必须按连续 instance index 排序。")
        raw_refs = [item.raw_segment_mask_ref.sha256 for item in self.segments]
        ownership_refs = [item.ownership_mask_ref.sha256 for item in self.segments]
        if len(set(raw_refs)) != len(raw_refs):
            raise ValueError("raw segment mask 内容不得重复。")
        if len(set(ownership_refs)) != len(ownership_refs):
            raise ValueError("ownership mask 内容不得重复。")
        expected_pairs = tuple(
            (left, right)
            for left in range(len(self.segments))
            for right in range(left + 1, len(self.segments))
        )
        actual_pairs = tuple(
            (item.left_instance_index, item.right_instance_index)
            for item in self.raw_relations
        )
        if actual_pairs != expected_pairs:
            raise ValueError("raw segment relations 必须精确覆盖全部 instance pair。")
        return self


class TargetHypothesis(FrozenModel):
    """一个可独立进入 Intent 分支的目标结构假设。."""

    schema_version: Literal["target_hypothesis_v3"] = "target_hypothesis_v3"
    hypothesis_id: NonEmptyString
    hypothesis_hash: Sha256Hex
    subject_mask_ref: ArtifactRefV2
    instance_mask_refs: tuple[ArtifactRefV2, ...]
    instance_geometries: tuple[InstanceGeometryV2, ...]
    confidence: FiniteFloat = Field(ge=0.0, le=1.0)
    bbox_uv: BBoxUv
    center_uv: tuple[FiniteFloat, FiniteFloat]
    area_ratio: FiniteFloat = Field(ge=0.0, le=1.0)
    axes_uv: tuple[FiniteFloat, FiniteFloat]
    orientation_rad: FiniteFloat
    fill_topology: Literal["solid", "hollow", "ring", "open"]
    component_count: int = Field(ge=1)
    instance_count: int = Field(ge=1)
    hole_count: int = Field(ge=0)
    relations: tuple[MeasuredRelation, ...] = ()
    radial_segment_evidence_ref: ArtifactRefV2 | None = None
    evidence_refs: tuple[ArtifactRefV2, ...] = ()

    @model_validator(mode="after")
    def _validate_geometry(self) -> TargetHypothesis:
        if any(not 0.0 <= value <= 1.0 for value in self.center_uv):
            raise ValueError("center_uv 必须位于 0 到 1。")
        if any(value <= 0.0 for value in self.axes_uv):
            raise ValueError("axes_uv 必须大于 0。")
        if len({ref.sha256 for ref in self.instance_mask_refs}) != len(
            self.instance_mask_refs
        ):
            raise ValueError("instance_mask_refs 不得包含重复内容。")
        if self.instance_count != len(self.instance_mask_refs):
            raise ValueError("instance_count 必须等于 instance_mask_refs 数量。")
        if self.instance_count != len(self.instance_geometries):
            raise ValueError("instance_count 必须等于 instance_geometries 数量。")
        if tuple(item.instance_index for item in self.instance_geometries) != tuple(
            range(self.instance_count)
        ):
            raise ValueError("instance_geometries 必须按连续 index 排序。")
        if tuple(item.mask_ref for item in self.instance_geometries) != (
            self.instance_mask_refs
        ):
            raise ValueError("instance_geometries 必须精确绑定 instance mask refs。")
        if self.fill_topology in {"ring", "hollow"} and self.hole_count < 1:
            raise ValueError("ring/hollow 假设必须至少包含一个 hole。")
        if self.fill_topology == "solid" and self.hole_count != 0:
            raise ValueError("solid 假设的 hole_count 必须为 0。")
        relation_ids = [item.relation_id for item in self.relations]
        if len(relation_ids) != len(set(relation_ids)):
            raise ValueError("TargetHypothesis relation_id 不得重复。")
        known_endpoints = {"subject"} | {
            f"instance_{index:04d}" for index in range(self.instance_count)
        }
        relation_keys: list[tuple[str, str, str]] = []
        symmetric_kinds = {"overlap", "touches", "disjoint"}
        for relation in self.relations:
            if (
                relation.subject_ref not in known_endpoints
                or relation.object_ref not in known_endpoints
            ):
                raise ValueError("TargetHypothesis relation endpoint 不存在。")
            if relation.subject_ref == relation.object_ref:
                raise ValueError("TargetHypothesis relation 不得引用自身。")
            if (
                relation.kind in symmetric_kinds
                and relation.subject_ref > relation.object_ref
            ):
                raise ValueError("对称 relation endpoint 必须按 id 升序规范化。")
            relation_keys.append(
                (relation.kind, relation.subject_ref, relation.object_ref)
            )
        if len(relation_keys) != len(set(relation_keys)):
            raise ValueError("TargetHypothesis relation business key 不得重复。")
        if self.radial_segment_evidence_ref is not None:
            if self.fill_topology != "ring" or self.instance_count < 3:
                raise ValueError("radial segment evidence 只适用于三段以上语义 ring。")
            if self.radial_segment_evidence_ref not in self.evidence_refs:
                raise ValueError("radial segment evidence ref 必须进入 evidence_refs 闭包。")
        return self


class TargetMeasurementsV2(FrozenModel):
    """V2 确定性测量及其全部结构假设。."""

    schema_version: Literal["target_measurements_v2_2"] = "target_measurements_v2_2"
    target_sha256: Sha256Hex
    image_size: tuple[int, int]
    target_hypotheses: tuple[TargetHypothesis, ...]
    palette_lab: tuple[LabSample, ...]
    region_statistics: tuple[RegionStatistics, ...]
    symmetry: SymmetryEvidence
    radiality: FiniteFloat = Field(ge=0.0, le=1.0)
    gradient_evidence: tuple[GradientEvidence, ...]
    edge_refs: tuple[ArtifactRefV2, ...]
    evidence_index_ref: ArtifactRefV2

    @model_validator(mode="after")
    def _validate_hypotheses(self) -> TargetMeasurementsV2:
        if any(size <= 0 for size in self.image_size):
            raise ValueError("image_size 必须是正整数。")
        if not self.target_hypotheses:
            raise ValueError("target_hypotheses 不能为空。")
        ids = [item.hypothesis_id for item in self.target_hypotheses]
        if len(set(ids)) != len(ids):
            raise ValueError("hypothesis_id 不得重复。")
        hashes = [item.hypothesis_hash for item in self.target_hypotheses]
        if len(set(hashes)) != len(hashes):
            raise ValueError("hypothesis_hash 不得重复。")
        for hypothesis in self.target_hypotheses:
            expected = compute_target_hypothesis_hash(self.target_sha256, hypothesis)
            if hypothesis.hypothesis_hash != expected:
                raise ValueError(
                    f"{hypothesis.hypothesis_id} 的 hypothesis_hash 与语义不一致。"
                )
        return self


def _quantized_confidence(value: float) -> str:
    return format(
        Decimal(str(value)).quantize(
            HYPOTHESIS_CONFIDENCE_QUANTIZATION,
            rounding=ROUND_HALF_EVEN,
        ),
        "f",
    )


def compute_target_hypothesis_hash(
    target_sha256: str,
    hypothesis: TargetHypothesis,
) -> str:
    """按 v1 投影计算假设身份，排除 record id、URI 和证据位置。."""
    relations = sorted(
        (
            {
                "kind": relation.kind,
                "subject_ref": relation.subject_ref,
                "object_ref": relation.object_ref,
                "confidence": _quantized_confidence(relation.confidence),
            }
            for relation in hypothesis.relations
        ),
        key=lambda item: (
            item["kind"],
            item["subject_ref"],
            item["object_ref"],
            item["confidence"],
        ),
    )
    return canonical_sha256(
        {
            "hash_version": HYPOTHESIS_HASH_VERSION,
            "target_sha256": target_sha256,
            "subject_mask_sha256": hypothesis.subject_mask_ref.sha256,
            "instance_masks": [
                {"instance_index": index, "sha256": ref.sha256}
                for index, ref in enumerate(hypothesis.instance_mask_refs)
            ],
            "instance_geometries": [
                {
                    "instance_index": item.instance_index,
                    "mask_sha256": item.mask_ref.sha256,
                    "bbox_uv": item.bbox_uv,
                    "center_uv": item.center_uv,
                    "area_ratio": item.area_ratio,
                    "axes_uv": item.axes_uv,
                    "orientation_rad": item.orientation_rad,
                    "fill_topology": item.fill_topology,
                    "component_count": item.component_count,
                    "hole_count": item.hole_count,
                }
                for item in hypothesis.instance_geometries
            ],
            "confidence": _quantized_confidence(hypothesis.confidence),
            "bbox_uv": hypothesis.bbox_uv,
            "center_uv": hypothesis.center_uv,
            "area_ratio": hypothesis.area_ratio,
            "axes_uv": hypothesis.axes_uv,
            "orientation_rad": hypothesis.orientation_rad,
            "fill_topology": hypothesis.fill_topology,
            "component_count": hypothesis.component_count,
            "instance_count": hypothesis.instance_count,
            "hole_count": hypothesis.hole_count,
            "relations": relations,
            "radial_segment_evidence": (
                None
                if hypothesis.radial_segment_evidence_ref is None
                else {
                    "sha256": hypothesis.radial_segment_evidence_ref.sha256,
                    "kind": hypothesis.radial_segment_evidence_ref.kind,
                    "schema_version": (
                        hypothesis.radial_segment_evidence_ref.schema_version
                    ),
                    "content_type": hypothesis.radial_segment_evidence_ref.content_type,
                    "size_bytes": hypothesis.radial_segment_evidence_ref.size_bytes,
                }
            ),
        }
    )
