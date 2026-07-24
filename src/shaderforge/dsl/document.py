"""最小 Shader DSL V1 的领域文档契约与严格预算校验."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DSL_SCHEMA_VERSION: Literal["shader_graph_v1"] = "shader_graph_v1"

MAX_LAYERS = 8
MAX_PRIMITIVES_PER_LAYER = 4
MAX_CSG_DEPTH = 2
MAX_TOTAL_PRIMITIVES = 32
MAX_EFFECTS_PER_LAYER = 3
ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
ROTATION_UNIT_TOLERANCE = 1.0e-3
MIN_POSITIVE_VALUE = 1.0e-2
MIN_SEGMENT_LENGTH = 1.0e-2
MIN_LINEAR_SPAN = 1.0e-2

_Finite = Annotated[float, Field(allow_inf_nan=False)]
_Unit = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
_Positive = Annotated[float, Field(ge=MIN_POSITIVE_VALUE, allow_inf_nan=False)]
_NonNegative = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]

Vec2 = tuple[_Finite, _Finite]
PositiveVec2 = tuple[_Positive, _Positive]
ColorRGBA = tuple[_Unit, _Unit, _Unit, _Unit]
NodeId = Annotated[str, Field(pattern=ID_PATTERN)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Transform(_StrictModel):
    """可选平移、正缩放与 cos/sin 旋转（逆时针为正）."""

    translate: Vec2 = (0.0, 0.0)
    scale: PositiveVec2 = (1.0, 1.0)
    rotation: Vec2 = (1.0, 0.0)

    @model_validator(mode="after")
    def validate_rotation_unit(self) -> Transform:
        """拒绝明显不满足单位长度的 rotation，避免角度周期折返."""
        cos_value, sin_value = self.rotation
        if abs(math.hypot(cos_value, sin_value) - 1.0) > ROTATION_UNIT_TOLERANCE:
            raise ValueError(
                f"rotation 必须接近单位向量，容差 {ROTATION_UNIT_TOLERANCE}。"
            )
        return self


class CircleShape(_StrictModel):
    """圆 primitive，signed distance 内部为负."""

    id: NodeId
    kind: Literal["circle"]
    transform: Transform | None = None
    radius: _Positive


class EllipseShape(_StrictModel):
    """椭圆 primitive，两个轴半径均为正."""

    id: NodeId
    kind: Literal["ellipse"]
    transform: Transform | None = None
    radii: PositiveVec2


class RoundedBoxShape(_StrictModel):
    """圆角矩形 primitive，corner_radius 不得超出 half_size."""

    id: NodeId
    kind: Literal["rounded_box"]
    transform: Transform | None = None
    half_size: PositiveVec2
    corner_radius: _NonNegative = 0.0

    @model_validator(mode="after")
    def validate_corner_radius(self) -> RoundedBoxShape:
        """拒绝会让圆角反向的自交 corner_radius."""
        if self.corner_radius > min(self.half_size):
            raise ValueError("rounded_box corner_radius 不得超过 min(half_size)。")
        return self


class SegmentShape(_StrictModel):
    """圆头 capsule 线段 primitive，from/to 不得重合."""

    id: NodeId
    kind: Literal["segment"]
    transform: Transform | None = None
    from_: Vec2 = Field(alias="from")
    to: Vec2
    radius: _Positive

    @model_validator(mode="after")
    def validate_span(self) -> SegmentShape:
        """拒绝无法定义 capsule 主轴的零长度 segment."""
        span = math.dist(self.from_, self.to)
        if span < MIN_SEGMENT_LENGTH:
            raise ValueError(f"segment 长度不得小于 {MIN_SEGMENT_LENGTH}。")
        return self


class UnionShape(_StrictModel):
    """二元并集 Boolean 节点."""

    id: NodeId
    kind: Literal["union"]
    transform: Transform | None = None
    left: ShapeExpr
    right: ShapeExpr


class SubtractShape(_StrictModel):
    """二元有序差集 Boolean 节点（base 减去 cut）."""

    id: NodeId
    kind: Literal["subtract"]
    transform: Transform | None = None
    base: ShapeExpr
    cut: ShapeExpr


class IntersectShape(_StrictModel):
    """二元交集 Boolean 节点."""

    id: NodeId
    kind: Literal["intersect"]
    transform: Transform | None = None
    left: ShapeExpr
    right: ShapeExpr


ShapeExpr = Annotated[
    CircleShape
    | EllipseShape
    | RoundedBoxShape
    | SegmentShape
    | UnionShape
    | SubtractShape
    | IntersectShape,
    Field(discriminator="kind"),
]
ShapePrimitive = CircleShape | EllipseShape | RoundedBoxShape | SegmentShape

UnionShape.model_rebuild()
SubtractShape.model_rebuild()
IntersectShape.model_rebuild()

_PRIMITIVE_TYPES = (CircleShape, EllipseShape, RoundedBoxShape, SegmentShape)


def shape_primitive_count(node: ShapeExpr) -> int:
    """统计 ShapeExpr 子树内的 primitive 数量."""
    if isinstance(node, _PRIMITIVE_TYPES):
        return 1
    if isinstance(node, SubtractShape):
        return shape_primitive_count(node.base) + shape_primitive_count(node.cut)
    return shape_primitive_count(node.left) + shape_primitive_count(node.right)


def shape_csg_depth(node: ShapeExpr) -> int:
    """返回 ShapeExpr 子树的 Boolean 嵌套深度，primitive 深度为 0."""
    if isinstance(node, _PRIMITIVE_TYPES):
        return 0
    if isinstance(node, SubtractShape):
        return 1 + max(shape_csg_depth(node.base), shape_csg_depth(node.cut))
    return 1 + max(shape_csg_depth(node.left), shape_csg_depth(node.right))


def shape_node_ids(node: ShapeExpr) -> tuple[str, ...]:
    """按先序收集 ShapeExpr 子树内全部节点 id."""
    if isinstance(node, _PRIMITIVE_TYPES):
        return (node.id,)
    if isinstance(node, SubtractShape):
        return (node.id, *shape_node_ids(node.base), *shape_node_ids(node.cut))
    return (node.id, *shape_node_ids(node.left), *shape_node_ids(node.right))


class SolidFill(_StrictModel):
    """Canvas 坐标下的纯色 Fill."""

    kind: Literal["solid"]
    color: ColorRGBA


class LinearFill(_StrictModel):
    """Canvas 坐标下的两端线性渐变 Fill."""

    kind: Literal["linear"]
    from_: Vec2 = Field(alias="from")
    to: Vec2
    start_color: ColorRGBA
    end_color: ColorRGBA
    spread: Literal["clamp"] = "clamp"

    @model_validator(mode="after")
    def validate_span(self) -> LinearFill:
        """拒绝无法定义投影方向的零跨度 linear."""
        if math.dist(self.from_, self.to) < MIN_LINEAR_SPAN:
            raise ValueError(f"linear from/to 跨度不得小于 {MIN_LINEAR_SPAN}。")
        return self


class RadialFill(_StrictModel):
    """Canvas 坐标下的径向渐变 Fill."""

    kind: Literal["radial"]
    center: Vec2
    radius: _Positive
    inner_color: ColorRGBA
    outer_color: ColorRGBA
    spread: Literal["clamp"] = "clamp"


Fill = Annotated[SolidFill | LinearFill | RadialFill, Field(discriminator="kind")]


class RimEffect(_StrictModel):
    """inner rim Effect，宽度向形状内部衰减."""

    kind: Literal["rim"]
    width: _Positive
    softness: _NonNegative = 0.0
    color: ColorRGBA


class ShadowEffect(_StrictModel):
    """位于 fill 后方的投影 Effect."""

    kind: Literal["shadow"]
    offset: Vec2
    blur: _NonNegative = 0.0
    spread: _NonNegative = 0.0
    color: ColorRGBA


class GlowEffect(_StrictModel):
    """位于 fill 后方的外发光 Effect."""

    kind: Literal["glow"]
    radius: _Positive
    softness: _NonNegative = 0.0
    color: ColorRGBA


Effect = Annotated[RimEffect | ShadowEffect | GlowEffect, Field(discriminator="kind")]

_EFFECT_CANONICAL_ORDER = {"shadow": 0, "glow": 1, "rim": 2}


class Layer(_StrictModel):
    """有序图层：一个 ShapeExpr、一个 Fill、最多三个 Effect."""

    id: NodeId
    visible: bool = True
    opacity: _Unit = 1.0
    shape: ShapeExpr
    fill: Fill
    effects: tuple[Effect, ...] = ()

    @model_validator(mode="after")
    def validate_effects(self) -> Layer:
        """拒绝超量或重复 kind 的 Effect，并按固定层内顺序规范化."""
        if len(self.effects) > MAX_EFFECTS_PER_LAYER:
            raise ValueError(f"每层最多 {MAX_EFFECTS_PER_LAYER} 个 effect。")
        kinds = [effect.kind for effect in self.effects]
        if len(set(kinds)) != len(kinds):
            raise ValueError("rim、shadow、glow 每种 effect 每层最多一个。")
        ordered = tuple(
            sorted(
                self.effects, key=lambda effect: _EFFECT_CANONICAL_ORDER[effect.kind]
            )
        )
        object.__setattr__(self, "effects", ordered)
        return self

    @model_validator(mode="after")
    def validate_shape_budget(self) -> Layer:
        """拒绝超出单层 primitive 与 CSG 深度预算的 ShapeExpr."""
        primitives = shape_primitive_count(self.shape)
        if primitives > MAX_PRIMITIVES_PER_LAYER:
            raise ValueError(
                f"每层最多 {MAX_PRIMITIVES_PER_LAYER} 个 primitive，当前 {primitives}。"
            )
        depth = shape_csg_depth(self.shape)
        if depth > MAX_CSG_DEPTH:
            raise ValueError(f"CSG 深度最多 {MAX_CSG_DEPTH}，当前 {depth}。")
        return self


class DslCanvas(_StrictModel):
    """opaque 背景的目标画布，V1 固定 sRGB 编码域."""

    width: int = Field(ge=16, le=1024)
    height: int = Field(ge=16, le=1024)
    background: ColorRGBA
    color_space: Literal["srgb_encoded_v1"] = "srgb_encoded_v1"
    output_alpha: Literal["opaque"] = "opaque"

    @model_validator(mode="after")
    def validate_opaque_background(self) -> DslCanvas:
        """拒绝非 opaque 背景，最终输出 Alpha 固定为 1."""
        if self.background[3] != 1.0:
            raise ValueError("canvas background alpha 必须为 1.0（opaque）。")
        return self


class ShaderDocument(_StrictModel):
    """最小 Shader DSL V1 的顶层文档，layers 数组按后到前排序."""

    schema_version: Literal["shader_graph_v1"] = DSL_SCHEMA_VERSION
    canvas: DslCanvas
    layers: tuple[Layer, ...] = Field(min_length=1, max_length=MAX_LAYERS)

    @model_validator(mode="after")
    def validate_document_budget(self) -> ShaderDocument:
        """拒绝重复 id 与超出全文 primitive 预算的文档."""
        ids: list[str] = []
        for layer in self.layers:
            ids.append(layer.id)
            ids.extend(shape_node_ids(layer.shape))
        if len(set(ids)) != len(ids):
            raise ValueError("文档内 layer 与 shape 节点 id 必须唯一。")
        total = sum(shape_primitive_count(layer.shape) for layer in self.layers)
        if total > MAX_TOTAL_PRIMITIVES:
            raise ValueError(
                f"全文最多 {MAX_TOTAL_PRIMITIVES} 个 primitive，当前 {total}。"
            )
        return self


def parse_dsl_document(data: Any) -> ShaderDocument:
    """把外部输入严格解析为 ShaderDocument，非法结构直接 fail closed."""
    return ShaderDocument.model_validate(data)


__all__ = [
    "DSL_SCHEMA_VERSION",
    "DslCanvas",
    "Effect",
    "Fill",
    "ID_PATTERN",
    "Layer",
    "LinearFill",
    "MAX_CSG_DEPTH",
    "MAX_EFFECTS_PER_LAYER",
    "MAX_LAYERS",
    "MAX_PRIMITIVES_PER_LAYER",
    "MAX_TOTAL_PRIMITIVES",
    "MIN_LINEAR_SPAN",
    "MIN_POSITIVE_VALUE",
    "MIN_SEGMENT_LENGTH",
    "ROTATION_UNIT_TOLERANCE",
    "CircleShape",
    "ColorRGBA",
    "EllipseShape",
    "GlowEffect",
    "IntersectShape",
    "NodeId",
    "PositiveVec2",
    "RadialFill",
    "RimEffect",
    "RoundedBoxShape",
    "SegmentShape",
    "ShaderDocument",
    "ShadowEffect",
    "ShapeExpr",
    "ShapePrimitive",
    "SolidFill",
    "SubtractShape",
    "Transform",
    "UnionShape",
    "Vec2",
    "parse_dsl_document",
    "shape_csg_depth",
    "shape_node_ids",
    "shape_primitive_count",
]
