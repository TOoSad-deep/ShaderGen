"""PNG-to-Shader V2.0 数据集 Manifest 与 expected-primitives taxonomy。."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator

from shaderforge.contracts.taxonomy import RequiredLayerTaxon
from shaderforge.genome.models import (
    EFFECT_NODE_REGISTRY_V0,
    EFFECT_NODE_REGISTRY_VERSION,
)

DATASET_SCHEMA_VERSION: Literal["png_to_shader_dataset_manifest_v1"] = (
    "png_to_shader_dataset_manifest_v1"
)
DATASET_READINESS_SCHEMA_VERSION: Literal["png_to_shader_dataset_readiness_v1"] = (
    "png_to_shader_dataset_readiness_v1"
)
DATASET_STAGE_GATE_SCHEMA_VERSION: Literal[
    "png_to_shader_dataset_stage_gate_v1"
] = "png_to_shader_dataset_stage_gate_v1"
EXPECTED_PRIMITIVES_TAXONOMY_SCHEMA_VERSION: Literal[
    "expected_primitives_taxonomy_v1"
] = "expected_primitives_taxonomy_v1"
EXPECTED_PRIMITIVES_TAXONOMY_VERSION: Literal[
    "png_to_shader_expected_primitives_v1"
] = "png_to_shader_expected_primitives_v1"
NODE_REGISTRY_VERSION: Literal["effect_node_registry_v0"] = "effect_node_registry_v0"
if EFFECT_NODE_REGISTRY_VERSION != NODE_REGISTRY_VERSION:  # pragma: no cover
    raise RuntimeError("Dataset node registry version 与 Genome 契约漂移。")
SPLIT_POLICY_VERSION: Literal["visual_family_hash_group_v1"] = (
    "visual_family_hash_group_v1"
)

DatasetSplitName = Literal["development", "validation", "release-held-out"]
DatasetSplitStatus = Literal["available", "not_populated"]
DatasetAccessPolicy = Literal[
    "development",
    "visible_validation",
    "sealed_release_test",
]
DatasetRole = Literal["regression", "evaluation"]
V2DatasetGateStage = Literal[
    "v2_1_intent",
    "v2_2_genome_compiler",
    "v2_3_graph_conformance",
    "v2_3_release_candidate",
]
FillTopology = Literal["solid", "hollow", "ring", "open"]
RequiredLayer = RequiredLayerTaxon
PrimitiveCategory = Literal[
    "geometry",
    "fill",
    "light",
    "mask_algebra",
    "composition",
    "output",
]

REQUIRED_SPLITS = (
    "development",
    "validation",
    "release-held-out",
)
CRITICAL_CLASS_IDS = (
    "multi_instance",
    "ring",
    "hollow",
    "required_highlight",
    "required_rim",
    "required_outline",
)
INITIAL_GENOME_NODE_KINDS = frozenset(item.kind for item in EFFECT_NODE_REGISTRY_V0)
V1_REGRESSION_IMAGE_SHA256 = frozenset(
    {
        "7ed59045ada3434c126f70a60a079d75ebf461359e34165f6f56700c1d360ac8",
        "799877174ff433b4764877f2835e71c2b87a62487ae67edf55ad487a5bf9f1da",
        "1fa4fb6e8ada9fdabc3bb2f14c1678f1e79cf93e85ee061c0b17e6a1534f2eeb",
        "a50d8f24fd99f98225c9d859e036ac6830307e1c5f7ffd87522ff37109298b6a",
        "9498fb7fd872848d17fe12e060e7a0b6a3c642e0619d3f168c08129a56d623c3",
        "b182875646efc1d9291b35d8763f0bf1fe5edf8b37b40f8afd259e5ef2723d07",
        "847aa84441d964390cd2232d3086cd67639b19390e776e1ea2c4e4d475019170",
        "4d71d1c6db64f707e7b74e3ae1636d619bfd1fc5dce03c20781a8f4ca0154c88",
        "541fa4659f685fda838eb482b8dc9f1c4c4c180da2f532b0125d0e60827b9e51",
        "5f182a927055e607dc85261c7d035d45fe5cef4081f833a71858dbcafe184140",
    }
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[a-z0-9][a-z0-9._:-]*$"
_PRIMITIVE_PATTERN = r"^[a-z][a-z0-9_]*$"


class _StrictModel(BaseModel):
    """统一启用严格类型、未知字段拒绝和不可变记录。."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExpectedPrimitiveEntry(_StrictModel):
    """一个 expected primitive 到首期 Genome 节点的版本化映射。."""

    primitive_id: str = Field(min_length=1, pattern=_PRIMITIVE_PATTERN)
    category: PrimitiveCategory
    node_kind: str = Field(min_length=1)
    node_version: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class ExpectedPrimitivesTaxonomy(_StrictModel):
    """V2.0 expected-primitives taxonomy。."""

    schema_version: Literal["expected_primitives_taxonomy_v1"]
    taxonomy_id: Literal["png_to_shader_expected_primitives"]
    taxonomy_version: Literal["png_to_shader_expected_primitives_v1"]
    node_registry_version: Literal["effect_node_registry_v0"]
    primitives: tuple[ExpectedPrimitiveEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique_primitive_ids(self) -> ExpectedPrimitivesTaxonomy:
        primitive_ids = [item.primitive_id for item in self.primitives]
        if len(primitive_ids) != len(set(primitive_ids)):
            raise ValueError("taxonomy primitive_id 不得重复。")
        return self

    @property
    def primitive_ids(self) -> frozenset[str]:
        """返回可被数据 Manifest 引用的 primitive id。."""
        return frozenset(item.primitive_id for item in self.primitives)


class ExpectedPrimitivesLabel(_StrictModel):
    """单个样本绑定的 expected-primitives 标签。."""

    taxonomy_version: Literal["png_to_shader_expected_primitives_v1"]
    items: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique_items(self) -> ExpectedPrimitivesLabel:
        if len(self.items) != len(set(self.items)):
            raise ValueError("expected_primitives.items 不得重复。")
        return self


class V2DatasetSample(_StrictModel):
    """一个带结构标签和内容身份的 V2 benchmark 样本。."""

    case_id: str = Field(min_length=1, pattern=_IDENTIFIER_PATTERN)
    dataset_role: DatasetRole
    source_suite_id: str = Field(min_length=1, pattern=_IDENTIFIER_PATTERN)
    image: str = Field(min_length=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    resolution: tuple[int, int]
    visual_family: str = Field(min_length=1, pattern=_IDENTIFIER_PATTERN)
    hash_group: str = Field(min_length=1, pattern=_IDENTIFIER_PATTERN)
    topology: FillTopology
    instance_count: int = Field(ge=1)
    hole_count: int = Field(ge=0)
    required_layers: tuple[RequiredLayer, ...]
    expected_primitives: ExpectedPrimitivesLabel

    @model_validator(mode="after")
    def _validate_sample_semantics(self) -> V2DatasetSample:
        width, height = self.resolution
        if width <= 0 or height <= 0:
            raise ValueError("resolution 必须包含两个正整数。")
        if len(self.required_layers) != len(set(self.required_layers)):
            raise ValueError("required_layers 不得重复。")
        if self.topology in {"ring", "hollow"} and self.hole_count == 0:
            raise ValueError("ring/hollow 样本必须至少标记一个 hole。")
        if self.topology == "solid" and self.hole_count != 0:
            raise ValueError("solid 样本的 hole_count 必须为 0。")
        return self


class V2DatasetSplit(_StrictModel):
    """一个冻结用途和可用性的 dataset split。."""

    name: DatasetSplitName
    status: DatasetSplitStatus
    access_policy: DatasetAccessPolicy
    purpose: str = Field(min_length=1)
    samples: tuple[V2DatasetSample, ...]

    @model_validator(mode="after")
    def _validate_status_matches_samples(self) -> V2DatasetSplit:
        if self.status == "available" and not self.samples:
            raise ValueError("available split 必须包含样本。")
        if self.status == "not_populated" and self.samples:
            raise ValueError("not_populated split 不得包含样本。")
        expected_policy = {
            "development": "development",
            "validation": "visible_validation",
            "release-held-out": "sealed_release_test",
        }[self.name]
        if self.access_policy != expected_policy:
            raise ValueError("split access_policy 与名称不一致。")
        return self


class ExpectedPrimitivesTaxonomyRef(_StrictModel):
    """Manifest 中对 taxonomy 文件的内容寻址引用。."""

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    taxonomy_version: Literal["png_to_shader_expected_primitives_v1"]
    node_registry_version: Literal["effect_node_registry_v0"]


class DatasetSourceRecord(_StrictModel):
    """一个 source suite 的内容寻址来源与许可记录。."""

    source_suite_id: str = Field(min_length=1, pattern=_IDENTIFIER_PATTERN)
    provenance_path: str = Field(min_length=1)
    provenance_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_url: str = Field(min_length=1)
    license_id: str = Field(min_length=1)
    license_url: str = Field(min_length=1)


class CriticalClassMinimums(_StrictModel):
    """validation/release gate 不得降低的关键类最小正例分母。."""

    multi_instance: int = Field(ge=10)
    ring: int = Field(ge=10)
    hollow: int = Field(ge=10)
    required_highlight: int = Field(ge=10)
    required_rim: int = Field(ge=10)
    required_outline: int = Field(ge=10)

    def as_dict(self) -> dict[str, int]:
        """按冻结类名返回最小分母。."""
        return {
            class_id: int(getattr(self, class_id)) for class_id in CRITICAL_CLASS_IDS
        }


class V2DatasetManifest(_StrictModel):
    """V2.0 三 split 数据 Manifest。."""

    schema_version: Literal["png_to_shader_dataset_manifest_v1"]
    manifest_id: str = Field(min_length=1, pattern=_IDENTIFIER_PATTERN)
    dataset_version: str = Field(min_length=1)
    contract_id: Literal["webgl1_static_no_texture_v1"]
    coordinate_system: Literal["shader_uv_bottom_left"]
    split_policy_version: Literal["visual_family_hash_group_v1"]
    expected_primitives_taxonomy: ExpectedPrimitivesTaxonomyRef
    source_records: tuple[DatasetSourceRecord, ...] = Field(min_length=1)
    critical_class_minimums: CriticalClassMinimums
    splits: tuple[V2DatasetSplit, ...]

    @model_validator(mode="after")
    def _validate_split_contract(self) -> V2DatasetManifest:
        split_names = [split.name for split in self.splits]
        if tuple(split_names) != REQUIRED_SPLITS:
            raise ValueError(
                "splits 必须按 development、validation、release-held-out 各出现一次。"
            )

        source_ids = [item.source_suite_id for item in self.source_records]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source_records.source_suite_id 不得重复。")

        seen_case_ids: set[str] = set()
        referenced_source_ids: set[str] = set()
        hash_group_splits: dict[str, str] = {}
        image_hash_splits: dict[str, str] = {}
        visual_family_splits: dict[str, str] = {}
        for split in self.splits:
            for sample in split.samples:
                referenced_source_ids.add(sample.source_suite_id)
                if sample.case_id in seen_case_ids:
                    raise ValueError(f"case_id 不得重复：{sample.case_id}。")
                seen_case_ids.add(sample.case_id)

                previous_split = hash_group_splits.setdefault(
                    sample.hash_group,
                    split.name,
                )
                if previous_split != split.name:
                    raise ValueError(
                        f"同一 hash_group 不得跨 split：{sample.hash_group}。"
                    )

                previous_family_split = visual_family_splits.setdefault(
                    sample.visual_family,
                    split.name,
                )
                if previous_family_split != split.name:
                    raise ValueError(
                        f"同一 visual_family 不得跨 split：{sample.visual_family}。"
                    )

                image_parts = Path(sample.image).parts
                is_v1_sample = (
                    sample.source_suite_id == "png_to_shader_v1_m0"
                    or "png_to_shader_v1" in image_parts
                    or sample.sha256 in V1_REGRESSION_IMAGE_SHA256
                )
                if is_v1_sample and (
                    split.name != "development" or sample.dataset_role != "regression"
                ):
                    raise ValueError("V1 样本只能登记到 development/regression。")
                if split.name == "release-held-out" and (
                    sample.dataset_role == "regression"
                ):
                    raise ValueError("release-held-out 不得包含 regression 样本。")

                previous_hash_split = image_hash_splits.setdefault(
                    sample.sha256,
                    split.name,
                )
                if previous_hash_split != split.name:
                    raise ValueError(
                        "同一图片 SHA-256 不得跨 split；请修正 hash_group："
                        f"{sample.sha256}。"
                    )
        if set(source_ids) != referenced_source_ids:
            raise ValueError("source_records 必须与样本 source_suite_id 精确对应。")
        return self

    def split(self, name: DatasetSplitName) -> V2DatasetSplit:
        """按名称返回唯一 split。."""
        return next(split for split in self.splits if split.name == name)


@dataclass(frozen=True)
class LoadedV2Dataset:
    """完成文件完整性与 taxonomy 校验的数据集。."""

    manifest: V2DatasetManifest
    taxonomy: ExpectedPrimitivesTaxonomy
    manifest_path: Path
    taxonomy_path: Path
    benchmark_root: Path
    manifest_sha256: str
    taxonomy_sha256: str
    gate_stage: V2DatasetGateStage | None

    def resolve_image(self, sample: V2DatasetSample) -> Path:
        """返回已限定在 benchmark 根内的样本绝对路径。."""
        return _safe_benchmark_path(
            self.benchmark_root,
            sample.image,
            field_name=f"sample {sample.case_id} image",
        )


class CriticalClassReadiness(_StrictModel):
    """一个关键类的实际分母和冻结门槛。."""

    class_id: Literal[
        "multi_instance",
        "ring",
        "hollow",
        "required_highlight",
        "required_rim",
        "required_outline",
    ]
    actual_denominator: int = Field(ge=0)
    minimum_denominator: int = Field(ge=10)
    sufficient: bool

    @model_validator(mode="after")
    def _validate_sufficiency(self) -> CriticalClassReadiness:
        expected = self.actual_denominator >= self.minimum_denominator
        if self.sufficient != expected:
            raise ValueError("critical class sufficient 与分母不一致。")
        return self


class SplitReadiness(_StrictModel):
    """单个 split 的可用性和关键类分母报告。."""

    name: DatasetSplitName
    status: DatasetSplitStatus
    sample_count: int = Field(ge=0)
    ready_for_gate: bool
    critical_classes: tuple[CriticalClassReadiness, ...]
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_split_readiness(self) -> SplitReadiness:
        class_ids = tuple(item.class_id for item in self.critical_classes)
        if class_ids != CRITICAL_CLASS_IDS:
            raise ValueError("readiness critical classes 必须完整且顺序固定。")
        expected_blockers: list[str] = []
        if self.status != "available":
            expected_blockers.append(f"split_status:{self.status}")
        expected_blockers.extend(
            f"insufficient_denominator:{item.class_id}:"
            f"{item.actual_denominator}/{item.minimum_denominator}"
            for item in self.critical_classes
            if not item.sufficient
        )
        expected_ready = self.status == "available" and not expected_blockers
        if self.ready_for_gate != expected_ready:
            raise ValueError("split ready_for_gate 与状态/分母不一致。")
        if self.blockers != tuple(expected_blockers):
            raise ValueError("split blockers 与状态/分母不一致。")
        return self

    def critical_class(self, class_id: str) -> CriticalClassReadiness:
        """按类名返回分母报告。."""
        return next(item for item in self.critical_classes if item.class_id == class_id)


class V2DatasetReadiness(_StrictModel):
    """validation 与 release-held-out 的独立 readiness 结论。."""

    schema_version: Literal["png_to_shader_dataset_readiness_v1"]
    manifest_id: str
    validation_ready: bool
    release_held_out_ready: bool
    ready: bool
    splits: tuple[SplitReadiness, ...]

    @model_validator(mode="after")
    def _validate_readiness_summary(self) -> V2DatasetReadiness:
        split_names = tuple(item.name for item in self.splits)
        if split_names != REQUIRED_SPLITS:
            raise ValueError("readiness splits 必须完整且顺序固定。")
        validation_ready = self.split("validation").ready_for_gate
        release_ready = self.split("release-held-out").ready_for_gate
        if self.validation_ready != validation_ready:
            raise ValueError("validation_ready 与 split 结论不一致。")
        if self.release_held_out_ready != release_ready:
            raise ValueError("release_held_out_ready 与 split 结论不一致。")
        if self.ready != (validation_ready and release_ready):
            raise ValueError("readiness ready 与 split 结论不一致。")
        return self

    def split(self, name: DatasetSplitName) -> SplitReadiness:
        """按名称返回 readiness split。."""
        return next(split for split in self.splits if split.name == name)


class V2DatasetStageGate(_StrictModel):
    """按实施阶段表达必须满足的 split，避免提前解封 release。."""

    schema_version: Literal["png_to_shader_dataset_stage_gate_v1"] = (
        DATASET_STAGE_GATE_SCHEMA_VERSION
    )
    stage: V2DatasetGateStage
    manifest_id: str = Field(min_length=1, pattern=_IDENTIFIER_PATTERN)
    dataset_version: str = Field(min_length=1)
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    taxonomy_sha256: str = Field(pattern=_SHA256_PATTERN)
    required_splits: tuple[DatasetSplitName, ...]
    ready: bool
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def _validate_stage_contract(self) -> V2DatasetStageGate:
        expected_splits = _required_splits_for_stage(self.stage)
        if self.required_splits != expected_splits:
            raise ValueError("required_splits 与 V2 阶段冻结策略不一致。")
        if self.ready != (not self.blockers):
            raise ValueError("stage gate ready 与 blockers 不一致。")
        return self


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 字段不得重复：{key}。")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON 不允许非有限数值：{value}。")


def _read_strict_json(path: Path, model_type: type[_StrictModel]) -> _StrictModel:
    raw_bytes = path.read_bytes()
    try:
        json.loads(
            raw_bytes,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name} 不是严格 JSON。") from exc
    return model_type.model_validate_json(raw_bytes, strict=True)


def _safe_benchmark_path(root: Path, relative_path: str, *, field_name: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute():
        raise ValueError(f"{field_name} 必须是 benchmark 根相对路径。")
    resolved_root = root.resolve()
    candidate = (resolved_root / relative).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError(f"{field_name} 越过 benchmark 根目录。")
    return candidate


def load_expected_primitives_taxonomy(
    taxonomy_path: str | Path,
) -> ExpectedPrimitivesTaxonomy:
    """加载 taxonomy，并验证首期 Effect Genome 节点覆盖。."""
    path = Path(taxonomy_path).resolve()
    taxonomy = _read_strict_json(path, ExpectedPrimitivesTaxonomy)
    if not isinstance(taxonomy, ExpectedPrimitivesTaxonomy):  # pragma: no cover
        raise TypeError("taxonomy loader 返回了错误模型。")
    registry_versions: dict[str, str] = {
        str(item.kind): str(item.node_version) for item in EFFECT_NODE_REGISTRY_V0
    }
    for primitive in taxonomy.primitives:
        expected_version = registry_versions.get(primitive.node_kind)
        if expected_version is None:
            raise ValueError(f"taxonomy node_kind 未登记：{primitive.node_kind}。")
        if primitive.node_version != expected_version:
            raise ValueError(
                "taxonomy node_version 与 effect_node_registry_v0 不一致："
                f"{primitive.node_kind}@{primitive.node_version}。"
            )
    covered_node_kinds = frozenset(
        primitive.node_kind for primitive in taxonomy.primitives
    )
    missing_node_kinds = sorted(
        set(INITIAL_GENOME_NODE_KINDS).difference(covered_node_kinds)
    )
    if missing_node_kinds:
        raise ValueError(
            f"taxonomy 未覆盖首期 Genome 节点：{', '.join(missing_node_kinds)}。"
        )
    return taxonomy


def load_v2_dataset_manifest(
    manifest_path: str | Path,
    *,
    benchmark_root: str | Path | None = None,
    gate_stage: V2DatasetGateStage | None = None,
) -> LoadedV2Dataset:
    """加载 V2 Manifest；阶段加载会在读取图片前执行 release 封存门禁。."""
    path = Path(manifest_path).resolve()
    root = (
        Path(benchmark_root).resolve()
        if benchmark_root is not None
        else path.parent.parent.resolve()
    )
    if not path.is_relative_to(root):
        raise ValueError("dataset manifest 必须位于 benchmark 根目录。")

    manifest = _read_strict_json(path, V2DatasetManifest)
    if not isinstance(manifest, V2DatasetManifest):  # pragma: no cover
        raise TypeError("dataset loader 返回了错误模型。")
    if gate_stage is not None:
        _required_splits_for_stage(gate_stage)
    release_split = manifest.split("release-held-out")
    if gate_stage in {
        "v2_1_intent",
        "v2_2_genome_compiler",
        "v2_3_graph_conformance",
    } and (
        release_split.status != "not_populated" or release_split.samples
    ):
        raise ValueError(
            "V2.1/V2.2/V2.3 Graph conformance 加载要求 "
            "release-held-out 保持未填充封存状态。"
        )

    taxonomy_ref = manifest.expected_primitives_taxonomy
    taxonomy_path = _safe_benchmark_path(
        root,
        taxonomy_ref.path,
        field_name="expected_primitives_taxonomy.path",
    )
    taxonomy_bytes = taxonomy_path.read_bytes()
    if sha256(taxonomy_bytes).hexdigest() != taxonomy_ref.sha256:
        raise ValueError("expected-primitives taxonomy SHA-256 与 Manifest 不一致。")
    taxonomy = load_expected_primitives_taxonomy(taxonomy_path)
    if taxonomy.taxonomy_version != taxonomy_ref.taxonomy_version:
        raise ValueError("expected-primitives taxonomy version 不一致。")
    if taxonomy.node_registry_version != taxonomy_ref.node_registry_version:
        raise ValueError("taxonomy node registry version 不一致。")

    known_primitives = taxonomy.primitive_ids
    for record in manifest.source_records:
        provenance_path = _safe_benchmark_path(
            root,
            record.provenance_path,
            field_name=f"source {record.source_suite_id} provenance_path",
        )
        if sha256(provenance_path.read_bytes()).hexdigest() != record.provenance_sha256:
            raise ValueError(
                f"{record.source_suite_id} 来源/许可记录 SHA-256 与 Manifest 不一致。"
            )
    for split in manifest.splits:
        for sample in split.samples:
            image_path = _safe_benchmark_path(
                root,
                sample.image,
                field_name=f"sample {sample.case_id} image",
            )
            image_bytes = image_path.read_bytes()
            if sha256(image_bytes).hexdigest() != sample.sha256:
                raise ValueError(f"{sample.case_id} 图片 SHA-256 与 Manifest 不一致。")
            with Image.open(image_path) as image:
                if image.size != sample.resolution:
                    raise ValueError(f"{sample.case_id} 图片尺寸与 Manifest 不一致。")
            unknown_primitives = sorted(
                set(sample.expected_primitives.items) - known_primitives
            )
            if unknown_primitives:
                raise ValueError(
                    f"{sample.case_id} 引用了 taxonomy 未登记 primitive："
                    f"{', '.join(unknown_primitives)}。"
                )

    return LoadedV2Dataset(
        manifest=manifest,
        taxonomy=taxonomy,
        manifest_path=path,
        taxonomy_path=taxonomy_path,
        benchmark_root=root,
        manifest_sha256=sha256(path.read_bytes()).hexdigest(),
        taxonomy_sha256=sha256(taxonomy_bytes).hexdigest(),
        gate_stage=gate_stage,
    )


def _critical_class_denominators(
    samples: tuple[V2DatasetSample, ...],
) -> dict[str, int]:
    return {
        "multi_instance": sum(item.instance_count > 1 for item in samples),
        "ring": sum(item.topology == "ring" for item in samples),
        "hollow": sum(item.topology == "hollow" for item in samples),
        "required_highlight": sum(
            "highlight" in item.required_layers for item in samples
        ),
        "required_rim": sum("rim" in item.required_layers for item in samples),
        "required_outline": sum("outline" in item.required_layers for item in samples),
    }


def evaluate_v2_dataset_readiness(
    dataset: LoadedV2Dataset | V2DatasetManifest,
) -> V2DatasetReadiness:
    """独立报告各 split 的真实分母，空 split 永不被视为通过。."""
    manifest = dataset.manifest if isinstance(dataset, LoadedV2Dataset) else dataset
    minimums = manifest.critical_class_minimums.as_dict()
    split_reports: list[SplitReadiness] = []
    for split in manifest.splits:
        actual = _critical_class_denominators(split.samples)
        class_reports = tuple(
            CriticalClassReadiness(
                class_id=class_id,  # type: ignore[arg-type]
                actual_denominator=actual[class_id],
                minimum_denominator=minimums[class_id],
                sufficient=actual[class_id] >= minimums[class_id],
            )
            for class_id in CRITICAL_CLASS_IDS
        )
        blockers: list[str] = []
        if split.status != "available":
            blockers.append(f"split_status:{split.status}")
        blockers.extend(
            f"insufficient_denominator:{item.class_id}:"
            f"{item.actual_denominator}/{item.minimum_denominator}"
            for item in class_reports
            if not item.sufficient
        )
        ready_for_gate = split.status == "available" and all(
            item.sufficient for item in class_reports
        )
        split_reports.append(
            SplitReadiness(
                name=split.name,
                status=split.status,
                sample_count=len(split.samples),
                ready_for_gate=ready_for_gate,
                critical_classes=class_reports,
                blockers=tuple(blockers),
            )
        )

    validation_ready = next(
        item.ready_for_gate for item in split_reports if item.name == "validation"
    )
    release_ready = next(
        item.ready_for_gate for item in split_reports if item.name == "release-held-out"
    )
    return V2DatasetReadiness(
        schema_version=DATASET_READINESS_SCHEMA_VERSION,
        manifest_id=manifest.manifest_id,
        validation_ready=validation_ready,
        release_held_out_ready=release_ready,
        ready=validation_ready and release_ready,
        splits=tuple(split_reports),
    )


def _required_splits_for_stage(
    stage: V2DatasetGateStage,
) -> tuple[DatasetSplitName, ...]:
    if stage in {
        "v2_1_intent",
        "v2_2_genome_compiler",
        "v2_3_graph_conformance",
    }:
        return ("validation",)
    if stage == "v2_3_release_candidate":
        return ("validation", "release-held-out")
    raise ValueError(f"不支持的 V2 dataset gate stage：{stage}。")


def evaluate_v2_dataset_stage_gate(
    dataset: LoadedV2Dataset,
    *,
    stage: V2DatasetGateStage,
) -> V2DatasetStageGate:
    """只基于完成文件完整性校验的数据集评估阶段门禁。."""
    if not isinstance(dataset, LoadedV2Dataset):
        raise TypeError("stage gate 只接受 load_v2_dataset_manifest() 的结果。")
    required_splits = _required_splits_for_stage(stage)
    if dataset.gate_stage != stage:
        raise ValueError("stage gate 要求使用相同 gate_stage 重新加载数据集。")
    refreshed = load_v2_dataset_manifest(
        dataset.manifest_path,
        benchmark_root=dataset.benchmark_root,
        gate_stage=stage,
    )
    if refreshed.manifest != dataset.manifest or refreshed.taxonomy != dataset.taxonomy:
        raise ValueError("stage gate 输入与当前磁盘 Manifest/taxonomy 不一致。")
    if (
        refreshed.manifest_sha256 != dataset.manifest_sha256
        or refreshed.taxonomy_sha256 != dataset.taxonomy_sha256
    ):
        raise ValueError("stage gate 输入与当前磁盘 Manifest/taxonomy 内容身份不一致。")
    readiness = evaluate_v2_dataset_readiness(refreshed)
    release = readiness.split("release-held-out")
    if stage in {
        "v2_1_intent",
        "v2_2_genome_compiler",
        "v2_3_graph_conformance",
    } and (
        release.status != "not_populated" or release.sample_count != 0
    ):
        raise ValueError(
            "V2.1/V2.2/V2.3 Graph conformance gate 要求 "
            "release-held-out 保持未填充封存状态。"
        )
    blockers = tuple(
        f"{split_name}:{blocker}"
        for split_name in required_splits
        for blocker in readiness.split(split_name).blockers
    )
    return V2DatasetStageGate(
        stage=stage,
        manifest_id=refreshed.manifest.manifest_id,
        dataset_version=refreshed.manifest.dataset_version,
        manifest_sha256=refreshed.manifest_sha256,
        taxonomy_sha256=refreshed.taxonomy_sha256,
        required_splits=required_splits,
        ready=not blockers,
        blockers=blockers,
    )


__all__ = [
    "CRITICAL_CLASS_IDS",
    "DATASET_READINESS_SCHEMA_VERSION",
    "DATASET_STAGE_GATE_SCHEMA_VERSION",
    "DATASET_SCHEMA_VERSION",
    "EXPECTED_PRIMITIVES_TAXONOMY_SCHEMA_VERSION",
    "EXPECTED_PRIMITIVES_TAXONOMY_VERSION",
    "INITIAL_GENOME_NODE_KINDS",
    "NODE_REGISTRY_VERSION",
    "SPLIT_POLICY_VERSION",
    "V1_REGRESSION_IMAGE_SHA256",
    "CriticalClassMinimums",
    "CriticalClassReadiness",
    "DatasetSourceRecord",
    "ExpectedPrimitiveEntry",
    "ExpectedPrimitivesLabel",
    "ExpectedPrimitivesTaxonomy",
    "ExpectedPrimitivesTaxonomyRef",
    "LoadedV2Dataset",
    "SplitReadiness",
    "V2DatasetManifest",
    "V2DatasetReadiness",
    "V2DatasetSample",
    "V2DatasetSplit",
    "V2DatasetGateStage",
    "V2DatasetStageGate",
    "evaluate_v2_dataset_stage_gate",
    "evaluate_v2_dataset_readiness",
    "load_expected_primitives_taxonomy",
    "load_v2_dataset_manifest",
]
