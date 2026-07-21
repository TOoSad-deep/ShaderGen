"""V2 beauty/diagnostic render 的结构事实重测与 typed 证据闭包。."""

from __future__ import annotations

import json
from collections import deque
from hashlib import sha256
from io import BytesIO
from typing import Any, Literal, TypeVar

import numpy as np
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, model_validator

from shaderforge.analysis.measurements_v2 import classify_instance_mask_topology_v2
from shaderforge.compiler import CompilationBundle, DiagnosticCompilationBundleV3
from shaderforge.compiler.models import (
    DIAGNOSTIC_OWNERSHIP_POLICY_VERSION,
    DIAGNOSTIC_VISIBLE_DELTA_BYTE_THRESHOLD,
)
from shaderforge.contracts import (
    FiniteFloat,
    FrozenModel,
    NonEmptyString,
    Sha256Hex,
    canonical_sha256,
)
from shaderforge.contracts.taxonomy import REQUIRED_LAYER_ORDER, RequiredLayerTaxon
from shaderforge.evaluation.attempt_artifacts import (
    RendererRequestReceiptV2,
    load_renderer_request,
)
from shaderforge.genome import TypedEffectGenome, compute_semantic_genome_hash
from shaderforge.intent import IntentIR
from shaderforge.store import ArtifactRefV2, ArtifactResolver

RENDERED_STRUCTURE_EVIDENCE_HASH_VERSION: Literal[
    "rendered_structure_evidence_hash_v4"
] = "rendered_structure_evidence_hash_v4"
RENDERED_STRUCTURE_VERIFICATION_HASH_VERSION: Literal[
    "rendered_structure_verification_hash_v4"
] = "rendered_structure_verification_hash_v4"
RENDERED_STRUCTURE_METRIC_VERSION: Literal["rendered_structure_metric_v3_2"] = (
    "rendered_structure_metric_v3_2"
)
RENDERED_STRUCTURE_RING_HOLE_AREA_RATIO_THRESHOLD = 0.35
RENDERED_STRUCTURE_MIN_CONTRIBUTION_PIXELS = 4
RENDERED_STRUCTURE_MIN_CONTRIBUTION_AREA_RATIO = 0.001
RENDERED_STRUCTURE_LAYER_DIAGNOSTIC_MAX_EDGE = 64
_EXTERIOR_VISIBLE_LAYER_ROLES = frozenset(
    {"background", "shadow", "haze", "glow", "outline"}
)


def _is_layer_contribution_visible_v3(
    *,
    layer: RequiredLayerTaxon,
    visible_pixel_count: int,
    visible_area_ratio: float,
    subject_overlap_ratio: float,
) -> bool:
    """按 v3.1 固定面积门槛与 layer 空间语义判定真实贡献。."""
    has_sufficient_area = (
        visible_pixel_count >= RENDERED_STRUCTURE_MIN_CONTRIBUTION_PIXELS
        and visible_area_ratio >= RENDERED_STRUCTURE_MIN_CONTRIBUTION_AREA_RATIO
    )
    return has_sufficient_area and (
        layer in _EXTERIOR_VISIBLE_LAYER_ROLES or subject_overlap_ratio > 0.0
    )


class RendererEnvironmentReceiptV3(FrozenModel):
    """影响 structure pixels 的 Renderer 环境语义身份。."""

    schema_version: Literal["renderer_environment_receipt_v3"] = (
        "renderer_environment_receipt_v3"
    )
    hash_version: Literal["renderer_environment_hash_v3"] = (
        "renderer_environment_hash_v3"
    )
    renderer_version: NonEmptyString
    browser_version: NonEmptyString
    gl_version: NonEmptyString
    glsl_version: NonEmptyString
    gl_vendor: NonEmptyString
    gl_renderer: NonEmptyString
    webgl_context_kind: Literal["webgl1"]
    canvas_alpha: bool
    canvas_antialias: bool
    canvas_depth: bool
    canvas_stencil: bool
    canvas_alpha_mode: Literal["preserve_transparent_alpha_v1", "force_opaque_alpha_v1"]
    canvas_clear_color_rgba: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    premultiplied_alpha: bool
    preserve_drawing_buffer: bool
    environment_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_hash(self) -> RendererEnvironmentReceiptV3:
        if any(value < 0.0 or value > 1.0 for value in self.canvas_clear_color_rgba):
            raise ValueError("Renderer clear color 必须位于 0 到 1。")
        if (self.canvas_alpha_mode == "preserve_transparent_alpha_v1") != (
            self.canvas_alpha is True
        ):
            raise ValueError("Renderer canvas alpha mode 与 context alpha 不一致。")
        if (
            self.canvas_alpha_mode == "preserve_transparent_alpha_v1"
            and self.canvas_clear_color_rgba[3] != 0.0
        ):
            raise ValueError("保留透明 alpha 时 clear alpha 必须为 0。")
        if (
            self.canvas_alpha_mode == "force_opaque_alpha_v1"
            and self.canvas_clear_color_rgba[3] != 1.0
        ):
            raise ValueError("强制不透明 alpha 时 clear alpha 必须为 1。")
        if self.environment_hash != compute_renderer_environment_hash(self):
            raise ValueError("Renderer environment semantic hash 不一致。")
        return self


class DiagnosticRenderReceiptV3(FrozenModel):
    """一个实际 Renderer diagnostic request/result 的完整身份。."""

    schema_version: Literal["diagnostic_render_receipt_v3"] = (
        "diagnostic_render_receipt_v3"
    )
    pass_id: NonEmptyString
    pass_kind: Literal[
        "subject_visible_delta", "instance_visible_delta", "layer_visible_delta"
    ]
    canonical_node_id: NonEmptyString
    ownership_policy_version: Literal[
        "stable_instance_ordinal_first_match_v1"
    ]
    source_ref: ArtifactRefV2
    source_sha256: Sha256Hex
    instance_index: int | None = Field(default=None, ge=0)
    layer: RequiredLayerTaxon | None = None
    renderer_request_ref: ArtifactRefV2
    renderer_request_artifact_sha256: Sha256Hex
    renderer_request_hash: Sha256Hex
    renderer_environment_ref: ArtifactRefV2
    renderer_environment_artifact_sha256: Sha256Hex
    renderer_environment_hash: Sha256Hex
    render_ref: ArtifactRefV2
    render_sha256: Sha256Hex

    @model_validator(mode="after")
    def _validate_receipt(self) -> DiagnosticRenderReceiptV3:
        if self.source_ref.sha256 != self.source_sha256:
            raise ValueError("Diagnostic receipt source hash/ref 不一致。")
        if self.renderer_request_ref.sha256 != self.renderer_request_artifact_sha256:
            raise ValueError("Diagnostic receipt request Artifact SHA/ref 不一致。")
        if (
            self.renderer_environment_ref.sha256
            != self.renderer_environment_artifact_sha256
        ):
            raise ValueError("Diagnostic receipt environment Artifact SHA/ref 不一致。")
        if self.render_ref.sha256 != self.render_sha256:
            raise ValueError("Diagnostic receipt render hash/ref 不一致。")
        if self.pass_kind == "subject_visible_delta":
            if self.instance_index is not None or self.layer is not None:
                raise ValueError("Subject diagnostic receipt identity 不完整。")
        elif self.pass_kind == "instance_visible_delta":
            if self.instance_index is None or self.layer is not None:
                raise ValueError("Instance diagnostic receipt identity 不完整。")
        elif self.layer is None or self.instance_index is not None:
            raise ValueError("Required-layer diagnostic receipt identity 不完整。")
        return self


class RenderedStructureEvidenceV4(FrozenModel):
    """Beauty 与全部结构 diagnostic render 的不可变候选证据。."""

    schema_version: Literal["rendered_structure_evidence_v4"] = (
        "rendered_structure_evidence_v4"
    )
    hash_version: Literal["rendered_structure_evidence_hash_v4"] = (
        RENDERED_STRUCTURE_EVIDENCE_HASH_VERSION
    )
    run_id: NonEmptyString
    candidate_id: NonEmptyString
    intent_id: NonEmptyString
    intent_ref: ArtifactRefV2
    intent_sha256: Sha256Hex
    target_hypothesis_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    genome_id: NonEmptyString
    genome_ref: ArtifactRefV2
    genome_sha256: Sha256Hex
    semantic_genome_hash: Sha256Hex
    ownership_policy_version: Literal[
        "stable_instance_ordinal_first_match_v1"
    ]
    compilation_ref: ArtifactRefV2
    compilation_sha256: Sha256Hex
    diagnostic_compilation_ref: ArtifactRefV2
    diagnostic_compilation_sha256: Sha256Hex
    beauty_renderer_request_ref: ArtifactRefV2
    beauty_renderer_request_artifact_sha256: Sha256Hex
    beauty_renderer_request_hash: Sha256Hex
    renderer_environment_ref: ArtifactRefV2
    renderer_environment_artifact_sha256: Sha256Hex
    renderer_environment_hash: Sha256Hex
    beauty_render_ref: ArtifactRefV2
    beauty_render_sha256: Sha256Hex
    diagnostic_receipts: tuple[DiagnosticRenderReceiptV3, ...] = Field(min_length=1)
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_evidence(self) -> RenderedStructureEvidenceV4:
        bindings = (
            (self.intent_ref, self.intent_sha256),
            (self.genome_ref, self.genome_sha256),
            (self.compilation_ref, self.compilation_sha256),
            (self.diagnostic_compilation_ref, self.diagnostic_compilation_sha256),
            (
                self.beauty_renderer_request_ref,
                self.beauty_renderer_request_artifact_sha256,
            ),
            (
                self.renderer_environment_ref,
                self.renderer_environment_artifact_sha256,
            ),
            (self.beauty_render_ref, self.beauty_render_sha256),
        )
        if any(ref.sha256 != digest for ref, digest in bindings):
            raise ValueError("Rendered structure evidence scalar hash/ref 不闭合。")
        pass_ids = [item.pass_id for item in self.diagnostic_receipts]
        if pass_ids != sorted(set(pass_ids)):
            raise ValueError("Diagnostic receipts 必须按 pass_id 唯一排序。")
        request_hashes = [
            self.beauty_renderer_request_hash,
            *(item.renderer_request_hash for item in self.diagnostic_receipts),
        ]
        if len(request_hashes) != len(set(request_hashes)):
            raise ValueError(
                "Beauty/diagnostic Renderer request hash 必须逐 pass 唯一。"
            )
        if any(
            item.renderer_environment_ref != self.renderer_environment_ref
            or item.renderer_environment_artifact_sha256
            != self.renderer_environment_artifact_sha256
            or item.renderer_environment_hash != self.renderer_environment_hash
            for item in self.diagnostic_receipts
        ):
            raise ValueError("Beauty/diagnostic 必须绑定同一 Renderer environment。")
        if any(
            item.ownership_policy_version != self.ownership_policy_version
            for item in self.diagnostic_receipts
        ):
            raise ValueError("Evidence/diagnostic ownership policy 不闭合。")
        if self.record_hash != compute_rendered_structure_evidence_hash(self):
            raise ValueError("Rendered structure evidence record hash 不一致。")
        return self


class LayerContributionResultV2(FrozenModel):
    """taxonomy 中一个 layer 的显式 enabled/visible 重测行。."""

    layer: RequiredLayerTaxon
    enabled_in_genome: bool
    required_by_intent: bool
    predicted_visible: bool
    visible_pixel_count: int = Field(ge=0)
    visible_area_ratio: float = Field(ge=0.0, le=1.0)
    subject_overlap_ratio: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_contribution(self) -> LayerContributionResultV2:
        if not self.enabled_in_genome and (
            self.predicted_visible
            or self.visible_pixel_count != 0
            or self.visible_area_ratio != 0.0
            or self.subject_overlap_ratio != 0.0
        ):
            raise ValueError("未启用 layer 必须是显式零贡献 negative row。")
        expected_visible = _is_layer_contribution_visible_v3(
            layer=self.layer,
            visible_pixel_count=self.visible_pixel_count,
            visible_area_ratio=self.visible_area_ratio,
            subject_overlap_ratio=self.subject_overlap_ratio,
        )
        if self.predicted_visible != expected_visible:
            raise ValueError("Layer predicted_visible 与 v3.1 重测字段不一致。")
        return self


class InstanceRelationResultV2(FrozenModel):
    """按 render pixels 重测的一条显式或隐式 instance relation。."""

    relation_id: NonEmptyString
    kind: Literal["overlap", "contains", "subtracts", "touches", "disjoint"]
    subject_ref: NonEmptyString
    object_ref: NonEmptyString
    measurement_basis: Literal["owned_visible_partition_v1"]
    intersection_pixel_count: int = Field(ge=0)
    subject_only_pixel_count: int = Field(ge=0)
    object_only_pixel_count: int = Field(ge=0)
    boundary_touch_pixel_count: int = Field(ge=0)
    passed: bool


class InstanceStructureResultV3(FrozenModel):
    """一张 actual instance visible-delta mask 的显式结构重测。."""

    instance_index: int = Field(ge=0)
    instance_id: NonEmptyString
    expected_topology: Literal["solid", "hollow", "ring", "open"]
    measured_topology: Literal["solid", "hollow", "ring", "open", "unknown"]
    expected_component_count: int = Field(ge=1)
    measured_component_count: int = Field(ge=0)
    expected_hole_count: int = Field(ge=0)
    measured_hole_count: int = Field(ge=0)
    passed: bool

    @model_validator(mode="after")
    def _validate_measurement(self) -> InstanceStructureResultV3:
        if self.expected_topology in {"ring", "hollow"}:
            if self.expected_hole_count < 1:
                raise ValueError("Expected ring/hollow instance 必须至少有一个 hole。")
        elif self.expected_hole_count != 0:
            raise ValueError("Expected solid/open instance 的 hole_count 必须为 0。")
        if self.measured_topology == "unknown":
            if self.measured_component_count != 0 or self.measured_hole_count != 0:
                raise ValueError("Unknown instance measurement 必须是空结构。")
        elif self.measured_component_count == 0:
            raise ValueError("已分类 instance measurement 必须至少有一个 component。")
        elif self.measured_topology in {"ring", "hollow"}:
            if self.measured_hole_count < 1:
                raise ValueError("Measured ring/hollow instance 必须至少有一个 hole。")
        elif self.measured_hole_count != 0:
            raise ValueError("Measured solid/open instance 的 hole_count 必须为 0。")
        expected_passed = (
            self.measured_component_count == self.expected_component_count
            and self.measured_hole_count == self.expected_hole_count
            and self.measured_topology == self.expected_topology
        )
        if self.passed != expected_passed:
            raise ValueError("Instance structure passed 与重测字段不一致。")
        return self


class VisibleDeltaMaskProjectionV3(FrozenModel):
    """不暴露 ndarray 的 canonical visible-delta mask 投影。."""

    schema_version: Literal["visible_delta_mask_projection_v3"] = (
        "visible_delta_mask_projection_v3"
    )
    metric_version: Literal["rendered_structure_metric_v3_2"] = (
        RENDERED_STRUCTURE_METRIC_VERSION
    )
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    active_pixel_count: int = Field(ge=0)
    canonical_bitmask_sha256: Sha256Hex

    @model_validator(mode="after")
    def _validate_area(self) -> VisibleDeltaMaskProjectionV3:
        if self.active_pixel_count > self.width * self.height:
            raise ValueError("Visible-delta active pixels 不得超过画布面积。")
        return self


class RenderedStructureVerificationV4(FrozenModel):
    """从 PNG bytes 重算而非信任 Evidence 声明的结构判定。."""

    schema_version: Literal["rendered_structure_verification_v4"] = (
        "rendered_structure_verification_v4"
    )
    hash_version: Literal["rendered_structure_verification_hash_v4"] = (
        RENDERED_STRUCTURE_VERIFICATION_HASH_VERSION
    )
    run_id: NonEmptyString
    candidate_id: NonEmptyString
    evidence_record_hash: Sha256Hex
    metric_version: Literal["rendered_structure_metric_v3_2"] = (
        RENDERED_STRUCTURE_METRIC_VERSION
    )
    ownership_policy_version: Literal[
        "stable_instance_ordinal_first_match_v1"
    ]
    status: Literal["structure_verified", "rejected"]
    measured_instance_count: int = Field(ge=0)
    measured_component_count: int = Field(ge=0)
    measured_hole_count: int = Field(ge=0)
    measured_topology: Literal["solid", "hollow", "ring", "open", "unknown"]
    renderer_canvas_contract: Literal[
        "preserve_transparent_alpha_v1", "force_opaque_alpha_v1"
    ]
    beauty_subject_iou: float | None = Field(default=None, ge=0.0, le=1.0)
    instance_masks_mutually_exclusive: bool
    instance_structure_results: tuple[InstanceStructureResultV3, ...]
    instance_relation_results: tuple[InstanceRelationResultV2, ...]
    diagnostic_union_iou: float = Field(ge=0.0, le=1.0)
    layer_contribution_results: tuple[LayerContributionResultV2, ...]
    reason_codes: tuple[NonEmptyString, ...]
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_verification(self) -> RenderedStructureVerificationV4:
        if tuple(item.layer for item in self.layer_contribution_results) != (
            REQUIRED_LAYER_ORDER
        ):
            raise ValueError("Layer contribution rows 必须按 taxonomy 完整覆盖十项。")
        if tuple(
            item.instance_index for item in self.instance_structure_results
        ) != tuple(range(len(self.instance_structure_results))):
            raise ValueError("Instance structure rows 必须按连续 index 完整覆盖。")
        instance_ids = [item.instance_id for item in self.instance_structure_results]
        if len(instance_ids) != len(set(instance_ids)):
            raise ValueError("Instance structure row 的 instance_id 必须唯一。")
        if self.measured_instance_count != len(self.instance_structure_results):
            raise ValueError("measured_instance_count 必须等于 instance rows 数量。")
        relation_ids = [item.relation_id for item in self.instance_relation_results]
        if relation_ids != sorted(set(relation_ids)):
            raise ValueError("Instance relation rows 必须按 relation_id 唯一排序。")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("Rendered structure reason_codes 必须唯一排序。")
        if self.renderer_canvas_contract == "preserve_transparent_alpha_v1":
            if self.beauty_subject_iou is None:
                raise ValueError("透明 canvas 必须记录 beauty/subject IoU。")
        elif self.beauty_subject_iou is not None:
            raise ValueError("Opaque canvas 不使用 beauty alpha 推导 subject。")
        passed = not self.reason_codes
        if (self.status == "structure_verified") != passed:
            raise ValueError("Rendered structure verification status/reasons 不一致。")
        if self.status == "structure_verified" and (
            not all(item.passed for item in self.instance_structure_results)
            or not all(item.passed for item in self.instance_relation_results)
            or not self.instance_masks_mutually_exclusive
            or self.diagnostic_union_iou < 0.90
            or self.measured_topology == "unknown"
            or any(
                item.required_by_intent and not item.predicted_visible
                for item in self.layer_contribution_results
            )
            or (
                self.renderer_canvas_contract == "preserve_transparent_alpha_v1"
                and (self.beauty_subject_iou is None or self.beauty_subject_iou < 0.90)
            )
        ):
            raise ValueError("structure_verified 与内部结构测量闭包不一致。")
        if self.record_hash != compute_rendered_structure_verification_hash(self):
            raise ValueError("Rendered structure verification record hash 不一致。")
        return self


def _hash_without_record(value: FrozenModel | dict[str, Any], version: str) -> str:
    payload = (
        value.model_dump(mode="python", exclude={"record_hash"})
        if isinstance(value, FrozenModel)
        else {key: item for key, item in value.items() if key != "record_hash"}
    )
    return canonical_sha256({"hash_version": version, "record": payload})


def compute_rendered_structure_evidence_hash(
    value: RenderedStructureEvidenceV4 | dict[str, Any],
) -> str:
    """计算排除自身字段的 rendered evidence hash。."""
    if isinstance(value, dict):
        value = {
            "schema_version": "rendered_structure_evidence_v4",
            "hash_version": RENDERED_STRUCTURE_EVIDENCE_HASH_VERSION,
            **value,
        }
    return _hash_without_record(value, RENDERED_STRUCTURE_EVIDENCE_HASH_VERSION)


def compute_renderer_environment_hash(
    value: RendererEnvironmentReceiptV3 | dict[str, Any],
) -> str:
    """计算排除自身字段的 Renderer environment semantic hash。."""
    payload = (
        value.model_dump(mode="python", exclude={"environment_hash"})
        if isinstance(value, RendererEnvironmentReceiptV3)
        else {
            "schema_version": "renderer_environment_receipt_v3",
            "hash_version": "renderer_environment_hash_v3",
            **{key: item for key, item in value.items() if key != "environment_hash"},
        }
    )
    return canonical_sha256(payload)


def compute_rendered_structure_verification_hash(
    value: RenderedStructureVerificationV4 | dict[str, Any],
) -> str:
    """计算排除自身字段的 rendered verification hash。."""
    if isinstance(value, dict):
        value = {
            "schema_version": "rendered_structure_verification_v4",
            "hash_version": RENDERED_STRUCTURE_VERIFICATION_HASH_VERSION,
            **value,
        }
    return _hash_without_record(value, RENDERED_STRUCTURE_VERIFICATION_HASH_VERSION)


def _read_exact(resolver: ArtifactResolver, ref: ArtifactRefV2) -> bytes:
    if resolver.resolve(ref.artifact_id) != ref:
        raise ValueError("Rendered structure resolver ref identity 不一致。")
    data = resolver.read_bytes(ref.artifact_id)
    if len(data) != ref.size_bytes or sha256(data).hexdigest() != ref.sha256:
        raise ValueError("Rendered structure Artifact bytes 完整性失败。")
    return data


def _require_ref_metadata(
    ref: ArtifactRefV2,
    *,
    kind: str,
    schema_version: str,
    content_type: str,
) -> None:
    if (
        ref.kind != kind
        or ref.schema_version != schema_version
        or ref.content_type != content_type
    ):
        raise ValueError(
            f"ArtifactRef metadata 不符合契约: {kind}/{schema_version}/{content_type}"
        )


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Strict JSON 存在重复 key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"Strict JSON 包含非有限数值: {value}")


def _strict_json(data: bytes) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite_json,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Artifact 不是 strict UTF-8 JSON。") from exc


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _strict_json_model(data: bytes, model_type: type[_ModelT]) -> _ModelT:
    """先拒绝 duplicate/nonfinite，再用 Pydantic JSON-mode strict 解析。."""
    _strict_json(data)
    return model_type.model_validate_json(data, strict=True)


def _png_mask(data: bytes) -> np.ndarray:
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Rendered structure render 不是有效 PNG。") from exc
    alpha = rgba[:, :, 3]
    if int(alpha.min()) != int(alpha.max()):
        return alpha >= 128
    rgb = rgba[:, :, :3]
    return np.max(rgb, axis=2) >= 128


def _png_alpha_mask(data: bytes) -> tuple[np.ndarray, np.ndarray]:
    """读取 beauty PNG 的真实 alpha，不在 opaque 输出上回退到 RGB."""
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Rendered structure beauty render 不是有效 PNG。") from exc
    alpha = rgba[:, :, 3]
    return alpha >= 128, alpha


def _png_visible_delta_mask(data: bytes) -> np.ndarray:
    """按冻结 byte threshold 解码连续 final-output delta，不复用二值阈值。."""
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            rgba = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Rendered structure delta render 不是有效 PNG。") from exc
    alpha = rgba[:, :, 3]
    channels = rgba if int(alpha.min()) != int(alpha.max()) else rgba[:, :, :3]
    return np.max(channels, axis=2) >= DIAGNOSTIC_VISIBLE_DELTA_BYTE_THRESHOLD


def measure_visible_delta_pixel_count_v2(render_png: bytes) -> int:
    """按 rendered_structure_metric_v3_2 冻结阈值统计 visible delta 像素。."""
    return int(_png_visible_delta_mask(render_png).sum())


def project_visible_delta_mask_v3(render_png: bytes) -> VisibleDeltaMaskProjectionV3:
    """用冻结阈值输出 shape、像素数和 canonical row-major bitmask SHA。."""
    mask = _png_visible_delta_mask(render_png)
    packed = np.packbits(mask.reshape(-1), bitorder="big").tobytes()
    return VisibleDeltaMaskProjectionV3(
        width=int(mask.shape[1]),
        height=int(mask.shape[0]),
        active_pixel_count=int(mask.sum()),
        canonical_bitmask_sha256=sha256(packed).hexdigest(),
    )


def _measure_instance_structure_mask_v2(
    mask: np.ndarray,
    *,
    instance_index: int,
    instance_id: str,
    expected_topology: Literal["solid", "hollow", "ring", "open"],
    expected_component_count: int,
    expected_hole_count: int,
) -> InstanceStructureResultV3:
    measured_components = len(_components(mask))
    measured_holes = _hole_count(mask)
    measured_topology: Literal["solid", "hollow", "ring", "open", "unknown"]
    if mask.any():
        measured_topology = classify_instance_mask_topology_v2(
            tuple(bool(item) for item in mask.flat),
            width=int(mask.shape[1]),
            height=int(mask.shape[0]),
        )
    else:
        measured_topology = "unknown"
    return InstanceStructureResultV3(
        instance_index=instance_index,
        instance_id=instance_id,
        expected_topology=expected_topology,
        measured_topology=measured_topology,
        expected_component_count=expected_component_count,
        measured_component_count=measured_components,
        expected_hole_count=expected_hole_count,
        measured_hole_count=measured_holes,
        passed=(
            measured_components == expected_component_count
            and measured_holes == expected_hole_count
            and measured_topology == expected_topology
        ),
    )


def measure_instance_structure_v3(
    render_png: bytes,
    *,
    instance_index: int,
    instance_id: str,
    expected_topology: Literal["solid", "hollow", "ring", "open"],
    expected_component_count: int,
    expected_hole_count: int,
) -> InstanceStructureResultV3:
    """按 verifier 同一实际 PNG 路径重测一张 instance structure。."""
    return _measure_instance_structure_mask_v2(
        _png_visible_delta_mask(render_png),
        instance_index=instance_index,
        instance_id=instance_id,
        expected_topology=expected_topology,
        expected_component_count=expected_component_count,
        expected_hole_count=expected_hole_count,
    )


def _png_size(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG":
                raise ValueError("Rendered structure render 不是 PNG。")
            image.load()
            return image.size
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Rendered structure render 不是有效 PNG。") from exc


def rendered_structure_diagnostic_size_v2(
    *,
    pass_kind: Literal[
        "subject_visible_delta", "instance_visible_delta", "layer_visible_delta"
    ],
    width: int,
    height: int,
) -> tuple[int, int]:
    """冻结 structure diagnostic request 尺寸；layer pass 最长边为 64。."""
    if width <= 0 or height <= 0:
        raise ValueError("Diagnostic size 必须为正整数。")
    if pass_kind in {"subject_visible_delta", "instance_visible_delta"}:
        return width, height
    scale = min(1.0, RENDERED_STRUCTURE_LAYER_DIAGNOSTIC_MAX_EDGE / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _resize_mask_nearest(mask: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    image = Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), mode="L")
    return np.asarray(image.resize(size, resample=Image.Resampling.NEAREST)) >= 128


def _boundary_touch_pixel_count(left: np.ndarray, right: np.ndarray) -> int:
    """冻结 4-neighbour 接触度量；重叠像素不计为 touches。."""
    if np.logical_and(left, right).any():
        return 0
    adjacent = np.zeros_like(left, dtype=bool)
    adjacent[1:, :] |= left[:-1, :]
    adjacent[:-1, :] |= left[1:, :]
    adjacent[:, 1:] |= left[:, :-1]
    adjacent[:, :-1] |= left[:, 1:]
    return int(np.logical_and(adjacent, right).sum())


def _relation_result(
    *,
    relation_id: str,
    kind: Literal["overlap", "contains", "subtracts", "touches", "disjoint"],
    subject_ref: str,
    object_ref: str,
    subject_mask: np.ndarray,
    object_mask: np.ndarray,
) -> InstanceRelationResultV2:
    intersection = int(np.logical_and(subject_mask, object_mask).sum())
    subject_only = int(np.logical_and(subject_mask, np.logical_not(object_mask)).sum())
    object_only = int(np.logical_and(object_mask, np.logical_not(subject_mask)).sum())
    touch = _boundary_touch_pixel_count(subject_mask, object_mask)
    if kind == "touches":
        passed = intersection == 0 and touch > 0
    elif kind == "disjoint":
        passed = intersection == 0 and touch == 0
    else:
        # V2.4 production 的 visible-delta instance masks 是互斥 partition。
        # overlap/contains 需要 raw-instance masks；subtracts 还需要方向化
        # minuend/subtrahend/result 证据，当前一律显式 unsupported。
        passed = False
    return InstanceRelationResultV2(
        relation_id=relation_id,
        kind=kind,
        subject_ref=subject_ref,
        object_ref=object_ref,
        measurement_basis="owned_visible_partition_v1",
        intersection_pixel_count=intersection,
        subject_only_pixel_count=subject_only,
        object_only_pixel_count=object_only,
        boundary_touch_pixel_count=touch,
        passed=passed,
    )


def measure_instance_relation_v2(
    *,
    relation_id: str,
    kind: Literal["overlap", "contains", "subtracts", "touches", "disjoint"],
    subject_ref: str,
    object_ref: str,
    subject_png: bytes,
    object_png: bytes,
) -> InstanceRelationResultV2:
    """公开冻结的二值 render relation 测量入口。."""
    subject_mask = _png_mask(subject_png)
    object_mask = _png_mask(object_png)
    if subject_mask.shape != object_mask.shape:
        raise ValueError("Relation render 尺寸不一致。")
    return _relation_result(
        relation_id=relation_id,
        kind=kind,
        subject_ref=subject_ref,
        object_ref=object_ref,
        subject_mask=subject_mask,
        object_mask=object_mask,
    )


def _components(mask: np.ndarray) -> tuple[np.ndarray, ...]:
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    result: list[np.ndarray] = []
    for y in range(height):
        for x in range(width):
            if not mask[y, x] or seen[y, x]:
                continue
            queue = deque([(y, x)])
            seen[y, x] = True
            component = np.zeros_like(mask, dtype=bool)
            while queue:
                cy, cx = queue.popleft()
                component[cy, cx] = True
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if (
                        0 <= ny < height
                        and 0 <= nx < width
                        and mask[ny, nx]
                        and not seen[ny, nx]
                    ):
                        seen[ny, nx] = True
                        queue.append((ny, nx))
            result.append(component)
    return tuple(result)


def _hole_count(mask: np.ndarray) -> int:
    background = np.logical_not(mask)
    height, width = mask.shape
    exterior = np.zeros_like(mask, dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    for y in range(height):
        for x in (0, width - 1):
            if background[y, x] and not exterior[y, x]:
                exterior[y, x] = True
                queue.append((y, x))
    for x in range(width):
        for y in (0, height - 1):
            if background[y, x] and not exterior[y, x]:
                exterior[y, x] = True
                queue.append((y, x))
    while queue:
        y, x = queue.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if (
                0 <= ny < height
                and 0 <= nx < width
                and background[ny, nx]
                and not exterior[ny, nx]
            ):
                exterior[ny, nx] = True
                queue.append((ny, nx))
    return len(_components(np.logical_and(background, np.logical_not(exterior))))


def _iou(left: np.ndarray, right: np.ndarray) -> float:
    union = int(np.logical_or(left, right).sum())
    return 1.0 if union == 0 else float(np.logical_and(left, right).sum() / union)


def _hole_area_ratio(mask: np.ndarray) -> float:
    """计算所有封闭孔洞占主体外包络面积的比例。."""
    points = np.argwhere(mask)
    if not len(points):
        return 0.0
    min_y, min_x = points.min(axis=0)
    max_y, max_x = points.max(axis=0)
    crop = mask[min_y : max_y + 1, min_x : max_x + 1]
    envelope = int(crop.sum())
    background = np.logical_not(crop)
    height, width = crop.shape
    exterior = np.zeros_like(crop, dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    for y in range(height):
        for x in (0, width - 1):
            if background[y, x] and not exterior[y, x]:
                exterior[y, x] = True
                queue.append((y, x))
    for x in range(width):
        for y in (0, height - 1):
            if background[y, x] and not exterior[y, x]:
                exterior[y, x] = True
                queue.append((y, x))
    while queue:
        y, x = queue.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if (
                0 <= ny < height
                and 0 <= nx < width
                and background[ny, nx]
                and not exterior[ny, nx]
            ):
                exterior[ny, nx] = True
                queue.append((ny, nx))
    hole_area = int(np.logical_and(background, np.logical_not(exterior)).sum())
    return 0.0 if envelope + hole_area == 0 else hole_area / (envelope + hole_area)


def _is_open_topology(mask: np.ndarray) -> bool:
    """冻结的 open 判定：中心开口连外界且主体覆盖至少三个角象限。."""
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


def measure_rendered_topology_v2(
    render_png: bytes,
) -> tuple[Literal["solid", "hollow", "ring", "open", "unknown"], int, float]:
    """按版本化像素算法测量 topology、hole count 与 hole-area ratio。."""
    mask = _png_mask(render_png)
    if not mask.any():
        return "unknown", 0, 0.0
    holes = _hole_count(mask)
    ratio = _hole_area_ratio(mask)
    if holes:
        topology: Literal["solid", "hollow", "ring", "open", "unknown"] = (
            "ring"
            if ratio >= RENDERED_STRUCTURE_RING_HOLE_AREA_RATIO_THRESHOLD
            else "hollow"
        )
    elif _is_open_topology(mask):
        topology = "open"
    else:
        topology = "solid"
    return topology, holes, ratio


def verify_rendered_structure_evidence(
    evidence: RenderedStructureEvidenceV4,
    *,
    resolver: ArtifactResolver,
    intent: IntentIR,
    genome: TypedEffectGenome,
    compilation_bundle: CompilationBundle,
    diagnostic_bundle: DiagnosticCompilationBundleV3,
) -> RenderedStructureVerificationV4:
    """重读所有真实 PNG，并按 Intent/Compiler identity fail closed。."""
    reasons: list[str] = []
    subject = intent.objects[0]
    beauty_request = None
    environment = None
    try:
        _require_ref_metadata(
            evidence.intent_ref,
            kind="intent",
            schema_version="intent_v3",
            content_type="application/json",
        )
        _require_ref_metadata(
            evidence.genome_ref,
            kind="genome",
            schema_version="genome_v0",
            content_type="application/json",
        )
        _require_ref_metadata(
            evidence.compilation_ref,
            kind="compilation_bundle",
            schema_version="compilation_bundle_v1",
            content_type="application/json",
        )
        _require_ref_metadata(
            evidence.diagnostic_compilation_ref,
            kind="diagnostic_compilation_bundle",
            schema_version="diagnostic_compilation_bundle_v3",
            content_type="application/json",
        )
        _require_ref_metadata(
            evidence.renderer_environment_ref,
            kind="renderer_environment",
            schema_version="renderer_environment_receipt_v3",
            content_type="application/json",
        )
        _require_ref_metadata(
            evidence.beauty_render_ref,
            kind="render_png",
            schema_version="render_png_v2",
            content_type="image/png",
        )
        persisted_intent = _strict_json_model(
            _read_exact(resolver, evidence.intent_ref), IntentIR
        )
        persisted_genome = _strict_json_model(
            _read_exact(resolver, evidence.genome_ref), TypedEffectGenome
        )
        persisted_compilation = _strict_json_model(
            _read_exact(resolver, evidence.compilation_ref), CompilationBundle
        )
        persisted_diagnostics = _strict_json_model(
            _read_exact(resolver, evidence.diagnostic_compilation_ref),
            DiagnosticCompilationBundleV3,
        )
        beauty_request = load_renderer_request(
            evidence.beauty_renderer_request_ref,
            resolver=resolver,
            run_id=evidence.run_id,
        )
        strict_beauty_request = _strict_json_model(
            _read_exact(resolver, evidence.beauty_renderer_request_ref),
            RendererRequestReceiptV2,
        )
        if beauty_request != strict_beauty_request:
            raise ValueError("Beauty Renderer request strict JSON identity 漂移。")
        environment = _strict_json_model(
            _read_exact(resolver, evidence.renderer_environment_ref),
            RendererEnvironmentReceiptV3,
        )
    except (FileNotFoundError, TypeError, ValueError):
        persisted_intent = None
        persisted_genome = None
        persisted_compilation = None
        persisted_diagnostics = None
        reasons.append("typed_artifact_recovery_failed")
    if (
        evidence.intent_id != intent.intent_id
        or evidence.genome_id != genome.genome_id
        or evidence.target_hypothesis_id != intent.target_hypothesis_id
        or evidence.target_hypothesis_hash != intent.target_hypothesis_hash
        or evidence.semantic_genome_hash != diagnostic_bundle.semantic_genome_hash
        or evidence.ownership_policy_version
        != DIAGNOSTIC_OWNERSHIP_POLICY_VERSION
        or diagnostic_bundle.ownership_policy_version
        != DIAGNOSTIC_OWNERSHIP_POLICY_VERSION
        or persisted_intent != intent
        or persisted_genome != genome
        or persisted_compilation != compilation_bundle
        or persisted_diagnostics != diagnostic_bundle
        or compute_semantic_genome_hash(genome) != evidence.semantic_genome_hash
        or beauty_request is None
        or beauty_request.request_hash != evidence.beauty_renderer_request_hash
        or beauty_request.target_hypothesis_hash != evidence.target_hypothesis_hash
        or beauty_request.semantic_genome_hash != evidence.semantic_genome_hash
        or beauty_request.compilation_ref != evidence.compilation_ref
        or beauty_request.glsl_ref != compilation_bundle.glsl_ref
        or not isinstance(beauty_request, RendererRequestReceiptV2)
        or beauty_request.render_profile != "beauty_full_v1"
        or beauty_request.beauty_capture_index != 0
        or beauty_request.diagnostic_pass_id is not None
        or (beauty_request.width, beauty_request.height) != intent.canvas.image_size
        or environment is None
        or environment.environment_hash != evidence.renderer_environment_hash
    ):
        reasons.append("identity_binding_mismatch")
    expected_passes = {
        item.pass_id: (
            item.pass_kind,
            item.canonical_node_id,
            item.source_ref,
            item.instance_index,
            item.layer,
            item.ownership_policy_version,
        )
        for item in diagnostic_bundle.passes
    }
    actual_passes = {
        item.pass_id: (
            item.pass_kind,
            item.canonical_node_id,
            item.source_ref,
            item.instance_index,
            item.layer,
            item.ownership_policy_version,
        )
        for item in evidence.diagnostic_receipts
    }
    if actual_passes != expected_passes:
        reasons.append("diagnostic_bundle_receipts_mismatch")
    request_by_pass: dict[str, Any] = {}
    for item in evidence.diagnostic_receipts:
        try:
            _require_ref_metadata(
                item.source_ref,
                kind="diagnostic_glsl",
                schema_version="diagnostic_glsl_es_100_v3",
                content_type="text/x-glsl; charset=utf-8",
            )
            _require_ref_metadata(
                item.render_ref,
                kind="diagnostic_render_png",
                schema_version="diagnostic_render_png_v3",
                content_type="image/png",
            )
            _read_exact(resolver, item.source_ref)
            request = load_renderer_request(
                item.renderer_request_ref,
                resolver=resolver,
                run_id=evidence.run_id,
            )
            strict_request = _strict_json_model(
                _read_exact(resolver, item.renderer_request_ref),
                RendererRequestReceiptV2,
            )
            if request != strict_request:
                raise ValueError(
                    "Diagnostic Renderer request strict JSON identity 漂移。"
                )
            loaded_environment = _strict_json_model(
                _read_exact(resolver, item.renderer_environment_ref),
                RendererEnvironmentReceiptV3,
            )
            expected_size = rendered_structure_diagnostic_size_v2(
                pass_kind=item.pass_kind,
                width=intent.canvas.image_size[0],
                height=intent.canvas.image_size[1],
            )
            if (
                request.request_hash != item.renderer_request_hash
                or not isinstance(request, RendererRequestReceiptV2)
                or request.target_hypothesis_hash != evidence.target_hypothesis_hash
                or request.semantic_genome_hash != evidence.semantic_genome_hash
                or request.compilation_ref != evidence.diagnostic_compilation_ref
                or request.glsl_ref != item.source_ref
                or item.ownership_policy_version
                != DIAGNOSTIC_OWNERSHIP_POLICY_VERSION
                or request.diagnostic_pass_id != item.pass_id
                or request.beauty_capture_index is not None
                or request.render_profile
                != (
                    "subject_visible_delta_full_v1"
                    if item.pass_kind == "subject_visible_delta"
                    else (
                        "instance_visible_delta_full_v1"
                        if item.pass_kind == "instance_visible_delta"
                        else "layer_visible_delta_lowres_v1"
                    )
                )
                or (request.width, request.height) != expected_size
                or loaded_environment != environment
                or loaded_environment.environment_hash != item.renderer_environment_hash
            ):
                raise ValueError(
                    "Diagnostic Renderer request/environment identity 漂移。"
                )
            request_by_pass[item.pass_id] = request
        except (FileNotFoundError, TypeError, ValueError):
            reasons.append(f"diagnostic_artifact_recovery_failed:{item.pass_id}")
    beauty_bytes = _read_exact(resolver, evidence.beauty_render_ref)
    beauty_alpha_mask, beauty_alpha = _png_alpha_mask(beauty_bytes)
    if _png_size(beauty_bytes) != intent.canvas.image_size:
        reasons.append("beauty_render_size_mismatch")
    receipts = {item.pass_id: item for item in evidence.diagnostic_receipts}
    subject_receipt = receipts.get("subject_visible_delta")
    subject_visible_mask: np.ndarray | None = None
    if subject_receipt is None:
        reasons.append("subject_diagnostic_missing")
    else:
        subject_request = request_by_pass.get(subject_receipt.pass_id)
        subject_bytes = _read_exact(resolver, subject_receipt.render_ref)
        if subject_request is None or _png_size(subject_bytes) != (
            subject_request.width,
            subject_request.height,
        ):
            reasons.append("diagnostic_render_size_mismatch:subject_visible_delta")
        else:
            subject_visible_mask = _png_visible_delta_mask(subject_bytes)
    instance_masks: list[np.ndarray] = []
    instance_masks_by_ref: dict[str, np.ndarray] = {}
    instance_structure_results: list[InstanceStructureResultV3] = []
    for index in range(subject.instance_count):
        expected_instance = subject.instances[index]
        receipt = receipts.get(f"instance_{index:04d}_visible_delta")
        if receipt is None:
            reasons.append(f"instance_diagnostic_missing:{index}")
            instance_structure_results.append(
                InstanceStructureResultV3(
                    instance_index=index,
                    instance_id=expected_instance.instance_id,
                    expected_topology=expected_instance.fill_topology,
                    measured_topology="unknown",
                    expected_component_count=expected_instance.component_count,
                    measured_component_count=0,
                    expected_hole_count=expected_instance.hole_count,
                    measured_hole_count=0,
                    passed=False,
                )
            )
            continue
        mask = _png_visible_delta_mask(_read_exact(resolver, receipt.render_ref))
        diagnostic_request = request_by_pass.get(receipt.pass_id)
        if diagnostic_request is None or _png_size(
            _read_exact(resolver, receipt.render_ref)
        ) != (
            diagnostic_request.width,
            diagnostic_request.height,
        ):
            reasons.append(f"diagnostic_render_size_mismatch:{receipt.pass_id}")
            instance_structure_results.append(
                InstanceStructureResultV3(
                    instance_index=index,
                    instance_id=expected_instance.instance_id,
                    expected_topology=expected_instance.fill_topology,
                    measured_topology="unknown",
                    expected_component_count=expected_instance.component_count,
                    measured_component_count=0,
                    expected_hole_count=expected_instance.hole_count,
                    measured_hole_count=0,
                    passed=False,
                )
            )
            continue
        instance_masks.append(mask)
        instance_masks_by_ref[expected_instance.instance_id] = mask
        instance_result = _measure_instance_structure_mask_v2(
            mask,
            instance_index=index,
            instance_id=expected_instance.instance_id,
            expected_topology=expected_instance.fill_topology,
            expected_component_count=expected_instance.component_count,
            expected_hole_count=expected_instance.hole_count,
        )
        instance_structure_results.append(instance_result)
        if not mask.any():
            reasons.append(f"instance_diagnostic_invalid:{index}")
        if (
            instance_result.measured_component_count
            != expected_instance.component_count
        ):
            reasons.append(f"instance_component_count_mismatch:{index}")
        if instance_result.measured_hole_count != expected_instance.hole_count:
            reasons.append(f"instance_hole_count_mismatch:{index}")
        if instance_result.measured_topology != expected_instance.fill_topology:
            reasons.append(f"instance_topology_mismatch:{index}")
    exclusive = True
    for left_index, left in enumerate(instance_masks):
        for right in instance_masks[left_index + 1 :]:
            if np.logical_and(left, right).any():
                exclusive = False
    union = np.zeros_like(beauty_alpha_mask, dtype=bool)
    for mask in instance_masks:
        union = np.logical_or(union, mask)
    addressable_masks = dict(instance_masks_by_ref)
    for object_intent in intent.objects:
        object_union = np.zeros_like(beauty_alpha_mask, dtype=bool)
        complete = True
        for instance in object_intent.instances:
            instance_mask = instance_masks_by_ref.get(instance.instance_id)
            if instance_mask is None:
                complete = False
                break
            object_union = np.logical_or(object_union, instance_mask)
        if complete:
            addressable_masks[object_intent.object_id] = object_union
    relation_results: list[InstanceRelationResultV2] = []
    explicit_pairs: set[frozenset[str]] = set()
    for relation in intent.relations:
        if relation.kind not in {"touches", "disjoint"}:
            reasons.append(f"instance_relation_unsupported_v2_4:{relation.relation_id}")
        relation_left = addressable_masks.get(relation.subject_ref)
        relation_right = addressable_masks.get(relation.object_ref)
        if relation_left is None or relation_right is None:
            reasons.append(f"instance_relation_endpoint_missing:{relation.relation_id}")
            continue
        explicit_pairs.add(frozenset((relation.subject_ref, relation.object_ref)))
        result = _relation_result(
            relation_id=relation.relation_id,
            kind=relation.kind,
            subject_ref=relation.subject_ref,
            object_ref=relation.object_ref,
            subject_mask=relation_left,
            object_mask=relation_right,
        )
        relation_results.append(result)
        if not result.passed:
            reasons.append(f"instance_relation_mismatch:{relation.relation_id}")
    for left_index, left_instance in enumerate(subject.instances):
        for right_instance in subject.instances[left_index + 1 :]:
            pair = frozenset((left_instance.instance_id, right_instance.instance_id))
            if pair in explicit_pairs:
                continue
            relation_left = instance_masks_by_ref.get(left_instance.instance_id)
            relation_right = instance_masks_by_ref.get(right_instance.instance_id)
            if relation_left is None or relation_right is None:
                continue
            result = _relation_result(
                relation_id=(
                    f"implicit-disjoint:{left_instance.instance_id}:"
                    f"{right_instance.instance_id}"
                ),
                kind="disjoint",
                subject_ref=left_instance.instance_id,
                object_ref=right_instance.instance_id,
                subject_mask=relation_left,
                object_mask=relation_right,
            )
            relation_results.append(result)
            if not result.passed:
                reasons.append(
                    "instance_relation_mismatch:"
                    f"implicit-disjoint:{left_instance.instance_id}:"
                    f"{right_instance.instance_id}"
                )
    for left_index, left in enumerate(instance_masks):
        for right_index, right in enumerate(
            instance_masks[left_index + 1 :], start=left_index + 1
        ):
            if _iou(left, right) >= 0.98:
                reasons.append(
                    f"instance_masks_near_duplicate:{left_index}:{right_index}"
                )
    if not exclusive:
        reasons.append("instance_masks_not_mutually_exclusive")
    relation_results.sort(key=lambda item: item.relation_id)
    beauty_subject_iou: float | None = None
    if environment is None:
        structure_mask = np.zeros_like(beauty_alpha_mask, dtype=bool)
        renderer_canvas_contract = "force_opaque_alpha_v1"
    elif environment.canvas_alpha_mode == "preserve_transparent_alpha_v1":
        renderer_canvas_contract = "preserve_transparent_alpha_v1"
        structure_mask = (
            np.zeros_like(beauty_alpha_mask, dtype=bool)
            if subject_visible_mask is None
            else subject_visible_mask
        )
        beauty_subject_iou = _iou(beauty_alpha_mask, structure_mask)
        if int(beauty_alpha.min()) != 0 or int(beauty_alpha.max()) < 128:
            reasons.append("transparent_beauty_alpha_contract_mismatch")
        if beauty_subject_iou < 0.90:
            reasons.append("transparent_beauty_subject_iou_below_threshold")
    else:
        renderer_canvas_contract = "force_opaque_alpha_v1"
        if int(beauty_alpha.min()) != 255 or int(beauty_alpha.max()) != 255:
            reasons.append("opaque_beauty_alpha_contract_mismatch")
        if subject_visible_mask is None:
            structure_mask = np.zeros_like(beauty_alpha_mask, dtype=bool)
        else:
            structure_mask = subject_visible_mask
    union_iou = _iou(union, structure_mask)
    if union_iou < 0.90:
        reasons.append("diagnostic_union_subject_iou_below_threshold")
    if not np.logical_and(union, structure_mask).any():
        reasons.append("diagnostic_union_not_visible_in_subject")
    measured_components = len(_components(structure_mask))
    topology_png = BytesIO()
    Image.fromarray(np.where(structure_mask, 255, 0).astype(np.uint8), mode="L").save(
        topology_png, format="PNG"
    )
    measured_topology, measured_holes, _ = measure_rendered_topology_v2(
        topology_png.getvalue()
    )
    if len(instance_masks) != subject.instance_count:
        reasons.append("instance_count_mismatch")
    if measured_components != subject.component_count:
        reasons.append("component_count_mismatch")
    if measured_holes != subject.hole_count:
        reasons.append("hole_count_mismatch")
    if measured_topology != subject.topology:
        reasons.append("topology_mismatch")
    required_layers = {item.role for item in intent.layers if item.required}
    enabled_layers = {
        item.layer
        for item in diagnostic_bundle.passes
        if item.pass_kind == "layer_visible_delta" and item.layer is not None
    }
    for missing_layer in sorted(required_layers - set(enabled_layers)):
        reasons.append(f"required_layer_diagnostic_missing:{missing_layer}")
    layer_results: list[LayerContributionResultV2] = []
    for layer in REQUIRED_LAYER_ORDER:
        receipt = receipts.get(f"layer_{layer}_visible_delta")
        required_by_intent = layer in required_layers
        enabled_in_genome = layer in enabled_layers
        if not enabled_in_genome:
            if required_by_intent:
                reasons.append(f"required_layer_diagnostic_missing:{layer}")
            layer_results.append(
                LayerContributionResultV2(
                    layer=layer,
                    enabled_in_genome=False,
                    required_by_intent=required_by_intent,
                    predicted_visible=False,
                    visible_pixel_count=0,
                    visible_area_ratio=0.0,
                    subject_overlap_ratio=0.0,
                )
            )
            continue
        if receipt is None:
            if required_by_intent:
                reasons.append(f"required_layer_diagnostic_missing:{layer}")
            layer_results.append(
                LayerContributionResultV2(
                    layer=layer,
                    enabled_in_genome=True,
                    required_by_intent=required_by_intent,
                    predicted_visible=False,
                    visible_pixel_count=0,
                    visible_area_ratio=0.0,
                    subject_overlap_ratio=0.0,
                )
            )
            continue
        mask = _png_visible_delta_mask(_read_exact(resolver, receipt.render_ref))
        diagnostic_request = request_by_pass.get(receipt.pass_id)
        if diagnostic_request is None or _png_size(
            _read_exact(resolver, receipt.render_ref)
        ) != (
            diagnostic_request.width,
            diagnostic_request.height,
        ):
            reasons.append(f"diagnostic_render_size_mismatch:{receipt.pass_id}")
            mask = np.zeros(
                (
                    diagnostic_request.height if diagnostic_request is not None else 1,
                    diagnostic_request.width if diagnostic_request is not None else 1,
                ),
                dtype=bool,
            )
        local_structure_mask = _resize_mask_nearest(
            structure_mask, (mask.shape[1], mask.shape[0])
        )
        visible = int(mask.sum())
        overlap = int(np.logical_and(mask, local_structure_mask).sum())
        overlap_ratio = 0.0 if visible == 0 else float(overlap / visible)
        visible_area_ratio = float(visible / mask.size)
        predicted_visible = _is_layer_contribution_visible_v3(
            layer=layer,
            visible_pixel_count=visible,
            visible_area_ratio=visible_area_ratio,
            subject_overlap_ratio=overlap_ratio,
        )
        if required_by_intent and not predicted_visible:
            reasons.append(f"required_layer_not_visible:{layer}")
        layer_results.append(
            LayerContributionResultV2(
                layer=layer,
                enabled_in_genome=True,
                required_by_intent=required_by_intent,
                predicted_visible=predicted_visible,
                visible_pixel_count=visible,
                visible_area_ratio=visible_area_ratio,
                subject_overlap_ratio=overlap_ratio,
            )
        )
    payload: dict[str, Any] = {
        "run_id": evidence.run_id,
        "candidate_id": evidence.candidate_id,
        "evidence_record_hash": evidence.record_hash,
        "metric_version": RENDERED_STRUCTURE_METRIC_VERSION,
        "ownership_policy_version": DIAGNOSTIC_OWNERSHIP_POLICY_VERSION,
        "status": "structure_verified" if not reasons else "rejected",
        "measured_instance_count": len(instance_structure_results),
        "measured_component_count": measured_components,
        "measured_hole_count": measured_holes,
        "measured_topology": measured_topology,
        "renderer_canvas_contract": renderer_canvas_contract,
        "beauty_subject_iou": beauty_subject_iou,
        "instance_masks_mutually_exclusive": exclusive,
        "instance_structure_results": tuple(instance_structure_results),
        "instance_relation_results": tuple(relation_results),
        "diagnostic_union_iou": union_iou,
        "layer_contribution_results": tuple(layer_results),
        "reason_codes": tuple(sorted(set(reasons))),
        "record_hash": "0" * 64,
    }
    payload["record_hash"] = compute_rendered_structure_verification_hash(payload)
    return RenderedStructureVerificationV4.model_validate(payload, strict=True)


# 兼容 import 名只指向新 Schema；旧 v2/v3 payload 会被 Literal 拒绝。
DiagnosticRenderReceiptV2 = DiagnosticRenderReceiptV3
RenderedStructureEvidenceV3 = RenderedStructureEvidenceV4
RenderedStructureVerificationV3 = RenderedStructureVerificationV4


__all__ = [
    "DiagnosticRenderReceiptV2",
    "DiagnosticRenderReceiptV3",
    "InstanceRelationResultV2",
    "InstanceStructureResultV3",
    "LayerContributionResultV2",
    "RENDERED_STRUCTURE_EVIDENCE_HASH_VERSION",
    "RENDERED_STRUCTURE_VERIFICATION_HASH_VERSION",
    "RenderedStructureEvidenceV3",
    "RenderedStructureEvidenceV4",
    "RenderedStructureVerificationV3",
    "RenderedStructureVerificationV4",
    "RendererEnvironmentReceiptV3",
    "VisibleDeltaMaskProjectionV3",
    "compute_renderer_environment_hash",
    "compute_rendered_structure_evidence_hash",
    "compute_rendered_structure_verification_hash",
    "measure_rendered_topology_v2",
    "measure_instance_relation_v2",
    "measure_instance_structure_v3",
    "measure_visible_delta_pixel_count_v2",
    "project_visible_delta_mask_v3",
    "rendered_structure_diagnostic_size_v2",
    "verify_rendered_structure_evidence",
]
