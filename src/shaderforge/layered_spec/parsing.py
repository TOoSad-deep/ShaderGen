"""模型 Layer 语义输出的严格解析与可信身份装配。."""

from __future__ import annotations

import re
from typing import Any, Mapping, cast

from shaderforge.layered_spec.blend_modes import (
    BLEND_MODES,
    DEFAULT_BLEND_MODE,
    BlendMode,
)
from shaderforge.layered_spec.hashing import (
    compute_layer_sha256,
    compute_layered_spec_sha256,
)
from shaderforge.layered_spec.models import (
    LAYER_PATCH_V1_SCHEMA_VERSION,
    LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION,
    LayeredShaderSpecV1,
    LayerPatchV1,
    LayerProgram,
)
from shaderforge.program_spec import (
    WEBGL1_RENDERER_CONTRACT_ID,
    AuthorIdentity,
    CanvasSpec,
    LayerPlanV1,
    ProgramSpecParseError,
    build_program_spec,
    recompute_plan_sha256,
)
from shaderforge.program_spec.models import (
    ID_PATTERN,
    LAYER_ROLES,
    MAX_LAYER_COUNT,
    SHA256_HEX_PATTERN,
)
from shaderforge.validation import ProgramSpecSafetyLimits

_MODEL_SPEC_KEYS = frozenset({"schema_version", "canvas", "layers"})
_MODEL_LAYER_KEYS = frozenset(
    {
        "layer_id",
        "role",
        "z_index",
        "blend_mode",
        "glsl_body",
        "uniform_schema",
        "uniform_values",
        "tunable_manifest",
    }
)
_MODEL_LAYER_REQUIRED_KEYS = _MODEL_LAYER_KEYS - {"blend_mode"}
_MODEL_PATCH_KEYS = frozenset(
    {
        "schema_version",
        "base_layered_spec_sha256",
        "target_layer_id",
        "expected_layer_sha256",
        "replacement",
    }
)
_FORBIDDEN_BODY_TOKEN = re.compile(
    r"(?m)^\s*#|\b(?:precision|varying|attribute|uniform)\b"
    r"|\bvoid\s+main\s*\(|\bgl_FragColor\b"
)
_INTERNAL_ROLE_MASK_MODE_UNIFORM = "u_sg_role_mask_mode"
_INTERNAL_BODY_TOKEN = re.compile(r"\bu_sg_role_mask_mode\b")
_NESTED_FUNCTION_TOKEN = re.compile(
    r"\b(?:void|bool|int|float|vec[234]|mat[234])\s+[A-Za-z_]\w*\s*\("
)
_PARSE_STUB_SOURCE = """precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;
void main() { gl_FragColor = vec4(1.0); }
"""


class LayeredSpecError(ValueError):
    """Layered 领域契约的机器可读 fail-closed 错误。."""

    def __init__(self, code: str, message: str) -> None:
        """记录机器可读错误码与人类可读消息."""
        self.code = code
        super().__init__(message)


def _mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LayeredSpecError("invalid_type", f"{name} 必须是对象。")
    return value


def _reject_keys(
    data: Mapping[str, Any],
    allowed: frozenset[str],
    *,
    allow_patch_hashes: bool = False,
) -> None:
    for key in data:
        if not isinstance(key, str):
            raise LayeredSpecError("non_string_key", "契约字段名必须是字符串。")
        if (key.endswith("_sha256") or key.endswith("_hash") or key == "hash") and not (
            allow_patch_hashes
            and key in {"base_layered_spec_sha256", "expected_layer_sha256"}
        ):
            raise LayeredSpecError(
                "model_forbidden_hash_field", f"模型不得自报哈希字段 {key}。"
            )
        if key in {"author_identity", "validation_attestation", "attestation"}:
            raise LayeredSpecError(
                "model_forbidden_trusted_field", f"模型不得自报可信字段 {key}。"
            )
        if key not in allowed:
            raise LayeredSpecError("unknown_field", f"未知字段 {key}。")


def _sha256(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or not SHA256_HEX_PATTERN.fullmatch(value):
        raise LayeredSpecError(
            "invalid_sha256", f"{name} 必须是 64 位小写十六进制 SHA-256。"
        )
    return value


def _clean_body(body: str) -> str:
    """遮蔽 GLSL 注释并保留换行。."""
    return re.sub(
        r"/\*.*?\*/|//[^\n]*",
        lambda match: "\n" * match.group(0).count("\n"),
        body,
        flags=re.DOTALL,
    )


def _balanced_body(cleaned_body: str) -> bool:
    """检查已遮蔽注释的括号/花括号，不允许逃逸固定函数 wrapper。."""
    stack: list[str] = []
    pairs = {")": "(", "}": "{", "]": "["}
    for char in cleaned_body:
        if char in "({[":
            stack.append(char)
        elif char in pairs:
            if not stack or stack.pop() != pairs[char]:
                return False
    return not stack


def _parse_bindings(
    layer: Mapping[str, Any],
    *,
    canvas: Mapping[str, Any],
    author_identity: AuthorIdentity,
) -> tuple[CanvasSpec, tuple[Any, ...], dict[str, Any], tuple[Any, ...]]:
    try:
        parsed = build_program_spec(
            {
                "schema_version": "shader_program_spec_v1",
                "fragment_source": _PARSE_STUB_SOURCE,
                "uniform_schema": layer.get("uniform_schema"),
                "uniform_values": layer.get("uniform_values"),
                "tunable_manifest": layer.get("tunable_manifest"),
                "canvas": canvas,
                "renderer_contract_id": WEBGL1_RENDERER_CONTRACT_ID,
            },
            author_identity=author_identity,
        )
    except ProgramSpecParseError as exc:
        raise LayeredSpecError(exc.code, str(exc)) from exc
    return (
        parsed.canvas,
        parsed.uniform_schema,
        dict(parsed.uniform_values),
        parsed.tunable_manifest,
    )


def _parse_layer(
    raw: Any,
    *,
    index: int,
    canvas: Mapping[str, Any],
    author_identity: AuthorIdentity,
) -> tuple[LayerProgram, CanvasSpec]:
    data = _mapping(raw, name=f"layers[{index}]")
    _reject_keys(data, _MODEL_LAYER_KEYS)
    missing = sorted(_MODEL_LAYER_REQUIRED_KEYS - set(data))
    if missing:
        raise LayeredSpecError("missing_field", f"layers[{index}] 缺少字段 {missing}。")
    layer_id = data["layer_id"]
    if not isinstance(layer_id, str) or not ID_PATTERN.fullmatch(layer_id):
        raise LayeredSpecError("invalid_layer_id", "layer_id 不符合 ID_PATTERN。")
    role = data["role"]
    if role not in LAYER_ROLES:
        raise LayeredSpecError("invalid_layer_role", "role 不在支持的角色集合内。")
    z_index = data["z_index"]
    if isinstance(z_index, bool) or not isinstance(z_index, int) or z_index < 0:
        raise LayeredSpecError("out_of_domain", "z_index 必须是非负整数。")
    raw_blend_mode = data.get("blend_mode", DEFAULT_BLEND_MODE)
    if not isinstance(raw_blend_mode, str) or raw_blend_mode not in BLEND_MODES:
        raise LayeredSpecError(
            "invalid_blend_mode",
            f"blend_mode 必须是受支持的模式之一：{', '.join(BLEND_MODES)}。",
        )
    blend_mode = cast(BlendMode, raw_blend_mode)
    body = data["glsl_body"]
    if not isinstance(body, str) or not body.strip():
        raise LayeredSpecError("invalid_type", "glsl_body 必须是非空字符串。")
    cleaned_body = _clean_body(body)
    if _FORBIDDEN_BODY_TOKEN.search(cleaned_body):
        raise LayeredSpecError(
            "forbidden_glsl_body_declaration",
            "glsl_body 不得包含预处理、全局声明、main 或 gl_FragColor。",
        )
    if _INTERNAL_BODY_TOKEN.search(cleaned_body):
        raise LayeredSpecError(
            "forbidden_internal_uniform_reference",
            "glsl_body 不得引用 Renderer 内部 diagnostic uniform。",
        )
    if _NESTED_FUNCTION_TOKEN.search(cleaned_body):
        raise LayeredSpecError(
            "forbidden_layer_helper",
            "第一版 glsl_body 不得声明 helper function。",
        )
    if not _balanced_body(cleaned_body):
        raise LayeredSpecError(
            "unbalanced_glsl_body", "glsl_body 的括号或花括号不平衡。"
        )
    if re.search(r"\breturn\b", cleaned_body) is None:
        raise LayeredSpecError(
            "missing_layer_return", "glsl_body 必须返回 premultiplied vec4。"
        )
    parsed_canvas, schema, values, tunables = _parse_bindings(
        data, canvas=canvas, author_identity=author_identity
    )
    if any(
        declaration.name == _INTERNAL_ROLE_MASK_MODE_UNIFORM for declaration in schema
    ):
        raise LayeredSpecError(
            "reserved_uniform",
            "u_sg_role_mask_mode 由 Layered Compiler 保留。",
        )
    layer_hash = compute_layer_sha256(
        layer_id=layer_id,
        role=role,
        z_index=z_index,
        blend_mode=blend_mode,
        glsl_body=body,
        uniform_schema=schema,
        uniform_values=values,
        tunable_manifest=tunables,
    )
    return (
        LayerProgram(
            layer_id=layer_id,
            role=role,
            z_index=z_index,
            blend_mode=blend_mode,
            glsl_body=body,
            uniform_schema=schema,
            uniform_values=values,
            tunable_manifest=tunables,
            layer_sha256=layer_hash,
        ),
        parsed_canvas,
    )


def _validate_global_uniforms(layers: tuple[LayerProgram, ...]) -> None:
    seen: set[str] = set()
    for layer in layers:
        for declaration in layer.uniform_schema:
            if declaration.name in seen:
                raise LayeredSpecError(
                    "duplicate_global_uniform",
                    f"uniform {declaration.name} 必须在全部 Layer 中全局唯一。",
                )
            seen.add(declaration.name)


def _validate_resource_limits(
    layers: tuple[LayerProgram, ...], canvas: CanvasSpec
) -> None:
    # 延迟 import 避免 parsing/compiler 的模块初始化环；此处只调用纯源码 emitter。
    from shaderforge.layered_spec.compiler import _emit_source

    limits = ProgramSpecSafetyLimits()
    tunable_count = sum(len(layer.tunable_manifest) for layer in layers)
    if tunable_count > limits.max_tunables:
        raise LayeredSpecError(
            "too_many_tunables",
            f"tunable 数量超过 {limits.max_tunables} 上限。",
        )
    if max(canvas.width, canvas.height) > limits.max_canvas_side:
        raise LayeredSpecError(
            "canvas_too_large",
            f"canvas 长边超过 {limits.max_canvas_side} 上限。",
        )
    if len(_emit_source(layers)) > limits.max_source_chars:
        raise LayeredSpecError(
            "source_too_large",
            f"编译后 fragment_source 超过 {limits.max_source_chars} 字符上限。",
        )


def _assemble_spec(
    *,
    plan_sha256: str,
    canvas: CanvasSpec,
    layers: tuple[LayerProgram, ...],
    author_identity: AuthorIdentity,
) -> LayeredShaderSpecV1:
    _validate_global_uniforms(layers)
    _validate_resource_limits(layers, canvas)
    spec_hash = compute_layered_spec_sha256(
        schema_version=LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION,
        plan_sha256=plan_sha256,
        canvas=canvas,
        layers=layers,
        author_identity=author_identity,
    )
    return LayeredShaderSpecV1(
        schema_version=LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION,
        plan_sha256=plan_sha256,
        canvas=canvas,
        layers=layers,
        author_identity=author_identity,
        layered_spec_sha256=spec_hash,
    )


def build_layered_shader_spec(
    model_output: Mapping[str, Any],
    layer_plan: LayerPlanV1,
    author_identity: AuthorIdentity,
) -> LayeredShaderSpecV1:
    """严格解析完整 Layered Spec，并绑定可信 LayerPlan 与 author identity。."""
    data = _mapping(model_output, name="model_output")
    _reject_keys(data, _MODEL_SPEC_KEYS)
    if data.get("schema_version") != LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION:
        raise LayeredSpecError(
            "invalid_schema_version",
            f"schema_version 必须是 {LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION}。",
        )
    if recompute_plan_sha256(layer_plan) != layer_plan.plan_sha256:
        raise LayeredSpecError("plan_hash_mismatch", "LayerPlan 内容哈希失配。")
    if author_identity.plan_sha256 != layer_plan.plan_sha256:
        raise LayeredSpecError(
            "author_plan_mismatch", "author identity 未绑定当前 LayerPlan。"
        )
    if author_identity.reference_sha256 != layer_plan.reference_sha256:
        raise LayeredSpecError(
            "author_reference_mismatch", "author identity 未绑定 LayerPlan 参考图。"
        )
    raw_canvas = _mapping(data.get("canvas"), name="canvas")
    raw_layers = data.get("layers")
    if not isinstance(raw_layers, list) or not 1 <= len(raw_layers) <= MAX_LAYER_COUNT:
        raise LayeredSpecError(
            "out_of_domain", f"layers 必须包含 1 到 {MAX_LAYER_COUNT} 项。"
        )
    if len(raw_layers) != len(layer_plan.layers):
        raise LayeredSpecError(
            "layer_plan_mismatch", "Layered Spec 层数必须与 LayerPlan 一致。"
        )
    parsed: list[LayerProgram] = []
    canvas: CanvasSpec | None = None
    for index, raw_layer in enumerate(raw_layers):
        layer, parsed_canvas = _parse_layer(
            raw_layer,
            index=index,
            canvas=raw_canvas,
            author_identity=author_identity,
        )
        planned = layer_plan.layers[index]
        if (
            layer.layer_id,
            layer.role,
            layer.z_index,
        ) != (
            planned.layer_id,
            planned.role,
            planned.z_index,
        ):
            raise LayeredSpecError(
                "layer_plan_mismatch",
                f"layers[{index}] 的 id/role/z_index 必须与 LayerPlan 逐项一致。",
            )
        parsed.append(layer)
        canvas = parsed_canvas
    assert canvas is not None
    return _assemble_spec(
        plan_sha256=layer_plan.plan_sha256,
        canvas=canvas,
        layers=tuple(parsed),
        author_identity=author_identity,
    )


def build_layer_patch(model_output: Mapping[str, Any]) -> LayerPatchV1:
    """严格解析 replace-one-layer Patch；replacement 哈希由可信层计算。."""
    data = _mapping(model_output, name="model_output")
    _reject_keys(data, _MODEL_PATCH_KEYS, allow_patch_hashes=True)
    if data.get("schema_version") != LAYER_PATCH_V1_SCHEMA_VERSION:
        raise LayeredSpecError(
            "invalid_schema_version",
            f"schema_version 必须是 {LAYER_PATCH_V1_SCHEMA_VERSION}。",
        )
    base_hash = _sha256(
        data.get("base_layered_spec_sha256"),
        name="base_layered_spec_sha256",
    )
    expected_hash = _sha256(
        data.get("expected_layer_sha256"), name="expected_layer_sha256"
    )
    target = data.get("target_layer_id")
    if not isinstance(target, str) or not ID_PATTERN.fullmatch(target):
        raise LayeredSpecError("invalid_layer_id", "target_layer_id 不合法。")
    replacement_data = _mapping(data.get("replacement"), name="replacement")
    # Patch replacement 的 binding/canvas 解析延迟到 apply，此处用可信占位身份和画布
    # 会错误引入外部语义，因此只做结构检查并由专用轻量 parser 规范化。
    replacement = _parse_patch_replacement(replacement_data)
    return LayerPatchV1(
        schema_version=LAYER_PATCH_V1_SCHEMA_VERSION,
        base_layered_spec_sha256=base_hash,
        target_layer_id=target,
        expected_layer_sha256=expected_hash,
        replacement=replacement,
    )


def _parse_patch_replacement(data: Mapping[str, Any]) -> LayerProgram:
    """在无画布/身份条件下复用 ProgramSpec binding parser。."""
    from shaderforge.program_spec import build_author_identity, sha256_hex_text

    placeholder = build_author_identity(
        reference_sha256=sha256_hex_text("layer-patch-parser"),
        instruction_sha256=sha256_hex_text("layer-patch-parser"),
        model_ref="trusted-layer-patch-parser",
        prompt_version="layer_patch_v1",
        role="initial",
    )
    layer, _canvas = _parse_layer(
        data,
        index=0,
        canvas={"width": 1, "height": 1},
        author_identity=placeholder,
    )
    return layer


parse_layer_patch = build_layer_patch
