"""模型语义输出的严格解析与可信规范化组装.

防伪边界（fail-closed）：模型输出中出现 ``validation_attestation``、
作者身份或任何自报哈希字段（``*_sha256``/``*_hash``）一律拒绝；
未知字段同样拒绝。所有哈希由可信层在解析后重算。
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from shaderforge.program_spec.hashing import (
    compute_binding_sha256,
    compute_plan_sha256,
    compute_source_sha256,
    compute_spec_sha256,
)
from shaderforge.program_spec.models import (
    AUTHOR_ROLES,
    ID_PATTERN,
    LAYER_PLAN_V1_SCHEMA_VERSION,
    LAYER_ROLES,
    MAX_AUTHOR_FIELD_CHARS,
    MAX_DOMINANT_COLORS,
    MAX_LAYER_COUNT,
    MAX_LAYER_NOTES_CHARS,
    RESERVED_UNIFORMS,
    SHA256_HEX_PATTERN,
    SHADER_PROGRAM_SPEC_V1_SCHEMA_VERSION,
    UNIFORM_COMPONENT_COUNTS,
    UNIFORM_NAME_PATTERN,
    UNIFORM_TYPES,
    WEBGL1_RENDERER_CONTRACT_ID,
    AuthorIdentity,
    AuthorRole,
    CanvasSpec,
    LayerAuthorIdentity,
    LayerPlanV1,
    LayerSpec,
    NormalizedRegion,
    RgbaColor,
    ShaderProgramSpecV1,
    TunableParameter,
    UniformDeclaration,
)

MODEL_SPEC_ALLOWED_KEYS = frozenset(
    {
        "schema_version",
        "fragment_source",
        "uniform_schema",
        "uniform_values",
        "tunable_manifest",
        "canvas",
        "renderer_contract_id",
    }
)
MODEL_PLAN_ALLOWED_KEYS = frozenset({"schema_version", "layers"})
LAYER_ALLOWED_KEYS = frozenset(
    {
        "layer_id",
        "role",
        "z_index",
        "region",
        "dominant_colors",
        "confidence",
        "notes",
    }
)
REGION_ALLOWED_KEYS = frozenset({"x", "y", "width", "height"})
UNIFORM_DECLARATION_ALLOWED_KEYS = frozenset({"type", "minimum", "maximum", "default"})
TUNABLE_ALLOWED_KEYS = frozenset({"path", "type", "minimum", "maximum", "step"})
CANVAS_ALLOWED_KEYS = frozenset({"width", "height"})
ATTESTATION_KEYS = frozenset({"validation_attestation", "attestation"})
REGION_BBOX_EPSILON = 1e-9


class ProgramSpecParseError(ValueError):
    """模型输出或可信元数据违反契约的 fail-closed 解析错误."""

    def __init__(self, code: str, message: str) -> None:
        """记录机器可读的违规代码与人类可读消息."""
        self.code = code
        super().__init__(message)


def _reject_forbidden_keys(data: Mapping[str, Any], allowed: frozenset[str]) -> None:
    for key in data:
        if not isinstance(key, str):
            raise ProgramSpecParseError(
                "non_string_key", "契约对象的字段名必须是字符串。"
            )
        if key in ATTESTATION_KEYS:
            raise ProgramSpecParseError(
                "model_forbidden_attestation",
                f"模型输出不得自带 {key}，attestation 只能由可信 Validator 签发。",
            )
        if key.endswith("_sha256") or key.endswith("_hash") or key == "hash":
            raise ProgramSpecParseError(
                "model_forbidden_hash_field",
                f"模型输出不得自报哈希字段 {key}，哈希由可信层重算。",
            )
        if key == "author_identity":
            raise ProgramSpecParseError(
                "model_forbidden_author_identity",
                "author_identity 由可信层按调用元数据绑定，模型不得自报。",
            )
        if key not in allowed:
            raise ProgramSpecParseError(
                "unknown_field", f"未知字段 {key}，契约严格拒绝未知字段。"
            )


def _require_mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProgramSpecParseError("invalid_type", f"{name} 必须是对象。")
    return value


def _finite_number(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProgramSpecParseError("non_finite_number", f"{name} 必须是有限数值。")
    result = float(value)
    if not math.isfinite(result):
        raise ProgramSpecParseError(
            "non_finite_number", f"{name} 必须有限，拒绝 NaN/Inf。"
        )
    return result


def _unit_interval(value: Any, *, name: str) -> float:
    result = _finite_number(value, name=name)
    if not 0.0 <= result <= 1.0:
        raise ProgramSpecParseError("out_of_domain", f"{name} 必须在 [0, 1] 内。")
    return result


def _positive_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProgramSpecParseError("invalid_type", f"{name} 必须是整数。")
    if value <= 0:
        raise ProgramSpecParseError("out_of_domain", f"{name} 必须为正整数。")
    result = int(value)
    return result


def _short_text(value: Any, *, name: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProgramSpecParseError("invalid_type", f"{name} 必须是非空字符串。")
    if len(value) > max_chars:
        raise ProgramSpecParseError(
            "out_of_domain", f"{name} 超过 {max_chars} 字符上限。"
        )
    return value


def _sha256_hex(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not SHA256_HEX_PATTERN.fullmatch(value):
        raise ProgramSpecParseError(
            "invalid_sha256", f"{name} 必须是 64 位小写十六进制 SHA-256。"
        )
    return value


def _component_tuple(value: Any, *, name: str, count: int) -> tuple[float, ...]:
    if count == 1:
        return (_finite_number(value, name=name),)
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise ProgramSpecParseError(
            "invalid_type", f"{name} 必须是长度 {count} 的数值数组。"
        )
    return tuple(
        _finite_number(item, name=f"{name}[{index}]")
        for index, item in enumerate(value)
    )


def _parse_uniform_declaration(name: Any, raw: Any) -> UniformDeclaration:
    if not isinstance(name, str) or not UNIFORM_NAME_PATTERN.fullmatch(name):
        raise ProgramSpecParseError(
            "invalid_uniform_name", "uniform 名必须是 u_ 开头的 ASCII 标识符。"
        )
    if name in RESERVED_UNIFORMS:
        raise ProgramSpecParseError(
            "reserved_uniform", f"uniform {name} 由 Renderer 保留并自动上传。"
        )
    data = _require_mapping(raw, name=f"uniform_schema[{name}]")
    _reject_forbidden_keys(data, UNIFORM_DECLARATION_ALLOWED_KEYS)
    raw_type = data.get("type")
    if raw_type not in UNIFORM_TYPES:
        raise ProgramSpecParseError(
            "invalid_uniform_type",
            f"uniform {name} 只支持 float、vec2、vec3 或 vec4。",
        )
    uniform_type = raw_type
    count = UNIFORM_COMPONENT_COUNTS[uniform_type]
    for field in ("minimum", "maximum", "default"):
        if field not in data:
            raise ProgramSpecParseError(
                "missing_field", f"uniform {name} 缺少 {field} 声明。"
            )
    minimum = _component_tuple(data["minimum"], name=f"{name}.minimum", count=count)
    maximum = _component_tuple(data["maximum"], name=f"{name}.maximum", count=count)
    default = _component_tuple(data["default"], name=f"{name}.default", count=count)
    for index in range(count):
        if not minimum[index] <= default[index] <= maximum[index]:
            raise ProgramSpecParseError(
                "out_of_domain",
                f"uniform {name} 的 default 必须落在 [minimum, maximum] 域内。",
            )
    return UniformDeclaration(
        name=name,
        type=uniform_type,
        minimum=minimum,
        maximum=maximum,
        default=default,
    )


def _parse_uniform_values(
    raw: Any, uniform_schema: tuple[UniformDeclaration, ...]
) -> dict[str, Any]:
    data = _require_mapping(raw, name="uniform_values")
    for key in data:
        if key.endswith("_sha256") or key.endswith("_hash"):
            raise ProgramSpecParseError(
                "model_forbidden_hash_field", "uniform_values 不得携带哈希字段。"
            )
    schema_by_name = {item.name: item for item in uniform_schema}
    missing = sorted(set(schema_by_name) - set(data))
    extra = sorted(set(data) - set(schema_by_name))
    if missing or extra:
        raise ProgramSpecParseError(
            "uniform_values_mismatch",
            f"uniform_values 必须与 uniform_schema 一一对应；missing={missing}，extra={extra}。",
        )
    values: dict[str, Any] = {}
    for name, declaration in schema_by_name.items():
        count = declaration.component_count
        parsed = _component_tuple(
            data[name], name=f"uniform_values[{name}]", count=count
        )
        for index in range(count):
            if (
                not declaration.minimum[index]
                <= parsed[index]
                <= declaration.maximum[index]
            ):
                raise ProgramSpecParseError(
                    "out_of_domain",
                    f"uniform {name} 的初值必须落在声明域内。",
                )
        values[name] = parsed[0] if count == 1 else parsed
    return values


def _parse_tunable_manifest(
    raw: Any, uniform_schema: tuple[UniformDeclaration, ...]
) -> tuple[TunableParameter, ...]:
    if not isinstance(raw, list):
        raise ProgramSpecParseError("invalid_type", "tunable_manifest 必须是数组。")
    schema_by_name = {item.name: item for item in uniform_schema}
    seen: set[str] = set()
    manifest: list[TunableParameter] = []
    for index, item in enumerate(raw):
        data = _require_mapping(item, name=f"tunable_manifest[{index}]")
        _reject_forbidden_keys(data, TUNABLE_ALLOWED_KEYS)
        for field in ("path", "type", "minimum", "maximum", "step"):
            if field not in data:
                raise ProgramSpecParseError(
                    "missing_field", f"tunable_manifest[{index}] 缺少 {field}。"
                )
        path = data["path"]
        if path not in schema_by_name:
            raise ProgramSpecParseError(
                "unknown_tunable_path",
                f"tunable 参数 {path} 必须引用已声明的 uniform。",
            )
        if path in seen:
            raise ProgramSpecParseError(
                "duplicate_tunable_path", f"tunable 参数 {path} 重复声明。"
            )
        seen.add(path)
        declaration = schema_by_name[path]
        if data["type"] != declaration.type:
            raise ProgramSpecParseError(
                "tunable_type_mismatch",
                f"tunable 参数 {path} 的类型必须与 uniform 声明一致。",
            )
        count = declaration.component_count
        minimum = _component_tuple(data["minimum"], name=f"{path}.minimum", count=count)
        maximum = _component_tuple(data["maximum"], name=f"{path}.maximum", count=count)
        for component in range(count):
            if not (
                declaration.minimum[component]
                <= minimum[component]
                <= maximum[component]
                <= declaration.maximum[component]
            ):
                raise ProgramSpecParseError(
                    "out_of_domain",
                    f"tunable 参数 {path} 的范围必须在 uniform 声明域内。",
                )
        step = _finite_number(data["step"], name=f"{path}.step")
        if step <= 0.0:
            raise ProgramSpecParseError(
                "out_of_domain", f"tunable 参数 {path} 的 step 必须为正有限数。"
            )
        manifest.append(
            TunableParameter(
                path=path,
                type=declaration.type,
                minimum=minimum,
                maximum=maximum,
                step=step,
            )
        )
    return tuple(manifest)


def _parse_canvas(raw: Any) -> CanvasSpec:
    data = _require_mapping(raw, name="canvas")
    _reject_forbidden_keys(data, CANVAS_ALLOWED_KEYS)
    for field in ("width", "height"):
        if field not in data:
            raise ProgramSpecParseError("missing_field", f"canvas 缺少 {field}。")
    return CanvasSpec(
        width=_positive_int(data["width"], name="canvas.width"),
        height=_positive_int(data["height"], name="canvas.height"),
    )


def _normalize_sampling_params(
    sampling_params: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """规范化采样身份字段：只接受字符串、有限数值、布尔或 null."""
    normalized_params: dict[str, Any] = {}
    for key, value in (sampling_params or {}).items():
        if not isinstance(key, str):
            raise ProgramSpecParseError(
                "invalid_type", "sampling_params 的 key 必须是字符串。"
            )
        if isinstance(value, bool) or value is None or isinstance(value, str):
            normalized_params[key] = value
        elif isinstance(value, (int, float)):
            normalized_params[key] = _finite_number(
                value, name=f"sampling_params[{key}]"
            )
        else:
            raise ProgramSpecParseError(
                "invalid_type",
                f"sampling_params[{key}] 只支持字符串、数值、布尔或 null。",
            )
    return normalized_params


def build_author_identity(
    *,
    reference_sha256: str,
    instruction_sha256: str,
    model_ref: str,
    prompt_version: str,
    role: AuthorRole,
    sampling_params: Mapping[str, Any] | None = None,
    plan_sha256: str | None = None,
    parent_spec_sha256: str | None = None,
    reference_content_type: str | None = None,
    input_context_sha256: str | None = None,
    repair_context_sha256: str | None = None,
) -> AuthorIdentity:
    """由可信层按实际调用元数据组装作者身份与血缘."""
    reference = _sha256_hex(reference_sha256, name="reference_sha256")
    instruction = _sha256_hex(instruction_sha256, name="instruction_sha256")
    if plan_sha256 is not None:
        plan_sha256 = _sha256_hex(plan_sha256, name="plan_sha256")
    if parent_spec_sha256 is not None:
        parent_spec_sha256 = _sha256_hex(parent_spec_sha256, name="parent_spec_sha256")
    if input_context_sha256 is not None:
        input_context_sha256 = _sha256_hex(
            input_context_sha256, name="input_context_sha256"
        )
    if repair_context_sha256 is not None:
        repair_context_sha256 = _sha256_hex(
            repair_context_sha256, name="repair_context_sha256"
        )
    if reference_content_type is not None:
        reference_content_type = _short_text(
            reference_content_type,
            name="reference_content_type",
            max_chars=MAX_AUTHOR_FIELD_CHARS,
        )
    if role not in AUTHOR_ROLES:
        raise ProgramSpecParseError(
            "invalid_author_role", f"author role 必须是 {sorted(AUTHOR_ROLES)} 之一。"
        )
    if role in {"refine", "repair"} and parent_spec_sha256 is None:
        raise ProgramSpecParseError(
            "missing_parent_spec",
            f"{role} 角色必须绑定父 spec_sha256。",
        )
    if role == "initial" and parent_spec_sha256 is not None:
        raise ProgramSpecParseError(
            "unexpected_parent_spec", "initial 角色不得携带父 spec_sha256。"
        )
    return AuthorIdentity(
        reference_sha256=reference,
        plan_sha256=plan_sha256,
        instruction_sha256=instruction,
        model_ref=_short_text(
            model_ref, name="model_ref", max_chars=MAX_AUTHOR_FIELD_CHARS
        ),
        prompt_version=_short_text(
            prompt_version, name="prompt_version", max_chars=MAX_AUTHOR_FIELD_CHARS
        ),
        role=role,
        parent_spec_sha256=parent_spec_sha256,
        sampling_params=_normalize_sampling_params(sampling_params),
        reference_content_type=reference_content_type,
        input_context_sha256=input_context_sha256,
        repair_context_sha256=repair_context_sha256,
    )


def build_program_spec(
    model_output: Mapping[str, Any],
    *,
    author_identity: AuthorIdentity,
) -> ShaderProgramSpecV1:
    """解析模型语义输出并由可信层重算哈希，组装完整 Spec.

    模型只输出语义字段；出现 attestation、作者身份或自报哈希字段即
    fail-closed 拒绝。
    """
    data = _require_mapping(model_output, name="model_output")
    _reject_forbidden_keys(data, MODEL_SPEC_ALLOWED_KEYS)
    if data.get("schema_version") != SHADER_PROGRAM_SPEC_V1_SCHEMA_VERSION:
        raise ProgramSpecParseError(
            "invalid_schema_version",
            f"schema_version 必须是 {SHADER_PROGRAM_SPEC_V1_SCHEMA_VERSION}。",
        )
    if data.get("renderer_contract_id") != WEBGL1_RENDERER_CONTRACT_ID:
        raise ProgramSpecParseError(
            "unsupported_renderer_contract",
            f"renderer_contract_id 当前只支持 {WEBGL1_RENDERER_CONTRACT_ID}。",
        )
    fragment_source = data.get("fragment_source")
    if not isinstance(fragment_source, str) or not fragment_source.strip():
        raise ProgramSpecParseError(
            "invalid_type", "fragment_source 必须是非空 GLSL 源码字符串。"
        )
    raw_schema = _require_mapping(data.get("uniform_schema"), name="uniform_schema")
    uniform_schema = tuple(
        _parse_uniform_declaration(name, raw) for name, raw in raw_schema.items()
    )
    uniform_values = _parse_uniform_values(data.get("uniform_values"), uniform_schema)
    tunable_manifest = _parse_tunable_manifest(
        data.get("tunable_manifest"), uniform_schema
    )
    canvas = _parse_canvas(data.get("canvas"))

    source_sha256 = compute_source_sha256(fragment_source)
    binding_sha256 = compute_binding_sha256(uniform_schema, uniform_values)
    spec_sha256 = compute_spec_sha256(
        schema_version=SHADER_PROGRAM_SPEC_V1_SCHEMA_VERSION,
        renderer_contract_id=WEBGL1_RENDERER_CONTRACT_ID,
        source_sha256=source_sha256,
        binding_sha256=binding_sha256,
        tunable_manifest=tunable_manifest,
        canvas=canvas,
        author_identity=author_identity,
    )
    return ShaderProgramSpecV1(
        schema_version=SHADER_PROGRAM_SPEC_V1_SCHEMA_VERSION,
        fragment_source=fragment_source,
        uniform_schema=uniform_schema,
        uniform_values=uniform_values,
        tunable_manifest=tunable_manifest,
        canvas=canvas,
        renderer_contract_id=WEBGL1_RENDERER_CONTRACT_ID,
        source_sha256=source_sha256,
        binding_sha256=binding_sha256,
        spec_sha256=spec_sha256,
        author_identity=author_identity,
        validation_attestation=None,
    )


def _parse_region(raw: Any) -> NormalizedRegion:
    data = _require_mapping(raw, name="region")
    _reject_forbidden_keys(data, REGION_ALLOWED_KEYS)
    for field in ("x", "y", "width", "height"):
        if field not in data:
            raise ProgramSpecParseError("missing_field", f"region 缺少 {field}。")
    region = NormalizedRegion(
        x=_unit_interval(data["x"], name="region.x"),
        y=_unit_interval(data["y"], name="region.y"),
        width=_unit_interval(data["width"], name="region.width"),
        height=_unit_interval(data["height"], name="region.height"),
    )
    if region.width <= 0.0 or region.height <= 0.0:
        raise ProgramSpecParseError(
            "out_of_domain", "region 的 width/height 必须大于 0。"
        )
    if (
        region.x + region.width > 1.0 + REGION_BBOX_EPSILON
        or region.y + region.height > 1.0 + REGION_BBOX_EPSILON
    ):
        raise ProgramSpecParseError(
            "out_of_domain", "region 必须落在归一化画面范围内。"
        )
    return region


def _parse_layer(raw: Any, *, index: int) -> LayerSpec:
    data = _require_mapping(raw, name=f"layers[{index}]")
    _reject_forbidden_keys(data, LAYER_ALLOWED_KEYS)
    for field in (
        "layer_id",
        "role",
        "z_index",
        "region",
        "dominant_colors",
        "confidence",
    ):
        if field not in data:
            raise ProgramSpecParseError(
                "missing_field", f"layers[{index}] 缺少 {field}。"
            )
    layer_id = data["layer_id"]
    if not isinstance(layer_id, str) or not ID_PATTERN.fullmatch(layer_id):
        raise ProgramSpecParseError(
            "invalid_layer_id", "layer_id 必须匹配 ID_PATTERN。"
        )
    role = data["role"]
    if role not in LAYER_ROLES:
        raise ProgramSpecParseError(
            "invalid_layer_role", f"layer role 必须是 {sorted(LAYER_ROLES)} 之一。"
        )
    z_index = data["z_index"]
    if isinstance(z_index, bool) or not isinstance(z_index, int) or z_index < 0:
        raise ProgramSpecParseError("out_of_domain", "z_index 必须是非负整数。")
    raw_colors = data["dominant_colors"]
    if (
        not isinstance(raw_colors, list)
        or not 1 <= len(raw_colors) <= MAX_DOMINANT_COLORS
    ):
        raise ProgramSpecParseError(
            "out_of_domain",
            f"dominant_colors 必须包含 1 到 {MAX_DOMINANT_COLORS} 个 RGBA 颜色。",
        )
    colors = []
    for color_index, raw_color in enumerate(raw_colors):
        if not isinstance(raw_color, (list, tuple)) or len(raw_color) != 4:
            raise ProgramSpecParseError(
                "invalid_type",
                f"dominant_colors[{color_index}] 必须是长度 4 的 RGBA 数组。",
            )
        colors.append(
            RgbaColor(
                r=_unit_interval(raw_color[0], name="color.r"),
                g=_unit_interval(raw_color[1], name="color.g"),
                b=_unit_interval(raw_color[2], name="color.b"),
                a=_unit_interval(raw_color[3], name="color.a"),
            )
        )
    notes = data.get("notes")
    if notes is not None:
        if not isinstance(notes, str) or len(notes) > MAX_LAYER_NOTES_CHARS:
            raise ProgramSpecParseError(
                "out_of_domain",
                f"notes 必须是不超过 {MAX_LAYER_NOTES_CHARS} 字符的字符串。",
            )
    return LayerSpec(
        layer_id=layer_id,
        role=role,
        z_index=z_index,
        region=_parse_region(data["region"]),
        dominant_colors=tuple(colors),
        confidence=_unit_interval(data["confidence"], name="confidence"),
        notes=notes,
    )


def build_layer_author_identity(
    *,
    model_ref: str,
    prompt_version: str,
    instruction_sha256: str | None = None,
    reference_content_type: str | None = None,
    sampling_params: Mapping[str, Any] | None = None,
    repair_context_sha256: str | None = None,
) -> LayerAuthorIdentity:
    """由可信层组装 LayerPlan 作者身份，绑定指令、媒体类型与实际采样身份."""
    if instruction_sha256 is not None:
        instruction_sha256 = _sha256_hex(instruction_sha256, name="instruction_sha256")
    if reference_content_type is not None:
        reference_content_type = _short_text(
            reference_content_type,
            name="reference_content_type",
            max_chars=MAX_AUTHOR_FIELD_CHARS,
        )
    if repair_context_sha256 is not None:
        repair_context_sha256 = _sha256_hex(
            repair_context_sha256, name="repair_context_sha256"
        )
    return LayerAuthorIdentity(
        model_ref=_short_text(
            model_ref, name="model_ref", max_chars=MAX_AUTHOR_FIELD_CHARS
        ),
        prompt_version=_short_text(
            prompt_version, name="prompt_version", max_chars=MAX_AUTHOR_FIELD_CHARS
        ),
        schema_version=LAYER_PLAN_V1_SCHEMA_VERSION,
        instruction_sha256=instruction_sha256,
        reference_content_type=reference_content_type,
        sampling_params=(
            _normalize_sampling_params(sampling_params)
            if sampling_params is not None
            else None
        ),
        repair_context_sha256=repair_context_sha256,
    )


def build_layer_plan(
    model_output: Mapping[str, Any],
    *,
    reference_sha256: str,
    author_identity: LayerAuthorIdentity,
    observations_ref: str | None = None,
) -> LayerPlanV1:
    """解析视觉分析 Author 的语义输出并由可信层组装 LayerPlanV1.

    参考图哈希、作者身份与 observations 引用都由可信层绑定，
    ``plan_sha256`` 由可信层对规范化 JSON 重算。
    """
    data = _require_mapping(model_output, name="model_output")
    _reject_forbidden_keys(data, MODEL_PLAN_ALLOWED_KEYS)
    if data.get("schema_version") != LAYER_PLAN_V1_SCHEMA_VERSION:
        raise ProgramSpecParseError(
            "invalid_schema_version",
            f"schema_version 必须是 {LAYER_PLAN_V1_SCHEMA_VERSION}。",
        )
    raw_layers = data.get("layers")
    if not isinstance(raw_layers, list) or not 1 <= len(raw_layers) <= MAX_LAYER_COUNT:
        raise ProgramSpecParseError(
            "out_of_domain", f"layers 必须包含 1 到 {MAX_LAYER_COUNT} 项。"
        )
    layers = tuple(
        _parse_layer(item, index=index) for index, item in enumerate(raw_layers)
    )
    layer_ids = [layer.layer_id for layer in layers]
    if len(set(layer_ids)) != len(layer_ids):
        raise ProgramSpecParseError(
            "duplicate_layer_id", "layers 中的 layer_id 必须唯一。"
        )
    reference = _sha256_hex(reference_sha256, name="reference_sha256")
    if observations_ref is not None:
        observations_ref = _sha256_hex(observations_ref, name="observations_ref")
    plan_sha256 = compute_plan_sha256(
        schema_version=LAYER_PLAN_V1_SCHEMA_VERSION,
        layers=layers,
        reference_sha256=reference,
        author_identity=author_identity,
        observations_ref=observations_ref,
    )
    return LayerPlanV1(
        schema_version=LAYER_PLAN_V1_SCHEMA_VERSION,
        layers=layers,
        reference_sha256=reference,
        author_identity=author_identity,
        observations_ref=observations_ref,
        plan_sha256=plan_sha256,
    )
