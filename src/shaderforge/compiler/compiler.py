"""Effect Genome 到 GLSL ES 1.00 的确定性 Compiler。."""

from __future__ import annotations

import heapq
import json
from collections.abc import Callable
from hashlib import sha256
from typing import Any, cast

from pydantic import BaseModel

from shaderforge.contracts import WEBGL1_STATIC_NO_TEXTURE_V1
from shaderforge.genome import (
    EFFECT_NODE_REGISTRY_V0,
    EffectGenome,
    EffectNode,
    ParameterSpec,
    TypedEffectGenome,
    compute_semantic_genome_hash,
)
from shaderforge.store import ArtifactCatalog
from shaderforge.validation import validate_shader

from .models import (
    DIAGNOSTIC_OWNERSHIP_POLICY_VERSION,
    DIAGNOSTIC_VISIBLE_DELTA_BYTE_THRESHOLD,
    CompilationBundle,
    CompilationProduct,
    CompilerAst,
    CompilerAstBinding,
    CompilerAstInput,
    CompilerAstNode,
    CompilerDefectError,
    CompilerParameterEntry,
    CompilerParameterTable,
    DiagnosticCompilationBundleV3,
    DiagnosticCompilationProductV3,
    DiagnosticPassArtifactV3,
    DiagnosticPassSourceV3,
    NodeLineEntry,
    NodeLineSourceMap,
)

_GLSL_CONTENT_TYPE = "text/x-glsl; charset=utf-8"
_JSON_CONTENT_TYPE = "application/json"
_NODE_OPS = {
    "circle_sdf": 6,
    "ellipse_sdf": 14,
    "rounded_rect_sdf": 20,
    "solid_fill": 8,
    "linear_gradient": 20,
    "gaussian_color_lobe": 24,
    "shadow": 18,
    "glow": 14,
    "rim_band": 14,
    "outline_band": 12,
    "arc_highlight": 28,
    "union_mask": 1,
    "intersection_mask": 1,
    "difference_mask": 3,
    "over_blend": 12,
    "color_output": 12,
}
_EXPECTED_KINDS = frozenset(spec.kind for spec in EFFECT_NODE_REGISTRY_V0)

_HEADER_LINES = (
    "precision mediump float;",
    "varying vec2 v_uv;",
    "uniform sampler2D u_image;",
    "uniform vec2 u_resolution;",
    "uniform float u_time;",
    "",
    "const float SF_EPS = 0.000001;",
    "const float SF_AA = 0.0015;",
    "float sf_mask(float d) { return 1.0 - smoothstep(-SF_AA, SF_AA, d); }",
    "vec2 sf_rotate(vec2 p, float a) {",
    "  float c = cos(a);",
    "  float s = sin(a);",
    "  return mat2(c, -s, s, c) * p;",
    "}",
    "float sf_circle(vec2 uv, vec2 center, float radius) {",
    "  return length(uv - center) - max(abs(radius), SF_EPS);",
    "}",
    "float sf_ellipse(vec2 uv, vec2 center, vec2 radii, float rotation) {",
    "  vec2 r = max(abs(radii), vec2(SF_EPS));",
    "  vec2 q = sf_rotate(uv - center, -rotation);",
    "  return (length(q / r) - 1.0) * min(r.x, r.y);",
    "}",
    "float sf_rounded_rect(vec2 uv, vec2 center, vec2 half_size, float radius, float rotation) {",
    "  vec2 h = max(abs(half_size), vec2(SF_EPS));",
    "  float r = clamp(abs(radius), 0.0, min(h.x, h.y));",
    "  vec2 q = abs(sf_rotate(uv - center, -rotation)) - h + vec2(r);",
    "  return length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0) - r;",
    "}",
    "float sf_safe_band(float distance_value, float center, float half_width, float softness) {",
    "  float safe_softness = max(abs(softness), SF_EPS);",
    "  return clamp(1.0 - (abs(distance_value - center) - abs(half_width)) / safe_softness, 0.0, 1.0);",
    "}",
    "vec4 sf_premultiply(vec4 color_value, float coverage) {",
    "  float alpha_value = clamp(color_value.a * coverage, 0.0, 1.0);",
    "  return vec4(max(color_value.rgb, vec3(0.0)) * alpha_value, alpha_value);",
    "}",
    "vec4 sf_over(vec4 background, vec4 foreground) {",
    "  return foreground + background * (1.0 - foreground.a);",
    "}",
    "vec3 sf_linear_to_srgb(vec3 value) {",
    "  vec3 v = max(value, vec3(0.0));",
    "  vec3 low = 12.92 * v;",
    "  vec3 high = 1.055 * pow(v, vec3(1.0 / 2.4)) - vec3(0.055);",
    "  return mix(low, high, step(vec3(0.0031308), v));",
    "}",
    "",
)


def _stable_model_json(model: BaseModel) -> bytes:
    """输出可按原 Schema 严格恢复的稳定 JSON bytes。."""
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _node_key(node: EffectNode) -> tuple[str, str, int]:
    return (node.semantic_role, node.kind, node.sibling_ordinal)


def _stable_topological_order(genome: TypedEffectGenome) -> tuple[EffectNode, ...]:
    node_by_id = {node.node_id: node for node in genome.nodes}
    indegree = {node_id: 0 for node_id in node_by_id}
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_by_id}
    for edge in genome.edges:
        indegree[edge.target_node_id] += 1
        adjacency[edge.source_node_id].append(edge.target_node_id)
    ready = [
        (_node_key(node), node.node_id)
        for node in genome.nodes
        if indegree[node.node_id] == 0
    ]
    heapq.heapify(ready)
    ordered: list[EffectNode] = []
    while ready:
        _, node_id = heapq.heappop(ready)
        ordered.append(node_by_id[node_id])
        for target_id in sorted(
            adjacency[node_id], key=lambda item: _node_key(node_by_id[item])
        ):
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                heapq.heappush(ready, (_node_key(node_by_id[target_id]), target_id))
    if len(ordered) != len(genome.nodes):
        raise CompilerDefectError("genome_not_dag", ("Genome stable topo 失败。",))
    return tuple(ordered)


def _typed_genome(genome: EffectGenome) -> TypedEffectGenome:
    if genome.contract_id != WEBGL1_STATIC_NO_TEXTURE_V1.contract_id:
        raise CompilerDefectError(
            "unsupported_render_contract",
            (f"不支持 RenderContract：{genome.contract_id}。",),
        )
    try:
        # JSON strict round-trip 同时重跑全部 sealed/closure validators，并允许
        # Pydantic 从 JSON 重建 provenance 中的冻结 ArtifactRefV2 dataclass。
        # 不能直接信任 TypedEffectGenome 实例：model_copy(update=...) 可制造未
        # 重新校验的副本，Compiler 边界必须再次闭合。
        return TypedEffectGenome.model_validate_json(
            genome.model_dump_json(), strict=True
        )
    except ValueError as exc:
        raise CompilerDefectError("invalid_or_unsupported_genome", (str(exc),)) from exc


def _parameter_symbol(index: int) -> str:
    return f"sf_p_{index:04d}"


def _node_symbol(canonical_node_id: str) -> str:
    return f"sf_{canonical_node_id}"


def _glsl_float(value: float) -> str:
    if value == 0.0:
        return "0.0"
    text = format(value, ".17g").lower()
    if "e" not in text and "." not in text:
        text += ".0"
    return text


def _glsl_value(parameter: ParameterSpec) -> str:
    value = parameter.value
    if parameter.dtype == "bool":
        return "true" if value is True else "false"
    if parameter.dtype == "int":
        return str(cast(int, value))
    if parameter.dtype == "float":
        return _glsl_float(cast(float, value))
    vector = cast(tuple[float, ...], value)
    return f"{parameter.dtype}({', '.join(_glsl_float(item) for item in vector)})"


def _build_ast(
    genome: TypedEffectGenome,
) -> tuple[CompilerAst, dict[str, ParameterSpec]]:
    semantic_hash = compute_semantic_genome_hash(genome)
    ordered = _stable_topological_order(genome)
    canonical_ids = {
        node.node_id: f"node_{index:04d}" for index, node in enumerate(ordered)
    }
    parameter_by_path = {parameter.path: parameter for parameter in genome.parameters}
    parameter_symbols = {
        parameter.path: _parameter_symbol(index)
        for index, parameter in enumerate(
            sorted(genome.parameters, key=lambda item: item.path)
        )
    }
    node_by_id = {node.node_id: node for node in genome.nodes}
    incoming = {(edge.target_node_id, edge.target_port): edge for edge in genome.edges}
    ast_nodes: list[CompilerAstNode] = []
    for node in ordered:
        inputs: list[CompilerAstInput] = []
        for port in node.inputs:
            edge = incoming[(node.node_id, port.name)]
            source = node_by_id[edge.source_node_id]
            source_type = next(
                item.port_type
                for item in source.outputs
                if item.name == edge.source_port
            )
            inputs.append(
                CompilerAstInput(
                    input_port=port.name,
                    source_canonical_node_id=canonical_ids[source.node_id],
                    source_port=edge.source_port,
                    value_type=port.port_type,
                    sdf_to_mask_conversion=edge.sdf_to_mask_conversion,
                )
            )
            if (
                source_type == "sdf"
                and port.port_type == "mask"
                and edge.sdf_to_mask_conversion is None
            ):
                raise CompilerDefectError(
                    "implicit_sdf_to_mask",
                    ("SDF→mask 缺少冻结 AA conversion。",),
                )
        bindings = tuple(
            CompilerAstBinding(
                binding_name=binding.binding_name,
                parameter_path=binding.parameter_path,
                parameter_symbol=parameter_symbols[binding.parameter_path],
            )
            for binding in sorted(
                node.parameter_bindings, key=lambda item: item.binding_name
            )
        )
        output = node.outputs[0]
        ast_nodes.append(
            CompilerAstNode(
                canonical_node_id=canonical_ids[node.node_id],
                kind=node.kind,
                semantic_role=node.semantic_role,
                sibling_ordinal=node.sibling_ordinal,
                inputs=tuple(inputs),
                bindings=bindings,
                output_port=output.name,
                output_type=output.port_type,
            )
        )
    return (
        CompilerAst(
            semantic_genome_hash=semantic_hash,
            nodes=tuple(ast_nodes),
            output_canonical_node_id=canonical_ids[genome.output_node_id],
        ),
        parameter_by_path,
    )


def _inputs(node: CompilerAstNode) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in node.inputs:
        expression = _node_symbol(item.source_canonical_node_id)
        if item.sdf_to_mask_conversion is not None:
            expression = f"sf_mask({expression})"
        result[item.input_port] = expression
    return result


def _bindings(node: CompilerAstNode) -> dict[str, str]:
    return {item.binding_name: item.parameter_symbol for item in node.bindings}


def _emit_node(node: CompilerAstNode) -> str:
    out = _node_symbol(node.canonical_node_id)
    inputs = _inputs(node)
    params = _bindings(node)
    declarations: dict[str, Callable[[], str]] = {
        "circle_sdf": lambda: (
            f"float {out} = sf_circle(v_uv, {params['center']}, {params['radius']});"
        ),
        "ellipse_sdf": lambda: (
            f"float {out} = sf_ellipse(v_uv, {params['center']}, {params['radii']}, {params['rotation']});"
        ),
        "rounded_rect_sdf": lambda: (
            f"float {out} = sf_rounded_rect(v_uv, {params['center']}, {params['half_size']}, {params['corner_radius']}, {params['rotation']});"
        ),
        "solid_fill": lambda: (
            f"vec4 {out} = sf_premultiply({params['color']}, {inputs['mask']});"
        ),
        "linear_gradient": lambda: (
            f"vec2 {out}_delta = {params['end']} - {params['start']};\n"
            f"  float {out}_t = clamp(dot(v_uv - {params['start']}, {out}_delta) / max(dot({out}_delta, {out}_delta), SF_EPS), 0.0, 1.0);\n"
            f"  vec4 {out} = sf_premultiply(mix({params['start_color']}, {params['end_color']}, {out}_t), {inputs['mask']});"
        ),
        "gaussian_color_lobe": lambda: (
            f"vec2 {out}_q = (v_uv - {params['center']}) / max(abs({params['sigma']}), vec2(SF_EPS));\n"
            f"  float {out}_coverage = {inputs['mask']} * exp(-0.5 * dot({out}_q, {out}_q)) * max({params['intensity']}, 0.0);\n"
            f"  vec4 {out} = sf_premultiply({params['color']}, {out}_coverage);"
        ),
        "shadow": lambda: (
            f"float {out}_distance = max({inputs['sdf']} - {params['spread']} + length({params['offset']}), 0.0);\n"
            f"  float {out}_coverage = exp(-({out}_distance * {out}_distance) / max({params['blur']} * {params['blur']}, SF_EPS));\n"
            f"  vec4 {out} = sf_premultiply({params['color']}, {out}_coverage);"
        ),
        "glow": lambda: (
            f"float {out}_coverage = exp(-abs({inputs['sdf']}) / max(abs({params['radius']}), SF_EPS)) * max({params['intensity']}, 0.0);\n"
            f"  vec4 {out} = sf_premultiply({params['color']}, {out}_coverage);"
        ),
        "rim_band": lambda: (
            f"float {out}_coverage = sf_safe_band({inputs['sdf']}, -0.5 * abs({params['width']}), 0.5 * abs({params['width']}), {params['softness']}) * max({params['intensity']}, 0.0);\n"
            f"  vec4 {out} = sf_premultiply({params['color']}, {out}_coverage);"
        ),
        "outline_band": lambda: (
            f"float {out}_coverage = sf_safe_band({inputs['sdf']}, 0.0, 0.5 * abs({params['width']}), {params['softness']});\n"
            f"  vec4 {out} = sf_premultiply({params['color']}, {out}_coverage);"
        ),
        "arc_highlight": lambda: (
            f"vec2 {out}_direction = {params['direction']} / max(length({params['direction']}), SF_EPS);\n"
            f"  vec2 {out}_radial = (v_uv - vec2(0.5)) / max(length(v_uv - vec2(0.5)), SF_EPS);\n"
            f"  float {out}_angle = acos(clamp(dot({out}_direction, {out}_radial), -1.0, 1.0));\n"
            f"  float {out}_arc = clamp(1.0 - ({out}_angle - 0.5 * abs({params['angular_width']})) / max(abs({params['softness']}), SF_EPS), 0.0, 1.0);\n"
            f"  float {out}_coverage = {out}_arc * sf_safe_band({inputs['sdf']}, 0.0, 0.5 * abs({params['thickness']}), {params['softness']}) * max({params['intensity']}, 0.0);\n"
            f"  vec4 {out} = sf_premultiply({params['color']}, {out}_coverage);"
        ),
        "union_mask": lambda: (
            f"float {out} = max({inputs['left']}, {inputs['right']});"
        ),
        "intersection_mask": lambda: (
            f"float {out} = min({inputs['left']}, {inputs['right']});"
        ),
        "difference_mask": lambda: (
            f"float {out} = {inputs['left']} * (1.0 - {inputs['right']});"
        ),
        "over_blend": lambda: (
            f"vec4 {out} = sf_over({inputs['background']}, {inputs['foreground']} * clamp({params['opacity']}, 0.0, 1.0));"
        ),
        "color_output": lambda: f"vec4 {out} = {inputs['color']};",
    }
    try:
        return declarations[node.kind]()
    except (
        KeyError
    ) as exc:  # pragma: no cover - registry/exhaustiveness gate covers this
        raise CompilerDefectError(
            "unsupported_node_kind", (f"未支持 NodeKind：{node.kind}。",)
        ) from exc


def _emit_glsl(
    ast: CompilerAst,
    parameter_by_path: dict[str, ParameterSpec],
) -> tuple[str, NodeLineSourceMap, CompilerParameterTable]:
    lines = list(_HEADER_LINES)
    parameter_entries: list[CompilerParameterEntry] = []
    for index, parameter in enumerate(
        sorted(parameter_by_path.values(), key=lambda item: item.path)
    ):
        symbol = _parameter_symbol(index)
        line_number = len(lines) + 1
        lines.append(f"const {parameter.dtype} {symbol} = {_glsl_value(parameter)};")
        parameter_entries.append(
            CompilerParameterEntry(
                parameter_path=parameter.path,
                parameter_symbol=symbol,
                dtype=parameter.dtype,
                declaration_line=line_number,
                optimizable=parameter.optimizable,
                quantization=parameter.quantization,
            )
        )
    lines.extend(("", "void main() {"))
    line_entries: list[NodeLineEntry] = []
    for node in ast.nodes:
        start_line = len(lines) + 1
        emitted = _emit_node(node).splitlines()
        lines.extend(
            f"  {line}" if index == 0 else line for index, line in enumerate(emitted)
        )
        line_entries.append(
            NodeLineEntry(
                canonical_node_id=node.canonical_node_id,
                kind=node.kind,
                semantic_role=node.semantic_role,
                start_line=start_line,
                end_line=len(lines),
            )
        )
    output = _node_symbol(ast.output_canonical_node_id)
    lines.extend(
        (
            f"  float sf_output_alpha = clamp({output}.a, 0.0, 1.0);",
            f"  vec3 sf_output_linear = {output}.rgb / max(sf_output_alpha, SF_EPS);",
            "  gl_FragColor = vec4(sf_linear_to_srgb(sf_output_linear), sf_output_alpha);",
            "}",
        )
    )
    source = "\n".join(lines) + "\n"
    return (
        source,
        NodeLineSourceMap(
            semantic_genome_hash=ast.semantic_genome_hash,
            entries=tuple(line_entries),
        ),
        CompilerParameterTable(
            semantic_genome_hash=ast.semantic_genome_hash,
            entries=tuple(parameter_entries),
        ),
    )


def compile_effect_genome(genome: EffectGenome) -> CompilationProduct:
    """确定性编译 Genome；任何非法输入或非法输出都在渲染前拒绝。."""
    if frozenset(_NODE_OPS) != _EXPECTED_KINDS:
        raise CompilerDefectError(
            "compiler_registry_not_exhaustive",
            ("Compiler NodeKind 表与 effect_node_registry_v0 不一致。",),
        )
    typed = _typed_genome(genome)
    ast, parameters = _build_ast(typed)
    source, line_map, parameter_table = _emit_glsl(ast, parameters)
    validation = validate_shader(source)
    if not validation.valid:
        diagnostics = tuple(
            f"{item.code}:{item.line or 0}" for item in validation.violations
        )
        raise CompilerDefectError("emitted_glsl_invalid", diagnostics)
    warnings = tuple(
        sorted(
            f"{item.code}:{item.line or 0}"
            for item in validation.violations
            if item.severity == "warning"
        )
    )
    return CompilationProduct(
        semantic_genome_hash=ast.semantic_genome_hash,
        glsl_source=source,
        glsl_sha256=sha256(source.encode("utf-8")).hexdigest(),
        ast=ast,
        node_line_map=line_map,
        compiler_parameter_table=parameter_table,
        estimated_ops=sum(_NODE_OPS[node.kind] for node in ast.nodes),
        numerical_risks=warnings,
        diagnostics=("deterministic_compile_succeeded",),
    )


def materialize_compilation(
    product: CompilationProduct,
    *,
    catalog: ArtifactCatalog,
    run_id: str,
) -> CompilationBundle:
    """把纯内存 Product 物化为内容寻址 CompilationBundle。."""
    glsl_ref = catalog.put(
        run_id=run_id,
        kind="compiled_glsl",
        schema_version="compiled_glsl_es_100_v1",
        content_type=_GLSL_CONTENT_TYPE,
        data=product.glsl_source.encode("utf-8"),
    )
    line_map_ref = catalog.put(
        run_id=run_id,
        kind="compiler_node_line_map",
        schema_version="compiler_node_line_map_v1",
        content_type=_JSON_CONTENT_TYPE,
        data=_stable_model_json(product.node_line_map),
    )
    parameter_table_ref = catalog.put(
        run_id=run_id,
        kind="compiler_parameter_table",
        schema_version="compiler_parameter_table_v1",
        content_type=_JSON_CONTENT_TYPE,
        data=_stable_model_json(product.compiler_parameter_table),
    )
    ast_ref = catalog.put(
        run_id=run_id,
        kind="compiler_ast",
        schema_version="compiler_ast_v1",
        content_type=_JSON_CONTENT_TYPE,
        data=_stable_model_json(product.ast),
    )
    return CompilationBundle(
        semantic_genome_hash=product.semantic_genome_hash,
        glsl_ref=glsl_ref,
        glsl_sha256=product.glsl_sha256,
        node_line_map_ref=line_map_ref,
        compiler_parameter_table_ref=parameter_table_ref,
        ast_ref=ast_ref,
        estimated_ops=product.estimated_ops,
        numerical_risks=product.numerical_risks,
        diagnostics=product.diagnostics,
    )


def _disabled_node_symbol(canonical_node_id: str) -> str:
    """从唯一 normal symbol 规则导出 disabled graph symbol。."""
    return _node_symbol(canonical_node_id).replace(
        "sf_node_", "sf_disabled_node_", 1
    )


def _disabled_graph_lines(
    ast: CompilerAst,
    *,
    targets: tuple[CompilerAstNode, ...],
) -> list[str]:
    """复制 AST 并把目标节点集合替换为对应 PortType 的空贡献。."""
    neutral_by_type = {"sdf": "1.0", "mask": "0.0", "color": "vec4(0.0)"}
    glsl_type_by_type = {"sdf": "float", "mask": "float", "color": "vec4"}
    target_ids = {item.canonical_node_id for item in targets}
    if not target_ids:
        raise CompilerDefectError("diagnostic_targets_missing", ())
    result: list[str] = []
    for node in ast.nodes:
        if node.canonical_node_id in target_ids:
            emitted = (
                f"{glsl_type_by_type[node.output_type]} "
                f"{_disabled_node_symbol(node.canonical_node_id)} = "
                f"{neutral_by_type[node.output_type]};"
            )
        else:
            emitted = _emit_node(node).replace("sf_node_", "sf_disabled_node_")
        result.extend(
            f"  {line}" if index == 0 else line
            for index, line in enumerate(emitted.splitlines())
        )
    return result


def _structure_expression(node: CompilerAstNode) -> str:
    symbol = _node_symbol(node.canonical_node_id)
    if node.output_type == "sdf":
        return f"sf_mask({symbol})"
    if node.output_type == "mask":
        return f"clamp({symbol}, 0.0, 1.0)"
    raise CompilerDefectError(
        "diagnostic_structure_target_not_structure",
        (node.canonical_node_id,),
    )


def _final_output_delta_expression(ast: CompilerAst) -> str:
    normal_output = _node_symbol(ast.output_canonical_node_id)
    disabled_output = _disabled_node_symbol(ast.output_canonical_node_id)
    return f"abs({normal_output} - {disabled_output})"


def _binary_structure_expression(node: CompilerAstNode) -> str:
    """把 raw instance topology 投影为与 PNG decoder 相同阈值的二值成员。."""
    threshold = DIAGNOSTIC_VISIBLE_DELTA_BYTE_THRESHOLD / 255.0
    return f"step({threshold:.17g}, {_structure_expression(node)})"


def _instance_visible_delta_source(
    beauty_source: str,
    *,
    ast: CompilerAst,
    target: CompilerAstNode,
    earlier_targets: tuple[CompilerAstNode, ...],
    all_targets: tuple[CompilerAstNode, ...],
) -> str:
    """按稳定 instance ordinal 输出唯一 ownership × subject visible delta。."""
    marker = "  float sf_output_alpha ="
    prefix, separator, _ = beauty_source.partition(marker)
    if not separator:
        raise CompilerDefectError(
            "diagnostic_output_marker_missing",
            ("Beauty GLSL 缺少冻结输出 marker。",),
        )
    topology_expression = _binary_structure_expression(target)
    earlier_union = "0.0"
    for earlier in earlier_targets:
        earlier_union = f"max({earlier_union}, {_binary_structure_expression(earlier)})"
    disabled_lines = _disabled_graph_lines(ast, targets=all_targets)
    threshold = DIAGNOSTIC_VISIBLE_DELTA_BYTE_THRESHOLD / 255.0
    return (
        prefix
        + "\n".join(disabled_lines)
        + "\n"
        + f"  vec4 sf_subject_delta = {_final_output_delta_expression(ast)};\n"
        + "  float sf_subject_delta_max = max(max(sf_subject_delta.r, "
        + "sf_subject_delta.g), max(sf_subject_delta.b, sf_subject_delta.a));\n"
        + f"  float sf_subject_visible = step({threshold:.17g}, "
        + "sf_subject_delta_max);\n"
        + f"  float sf_instance_member = {topology_expression};\n"
        + f"  float sf_earlier_instance_member = {earlier_union};\n"
        + "  float sf_instance_owner = sf_instance_member * "
        + "(1.0 - sf_earlier_instance_member);\n"
        + "  float sf_diagnostic_coverage = sf_instance_owner * "
        + "sf_subject_visible;\n"
        + "  gl_FragColor = vec4(vec3(sf_diagnostic_coverage), "
        + "sf_diagnostic_coverage);\n}\n"
    )


def _subject_visible_delta_source(
    beauty_source: str,
    *,
    ast: CompilerAst,
    instance_targets: tuple[CompilerAstNode, ...],
) -> str:
    """输出全部 instance structure roots 的 raw union × final-output delta。."""
    marker = "  float sf_output_alpha ="
    prefix, separator, _ = beauty_source.partition(marker)
    if not separator:
        raise CompilerDefectError(
            "diagnostic_output_marker_missing",
            ("Beauty GLSL 缺少冻结输出 marker。",),
        )
    expressions = [_binary_structure_expression(item) for item in instance_targets]
    raw_union = expressions[0]
    for expression in expressions[1:]:
        raw_union = f"max({raw_union}, {expression})"
    disabled_lines = _disabled_graph_lines(ast, targets=instance_targets)
    threshold = DIAGNOSTIC_VISIBLE_DELTA_BYTE_THRESHOLD / 255.0
    return (
        prefix
        + "\n".join(disabled_lines)
        + "\n"
        + f"  vec4 sf_subject_delta = {_final_output_delta_expression(ast)};\n"
        + "  float sf_subject_delta_max = max(max(sf_subject_delta.r, "
        + "sf_subject_delta.g), max(sf_subject_delta.b, sf_subject_delta.a));\n"
        + f"  float sf_subject_visible = step({threshold:.17g}, "
        + "sf_subject_delta_max);\n"
        + f"  float sf_diagnostic_coverage = {raw_union} * sf_subject_visible;\n"
        + "  gl_FragColor = vec4(vec3(sf_diagnostic_coverage), "
        + "sf_diagnostic_coverage);\n}\n"
    )


def _visible_layer_contribution_source(
    beauty_source: str,
    *,
    ast: CompilerAst,
    target: CompilerAstNode,
) -> str:
    """输出 layer-disabled 与正常 final output 的可见像素 delta。."""
    marker = "  float sf_output_alpha ="
    prefix, separator, _ = beauty_source.partition(marker)
    if not separator:
        raise CompilerDefectError(
            "diagnostic_output_marker_missing",
            ("Beauty GLSL 缺少冻结输出 marker。",),
        )
    if target.output_type != "color":
        raise CompilerDefectError(
            "layer_contribution_target_not_color",
            (target.canonical_node_id,),
        )
    disabled_lines = _disabled_graph_lines(ast, targets=(target,))
    return (
        prefix
        + "\n".join(disabled_lines)
        + "\n"
        + f"  vec4 sf_layer_delta = {_final_output_delta_expression(ast)};\n"
        + "  float sf_diagnostic_coverage = clamp(max(max(sf_layer_delta.r, "
        + "sf_layer_delta.g), max(sf_layer_delta.b, sf_layer_delta.a)), 0.0, 1.0);\n"
        + "  gl_FragColor = vec4(vec3(sf_diagnostic_coverage), "
        + "sf_diagnostic_coverage);\n}\n"
    )


def compile_diagnostic_passes(
    genome: EffectGenome,
) -> DiagnosticCompilationProductV3:
    """从 Genome 节点而非 Manifest/target mask 生成结构诊断 pass。."""
    typed = _typed_genome(genome)
    ast, parameters = _build_ast(typed)
    beauty_source, _, _ = _emit_glsl(ast, parameters)
    instance_nodes: dict[int, CompilerAstNode] = {}
    layer_nodes: dict[str, CompilerAstNode] = {}
    for node in ast.nodes:
        role = str(node.semantic_role)
        if role.startswith("instance_"):
            parts = role.split("_", 2)
            if (
                len(parts) == 3
                and parts[1].isdigit()
                and parts[2]
                in {
                    "mask",
                    "geometry",
                }
            ):
                index = int(parts[1])
                # Topology mask 覆盖原始 outer geometry；solid 则只存在 geometry。
                if parts[2] == "mask" or index not in instance_nodes:
                    instance_nodes[index] = node
        elif role.startswith("layer_"):
            layer = role.removeprefix("layer_")
            if layer in {
                "background",
                "shadow",
                "base_fill",
                "color_lobe",
                "haze",
                "rim",
                "outline",
                "highlight",
                "detail",
                "glow",
            }:
                layer_nodes[layer] = node
    if not instance_nodes:
        # 兼容 V2.2 旧 solid Genome；诊断仍从唯一 geometry node 导出。
        geometries = [
            node
            for node in ast.nodes
            if node.kind in {"circle_sdf", "ellipse_sdf", "rounded_rect_sdf"}
        ]
        if len(geometries) == 1:
            instance_nodes[0] = geometries[0]
    passes: list[DiagnosticPassSourceV3] = []
    ordered_instance_nodes = tuple(
        node for _, node in sorted(instance_nodes.items())
    )
    if ordered_instance_nodes:
        subject_source = _subject_visible_delta_source(
            beauty_source,
            ast=ast,
            instance_targets=ordered_instance_nodes,
        )
        subject_identity = ordered_instance_nodes[0]
        passes.append(
            DiagnosticPassSourceV3(
                semantic_genome_hash=ast.semantic_genome_hash,
                ownership_policy_version=DIAGNOSTIC_OWNERSHIP_POLICY_VERSION,
                pass_id="subject_visible_delta",
                pass_kind="subject_visible_delta",
                canonical_node_id=subject_identity.canonical_node_id,
                node_kind=subject_identity.kind,
                node_output_type=subject_identity.output_type,
                glsl_source=subject_source,
                glsl_sha256=sha256(subject_source.encode("utf-8")).hexdigest(),
            )
        )
    for index, node in sorted(instance_nodes.items()):
        earlier_nodes = tuple(
            previous_node
            for previous_index, previous_node in sorted(instance_nodes.items())
            if previous_index < index
        )
        source = _instance_visible_delta_source(
            beauty_source,
            ast=ast,
            target=node,
            earlier_targets=earlier_nodes,
            all_targets=ordered_instance_nodes,
        )
        passes.append(
            DiagnosticPassSourceV3(
                semantic_genome_hash=ast.semantic_genome_hash,
                ownership_policy_version=DIAGNOSTIC_OWNERSHIP_POLICY_VERSION,
                pass_id=f"instance_{index:04d}_visible_delta",
                pass_kind="instance_visible_delta",
                canonical_node_id=node.canonical_node_id,
                node_kind=node.kind,
                node_output_type=node.output_type,
                instance_index=index,
                glsl_source=source,
                glsl_sha256=sha256(source.encode("utf-8")).hexdigest(),
            )
        )
    for layer, node in sorted(layer_nodes.items()):
        source = _visible_layer_contribution_source(
            beauty_source,
            ast=ast,
            target=node,
        )
        passes.append(
            DiagnosticPassSourceV3(
                semantic_genome_hash=ast.semantic_genome_hash,
                ownership_policy_version=DIAGNOSTIC_OWNERSHIP_POLICY_VERSION,
                pass_id=f"layer_{layer}_visible_delta",
                pass_kind="layer_visible_delta",
                canonical_node_id=node.canonical_node_id,
                node_kind=node.kind,
                node_output_type=node.output_type,
                layer=cast(Any, layer),
                glsl_source=source,
                glsl_sha256=sha256(source.encode("utf-8")).hexdigest(),
            )
        )
    if not passes:
        raise CompilerDefectError(
            "diagnostic_passes_missing",
            ("Genome 未生成 instance 或 required-layer diagnostic pass。",),
        )
    passes.sort(key=lambda item: item.pass_id)
    for item in passes:
        validation = validate_shader(item.glsl_source)
        if not validation.valid:
            raise CompilerDefectError(
                "emitted_diagnostic_glsl_invalid",
                tuple(
                    f"{item.pass_id}:{violation.code}:{violation.line or 0}"
                    for violation in validation.violations
                ),
            )
    return DiagnosticCompilationProductV3(
        semantic_genome_hash=ast.semantic_genome_hash,
        ownership_policy_version=DIAGNOSTIC_OWNERSHIP_POLICY_VERSION,
        passes=tuple(passes),
    )


def materialize_diagnostic_compilation(
    product: DiagnosticCompilationProductV3,
    *,
    catalog: ArtifactCatalog,
    run_id: str,
) -> DiagnosticCompilationBundleV3:
    """逐 pass 内容寻址物化，保留 node/source/hash identity。."""
    artifacts: list[DiagnosticPassArtifactV3] = []
    for item in product.passes:
        ref = catalog.put(
            run_id=run_id,
            kind="diagnostic_glsl",
            schema_version="diagnostic_glsl_es_100_v3",
            content_type=_GLSL_CONTENT_TYPE,
            data=item.glsl_source.encode("utf-8"),
        )
        artifacts.append(
            DiagnosticPassArtifactV3(
                pass_id=item.pass_id,
                pass_kind=item.pass_kind,
                canonical_node_id=item.canonical_node_id,
                node_kind=item.node_kind,
                node_output_type=item.node_output_type,
                ownership_policy_version=item.ownership_policy_version,
                instance_index=item.instance_index,
                layer=item.layer,
                source_ref=ref,
                source_sha256=item.glsl_sha256,
            )
        )
    return DiagnosticCompilationBundleV3(
        semantic_genome_hash=product.semantic_genome_hash,
        ownership_policy_version=product.ownership_policy_version,
        passes=tuple(artifacts),
    )


__all__ = [
    "compile_diagnostic_passes",
    "compile_effect_genome",
    "materialize_compilation",
    "materialize_diagnostic_compilation",
]
