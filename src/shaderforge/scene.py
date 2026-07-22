"""scene_mvp 的版本化单主体场景与 typed patch 契约。."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Color = tuple[float, float, float]
Point = tuple[float, float]
MAX_MIN_FEATURES = 4
CIRCLE_AXES_TOLERANCE = 0.01
MIN_SCENE_VERSION: Literal["png_to_shader_min_scene_v3"] = "png_to_shader_min_scene_v3"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Canvas(_StrictModel):
    """目标画布及背景色。."""

    width: int = Field(ge=16, le=2048)
    height: int = Field(ge=16, le=2048)
    background: Color


class Primitive(_StrictModel):
    """单主体圆或椭圆；circle 在契约层必须近似等轴。."""

    type: Literal["circle", "ellipse"]
    center: Point
    axes: Point

    @model_validator(mode="after")
    def validate_axes(self) -> Primitive:
        """拒绝非正 axes 以及明显不等轴的 circle。."""
        if min(self.axes) <= 0.0:
            raise ValueError("primitive axes 必须为正数。")
        if (
            self.type == "circle"
            and abs(self.axes[0] - self.axes[1]) > CIRCLE_AXES_TOLERANCE
        ):
            raise ValueError(f"circle axes 差值不得超过 {CIRCLE_AXES_TOLERANCE}。")
        return self


class SolidColorField(_StrictModel):
    """与位置无关的纯色颜色场。."""

    model: Literal["solid"]
    color: Color


class RadialColorField(_StrictModel):
    """object-local 径向颜色场。."""

    model: Literal["radial"]
    inner: Color
    outer: Color
    origin: Point = (-0.35, 0.55)
    scale: float = Field(default=1.25, gt=0.05, le=4.0)


class LinearColorField(_StrictModel):
    """object-local 线性颜色场。."""

    model: Literal["linear"]
    start: Color
    end: Color
    direction: Point
    offset: float = Field(default=0.5, ge=-2.0, le=3.0)
    scale: float = Field(default=1.0, gt=0.05, le=4.0)

    @model_validator(mode="after")
    def validate_direction(self) -> LinearColorField:
        """拒绝无法定义线性投影方向的零向量。."""
        if self.direction[0] ** 2 + self.direction[1] ** 2 < 1.0e-6:
            raise ValueError("linear direction 不得为零向量。")
        return self


ColorField = Annotated[
    SolidColorField | RadialColorField | LinearColorField,
    Field(discriminator="model"),
]


class Feature(_StrictModel):
    """固定模板可识别的局部视觉特征。."""

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    type: Literal["polar_arc", "shadow", "rim", "edge_line", "gaussian_lobe", "glow"]
    center: Point = (0.0, -0.7)
    axes: Point = (0.55, 0.14)
    color: Color = (0.08, 0.12, 0.12)
    intensity: float = Field(default=0.25, ge=0.0, le=2.0)

    @model_validator(mode="after")
    def validate_axes(self) -> Feature:
        """拒绝无法形成局部 footprint 的非正 axes。."""
        if min(self.axes) <= 0.0:
            raise ValueError("feature axes 必须为正数。")
        return self


class SceneObject(_StrictModel):
    """单主体及其颜色与特征。."""

    primitive: Primitive
    color_field: ColorField
    features: tuple[Feature, ...] = Field(default=(), max_length=MAX_MIN_FEATURES)

    @model_validator(mode="after")
    def ensure_unique_feature_ids(self) -> SceneObject:
        """拒绝会让 typed patch 指向不确定的重复 id。."""
        ids = [feature.id for feature in self.features]
        if len(ids) != len(set(ids)):
            raise ValueError("feature id 必须唯一。")
        return self


class MinScene(_StrictModel):
    """scene_mvp 唯一可编辑 Shader 表示。."""

    schema_version: Literal["png_to_shader_min_scene_v3"] = MIN_SCENE_VERSION
    canvas: Canvas
    object: SceneObject


class AddFeaturePatch(_StrictModel):
    """增加一个完整 feature。."""

    op: Literal["add_feature"]
    feature: Feature


class RemoveFeaturePatch(_StrictModel):
    """按稳定 id 删除 feature。."""

    op: Literal["remove_feature"]
    feature_id: str


class ReplaceFeaturePatch(_StrictModel):
    """按稳定 id 原子替换完整 feature。."""

    op: Literal["replace_feature"]
    feature_id: str
    feature: Feature

    @model_validator(mode="after")
    def preserve_stable_id(self) -> ReplaceFeaturePatch:
        """替换前后必须保留相同稳定 id。."""
        if self.feature.id != self.feature_id:
            raise ValueError("replace_feature 必须保持稳定 feature id。")
        return self


class ReplaceColorFieldPatch(_StrictModel):
    """原子替换完整 typed ColorField。."""

    op: Literal["replace_color_field"]
    color_field: ColorField


MinScenePatch = Annotated[
    AddFeaturePatch | RemoveFeaturePatch | ReplaceFeaturePatch | ReplaceColorFieldPatch,
    Field(discriminator="op"),
]


def apply_scene_patch(scene: MinScene, patch: MinScenePatch) -> MinScene:
    """原子应用白名单 patch，并重新通过完整 scene 契约。."""
    data = scene.model_dump(mode="python")
    obj = data["object"]
    features = list(obj["features"])
    ids = [item["id"] for item in features]
    if patch.op == "add_feature":
        if patch.feature.id in ids:
            raise ValueError("add_feature 的 feature id 已存在。")
        features.append(patch.feature.model_dump(mode="python"))
        obj["features"] = features
    elif patch.op == "remove_feature":
        if patch.feature_id not in ids:
            raise ValueError("remove_feature 指向的 feature id 不存在。")
        obj["features"] = [item for item in features if item["id"] != patch.feature_id]
    elif patch.op == "replace_feature":
        if patch.feature_id not in ids:
            raise ValueError("replace_feature 指向的 feature id 不存在。")
        obj["features"] = [
            patch.feature.model_dump(mode="python")
            if item["id"] == patch.feature_id
            else item
            for item in features
        ]
    else:
        obj["color_field"] = patch.color_field.model_dump(mode="python")
    return MinScene.model_validate(data)


__all__ = [
    "AddFeaturePatch",
    "CIRCLE_AXES_TOLERANCE",
    "Canvas",
    "ColorField",
    "Feature",
    "LinearColorField",
    "MAX_MIN_FEATURES",
    "MIN_SCENE_VERSION",
    "MinScene",
    "MinScenePatch",
    "Primitive",
    "RadialColorField",
    "RemoveFeaturePatch",
    "ReplaceColorFieldPatch",
    "ReplaceFeaturePatch",
    "SceneObject",
    "SolidColorField",
    "apply_scene_patch",
]
