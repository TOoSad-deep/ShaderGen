"""最小 PNG-to-Shader 流水线的版本化场景契约。."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Color = tuple[float, float, float]
Point = tuple[float, float]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Canvas(_StrictModel):
    """目标画布及背景色。."""

    width: int = Field(ge=16, le=2048)
    height: int = Field(ge=16, le=2048)
    background: Color


class Primitive(_StrictModel):
    """首版单主体圆或椭圆。."""

    type: Literal["circle", "ellipse"]
    center: Point
    axes: Point


class ColorField(_StrictModel):
    """主体的径向渐变颜色场。."""

    model: Literal["solid", "radial"]
    inner: Color
    outer: Color
    origin: Point = (-0.35, 0.55)
    scale: float = Field(default=1.25, gt=0.05, le=4.0)


class Feature(_StrictModel):
    """模板可识别的轻量视觉特征。."""

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    type: Literal["polar_arc", "shadow", "rim", "edge_line"]
    center: Point = (0.0, -0.7)
    axes: Point = (0.55, 0.14)
    color: Color = (0.08, 0.12, 0.12)
    intensity: float = Field(default=0.25, ge=0.0, le=2.0)


class SceneObject(_StrictModel):
    """单主体及其颜色与特征。."""

    primitive: Primitive
    color_field: ColorField
    features: tuple[Feature, ...] = ()

    @model_validator(mode="after")
    def ensure_unique_feature_ids(self) -> SceneObject:
        """拒绝可能导致 patch 指向不确定的重复 id。."""
        ids = [feature.id for feature in self.features]
        if len(ids) != len(set(ids)):
            raise ValueError("feature id 必须唯一。")
        return self


class MinScene(_StrictModel):
    """scene_mvp 唯一可编辑 Shader 表示。."""

    schema_version: Literal["png_to_shader_min_scene_v1"] = (
        "png_to_shader_min_scene_v1"
    )
    canvas: Canvas
    object: SceneObject


class AddFeaturePatch(_StrictModel):
    """向 scene 增加一个完整 typed feature。."""

    op: Literal["add_feature"]
    feature: Feature


class RemoveFeaturePatch(_StrictModel):
    """按稳定 id 删除 feature。."""

    op: Literal["remove_feature"]
    feature_id: str


class SwapModelPatch(_StrictModel):
    """切换颜色场模型。."""

    op: Literal["swap_model"]
    model: Literal["solid", "radial"]


MinScenePatch = Annotated[
    AddFeaturePatch | RemoveFeaturePatch | SwapModelPatch,
    Field(discriminator="op"),
]


def apply_scene_patch(scene: MinScene, patch: MinScenePatch) -> MinScene:
    """应用白名单 patch，并再次通过完整 scene 契约。."""
    data = scene.model_dump(mode="python")
    obj = data["object"]
    if patch.op == "add_feature":
        obj["features"] = [
            *obj["features"],
            patch.feature.model_dump(mode="python"),
        ]
    elif patch.op == "remove_feature":
        obj["features"] = [
            item for item in obj["features"] if item["id"] != patch.feature_id
        ]
    else:
        obj["color_field"]["model"] = patch.model
    return MinScene.model_validate(data)


__all__ = [
    "AddFeaturePatch",
    "Canvas",
    "ColorField",
    "Feature",
    "MinScene",
    "MinScenePatch",
    "Primitive",
    "RemoveFeaturePatch",
    "SceneObject",
    "SwapModelPatch",
    "apply_scene_patch",
]
