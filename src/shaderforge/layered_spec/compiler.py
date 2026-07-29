"""把 LayeredShaderSpecV1 确定性编译为现有 ProgramSpec。."""

from __future__ import annotations

import re

from shaderforge.layered_spec.hashing import (
    recompute_layer_sha256,
    recompute_layered_spec_sha256,
)
from shaderforge.layered_spec.models import (
    LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION,
    LayeredShaderSpecV1,
    LayerProgram,
    tunable_parameter_to_model_dict,
    uniform_declaration_to_model_dict,
)
from shaderforge.layered_spec.parsing import LayeredSpecError, _validate_global_uniforms
from shaderforge.program_spec import (
    WEBGL1_RENDERER_CONTRACT_ID,
    ShaderProgramSpecV1,
    build_program_spec,
)
from shaderforge.validation import repair_constant_reversed_smoothsteps

LAYERED_COMPILER_VERSION = "layered_to_program_spec_v1_2"


def _function_name(index: int, layer_id: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9_]", "_", layer_id)
    return f"sg_layer_{index}_{safe_id}"


def _emit_source(layers: tuple[LayerProgram, ...]) -> str:
    declarations = [
        "precision mediump float;",
        "varying vec2 v_uv;",
        "uniform sampler2D u_image;",
        "uniform vec2 u_resolution;",
        "uniform float u_time;",
    ]
    uniforms = sorted(
        (declaration for layer in layers for declaration in layer.uniform_schema),
        key=lambda item: item.name,
    )
    declarations.extend(
        f"uniform {declaration.type} {declaration.name};" for declaration in uniforms
    )
    sections = ["\n".join(declarations)]
    indexed_layers: list[tuple[int, LayerProgram]] = list(enumerate(layers))
    for index, layer in indexed_layers:
        body = "\n".join(f"    {line}" for line in layer.glsl_body.splitlines())
        sections.append(
            f"vec4 {_function_name(index, layer.layer_id)}(vec2 uv) {{\n{body}\n}}"
        )
    main = [
        "void main() {",
        "    vec4 accum = vec4(0.0);",
        "    vec4 layer;",
    ]
    # LayerPlan 的 z_index 决定从后到前；同 z_index 以 canonical tuple 顺序消歧。
    for index, layer in sorted(
        indexed_layers, key=lambda item: (item[1].z_index, item[0])
    ):
        main.append(f"    layer = {_function_name(index, layer.layer_id)}(v_uv);")
        main.append("    accum = layer + accum * (1.0 - layer.a);")
    main.extend(
        [
            "    vec3 opaque_rgb = accum.rgb + vec3(1.0) * (1.0 - accum.a);",
            "    gl_FragColor = vec4(opaque_rgb, 1.0);",
            "}",
        ]
    )
    sections.append("\n".join(main))
    return "\n\n".join(sections) + "\n"


def compile_layered_shader(
    layered_spec: LayeredShaderSpecV1,
) -> ShaderProgramSpecV1:
    """校验 Layered 内容完整性并生成 canonical ShaderProgramSpecV1。."""
    if layered_spec.schema_version != LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION:
        raise LayeredSpecError("invalid_schema_version", "Layered Spec 版本不受支持。")
    for layer in layered_spec.layers:
        if recompute_layer_sha256(layer) != layer.layer_sha256:
            raise LayeredSpecError(
                "layer_hash_mismatch", f"Layer {layer.layer_id} 内容哈希失配。"
            )
    if recompute_layered_spec_sha256(layered_spec) != layered_spec.layered_spec_sha256:
        raise LayeredSpecError(
            "layered_spec_hash_mismatch", "Layered Spec 内容哈希失配。"
        )
    _validate_global_uniforms(layered_spec.layers)
    uniform_schema = sorted(
        (
            declaration
            for layer in layered_spec.layers
            for declaration in layer.uniform_schema
        ),
        key=lambda item: item.name,
    )
    uniform_values = {
        name: value
        for layer in layered_spec.layers
        for name, value in layer.uniform_values.items()
    }
    tunables = sorted(
        (
            tunable
            for layer in layered_spec.layers
            for tunable in layer.tunable_manifest
        ),
        key=lambda item: item.path,
    )
    source = _emit_source(layered_spec.layers)
    repair = repair_constant_reversed_smoothsteps(source)
    if repair is not None:
        source = repair.source
    return build_program_spec(
        {
            "schema_version": "shader_program_spec_v1",
            "fragment_source": source,
            "uniform_schema": {
                item.name: uniform_declaration_to_model_dict(item)
                for item in uniform_schema
            },
            "uniform_values": {
                name: list(value) if isinstance(value, tuple) else value
                for name, value in uniform_values.items()
            },
            "tunable_manifest": [
                tunable_parameter_to_model_dict(item) for item in tunables
            ],
            "canvas": layered_spec.canvas.to_dict(),
            "renderer_contract_id": WEBGL1_RENDERER_CONTRACT_ID,
        },
        author_identity=layered_spec.author_identity,
        derivation_provenance=layered_spec.derivation_provenance,
    )
