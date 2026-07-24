"""最小 Shader DSL V1 的确定性 specialized WebGL1 Compiler.

编译链固定为：严格解析（ShaderDocument）→ canonical document → typed IR
（按层静态展开）→ resource plan → specialized WebGL1 GLSL → 静态验证。
Compiler 不实现任意节点解释器；GLSL 完全按实际 Layer/CSG 结构静态展开，
非 active block 的参数一律烘焙为源码常量，active block 的连续参数打包为
packed vec4 uniform，且自定义 fragment uniform vector 不超过 14。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Literal

from shaderforge.contracts.webgl1 import WEBGL1_STATIC_NO_TEXTURE_V1
from shaderforge.dsl.canonical import (
    ManifestEntry,
    document_sha256,
    layer_param_path,
    node_param_path,
    parameter_manifest,
    parameter_manifest_sha256,
    topology_sha256,
)
from shaderforge.dsl.document import (
    CircleShape,
    EllipseShape,
    Layer,
    LinearFill,
    RadialFill,
    RoundedBoxShape,
    SegmentShape,
    ShaderDocument,
    ShapeExpr,
    SubtractShape,
    Transform,
    shape_csg_depth,
    shape_primitive_count,
)
from shaderforge.validation import validate_shader

DSL_COMPILER_VERSION = "shader_dsl_compiler_v1"
MAX_CUSTOM_FRAGMENT_UNIFORM_VEC4 = 14
RENDERER_RESERVED_UNIFORM_VECTORS = 1  # u_resolution
_AA_PIXEL_WIDTH = 2.0
_SWIZZLE = "xyzw"

_PRIMITIVE_TYPES = (CircleShape, EllipseShape, RoundedBoxShape, SegmentShape)


@dataclass(frozen=True)
class DslUniformSpec:
    """Compiler 输出的 typed uniform 声明（V1 只打包 vec4）."""

    type: Literal["vec4"] = "vec4"


@dataclass(frozen=True)
class DslResourceSummary:
    """编译产物的资源摘要，供 run manifest 与 UI 只读展示."""

    layer_count: int
    visible_layer_count: int
    primitive_total: int
    max_primitives_per_layer: int
    max_csg_depth: int
    baked_parameter_count: int
    active_parameter_count: int
    custom_fragment_uniform_vec4: int
    fragment_uniform_vectors_total: int
    fragment_source_chars: int
    fragment_source_lines: int

    def to_dict(self) -> dict[str, int]:
        """返回适合日志与 manifest 的普通字典."""
        return asdict(self)


@dataclass(frozen=True)
class CompiledDslShader:
    """DSL 文档确定性编译出的 WebGL1 fragment 产物与哈希."""

    dsl_schema_version: str
    compiler_version: str
    render_contract_id: str
    document_sha256: str
    topology_sha256: str
    parameter_manifest_sha256: str
    glsl_sha256: str
    fragment_source: str
    uniform_schema: dict[str, DslUniformSpec]
    uniform_values: dict[str, tuple[float, float, float, float]]
    parameter_manifest: tuple[ManifestEntry, ...]
    resource_summary: DslResourceSummary


def pack_active_uniforms(
    entries: tuple[ManifestEntry, ...],
) -> tuple[dict[str, tuple[float, float, float, float]], dict[str, str]]:
    """把 active block 连续参数按路径序打包为 vec4 uniform.

    返回 (uniform 值表, 参数路径 → GLSL 引用) 二元组；超过 14 个 vec4
    的自定义 fragment uniform 资源计划时直接拒绝。
    """
    ordered = sorted(entries, key=lambda entry: entry.path)
    vec4_count = (len(ordered) + 3) // 4
    if vec4_count > MAX_CUSTOM_FRAGMENT_UNIFORM_VEC4:
        raise ValueError(
            "active block 需要 "
            f"{vec4_count} 个 vec4 uniform，超过 "
            f"{MAX_CUSTOM_FRAGMENT_UNIFORM_VEC4} 的资源计划上限。"
        )
    values: dict[str, tuple[float, float, float, float]] = {}
    refs: dict[str, str] = {}
    for index, entry in enumerate(ordered):
        name = f"u_active_{index // 4}"
        refs[entry.path] = f"{name}.{_SWIZZLE[index % 4]}"
        slot = list(values.get(name, (0.0, 0.0, 0.0, 0.0)))
        slot[index % 4] = entry.value
        values[name] = (slot[0], slot[1], slot[2], slot[3])
    return values, refs


class _ParamResolver:
    """把 manifest 路径解析为烘焙字面量或 active uniform 引用."""

    def __init__(
        self,
        manifest: tuple[ManifestEntry, ...],
        active_refs: dict[str, str],
    ) -> None:
        self._values = {entry.path: entry.value for entry in manifest}
        self._active_refs = active_refs

    def ref(self, path: str) -> str:
        """返回参数在 GLSL 中的确定性引用表达式."""
        active = self._active_refs.get(path)
        if active is not None:
            return active
        value = self._values[path]
        normalized = 0.0 if value == 0.0 else value
        literal = format(normalized, ".9g")
        if "." not in literal and "e" not in literal:
            literal = f"{literal}.0"
        return literal


class _DistanceEmitter:
    """把单个 Layer 的 ShapeExpr 静态展开为 signed distance 表达式."""

    def __init__(self, resolver: _ParamResolver) -> None:
        self._resolver = resolver
        self._temp_index = 0
        self.declarations: list[str] = []

    def emit(self, node: ShapeExpr, point: str) -> str:
        """返回 node 在 point 处的 signed distance GLSL 表达式（内负外正）."""
        correction: str | None = None
        if node.transform is not None:
            point, correction = self._apply_transform(node.id, node.transform, point)
        if isinstance(node, CircleShape):
            radius = self._resolver.ref(node_param_path(node.id, "radius"))
            expr = f"(length({point}) - ({radius}))"
        elif isinstance(node, EllipseShape):
            rx = self._resolver.ref(node_param_path(node.id, "radii.x"))
            ry = self._resolver.ref(node_param_path(node.id, "radii.y"))
            expr = f"_sfEllipse({point}, vec2({rx}, {ry}))"
        elif isinstance(node, RoundedBoxShape):
            hx = self._resolver.ref(node_param_path(node.id, "half_size.x"))
            hy = self._resolver.ref(node_param_path(node.id, "half_size.y"))
            corner = self._resolver.ref(node_param_path(node.id, "corner_radius"))
            expr = f"_sfRoundBox({point}, vec2({hx}, {hy}), {corner})"
        elif isinstance(node, SegmentShape):
            fx = self._resolver.ref(node_param_path(node.id, "from.x"))
            fy = self._resolver.ref(node_param_path(node.id, "from.y"))
            tx = self._resolver.ref(node_param_path(node.id, "to.x"))
            ty = self._resolver.ref(node_param_path(node.id, "to.y"))
            radius = self._resolver.ref(node_param_path(node.id, "radius"))
            expr = f"_sfSegment({point}, vec2({fx}, {fy}), vec2({tx}, {ty}), {radius})"
        elif isinstance(node, SubtractShape):
            base = self.emit(node.base, point)
            cut = self.emit(node.cut, point)
            expr = f"max({base}, -({cut}))"
        else:
            left = self.emit(node.left, point)
            right = self.emit(node.right, point)
            combine = "min" if node.kind == "union" else "max"
            expr = f"{combine}({left}, {right})"
        if correction is not None:
            expr = f"({expr}) * ({correction})"
        return expr

    def _apply_transform(
        self, node_id: str, transform: Transform, point: str
    ) -> tuple[str, str]:
        """对 point 施加逆变换，并返回冻结的非均匀缩放距离校正因子."""
        tx = self._resolver.ref(node_param_path(node_id, "transform.translate.x"))
        ty = self._resolver.ref(node_param_path(node_id, "transform.translate.y"))
        sx = self._resolver.ref(node_param_path(node_id, "transform.scale.x"))
        sy = self._resolver.ref(node_param_path(node_id, "transform.scale.y"))
        cos_v = self._resolver.ref(node_param_path(node_id, "transform.rotation.cos"))
        sin_v = self._resolver.ref(node_param_path(node_id, "transform.rotation.sin"))
        name = f"_t{self._temp_index}"
        self._temp_index += 1
        self.declarations.append(
            f"    vec2 {name} = vec2("
            f"({cos_v}) * ({point}.x - ({tx})) + ({sin_v}) * ({point}.y - ({ty})), "
            f"({cos_v}) * ({point}.y - ({ty})) - ({sin_v}) * ({point}.x - ({tx}))"
            f") / vec2({sx}, {sy});"
        )
        return name, f"min({sx}, {sy})"


def _premultiply(color_var: str, weight: str) -> str:
    """返回 straight RGBA 颜色经权重调制后的 premultiplied 表达式."""
    return f"vec4({color_var}.rgb * ({color_var}.a * ({weight})), {color_var}.a * ({weight}))"


def _emit_color(prefix: str, resolver: _ParamResolver) -> str:
    channels = ", ".join(resolver.ref(f"{prefix}.{channel}") for channel in "rgba")
    return f"vec4({channels})"


def _emit_fill(lines: list[str], layer: Layer, resolver: _ParamResolver) -> None:
    prefix = layer_param_path(layer.id, "fill")
    fill = layer.fill
    if isinstance(fill, LinearFill):
        fx = resolver.ref(f"{prefix}.from.x")
        fy = resolver.ref(f"{prefix}.from.y")
        tx = resolver.ref(f"{prefix}.to.x")
        ty = resolver.ref(f"{prefix}.to.y")
        lines.append(f"    vec2 _fl_d = vec2(({tx}) - ({fx}), ({ty}) - ({fy}));")
        lines.append(
            "    float _fl_t = clamp(dot(p - vec2("
            f"{fx}, {fy}), _fl_d) / dot(_fl_d, _fl_d), 0.0, 1.0);"
        )
        lines.append(
            f"    vec4 _fl_c0 = {_emit_color(f'{prefix}.start_color', resolver)};"
        )
        lines.append(
            f"    vec4 _fl_c1 = {_emit_color(f'{prefix}.end_color', resolver)};"
        )
        lines.append("    vec4 _fl = mix(_fl_c0, _fl_c1, _fl_t);")
    elif isinstance(fill, RadialFill):
        cx = resolver.ref(f"{prefix}.center.x")
        cy = resolver.ref(f"{prefix}.center.y")
        radius = resolver.ref(f"{prefix}.radius")
        lines.append(
            "    float _fl_t = clamp(length(p - vec2("
            f"{cx}, {cy})) / ({radius}), 0.0, 1.0);"
        )
        lines.append(
            f"    vec4 _fl_c0 = {_emit_color(f'{prefix}.inner_color', resolver)};"
        )
        lines.append(
            f"    vec4 _fl_c1 = {_emit_color(f'{prefix}.outer_color', resolver)};"
        )
        lines.append("    vec4 _fl = mix(_fl_c0, _fl_c1, _fl_t);")
    else:
        lines.append(f"    vec4 _fl = {_emit_color(f'{prefix}.color', resolver)};")
    lines.append("    float _fl_a = _fl.a * _cov;")
    lines.append("    vec4 _fp = vec4(_fl.rgb * _fl_a, _fl_a);")
    lines.append("    _acc = _fp + _acc * (1.0 - _fp.a);")


def _emit_layer_function(
    index: int, layer: Layer, resolver: _ParamResolver
) -> list[str]:
    lines = [f"vec4 _sf_layer_{index}(vec2 p, float aa) {{"]
    lines.append(f"    float _d = _sf_dist_{index}(p);")
    lines.append("    float _cov = clamp(0.5 - _d / aa, 0.0, 1.0);")
    lines.append("    vec4 _acc = vec4(0.0);")
    for effect in layer.effects:
        if effect.kind == "rim":
            continue  # rim 固定在 fill 之后合成
        prefix = layer_param_path(layer.id, f"effect.{effect.kind}")
        color = _emit_color(f"{prefix}.color", resolver)
        if effect.kind == "shadow":
            ox = resolver.ref(f"{prefix}.offset.x")
            oy = resolver.ref(f"{prefix}.offset.y")
            blur = resolver.ref(f"{prefix}.blur")
            spread = resolver.ref(f"{prefix}.spread")
            lines.append(f"    vec4 _sh_c = {color};")
            lines.append(f"    float _sh_d = _sf_dist_{index}(p - vec2({ox}, {oy}));")
            lines.append(
                "    float _sh_w = clamp(0.5 - (_sh_d - ("
                f"{spread})) / (2.0 * max({blur}, aa)), 0.0, 1.0);"
            )
            lines.append(f"    vec4 _sh = {_premultiply('_sh_c', '_sh_w')};")
            lines.append("    _acc = _sh + _acc * (1.0 - _sh.a);")
        elif effect.kind == "glow":
            radius = resolver.ref(f"{prefix}.radius")
            softness = resolver.ref(f"{prefix}.softness")
            lines.append(f"    vec4 _gl_c = {color};")
            lines.append(
                "    float _gl_w = clamp(("
                f"{radius} - _d) / max({softness}, aa), 0.0, 1.0) * (1.0 - _cov);"
            )
            lines.append(f"    vec4 _gl = {_premultiply('_gl_c', '_gl_w')};")
            lines.append("    _acc = _gl + _acc * (1.0 - _gl.a);")
    _emit_fill(lines, layer, resolver)
    for effect in layer.effects:
        if effect.kind != "rim":
            continue
        prefix = layer_param_path(layer.id, "effect.rim")
        color = _emit_color(f"{prefix}.color", resolver)
        width = resolver.ref(f"{prefix}.width")
        softness = resolver.ref(f"{prefix}.softness")
        lines.append(f"    vec4 _rm_c = {color};")
        lines.append(
            "    float _rm_w = clamp((_d + ("
            f"{width})) / max({softness}, aa), 0.0, 1.0) * _cov;"
        )
        lines.append(f"    vec4 _rm = {_premultiply('_rm_c', '_rm_w')};")
        lines.append("    _acc = _rm + _acc * (1.0 - _rm.a);")
    opacity = resolver.ref(layer_param_path(layer.id, "opacity"))
    lines.append(f"    return _acc * ({opacity});")
    lines.append("}")
    return lines


_SDF_HELPERS = """float _sfEllipse(vec2 q, vec2 r) {
    float k0 = length(q / r);
    float k1 = length(q / (r * r));
    return k0 * (k0 - 1.0) / max(k1, 0.00000100);
}

float _sfRoundBox(vec2 q, vec2 h, float cr) {
    vec2 d = abs(q) - h + vec2(cr);
    return length(max(d, vec2(0.0))) + min(max(d.x, d.y), 0.0) - cr;
}

float _sfSegment(vec2 q, vec2 a, vec2 b, float r) {
    vec2 pa = q - a;
    vec2 ba = b - a;
    float h = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0);
    return length(pa - ba * h) - r;
}"""


def _emit_fragment_source(
    document: ShaderDocument,
    resolver: _ParamResolver,
    uniform_names: tuple[str, ...],
) -> str:
    sections: list[str] = []
    header = [
        "precision mediump float;",
        "varying vec2 v_uv;",
        "uniform sampler2D u_image;",
        "uniform vec2 u_resolution;",
        "uniform float u_time;",
    ]
    header.extend(f"uniform vec4 {name};" for name in uniform_names)
    sections.append("\n".join(header))
    sections.append(_SDF_HELPERS)

    visible = [
        (index, layer) for index, layer in enumerate(document.layers) if layer.visible
    ]
    for index, layer in visible:
        emitter = _DistanceEmitter(resolver)
        distance_expr = emitter.emit(layer.shape, "p")
        dist_lines = [f"float _sf_dist_{index}(vec2 p) {{"]
        dist_lines.extend(emitter.declarations)
        dist_lines.append(f"    return {distance_expr};")
        dist_lines.append("}")
        sections.append("\n".join(dist_lines))
        sections.append("\n".join(_emit_layer_function(index, layer, resolver)))

    background = ", ".join(
        resolver.ref(f"canvas.background.{channel}") for channel in "rgb"
    )
    main_lines = [
        "void main() {",
        "    float _unit = min(u_resolution.x, u_resolution.y);",
        "    vec2 p = (2.0 * gl_FragCoord.xy - u_resolution) / _unit;",
        f"    float aa = {_AA_PIXEL_WIDTH:.8f} / _unit;",
        f"    vec4 _acc = vec4({background}, 1.0);",
        "    vec4 _lay;",
    ]
    for index, _layer in visible:
        main_lines.append(f"    _lay = _sf_layer_{index}(p, aa);")
        main_lines.append("    _acc = _lay + _acc * (1.0 - _lay.a);")
    main_lines.append("    gl_FragColor = vec4(_acc.rgb, 1.0);")
    main_lines.append("}")
    sections.append("\n".join(main_lines))
    return "\n\n".join(sections) + "\n"


def compile_dsl_shader(
    document: ShaderDocument, *, active_block: str | None = None
) -> CompiledDslShader:
    """把 ShaderDocument 确定性编译为 specialized WebGL1 fragment 产物.

    默认（active_block=None）把全部参数烘焙为源码常量；指定 active block 时，
    仅该 block 的连续参数提升为 packed vec4 uniform，其余保持烘焙。
    """
    manifest = parameter_manifest(document)
    if active_block is None:
        active_entries: tuple[ManifestEntry, ...] = ()
    else:
        active_entries = tuple(
            entry for entry in manifest if entry.block == active_block
        )
        if not active_entries:
            raise ValueError(f"文档中不存在 active block：{active_block}。")
    uniform_values, uniform_refs = pack_active_uniforms(active_entries)
    resolver = _ParamResolver(manifest, uniform_refs)
    uniform_names = tuple(f"u_active_{index}" for index in range(len(uniform_values)))
    source = _emit_fragment_source(document, resolver, uniform_names)

    validation = validate_shader(source)
    if not validation.valid:
        details = "; ".join(violation.code for violation in validation.violations)
        raise RuntimeError(f"DSL Compiler 生成的 GLSL 未通过静态验证：{details}。")

    primitive_counts = [shape_primitive_count(layer.shape) for layer in document.layers]
    summary = DslResourceSummary(
        layer_count=len(document.layers),
        visible_layer_count=sum(1 for layer in document.layers if layer.visible),
        primitive_total=sum(primitive_counts),
        max_primitives_per_layer=max(primitive_counts),
        max_csg_depth=max(shape_csg_depth(layer.shape) for layer in document.layers),
        baked_parameter_count=len(manifest) - len(active_entries),
        active_parameter_count=len(active_entries),
        custom_fragment_uniform_vec4=len(uniform_values),
        fragment_uniform_vectors_total=(
            len(uniform_values) + RENDERER_RESERVED_UNIFORM_VECTORS
        ),
        fragment_source_chars=len(source),
        fragment_source_lines=source.count("\n"),
    )
    return CompiledDslShader(
        dsl_schema_version=document.schema_version,
        compiler_version=DSL_COMPILER_VERSION,
        render_contract_id=WEBGL1_STATIC_NO_TEXTURE_V1.contract_id,
        document_sha256=document_sha256(document),
        topology_sha256=topology_sha256(document),
        parameter_manifest_sha256=parameter_manifest_sha256(document),
        glsl_sha256=sha256(source.encode("utf-8")).hexdigest(),
        fragment_source=source,
        uniform_schema={name: DslUniformSpec() for name in uniform_names},
        uniform_values=uniform_values,
        parameter_manifest=manifest,
        resource_summary=summary,
    )


__all__ = [
    "DSL_COMPILER_VERSION",
    "MAX_CUSTOM_FRAGMENT_UNIFORM_VEC4",
    "RENDERER_RESERVED_UNIFORM_VECTORS",
    "CompiledDslShader",
    "DslResourceSummary",
    "DslUniformSpec",
    "compile_dsl_shader",
    "pack_active_uniforms",
]
