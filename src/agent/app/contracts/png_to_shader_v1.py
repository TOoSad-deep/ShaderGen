"""PNG 转无贴图 Shader V1 的模型角色结构化契约."""

from __future__ import annotations

import math
import re
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)
from typing_extensions import Self

from shaderforge.contracts import ProblemDomain

NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
ShortSummary = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=800),
]
Sha256Text = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
UnitFloat = Annotated[float, Field(strict=True, ge=0.0, le=1.0, allow_inf_nan=False)]
FiniteFloat = Annotated[float, Field(strict=True, allow_inf_nan=False)]

_SHADER_MAIN_RE = re.compile(r"\bvoid\s+main\s*\(")
_TEXTURE_RE = re.compile(
    r"\b(?:texture2D|textureCube|texelFetch|texture)\b", re.IGNORECASE
)


def _looks_like_complete_shader(value: str) -> bool:
    return bool(
        _SHADER_MAIN_RE.search(value)
        and ("gl_FragColor" in value or "precision mediump float" in value)
    )


def _reject_embedded_shader(values: list[str], *, field_name: str) -> list[str]:
    if any(_looks_like_complete_shader(value) for value in values):
        raise ValueError(f"{field_name} 不得包含完整 GLSL 源码。")
    return values


def _reject_texture_strategy(values: list[str], *, field_name: str) -> list[str]:
    if any(_TEXTURE_RE.search(value) for value in values):
        raise ValueError(f"{field_name} 不得建议纹理采样。")
    return values


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for nested in value.values() for item in _string_values(nested)]
    if isinstance(value, (list, tuple)):
        return [item for nested in value for item in _string_values(nested)]
    return []


def _require_unique(values: list[str], *, field_name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} 不能包含重复值。")


class AuthorMode(str, Enum):
    """ShaderAuthorAgent 的三种受限模式."""

    INITIAL = "initial"
    COMPILE_REPAIR = "compile_repair"
    VISUAL_REFINE = "visual_refine"


class ContractModel(BaseModel):
    """所有角色输出共享的严格基础模型."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
    )

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 兼容的普通字典."""
        return self.model_dump(mode="json")


class BBoxUv(ContractModel):
    """左下原点 UV 坐标中的边界框."""

    min_x: UnitFloat
    min_y: UnitFloat
    max_x: UnitFloat
    max_y: UnitFloat

    @model_validator(mode="after")
    def validate_extents(self) -> Self:
        """保证边界框具有正面积."""
        if self.min_x >= self.max_x or self.min_y >= self.max_y:
            raise ValueError("bbox_uv 必须满足 min < max。")
        return self


class SubjectAnalysis(ContractModel):
    """参考图主体的几何分析."""

    shape_family: Literal[
        "circle", "ellipse", "rounded_rect", "irregular", "multi_object"
    ]
    center_uv: tuple[UnitFloat, UnitFloat] | None
    size_uv: tuple[UnitFloat, UnitFloat] | None
    rotation_degrees: FiniteFloat | None
    foreground_measurement_ref: NonEmptyString | None
    confidence: UnitFloat


class BackgroundAnalysis(ContractModel):
    """背景颜色场与阴影分析."""

    type: Literal["solid", "linear_gradient", "radial_gradient", "layered"]
    colors: Annotated[list[NonEmptyString], Field(min_length=1, max_length=12)]
    shadow_or_glow: ShortSummary
    confidence: UnitFloat


class VisualLayer(ContractModel):
    """按合成顺序排列的单个视觉层."""

    layer_id: NonEmptyString
    role: Literal[
        "background",
        "shadow",
        "base_fill",
        "color_lobe",
        "haze",
        "rim",
        "outline",
        "highlight",
        "detail",
    ]
    order: Annotated[StrictInt, Field(ge=0)]
    region_description: ShortSummary
    color_observation: ShortSummary
    field_type: Literal["position", "direction", "radial", "sdf", "noise", "constant"]
    primitive_candidates: Annotated[
        list[NonEmptyString], Field(min_length=1, max_length=8)
    ]
    confidence: UnitFloat

    @field_validator("primitive_candidates")
    @classmethod
    def reject_texture_primitives(cls, values: list[str]) -> list[str]:
        """拒绝把参考图采样伪装成程序化 primitive."""
        return _reject_texture_strategy(values, field_name="primitive_candidates")


class CoordinateAdvice(ContractModel):
    """各视觉层的坐标场使用建议."""

    position_fields: Annotated[list[NonEmptyString], Field(max_length=16)]
    direction_fields: Annotated[list[NonEmptyString], Field(max_length=16)]
    radial_fields: Annotated[list[NonEmptyString], Field(max_length=16)]
    short_side_normalization_recommended: StrictBool
    notes: Annotated[list[ShortSummary], Field(max_length=12)]


class AnalysisRegion(ContractModel):
    """后续评分和保护使用的 ROI."""

    region_id: NonEmptyString
    bbox_uv: tuple[UnitFloat, UnitFloat, UnitFloat, UnitFloat]
    purpose: Literal["geometry", "color", "edge", "highlight", "shadow", "protection"]
    confidence: UnitFloat

    @field_validator("bbox_uv")
    @classmethod
    def validate_bbox(
        cls, value: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        """保证数组形式的 bbox 具有正面积."""
        if value[0] >= value[2] or value[1] >= value[3]:
            raise ValueError("bbox_uv 必须满足 min < max。")
        return value


class AnalysisProbe(ContractModel):
    """目标图中的代表像素探针."""

    probe_id: NonEmptyString
    uv: tuple[UnitFloat, UnitFloat]
    purpose: ShortSummary
    measurement_ref: NonEmptyString | None


class StrategyCandidate(ContractModel):
    """供 Author 选择的程序化拟合策略."""

    strategy: Literal["sdf_layered_2d", "analytic_normal_2d", "composite_sdf"]
    rank: Annotated[StrictInt, Field(ge=1, le=3)]
    reason: ShortSummary
    required_layers: Annotated[list[NonEmptyString], Field(min_length=1, max_length=16)]
    complexity: Literal["low", "medium", "high"]


class VisualAnalysis(ContractModel):
    """VisualAnalysisAgent 的唯一业务输出."""

    analysis_version: Literal["visual_analysis_v1_2"]
    summary: ShortSummary
    subject: SubjectAnalysis
    background: BackgroundAnalysis
    layers: Annotated[list[VisualLayer], Field(min_length=1, max_length=16)]
    coordinate_advice: CoordinateAdvice
    regions_of_interest: Annotated[list[AnalysisRegion], Field(max_length=8)]
    representative_probes: Annotated[list[AnalysisProbe], Field(max_length=12)]
    strategy_candidates: Annotated[
        list[StrategyCandidate], Field(min_length=1, max_length=3)
    ]
    risks: Annotated[list[ShortSummary], Field(max_length=12)]
    unknowns: Annotated[list[ShortSummary], Field(max_length=12)]

    @model_validator(mode="after")
    def validate_role_and_references(self) -> Self:
        """检查顺序、标识引用和 Analyst 职责边界."""
        layer_ids = [layer.layer_id for layer in self.layers]
        _require_unique(layer_ids, field_name="layers.layer_id")
        orders = [layer.order for layer in self.layers]
        if orders != sorted(orders) or len(orders) != len(set(orders)):
            raise ValueError("layers.order 必须按严格递增顺序排列。")

        region_ids = [region.region_id for region in self.regions_of_interest]
        probe_ids = [probe.probe_id for probe in self.representative_probes]
        ranks = [strategy.rank for strategy in self.strategy_candidates]
        _require_unique(region_ids, field_name="regions_of_interest.region_id")
        _require_unique(probe_ids, field_name="representative_probes.probe_id")
        if len(ranks) != len(set(ranks)):
            raise ValueError("strategy_candidates.rank 不能重复。")

        referenced_layers = (
            self.coordinate_advice.position_fields
            + self.coordinate_advice.direction_fields
            + self.coordinate_advice.radial_fields
        )
        referenced_layers += [
            layer
            for strategy in self.strategy_candidates
            for layer in strategy.required_layers
        ]
        missing = sorted(set(referenced_layers) - set(layer_ids))
        if missing:
            raise ValueError(f"视觉分析引用了不存在的 layer_id：{missing}。")

        role_text = _string_values(self.model_dump(mode="json"))
        _reject_embedded_shader(role_text, field_name="VisualAnalysis")
        return self


class ShaderParameter(ContractModel):
    """Author 可追踪、可定向修订的 Shader 参数."""

    name: NonEmptyString
    semantic_role: NonEmptyString
    problem_domain: ProblemDomain
    current_value: NonEmptyString
    safe_range: NonEmptyString
    affected_regions: Annotated[list[NonEmptyString], Field(max_length=12)]


class ShaderAuthorResult(ContractModel):
    """ShaderAuthorAgent 三种模式共享的完整候选输出."""

    author_version: Literal[
        "shader_author_initial_v1_1",
        "shader_author_compile_repair_v1_1",
        "shader_author_visual_refine_v1",
    ]
    mode: AuthorMode
    base_candidate_id: NonEmptyString | None = None
    glsl: Annotated[str, StringConstraints(min_length=1, max_length=30_000)]
    strategy_summary: ShortSummary
    implemented_layers: Annotated[
        list[NonEmptyString], Field(min_length=1, max_length=16)
    ]
    parameter_manifest: Annotated[list[ShaderParameter], Field(max_length=64)]
    changed_problem_domain: Literal[
        "initial_build",
        "runtime_compile",
        "geometry",
        "background_shadow",
        "base_color_field",
        "rim_edge",
        "highlight",
        "fine_detail",
        "global_balance",
    ]
    changed_parameters: Annotated[list[NonEmptyString], Field(max_length=6)]
    protected_regions: Annotated[list[NonEmptyString], Field(max_length=16)]
    expected_metric_changes: Annotated[list[ShortSummary], Field(max_length=12)]
    known_limitations: Annotated[list[ShortSummary], Field(max_length=12)]

    @field_validator("glsl")
    @classmethod
    def validate_complete_glsl(cls, value: str) -> str:
        """只确认完整源码形状；编译和静态检查属于 M1 事实层."""
        if "```" in value:
            raise ValueError("glsl 字段不得包含 Markdown fence。")
        if not _SHADER_MAIN_RE.search(value) or "gl_FragColor" not in value:
            raise ValueError("glsl 必须包含完整 main 和 gl_FragColor 输出。")
        return value

    @model_validator(mode="after")
    def validate_unique_manifest(self) -> Self:
        """保证候选清单可被确定性比较."""
        _require_unique(self.implemented_layers, field_name="implemented_layers")
        _require_unique(
            [parameter.name for parameter in self.parameter_manifest],
            field_name="parameter_manifest.name",
        )
        _require_unique(self.changed_parameters, field_name="changed_parameters")
        _require_unique(self.protected_regions, field_name="protected_regions")
        return self


class ReviewEvidence(ContractModel):
    """Critic 对单个 ROI 的事实证据."""

    region_id: NonEmptyString
    observation: ShortSummary
    reference_vs_render: ShortSummary
    metric_refs: Annotated[list[NonEmptyString], Field(max_length=12)]
    severity: Literal["low", "medium", "high"]


class RecommendedChange(ContractModel):
    """Critic 对 Author 的定向修订建议."""

    target: NonEmptyString
    action: Literal[
        "increase",
        "decrease",
        "move",
        "narrow",
        "widen",
        "recolor",
        "reshape",
        "replace_formula",
    ]
    direction: ShortSummary
    reason: ShortSummary

    @model_validator(mode="after")
    def reject_texture_advice(self) -> Self:
        """拒绝 Critic 越界建议采样参考图."""
        _reject_texture_strategy(
            [self.target, self.direction, self.reason], field_name="recommended_changes"
        )
        return self


class VisualReview(ContractModel):
    """VisualCriticAgent 的唯一业务输出."""

    review_version: Literal["visual_critic_v1"]
    candidate_id: NonEmptyString
    overall_assessment: ShortSummary
    primary_problem_domain: ProblemDomain
    evidence: Annotated[list[ReviewEvidence], Field(max_length=5)]
    recommended_changes: Annotated[list[RecommendedChange], Field(max_length=3)]
    protected_regions: Annotated[list[NonEmptyString], Field(max_length=16)]
    do_not_change: Annotated[list[NonEmptyString], Field(max_length=16)]
    stop_recommendation: Literal["continue", "accept", "model_limit", "cannot_evaluate"]
    confidence: UnitFloat

    @model_validator(mode="after")
    def validate_role_and_actionability(self) -> Self:
        """禁止 Critic 写 Shader，并保证继续建议可执行."""
        role_text = _string_values(self.model_dump(mode="json"))
        _reject_embedded_shader(role_text, field_name="VisualReview")
        if self.stop_recommendation == "continue" and (
            not self.evidence or not self.recommended_changes
        ):
            raise ValueError("continue 必须同时提供 evidence 和 recommended_changes。")
        _require_unique(self.protected_regions, field_name="protected_regions")
        _require_unique(self.do_not_change, field_name="do_not_change")
        return self


class CandidateRecordInput(ContractModel):
    """传给 Author/Critic 的不可变候选绑定摘要."""

    candidate_id: NonEmptyString
    parent_candidate_id: NonEmptyString | None
    glsl_sha256: Sha256Text
    render_sha256: Sha256Text | None
    prompt_version: NonEmptyString
    model_ref: NonEmptyString
    iteration: Annotated[StrictInt, Field(ge=0)]


class RenderEvidenceBinding(ContractModel):
    """当前渲染图与候选源码的强绑定."""

    candidate_id: NonEmptyString
    glsl_sha256: Sha256Text
    image_sha256: Sha256Text


class ModelCallAudit(ContractModel):
    """一次语义调用或 JSON 修复调用的安全审计记录."""

    role: Literal["visual_analysis", "shader_author", "visual_critic", "json_repair"]
    mode: AuthorMode | None
    attempt: Annotated[StrictInt, Field(ge=1, le=2)]
    requested_model_ref: NonEmptyString
    model_ref: NonEmptyString
    model_identity_source: Literal["response_metadata", "configured_fallback"]
    response_format: Literal["text", "json_object"]
    prompt_version: NonEmptyString
    repair_prompt_version: NonEmptyString | None
    latency_ms: Annotated[StrictInt, Field(ge=0)]
    output_sha256: Sha256Text
    parse_status: Literal["valid", "invalid"]
    error_codes: Annotated[list[NonEmptyString], Field(max_length=20)]
    validation_issues: list[dict[str, str]] = Field(default_factory=list, max_length=20)
    input_tokens: Annotated[StrictInt, Field(ge=0)] | None = None
    output_tokens: Annotated[StrictInt, Field(ge=0)] | None = None
    total_tokens: Annotated[StrictInt, Field(ge=0)] | None = None


class CandidateProvenance(ContractModel):
    """候选源码可复现所需的模型和 Prompt 来源."""

    role: Literal["shader_author"] = "shader_author"
    mode: AuthorMode
    model_ref: NonEmptyString
    requested_model_ref: NonEmptyString
    model_identity_source: Literal["response_metadata", "configured_fallback"]
    prompt_version: NonEmptyString
    final_attempt: Annotated[StrictInt, Field(ge=1, le=2)]
    repair_prompt_version: NonEmptyString | None
    output_sha256: Sha256Text
    glsl_sha256: Sha256Text


def is_finite_number(value: object) -> bool:
    """返回值是否为非布尔有限数，用于边界测试和适配器."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )
