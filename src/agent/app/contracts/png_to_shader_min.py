"""scene_mvp Model Author 的严格结构化输出契约。."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from shaderforge.public import (
    AddFeaturePatch,
    Feature,
    MinScene,
    RemoveFeaturePatch,
    SwapModelPatch,
    apply_scene_patch,
)


class _StrictModel(BaseModel):
    """拒绝模型输出中的未知字段。."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AddFeatureAuthorPatch(_StrictModel):
    """向唯一 feature 列表增加一个完整 typed feature。."""

    operation: Literal["add"]
    path: Literal["/object/features"]
    value: Feature


class RemoveFeatureAuthorPatch(_StrictModel):
    """按稳定 id 删除一个 feature。."""

    operation: Literal["remove"]
    path: Literal["/object/features"]
    value: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ReplaceColorModelAuthorPatch(_StrictModel):
    """只允许切换颜色场的模板白名单模型。."""

    operation: Literal["replace"]
    path: Literal["/object/color_field/model"]
    value: Literal["solid", "radial"]


MinAuthorPatch = Annotated[
    AddFeatureAuthorPatch | RemoveFeatureAuthorPatch | ReplaceColorModelAuthorPatch,
    Field(discriminator="operation"),
]


def apply_min_author_patch(scene: MinScene, patch: MinAuthorPatch) -> MinScene:
    """把 Agent 白名单 patch 适配到领域 scene patch，并重新完整校验。."""
    if isinstance(patch, AddFeatureAuthorPatch):
        return apply_scene_patch(
            scene,
            AddFeaturePatch(op="add_feature", feature=patch.value),
        )
    if isinstance(patch, RemoveFeatureAuthorPatch):
        if patch.value not in {feature.id for feature in scene.object.features}:
            raise ValueError("remove patch 指向的 feature id 不存在。")
        return apply_scene_patch(
            scene,
            RemoveFeaturePatch(op="remove_feature", feature_id=patch.value),
        )
    return apply_scene_patch(
        scene,
        SwapModelPatch(op="swap_model", model=patch.value),
    )


__all__ = [
    "AddFeatureAuthorPatch",
    "MinAuthorPatch",
    "RemoveFeatureAuthorPatch",
    "ReplaceColorModelAuthorPatch",
    "apply_min_author_patch",
]
