"""scene_mvp Model Author 的严格结构化输出契约。."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shaderforge.public import (
    AddFeaturePatch,
    ColorField,
    Feature,
    MinScene,
    RemoveFeaturePatch,
    ReplaceColorFieldPatch,
    ReplaceFeaturePatch,
    apply_scene_patch,
)


class _StrictModel(BaseModel):
    """拒绝模型输出中的未知字段。."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AddFeatureAuthorPatch(_StrictModel):
    """增加完整 feature 的 Author patch。."""

    operation: Literal["add"]
    path: Literal["/object/features"]
    value: Feature


class RemoveFeatureAuthorPatch(_StrictModel):
    """按稳定 id 删除 feature 的 Author patch。."""

    operation: Literal["remove"]
    path: Literal["/object/features"]
    value: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class FeatureReplacement(_StrictModel):
    feature_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    feature: Feature


class ReplaceFeatureAuthorPatch(_StrictModel):
    """按稳定 id 替换完整 feature 的 Author patch。."""

    operation: Literal["replace"]
    path: Literal["/object/features"]
    value: FeatureReplacement


class ReplaceColorFieldAuthorPatch(_StrictModel):
    """替换完整 typed ColorField 的 Author patch。."""

    operation: Literal["replace"]
    path: Literal["/object/color_field"]
    value: ColorField


# 两种 replace 共享 operation，依靠严格 path 与 value shape 判别，不能用单字段 discriminator。
MinAuthorPatch = (
    AddFeatureAuthorPatch
    | RemoveFeatureAuthorPatch
    | ReplaceFeatureAuthorPatch
    | ReplaceColorFieldAuthorPatch
)


def summarize_min_author_patch(patch: MinAuthorPatch) -> dict[str, str | None]:
    """返回不含完整 value 的 typed Patch 身份和规范化 SHA-256 指纹。."""
    canonical = json.dumps(
        patch.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    operation: str
    feature_id: str | None = None
    feature_type: str | None = None
    if isinstance(patch, AddFeatureAuthorPatch):
        operation = "add_feature"
        feature_id = patch.value.id
        feature_type = patch.value.type
    elif isinstance(patch, RemoveFeatureAuthorPatch):
        operation = "remove_feature"
        feature_id = patch.value
    elif isinstance(patch, ReplaceFeatureAuthorPatch):
        operation = "replace_feature"
        feature_id = patch.value.feature_id
        feature_type = patch.value.feature.type
    else:
        operation = "replace_color_field"
    return {
        "patch_operation": operation,
        "feature_id": feature_id,
        "feature_type": feature_type,
        "patch_fingerprint": sha256(canonical).hexdigest(),
    }


def apply_min_author_patch(scene: MinScene, patch: MinAuthorPatch) -> MinScene:
    """把 Agent 白名单 patch 适配到领域 scene patch，并重新完整校验。."""
    if isinstance(patch, AddFeatureAuthorPatch):
        return apply_scene_patch(
            scene, AddFeaturePatch(op="add_feature", feature=patch.value)
        )
    if isinstance(patch, RemoveFeatureAuthorPatch):
        return apply_scene_patch(
            scene,
            RemoveFeaturePatch(op="remove_feature", feature_id=patch.value),
        )
    if isinstance(patch, ReplaceFeatureAuthorPatch):
        return apply_scene_patch(
            scene,
            ReplaceFeaturePatch(
                op="replace_feature",
                feature_id=patch.value.feature_id,
                feature=patch.value.feature,
            ),
        )
    return apply_scene_patch(
        scene,
        ReplaceColorFieldPatch(op="replace_color_field", color_field=patch.value),
    )


__all__ = [
    "AddFeatureAuthorPatch",
    "MinAuthorPatch",
    "RemoveFeatureAuthorPatch",
    "ReplaceColorFieldAuthorPatch",
    "ReplaceFeatureAuthorPatch",
    "apply_min_author_patch",
    "summarize_min_author_patch",
]
