"""LayerPlan + 直接 GLSL Author shadow 的模型 JSON schema 与薄 adapter.

唯一 canonical 契约是 ``shaderforge.program_spec``：本模块不再定义任何
LayerPlan/ProgramSpec 数据结构、规范化规则或哈希语义，只保留：

- 发给模型的严格 JSON Schema（形状与 canonical 模型输出完全一致）；
- fail-closed 的严格 JSON 预检（字符上限、重复 key、非有限数、模型自带
  attestation/哈希/author_identity 字段）与 shadow GLSL 契约检查：
  fragment_source 必须满足 canonical ``validate_shader`` 全量静态规则
  （precision mediump float、varying vec2 v_uv、uniform sampler2D u_image、
  uniform vec2 u_resolution、uniform float u_time、void main() 声明齐备，
  禁止任何纹理采样调用与扩展），且只允许保留的兼容 sampler 声明
  ``uniform sampler2D u_image;``——仅声明、不可采样，任何其他 sampler
  声明一律拒绝；保留 uniform（u_image/u_resolution/u_time）不进入
  ProgramSpec 的 uniform_schema/uniform_values；
- 把校验过的语义 mapping 交给 ``build_layer_plan``/``build_program_spec``
  装配为 canonical ``LayerPlanV1``/``ShaderProgramSpecV1`` 的薄入口。

所有 ``*_sha256`` 与 author/input 身份绑定都由 shaderforge 可信层重算；
Author 不签发任何 attestation。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from shaderforge.evaluation import MIN_SCENE_METRIC_VERSION
from shaderforge.program_spec import (
    LAYER_PLAN_V1_SCHEMA_VERSION,
    SHADER_PROGRAM_SPEC_V1_SCHEMA_VERSION,
    WEBGL1_RENDERER_CONTRACT_ID,
    AuthorIdentity,
    CanvasSpec,
    LayerAuthorIdentity,
    LayerPlanV1,
    ProgramSpecParseError,
    ShaderProgramSpecV1,
    TunableParameter,
    UniformDeclaration,
    build_layer_plan,
    build_program_spec,
    canonical_json,
    sha256_hex_text,
)
from shaderforge.validation import validate_shader

LAYER_PLAN_SCHEMA_VERSION = LAYER_PLAN_V1_SCHEMA_VERSION
PROGRAM_SPEC_SCHEMA_VERSION = SHADER_PROGRAM_SPEC_V1_SCHEMA_VERSION
RENDERER_CONTRACT_ID = WEBGL1_RENDERER_CONTRACT_ID

_PLAN_MAX_CHARS = 40_000
_SPEC_MAX_CHARS = 100_000

# webgl1_static_no_texture_v1：fragment_source 必须满足 canonical
# validate_shader 的全部静态规则（precision/v_uv/u_image/u_resolution/u_time/
# main/gl_FragColor 声明齐备，禁止纹理采样调用、扩展、WebGL2 语法等）；
# 本层额外要求：只允许保留的兼容 sampler 声明 ``uniform sampler2D u_image;``
# （仅声明、不可采样），任何其他 sampler 声明一律拒绝。
_GLSL_COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
_GLSL_SAMPLER_DECLARATION_RE = re.compile(
    r"\buniform\s+(sampler[A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;"
)
# 模型输出中禁止出现的可信层字段：attestation、任何哈希字段、author_identity。
_FORBIDDEN_MODEL_KEY_MARKERS = ("attestation", "sha256", "hash", "author_identity")

_UNIFORM_TYPES = ["float", "vec2", "vec3", "vec4"]
# 与 canonical UNIFORM_NAME_PATTERN 一致；仅用于 Prompt JSON Schema 展示。
_UNIFORM_NAME_REGEX = r"^u_[A-Za-z0-9_]+$"
_LAYER_ROLES = ["background", "subject", "highlight", "shadow", "glow", "detail"]

_LAYER_PLAN_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "layers"],
    "properties": {
        "schema_version": {"const": LAYER_PLAN_SCHEMA_VERSION},
        "layers": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "layer_id",
                    "role",
                    "z_index",
                    "region",
                    "dominant_colors",
                    "confidence",
                ],
                "properties": {
                    "layer_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9_-]{1,64}$",
                    },
                    "role": {"enum": _LAYER_ROLES},
                    "z_index": {"type": "integer", "minimum": 0},
                    "region": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["x", "y", "width", "height"],
                        "properties": {
                            name: {
                                "type": "number",
                                "minimum": 0.0,
                                "maximum": 1.0,
                            }
                            for name in ("x", "y", "width", "height")
                        },
                    },
                    "dominant_colors": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 4,
                        "items": {
                            "type": "array",
                            "minItems": 4,
                            "maxItems": 4,
                            "items": {
                                "type": "number",
                                "minimum": 0.0,
                                "maximum": 1.0,
                            },
                        },
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "notes": {"type": "string", "maxLength": 280},
                },
            },
        },
    },
}

_COMPONENTS_SCHEMA: dict[str, object] = {
    "oneOf": [
        {"type": "number"},
        {
            "type": "array",
            "items": {"type": "number"},
            "minItems": 2,
            "maxItems": 4,
        },
    ]
}

_PROGRAM_SPEC_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "fragment_source",
        "uniform_schema",
        "uniform_values",
        "tunable_manifest",
        "canvas",
        "renderer_contract_id",
    ],
    "properties": {
        "schema_version": {"const": PROGRAM_SPEC_SCHEMA_VERSION},
        "fragment_source": {"type": "string", "minLength": 1},
        # canonical 形状：uniform_schema 是 {u_ 名: 声明} 映射；float 分量
        # 用标量，vecN 用长度 N 数组。
        "uniform_schema": {
            "type": "object",
            "propertyNames": {"pattern": _UNIFORM_NAME_REGEX},
            "additionalProperties": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "minimum", "maximum", "default"],
                "properties": {
                    "type": {"enum": _UNIFORM_TYPES},
                    "minimum": _COMPONENTS_SCHEMA,
                    "maximum": _COMPONENTS_SCHEMA,
                    "default": _COMPONENTS_SCHEMA,
                },
            },
        },
        "uniform_values": {
            "type": "object",
            "propertyNames": {"pattern": _UNIFORM_NAME_REGEX},
            "additionalProperties": _COMPONENTS_SCHEMA,
        },
        # canonical 形状：tunable path 就是 uniform 名本身。
        "tunable_manifest": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "type", "minimum", "maximum", "step"],
                "properties": {
                    "path": {"type": "string", "pattern": _UNIFORM_NAME_REGEX},
                    "type": {"enum": _UNIFORM_TYPES},
                    "minimum": _COMPONENTS_SCHEMA,
                    "maximum": _COMPONENTS_SCHEMA,
                    "step": {"type": "number", "exclusiveMinimum": 0.0},
                },
            },
        },
        "canvas": {
            "type": "object",
            "additionalProperties": False,
            "required": ["width", "height"],
            "properties": {
                "width": {"type": "integer", "minimum": 1},
                "height": {"type": "integer", "minimum": 1},
            },
        },
        "renderer_contract_id": {"const": RENDERER_CONTRACT_ID},
    },
}


class LayerPlanGlslAuthorParseError(ValueError):
    """表示模型输出不是 shadow Author 允许的完整 JSON 值."""

    def __init__(
        self,
        code: str,
        *,
        details: tuple[dict[str, str], ...] = (),
    ) -> None:
        """保留稳定错误码和脱敏校验位置，不泄露原始值."""
        self.code = code
        self.details = details
        super().__init__(code)


@dataclass(frozen=True)
class ValidatedIncumbent:
    """Refine Author 的 validated incumbent 输入：全量校验通过的 current_best.

    ``program_spec`` 必须是 canonical ``ShaderProgramSpecV1``。
    """

    program_spec: ShaderProgramSpecV1
    mae: float
    loss: float
    metrics: dict[str, float] = field(default_factory=dict)
    residual_summary: dict[str, Any] = field(default_factory=dict)
    metric_version: str = MIN_SCENE_METRIC_VERSION

    def __post_init__(self) -> None:
        """拒绝任何非 canonical 的 Spec 表示，保持单一执行真相."""
        if not isinstance(self.program_spec, ShaderProgramSpecV1):
            raise TypeError("program_spec 必须是 shaderforge 的 ShaderProgramSpecV1。")


# --- 角色输入上下文绑定：content_type / current_render / 评估上下文 ---

AUTHOR_INPUT_CONTEXT_VERSION = "author_input_context_v1"

# shadow A/B 的参考图预处理与 metric 背景推导事实（必须与 service 的
# ``derive_canvas``/``border_background`` 规则保持一致），作为 refine
# 评估上下文的一部分纳入 ``input_context_sha256``。
SHADOW_METRIC_PREPROCESS: dict[str, Any] = {
    "preprocess_version": "shadow_ab_preprocess_v1",
    "max_work_side": 256,
    "min_short_side": 16,
    "resample": "lanczos",
    "background": "border_median_rgb",
}


def refine_evaluation_context(incumbent: ValidatedIncumbent) -> dict[str, Any]:
    """返回 refine 输入的 canonical 评估上下文（含 metric/preprocess 版本）."""
    return {
        "mae": incumbent.mae,
        "loss": incumbent.loss,
        "metrics_sha256": sha256_hex_text(canonical_json(dict(incumbent.metrics))),
        "residual_sha256": sha256_hex_text(
            canonical_json(dict(incumbent.residual_summary))
        ),
        "metric_version": incumbent.metric_version,
        "preprocess": dict(SHADOW_METRIC_PREPROCESS),
    }


def initial_input_context_sha256(
    *,
    reference_content_type: str,
    canvas_width: int,
    canvas_height: int,
    layer_plan_sha256: str | None,
) -> str:
    """返回 Initial Author 输入上下文的内容哈希（绑定 content_type 等）."""
    return sha256_hex_text(
        canonical_json(
            {
                "version": AUTHOR_INPUT_CONTEXT_VERSION,
                "role": "initial",
                "reference_content_type": reference_content_type,
                "canvas": {"width": canvas_width, "height": canvas_height},
                "layer_plan_sha256": layer_plan_sha256,
            }
        )
    )


def refine_input_context_sha256(
    *,
    reference_content_type: str,
    current_render_sha256: str,
    current_render_content_type: str,
    evaluation: Mapping[str, Any],
    layer_plan_sha256: str | None,
) -> str:
    """返回 Refine Author 输入上下文的内容哈希.

    绑定参考图与 current_render 的媒体类型、current_render 内容哈希、
    canonical 评估上下文与注入的 LayerPlan。
    """
    return sha256_hex_text(
        canonical_json(
            {
                "version": AUTHOR_INPUT_CONTEXT_VERSION,
                "role": "refine",
                "reference_content_type": reference_content_type,
                "current_render_sha256": current_render_sha256,
                "current_render_content_type": current_render_content_type,
                "evaluation": dict(evaluation),
                "layer_plan_sha256": layer_plan_sha256,
            }
        )
    )


# --- 严格 JSON 预检与防伪字段拒绝（fail-closed） ---


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"JSON 不允许非有限数：{value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"JSON key 重复：{key}")
        value[key] = item
    return value


def _load_strict_json(text: str, *, max_chars: int) -> Any:
    if len(text) > max_chars:
        raise ValueError("JSON 输出超过字符上限。")
    return json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_non_finite,
    )


def _assert_no_trusted_fields(value: Any) -> None:
    """递归拒绝模型自带的 attestation/哈希/author_identity 字段."""
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in _FORBIDDEN_MODEL_KEY_MARKERS):
                raise LayerPlanGlslAuthorParseError(
                    "untrusted_attestation_or_hash_field",
                    details=(
                        {
                            "location": str(key),
                            "type": "forbidden_field",
                            "message": "模型不得输出 attestation/哈希/身份字段。",
                        },
                    ),
                )
            _assert_no_trusted_fields(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_trusted_fields(item)


def _canonical_error_details(
    error: ProgramSpecParseError,
) -> tuple[dict[str, str], ...]:
    """把 canonical 解析错误映射为脱敏的稳定 details，不包含原始输入值."""
    return (
        {
            "location": "",
            "type": error.code,
            "message": str(error)[:240],
        },
    )


# --- 薄 Parser：严格预检 + canonical 语义校验，返回未装配 semantics mapping ---

# 只在解析期校验语义、用完即弃的 probe 身份；绝不进入任何返回值。
_PARSE_PROBE_IDENTITY = AuthorIdentity(
    reference_sha256="0" * 64,
    instruction_sha256="0" * 64,
    model_ref="parse-probe",
    prompt_version="parse-probe",
    role="initial",
    sampling_params={},
)


def parse_layer_plan_semantics(text: str) -> dict[str, Any]:
    """解析视觉分析 Author 输出，返回校验过的 canonical 语义 mapping.

    语义校验完全委托 ``build_layer_plan``（用一次即弃的 probe 身份执行，
    不绑定真实身份）；真实装配由 ``assemble_layer_plan`` 完成。
    """
    try:
        payload = _load_strict_json(text, max_chars=_PLAN_MAX_CHARS)
    except ValueError as exc:
        raise LayerPlanGlslAuthorParseError("invalid_layer_plan_json") from exc
    _assert_no_trusted_fields(payload)
    if not isinstance(payload, dict):
        raise LayerPlanGlslAuthorParseError("invalid_layer_plan_json")
    try:
        build_layer_plan(
            payload,
            reference_sha256="0" * 64,
            author_identity=LayerAuthorIdentity(
                model_ref="parse-probe",
                prompt_version="parse-probe",
                schema_version=LAYER_PLAN_SCHEMA_VERSION,
            ),
        )
    except ProgramSpecParseError as exc:
        raise LayerPlanGlslAuthorParseError(
            "invalid_layer_plan_json",
            details=_canonical_error_details(exc),
        ) from exc
    return payload


def parse_program_spec_semantics(
    text: str,
    *,
    expected_width: int,
    expected_height: int,
) -> dict[str, Any]:
    """解析直接 GLSL Author 输出，返回校验过的 canonical 语义 mapping.

    语义校验完全委托 ``build_program_spec``（probe 身份即弃）；本层额外
    固定画布并执行 shadow GLSL 契约（canonical validate_shader 全量规则
    + 只放行 ``uniform sampler2D u_image;`` 兼容声明）。
    """
    try:
        payload = _load_strict_json(text, max_chars=_SPEC_MAX_CHARS)
    except ValueError as exc:
        raise LayerPlanGlslAuthorParseError("invalid_program_spec_json") from exc
    _assert_no_trusted_fields(payload)
    if not isinstance(payload, dict):
        raise LayerPlanGlslAuthorParseError("invalid_program_spec_json")
    try:
        build_program_spec(payload, author_identity=_PARSE_PROBE_IDENTITY)
    except ProgramSpecParseError as exc:
        raise LayerPlanGlslAuthorParseError(
            "invalid_program_spec_json",
            details=_canonical_error_details(exc),
        ) from exc
    canvas = payload.get("canvas")
    if not isinstance(canvas, dict) or (
        canvas.get("width") != expected_width or canvas.get("height") != expected_height
    ):
        raise LayerPlanGlslAuthorParseError("program_spec_canvas_mismatch")
    glsl_violations = _shadow_glsl_violations(str(payload.get("fragment_source", "")))
    if glsl_violations:
        raise LayerPlanGlslAuthorParseError(
            "glsl_renderer_contract_violation",
            details=tuple(
                {
                    "location": "fragment_source",
                    "type": code,
                    "message": "fragment_source 违反 webgl1_static_no_texture_v1 契约。",
                }
                for code in glsl_violations[:12]
            ),
        )
    return payload


def _shadow_glsl_violations(source: str) -> tuple[str, ...]:
    """返回 shadow GLSL 契约违规代码（去重保序），空元组表示通过.

    canonical ``validate_shader`` 全量规则 + 额外 sampler 声明禁令：
    只允许保留的兼容声明 ``uniform sampler2D u_image;``（仅声明不可采样）。
    """
    codes: list[str] = []
    stripped = _GLSL_COMMENT_RE.sub("", source)
    for match in _GLSL_SAMPLER_DECLARATION_RE.finditer(stripped):
        if not (match.group(1) == "sampler2D" and match.group(2) == "u_image"):
            codes.append("extra_sampler_declaration")
    result = validate_shader(source)
    codes.extend(item.code for item in result.violations if item.severity == "error")
    return tuple(dict.fromkeys(codes))


# --- 薄装配：真实 author/input 身份 + canonical build/hash ---


def assemble_layer_plan(
    semantics: Mapping[str, Any],
    *,
    reference_sha256: str,
    author_identity: LayerAuthorIdentity,
    observations_ref: str | None = None,
) -> LayerPlanV1:
    """由可信层绑定参考图哈希与真实调用身份，返回 canonical LayerPlanV1."""
    return build_layer_plan(
        semantics,
        reference_sha256=reference_sha256,
        author_identity=author_identity,
        observations_ref=observations_ref,
    )


def assemble_program_spec(
    semantics: Mapping[str, Any],
    *,
    author_identity: AuthorIdentity,
) -> ShaderProgramSpecV1:
    """由可信层绑定真实 author/input 身份，返回 canonical ShaderProgramSpecV1.

    refine/repair 角色的父绑定由 ``build_author_identity`` fail-closed
    强制；``spec_sha256`` 由 canonical 层对规范化语义字段重算。
    """
    return build_program_spec(semantics, author_identity=author_identity)


def layer_plan_json_schema() -> dict[str, object]:
    """返回视觉分析 Prompt/结构修复使用的严格 Schema（canonical 形状）."""
    return dict(_LAYER_PLAN_JSON_SCHEMA)


def program_spec_json_schema() -> dict[str, object]:
    """返回直接 GLSL Prompt/结构修复使用的严格 Schema（canonical 形状）."""
    return dict(_PROGRAM_SPEC_JSON_SCHEMA)


__all__ = [
    "AUTHOR_INPUT_CONTEXT_VERSION",
    "LAYER_PLAN_SCHEMA_VERSION",
    "PROGRAM_SPEC_SCHEMA_VERSION",
    "RENDERER_CONTRACT_ID",
    "SHADOW_METRIC_PREPROCESS",
    "AuthorIdentity",
    "CanvasSpec",
    "LayerAuthorIdentity",
    "LayerPlanGlslAuthorParseError",
    "LayerPlanV1",
    "ShaderProgramSpecV1",
    "TunableParameter",
    "UniformDeclaration",
    "ValidatedIncumbent",
    "assemble_layer_plan",
    "assemble_program_spec",
    "initial_input_context_sha256",
    "layer_plan_json_schema",
    "parse_layer_plan_semantics",
    "parse_program_spec_semantics",
    "program_spec_json_schema",
    "refine_evaluation_context",
    "refine_input_context_sha256",
]
