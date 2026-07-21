"""V2.2 sealed Effect Node union 与严格 Genome 校验。."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal, TypeAlias

from pydantic import Field, model_validator

from shaderforge.genome.models import (
    EffectEdge,
    EffectGenome,
    EffectNode,
    NodeKind,
    NodePort,
    ParameterBinding,
    ParameterSpec,
    PortType,
    _ports,
)


@dataclass(frozen=True, slots=True)
class ParameterBindingContract:
    """一个 node binding 允许引用的 ParameterSpec 形状。."""

    dtypes: tuple[str, ...]
    units: tuple[str, ...]
    coordinate_spaces: tuple[str | None, ...] = (None,)
    color_spaces: tuple[str | None, ...] = (None,)

    def validate(self, parameter: ParameterSpec, *, binding_name: str) -> None:
        """拒绝不符合 dtype/unit/space 契约的参数。."""
        if parameter.dtype not in self.dtypes:
            raise ValueError(
                f"binding {binding_name} 不允许 dtype={parameter.dtype}。"
            )
        if parameter.unit not in self.units:
            raise ValueError(f"binding {binding_name} 不允许 unit={parameter.unit}。")
        if parameter.coordinate_space not in self.coordinate_spaces:
            raise ValueError(
                f"binding {binding_name} 不允许 "
                f"coordinate_space={parameter.coordinate_space}。"
            )
        if parameter.color_space not in self.color_spaces:
            raise ValueError(
                f"binding {binding_name} 不允许 "
                f"color_space={parameter.color_space}。"
            )


_UV_SCALAR = ParameterBindingContract(
    dtypes=("float",),
    units=("normalized",),
    coordinate_spaces=("shader_uv_bottom_left",),
)
_UV_VEC2 = ParameterBindingContract(
    dtypes=("vec2",),
    units=("normalized",),
    coordinate_spaces=("shader_uv_bottom_left",),
)
_ANGLE = ParameterBindingContract(dtypes=("float",), units=("radians",))
_DIRECTION = ParameterBindingContract(
    dtypes=("vec2",),
    units=("unit_vector",),
    coordinate_spaces=("shader_uv_bottom_left",),
)
_COLOR = ParameterBindingContract(
    dtypes=("vec4",), units=("rgba",), color_spaces=("linear_rgb",)
)
_RATIO = ParameterBindingContract(dtypes=("float",), units=("ratio",))


class _SealedEffectNode(EffectNode):
    """固定 kind/ports/binding payload 的 V2.2 node 基类。."""

    binding_contracts: ClassVar[dict[str, ParameterBindingContract]]

    @model_validator(mode="after")
    def _validate_exact_binding_payload(self) -> _SealedEffectNode:
        expected = set(self.binding_contracts)
        actual = {binding.binding_name for binding in self.parameter_bindings}
        if actual != expected:
            missing = sorted(expected - actual)
            unknown = sorted(actual - expected)
            raise ValueError(
                f"{self.kind} parameter bindings 必须精确匹配 sealed payload；"
                f"missing={missing}, unknown={unknown}。"
            )
        return self


class CircleSDFNode(_SealedEffectNode):
    """圆形 signed-distance node。."""

    kind: Literal["circle_sdf"] = "circle_sdf"
    inputs: tuple[NodePort, ...] = ()
    outputs: tuple[NodePort, ...] = _ports(("sdf", "sdf"))
    distance_semantics: Literal["euclidean_negative_inside_v1"] = (
        "euclidean_negative_inside_v1"
    )
    binding_contracts = {"center": _UV_VEC2, "radius": _UV_SCALAR}


class EllipseSDFNode(_SealedEffectNode):
    """椭圆 signed-distance node。."""

    kind: Literal["ellipse_sdf"] = "ellipse_sdf"
    inputs: tuple[NodePort, ...] = ()
    outputs: tuple[NodePort, ...] = _ports(("sdf", "sdf"))
    distance_semantics: Literal["ellipse_negative_inside_v1"] = (
        "ellipse_negative_inside_v1"
    )
    binding_contracts = {
        "center": _UV_VEC2,
        "radii": _UV_VEC2,
        "rotation": _ANGLE,
    }


class RoundedRectSDFNode(_SealedEffectNode):
    """圆角矩形 signed-distance node。."""

    kind: Literal["rounded_rect_sdf"] = "rounded_rect_sdf"
    inputs: tuple[NodePort, ...] = ()
    outputs: tuple[NodePort, ...] = _ports(("sdf", "sdf"))
    distance_semantics: Literal["rounded_rect_negative_inside_v1"] = (
        "rounded_rect_negative_inside_v1"
    )
    binding_contracts = {
        "center": _UV_VEC2,
        "half_size": _UV_VEC2,
        "corner_radius": _UV_SCALAR,
        "rotation": _ANGLE,
    }


class SolidFillNode(_SealedEffectNode):
    """线性空间纯色填充 node。."""

    kind: Literal["solid_fill"] = "solid_fill"
    inputs: tuple[NodePort, ...] = _ports(("mask", "mask"))
    outputs: tuple[NodePort, ...] = _ports(("color", "color"))
    color_semantics: Literal["linear_premultiplied_rgba_v1"] = (
        "linear_premultiplied_rgba_v1"
    )
    binding_contracts = {"color": _COLOR}


class LinearGradientNode(_SealedEffectNode):
    """线性空间渐变填充 node。."""

    kind: Literal["linear_gradient"] = "linear_gradient"
    inputs: tuple[NodePort, ...] = _ports(("mask", "mask"))
    outputs: tuple[NodePort, ...] = _ports(("color", "color"))
    interpolation_semantics: Literal["linear_rgb_clamped_v1"] = (
        "linear_rgb_clamped_v1"
    )
    binding_contracts = {
        "start": _UV_VEC2,
        "end": _UV_VEC2,
        "start_color": _COLOR,
        "end_color": _COLOR,
    }


class GaussianColorLobeNode(_SealedEffectNode):
    """各向异性 Gaussian 色团 node。."""

    kind: Literal["gaussian_color_lobe"] = "gaussian_color_lobe"
    inputs: tuple[NodePort, ...] = _ports(("mask", "mask"))
    outputs: tuple[NodePort, ...] = _ports(("color", "color"))
    lobe_semantics: Literal["anisotropic_gaussian_linear_rgb_v1"] = (
        "anisotropic_gaussian_linear_rgb_v1"
    )
    binding_contracts = {
        "center": _UV_VEC2,
        "sigma": _UV_VEC2,
        "color": _COLOR,
        "intensity": _RATIO,
    }


class ShadowNode(_SealedEffectNode):
    """由 SDF 导出的外阴影 node。."""

    kind: Literal["shadow"] = "shadow"
    inputs: tuple[NodePort, ...] = _ports(("sdf", "sdf"))
    outputs: tuple[NodePort, ...] = _ports(("color", "color"))
    shadow_semantics: Literal["outer_gaussian_from_sdf_v1"] = (
        "outer_gaussian_from_sdf_v1"
    )
    binding_contracts = {
        "offset": _UV_VEC2,
        "blur": _UV_SCALAR,
        "spread": _UV_SCALAR,
        "color": _COLOR,
    }


class GlowNode(_SealedEffectNode):
    """由 SDF 导出的对称辉光 node。."""

    kind: Literal["glow"] = "glow"
    inputs: tuple[NodePort, ...] = _ports(("sdf", "sdf"))
    outputs: tuple[NodePort, ...] = _ports(("color", "color"))
    glow_semantics: Literal["symmetric_gaussian_from_sdf_v1"] = (
        "symmetric_gaussian_from_sdf_v1"
    )
    binding_contracts = {
        "radius": _UV_SCALAR,
        "intensity": _RATIO,
        "color": _COLOR,
    }


class RimBandNode(_SealedEffectNode):
    """SDF 内沿 rim band node。."""

    kind: Literal["rim_band"] = "rim_band"
    inputs: tuple[NodePort, ...] = _ports(("sdf", "sdf"))
    outputs: tuple[NodePort, ...] = _ports(("color", "color"))
    band_semantics: Literal["inner_rim_band_v1"] = "inner_rim_band_v1"
    binding_contracts = {
        "width": _UV_SCALAR,
        "softness": _UV_SCALAR,
        "intensity": _RATIO,
        "color": _COLOR,
    }


class OutlineBandNode(_SealedEffectNode):
    """SDF 居中轮廓 band node。."""

    kind: Literal["outline_band"] = "outline_band"
    inputs: tuple[NodePort, ...] = _ports(("sdf", "sdf"))
    outputs: tuple[NodePort, ...] = _ports(("color", "color"))
    band_semantics: Literal["centered_outline_band_v1"] = (
        "centered_outline_band_v1"
    )
    binding_contracts = {
        "width": _UV_SCALAR,
        "softness": _UV_SCALAR,
        "color": _COLOR,
    }


class ArcHighlightNode(_SealedEffectNode):
    """受方向约束的 SDF 弧形高光 node。."""

    kind: Literal["arc_highlight"] = "arc_highlight"
    inputs: tuple[NodePort, ...] = _ports(("sdf", "sdf"))
    outputs: tuple[NodePort, ...] = _ports(("color", "color"))
    arc_semantics: Literal["oriented_sdf_arc_v1"] = "oriented_sdf_arc_v1"
    binding_contracts = {
        "direction": _DIRECTION,
        "angular_width": _ANGLE,
        "thickness": _UV_SCALAR,
        "softness": _UV_SCALAR,
        "intensity": _RATIO,
        "color": _COLOR,
    }


class UnionMaskNode(_SealedEffectNode):
    """coverage max 并集 mask node。."""

    kind: Literal["union_mask"] = "union_mask"
    inputs: tuple[NodePort, ...] = _ports(("left", "mask"), ("right", "mask"))
    outputs: tuple[NodePort, ...] = _ports(("mask", "mask"))
    operation: Literal["coverage_max_v1"] = "coverage_max_v1"
    binding_contracts = {}


class IntersectionMaskNode(_SealedEffectNode):
    """coverage min 交集 mask node。."""

    kind: Literal["intersection_mask"] = "intersection_mask"
    inputs: tuple[NodePort, ...] = _ports(("left", "mask"), ("right", "mask"))
    outputs: tuple[NodePort, ...] = _ports(("mask", "mask"))
    operation: Literal["coverage_min_v1"] = "coverage_min_v1"
    binding_contracts = {}


class DifferenceMaskNode(_SealedEffectNode):
    """coverage 乘补集的差集 mask node。."""

    kind: Literal["difference_mask"] = "difference_mask"
    inputs: tuple[NodePort, ...] = _ports(("left", "mask"), ("right", "mask"))
    outputs: tuple[NodePort, ...] = _ports(("mask", "mask"))
    operation: Literal["coverage_left_times_one_minus_right_v1"] = (
        "coverage_left_times_one_minus_right_v1"
    )
    binding_contracts = {}


class OverBlendNode(_SealedEffectNode):
    """预乘 alpha source-over 合成 node。."""

    kind: Literal["over_blend"] = "over_blend"
    inputs: tuple[NodePort, ...] = _ports(
        ("background", "color"), ("foreground", "color")
    )
    outputs: tuple[NodePort, ...] = _ports(("color", "color"))
    composition: Literal["premultiplied_source_over_v1"] = (
        "premultiplied_source_over_v1"
    )
    binding_contracts = {"opacity": _RATIO}


class ColorOutputNode(_SealedEffectNode):
    """把线性颜色交给运行时 contract 的输出 node。."""

    kind: Literal["color_output"] = "color_output"
    inputs: tuple[NodePort, ...] = _ports(("color", "color"))
    outputs: tuple[NodePort, ...] = _ports(("color", "color"))
    output_semantics: Literal["linear_to_contract_output_v1"] = (
        "linear_to_contract_output_v1"
    )
    binding_contracts = {}


TypedEffectNode: TypeAlias = Annotated[
    CircleSDFNode
    | EllipseSDFNode
    | RoundedRectSDFNode
    | SolidFillNode
    | LinearGradientNode
    | GaussianColorLobeNode
    | ShadowNode
    | GlowNode
    | RimBandNode
    | OutlineBandNode
    | ArcHighlightNode
    | UnionMaskNode
    | IntersectionMaskNode
    | DifferenceMaskNode
    | OverBlendNode
    | ColorOutputNode,
    Field(discriminator="kind"),
]


class TypedEffectEdge(EffectEdge):
    """V2.2 edge；SDF→mask 必须显式声明唯一冻结的 AA 转换。."""

    sdf_to_mask_conversion: Literal["analytic_fixed_width_v1"] | None = None


class TypedEffectGenome(EffectGenome):
    """只接受 sealed node union、闭合参数和显式 SDF→mask AA 的 Genome。."""

    nodes: tuple[TypedEffectNode, ...]
    edges: tuple[TypedEffectEdge, ...]

    @model_validator(mode="after")
    def _validate_typed_closure(self) -> TypedEffectGenome:
        node_by_id = {node.node_id: node for node in self.nodes}
        parameter_by_path = {parameter.path: parameter for parameter in self.parameters}
        bound_paths: set[str] = set()
        for node in self.nodes:
            for binding in node.parameter_bindings:
                parameter = parameter_by_path[binding.parameter_path]
                node.binding_contracts[binding.binding_name].validate(
                    parameter, binding_name=f"{node.kind}.{binding.binding_name}"
                )
                bound_paths.add(binding.parameter_path)
        unused = sorted(set(parameter_by_path) - bound_paths)
        if unused:
            raise ValueError(f"Typed Genome 不允许未绑定参数：{unused}。")

        for edge in self.edges:
            source = node_by_id[edge.source_node_id]
            target = node_by_id[edge.target_node_id]
            source_type = _port_type(source.outputs, edge.source_port)
            target_type = _port_type(target.inputs, edge.target_port)
            is_sdf_to_mask = source_type == "sdf" and target_type == "mask"
            if is_sdf_to_mask and edge.sdf_to_mask_conversion is None:
                raise ValueError("SDF→mask edge 必须显式声明 analytic AA conversion。")
            if not is_sdf_to_mask and edge.sdf_to_mask_conversion is not None:
                raise ValueError("只有 SDF→mask edge 可以声明 AA conversion。")
        return self


def _port_type(ports: tuple[NodePort, ...], name: str) -> PortType:
    for port in ports:
        if port.name == name:
            return port.port_type
    raise ValueError(f"未知 port：{name}。")  # pragma: no cover - 父 validator 已拒绝


def binding_contracts_for_kind(
    kind: NodeKind,
) -> dict[str, ParameterBindingContract]:
    """返回只读用途的 binding contract 副本。."""
    node_types: dict[NodeKind, type[_SealedEffectNode]] = {
        "circle_sdf": CircleSDFNode,
        "ellipse_sdf": EllipseSDFNode,
        "rounded_rect_sdf": RoundedRectSDFNode,
        "solid_fill": SolidFillNode,
        "linear_gradient": LinearGradientNode,
        "gaussian_color_lobe": GaussianColorLobeNode,
        "shadow": ShadowNode,
        "glow": GlowNode,
        "rim_band": RimBandNode,
        "outline_band": OutlineBandNode,
        "arc_highlight": ArcHighlightNode,
        "union_mask": UnionMaskNode,
        "intersection_mask": IntersectionMaskNode,
        "difference_mask": DifferenceMaskNode,
        "over_blend": OverBlendNode,
        "color_output": ColorOutputNode,
    }
    return dict(node_types[kind].binding_contracts)


def bindings(**paths: str) -> tuple[ParameterBinding, ...]:
    """按 binding name 稳定排序构造绑定，供模板/测试复用。."""
    return tuple(
        ParameterBinding(binding_name=name, parameter_path=path)
        for name, path in sorted(paths.items())
    )
