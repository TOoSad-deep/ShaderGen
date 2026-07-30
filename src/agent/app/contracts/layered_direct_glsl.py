"""Direct layered GLSL Author 的严格模型输出 adapter.

本模块不定义 LayeredShaderSpec/Patch 的领域真相。它只负责严格 JSON
预检、给模型的 Schema，以及把已验证的语义交给 ``shaderforge.layered_spec``
可信装配。模型不能自报 Initial 哈希或 author identity；Refine 的两个哈希
仅是 Patch 乐观并发前置条件，仍由领域层验证。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from shaderforge.layered_spec import (
    BLEND_MODES,
    LAYER_PATCH_V1_SCHEMA_VERSION,
    LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION,
    LayeredShaderSpecV1,
    LayeredSpecError,
    LayerPatchV1,
    build_layer_patch,
    build_layered_shader_spec,
)
from shaderforge.program_spec import (
    AuthorIdentity,
    LayerPlanV1,
    build_author_identity,
)

_INITIAL_MAX_CHARS = 100_000
_PATCH_MAX_CHARS = 50_000
_FORBIDDEN_TRUSTED_MARKERS = ("attestation", "author_identity")
_UNIFORM_TYPES = ["float", "vec2", "vec3", "vec4"]
_UNIFORM_NAME_REGEX = r"^u_[A-Za-z0-9_]+$"
_LAYER_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_OPTIMIZATION_OBJECTIVES = ("geometry", "color", "edge", "effect")
_REGION_POLICIES = (
    "layer_region",
    "worst_residual_intersection",
    "full_canvas",
)


class LayeredDirectAuthorParseError(ValueError):
    """稳定、脱敏的 direct layered Author 解析错误."""

    def __init__(self, code: str) -> None:
        """保存稳定机器错误码."""
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ParsedAuthorOutput:
    """Validated Author semantics and optional, non-semantic focus advice."""

    semantics: dict[str, Any]
    optimization_focus_payload: Mapping[str, Any] | None = None


def _components_schema() -> dict[str, object]:
    return {
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


def _layer_schema() -> dict[str, object]:
    components = _components_schema()
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "layer_id",
            "role",
            "z_index",
            "blend_mode",
            "glsl_body",
            "uniform_schema",
            "uniform_values",
            "tunable_manifest",
        ],
        "properties": {
            "layer_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
            "role": {
                "enum": [
                    "background",
                    "subject",
                    "highlight",
                    "shadow",
                    "glow",
                    "detail",
                ]
            },
            "z_index": {"type": "integer", "minimum": 0},
            "blend_mode": {"enum": list(BLEND_MODES)},
            "glsl_body": {"type": "string", "minLength": 1, "maxLength": 30000},
            "uniform_schema": {
                "type": "object",
                "propertyNames": {"pattern": _UNIFORM_NAME_REGEX},
                "additionalProperties": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["type", "minimum", "maximum", "default"],
                    "properties": {
                        "type": {"enum": _UNIFORM_TYPES},
                        "minimum": components,
                        "maximum": components,
                        "default": components,
                    },
                },
            },
            "uniform_values": {
                "type": "object",
                "propertyNames": {"pattern": _UNIFORM_NAME_REGEX},
                "additionalProperties": components,
            },
            "tunable_manifest": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path", "type", "minimum", "maximum", "step"],
                    "properties": {
                        "path": {"type": "string", "pattern": _UNIFORM_NAME_REGEX},
                        "type": {"enum": _UNIFORM_TYPES},
                        "minimum": components,
                        "maximum": components,
                        "step": {"type": "number", "exclusiveMinimum": 0.0},
                    },
                },
            },
        },
    }


def _optimization_focus_schema() -> dict[str, object]:
    """Return the JSON Schema for advisory local optimization focus."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "target_layer_id",
            "objective",
            "active_components",
            "region_policy",
        ],
        "properties": {
            "target_layer_id": {
                "type": "string",
                "pattern": "^[A-Za-z0-9_-]{1,64}$",
            },
            "objective": {"enum": list(_OPTIMIZATION_OBJECTIVES)},
            "active_components": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["path", "component_indices"],
                    "properties": {
                        "path": {"type": "string", "minLength": 1},
                        "component_indices": {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "integer", "minimum": 0},
                        },
                    },
                },
            },
            "region_policy": {"enum": list(_REGION_POLICIES)},
        },
    }


def _planned_layer_schema(layer: Any) -> dict[str, object]:
    schema = _layer_schema()
    properties = schema["properties"]
    assert isinstance(properties, dict)
    properties["layer_id"] = {"const": layer.layer_id}
    properties["role"] = {"const": layer.role}
    properties["z_index"] = {"const": layer.z_index}
    return schema


def layered_shader_spec_json_schema(
    *,
    layer_plan: LayerPlanV1 | None = None,
    canvas_width: int | None = None,
    canvas_height: int | None = None,
) -> dict[str, object]:
    """返回 Initial Schema；提供 Plan 时把本轮固定身份直接编码进去."""
    if layer_plan is None:
        canvas_properties: dict[str, object] = {
            "width": {"type": "integer", "minimum": 1},
            "height": {"type": "integer", "minimum": 1},
        }
        layers_schema: dict[str, object] = {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": _layer_schema(),
        }
    else:
        if canvas_width is None or canvas_height is None:
            raise ValueError("绑定 LayerPlan 的 Schema 必须提供 canvas 尺寸。")
        canvas_properties = {
            "width": {"const": canvas_width},
            "height": {"const": canvas_height},
        }
        layers_schema = {
            "type": "array",
            "minItems": len(layer_plan.layers),
            "maxItems": len(layer_plan.layers),
            "prefixItems": [
                _planned_layer_schema(layer) for layer in layer_plan.layers
            ],
            "items": False,
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "canvas", "layers"],
        "properties": {
            "schema_version": {"const": LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION},
            "canvas": {
                "type": "object",
                "additionalProperties": False,
                "required": ["width", "height"],
                "properties": canvas_properties,
            },
            "layers": layers_schema,
            "optimization_focus": _optimization_focus_schema(),
        },
    }


def layer_patch_json_schema() -> dict[str, object]:
    """返回 Refine 的唯一 replace-layer Patch Schema."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "base_layered_spec_sha256",
            "target_layer_id",
            "expected_layer_sha256",
            "replacement",
        ],
        "properties": {
            "schema_version": {"const": LAYER_PATCH_V1_SCHEMA_VERSION},
            "base_layered_spec_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "target_layer_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
            "expected_layer_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "replacement": _layer_schema(),
            "optimization_focus": _optimization_focus_schema(),
        },
    }


def _reject_non_finite(value: str) -> None:
    raise ValueError(value)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(key)
        result[key] = value
    return result


def _load_object(text: str, *, max_chars: int, error_code: str) -> dict[str, Any]:
    try:
        if len(text) > max_chars:
            raise ValueError("too_long")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LayeredDirectAuthorParseError(error_code) from exc
    if not isinstance(value, dict):
        raise LayeredDirectAuthorParseError(error_code)
    return value


def _assert_no_untrusted_initial_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            if (
                "sha256" in lowered
                or lowered == "hash"
                or any(marker in lowered for marker in _FORBIDDEN_TRUSTED_MARKERS)
            ):
                raise LayeredDirectAuthorParseError(
                    "untrusted_attestation_or_hash_field"
                )
            _assert_no_untrusted_initial_fields(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_untrusted_initial_fields(item)


def _assert_no_untrusted_patch_fields(value: Any, *, at_root: bool = True) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            allowed_guard = at_root and key in {
                "base_layered_spec_sha256",
                "expected_layer_sha256",
            }
            if ("sha256" in lowered or lowered == "hash") and not allowed_guard:
                raise LayeredDirectAuthorParseError(
                    "untrusted_attestation_or_hash_field"
                )
            if any(marker in lowered for marker in _FORBIDDEN_TRUSTED_MARKERS):
                raise LayeredDirectAuthorParseError(
                    "untrusted_attestation_or_hash_field"
                )
            _assert_no_untrusted_patch_fields(item, at_root=False)
    elif isinstance(value, list):
        for item in value:
            _assert_no_untrusted_patch_fields(item, at_root=False)


def _valid_non_negative_index(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _extract_optimization_focus(
    payload: dict[str, Any],
) -> Mapping[str, Any] | None:
    """Strip and normalize focus advice; malformed input becomes None."""
    candidate = payload.pop("optimization_focus", None)
    if not isinstance(candidate, Mapping) or set(candidate) != {
        "target_layer_id",
        "objective",
        "active_components",
        "region_policy",
    }:
        return None

    target_layer_id = candidate.get("target_layer_id")
    objective = candidate.get("objective")
    active_components = candidate.get("active_components")
    region_policy = candidate.get("region_policy")
    if (
        not isinstance(target_layer_id, str)
        or _LAYER_ID_REGEX.fullmatch(target_layer_id) is None
        or objective not in _OPTIMIZATION_OBJECTIVES
        or region_policy not in _REGION_POLICIES
        or not isinstance(active_components, list)
        or not active_components
    ):
        return None

    normalized_components: list[dict[str, Any]] = []
    for component in active_components:
        if not isinstance(component, Mapping) or set(component) != {
            "path",
            "component_indices",
        }:
            return None
        path = component.get("path")
        indices = component.get("component_indices")
        if (
            not isinstance(path, str)
            or not path
            or not isinstance(indices, list)
            or not indices
            or not all(_valid_non_negative_index(index) for index in indices)
        ):
            return None
        normalized_components.append({"path": path, "component_indices": list(indices)})
    return {
        "target_layer_id": target_layer_id,
        "objective": objective,
        "active_components": normalized_components,
        "region_policy": region_policy,
    }


def parse_layered_shader_spec_semantics(
    text: str, *, layer_plan: LayerPlanV1
) -> ParsedAuthorOutput:
    """解析 Initial JSON，并以 probe identity 验证领域语义."""
    payload = _load_object(
        text,
        max_chars=_INITIAL_MAX_CHARS,
        error_code="invalid_layered_shader_spec_json",
    )
    optimization_focus_payload = _extract_optimization_focus(payload)
    _assert_no_untrusted_initial_fields(payload)
    probe_identity = build_author_identity(
        reference_sha256=layer_plan.reference_sha256,
        instruction_sha256="0" * 64,
        model_ref="parse-probe",
        prompt_version="parse-probe",
        role="initial",
        sampling_params={},
        plan_sha256=layer_plan.plan_sha256,
    )
    try:
        build_layered_shader_spec(
            payload,
            layer_plan=layer_plan,
            author_identity=probe_identity,
        )
    except LayeredSpecError as exc:
        raise LayeredDirectAuthorParseError(
            f"invalid_layered_shader_spec_{exc.code}"
        ) from exc
    return ParsedAuthorOutput(payload, optimization_focus_payload)


def parse_layer_patch_semantics(text: str) -> ParsedAuthorOutput:
    """解析 Refine JSON；base/expected hash 留给 Patch 应用时可信验证."""
    payload = _load_object(
        text, max_chars=_PATCH_MAX_CHARS, error_code="invalid_layer_patch_json"
    )
    optimization_focus_payload = _extract_optimization_focus(payload)
    _assert_no_untrusted_patch_fields(payload)
    try:
        build_layer_patch(payload)
    except LayeredSpecError as exc:
        raise LayeredDirectAuthorParseError(f"invalid_layer_patch_{exc.code}") from exc
    return ParsedAuthorOutput(payload, optimization_focus_payload)


def assemble_layered_shader_spec(
    semantics: Mapping[str, Any],
    *,
    layer_plan: LayerPlanV1,
    author_identity: AuthorIdentity,
) -> LayeredShaderSpecV1:
    """由可信调用身份装配 canonical LayeredShaderSpecV1."""
    return build_layered_shader_spec(
        semantics, layer_plan=layer_plan, author_identity=author_identity
    )


def assemble_layer_patch(semantics: Mapping[str, Any]) -> LayerPatchV1:
    """装配 canonical Patch；其 guard 仅在 apply 时对 incumbent 生效."""
    return build_layer_patch(semantics)


__all__ = [
    "LayerPatchV1",
    "LayeredDirectAuthorParseError",
    "LayeredShaderSpecV1",
    "ParsedAuthorOutput",
    "assemble_layer_patch",
    "assemble_layered_shader_spec",
    "layer_patch_json_schema",
    "layered_shader_spec_json_schema",
    "parse_layer_patch_semantics",
    "parse_layered_shader_spec_semantics",
]
