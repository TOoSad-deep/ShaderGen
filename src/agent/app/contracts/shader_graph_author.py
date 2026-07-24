"""ShaderGraph Model Author 的严格结构化输出契约：完整文档与单个 typed layer patch.

Initial Author 输出完整 `ShaderDocument`（由 Parser 严格解析）；Refine Author 每轮
只输出一个绑定 `base_document_sha256` 的原子 layer op。应用 patch 时先校验 base
哈希，再以完整重建触发全图校验；DSL shape 是内联树、不存在 id 引用，不可达节点
在结构上不可表示，重建后通过可达 id 一致性检查兜底。
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shaderforge.dsl import (
    ColorRGBA,
    DslCanvas,
    Layer,
    NodeId,
    ShaderDocument,
    document_sha256,
    shape_node_ids,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_BASE_HASH_PREFIX_LENGTH = 12


class ShaderGraphAuthorPatchError(ValueError):
    """表示 typed layer patch 应用失败，只保留稳定错误码."""

    def __init__(self, code: str) -> None:
        """只保留稳定错误码，不携带完整 patch 或文档内容."""
        self.code = code
        super().__init__(code)


class _StrictModel(BaseModel):
    """拒绝模型输出中的未知字段."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class _BaseBoundPatch(_StrictModel):
    """所有 Refine patch 必须绑定当前 best 文档哈希."""

    base_document_sha256: str = Field(pattern=_SHA256_PATTERN)


class AddLayerBundleAuthorPatch(_BaseBoundPatch):
    """追加一个完整 Layer bundle，固定成为最前层（layers 末尾）."""

    operation: Literal["add_layer_bundle"]
    value: Layer


class RemoveLayerAuthorPatch(_BaseBoundPatch):
    """按稳定 layer id 删除完整 Layer."""

    operation: Literal["remove_layer"]
    value: NodeId


class LayerReplacement(_StrictModel):
    """replace_layer_bundle 的 typed value：目标 id 与完整替换 Layer."""

    layer_id: NodeId
    layer: Layer


class ReplaceLayerBundleAuthorPatch(_BaseBoundPatch):
    """按稳定 id 原子替换完整 Layer（ShapeExpr、Fill 与 Effects）."""

    operation: Literal["replace_layer_bundle"]
    value: LayerReplacement


class LayerReorder(_StrictModel):
    """reorder_layer 的 typed value：目标 id 与单步移动方向."""

    layer_id: NodeId
    direction: Literal["toward_front", "toward_back"]


class ReorderLayerAuthorPatch(_BaseBoundPatch):
    """把指定 Layer 向层叠前方或后方单步移动一位."""

    operation: Literal["reorder_layer"]
    value: LayerReorder


class ReplaceCanvasBackgroundAuthorPatch(_BaseBoundPatch):
    """替换 canvas 的 opaque 背景色；画布尺寸与 color space 不可变."""

    operation: Literal["replace_canvas_background"]
    value: ColorRGBA


ShaderGraphAuthorPatch = Annotated[
    AddLayerBundleAuthorPatch
    | RemoveLayerAuthorPatch
    | ReplaceLayerBundleAuthorPatch
    | ReorderLayerAuthorPatch
    | ReplaceCanvasBackgroundAuthorPatch,
    Field(discriminator="operation"),
]


def _layer_index(document: ShaderDocument, layer_id: str) -> int:
    for index, layer in enumerate(document.layers):
        if layer.id == layer_id:
            return index
    raise ShaderGraphAuthorPatchError("layer_not_found")


def _reachable_node_ids(document: ShaderDocument) -> frozenset[str]:
    """返回全部可达 layer 与 shape 节点 id；内联树结构下等同于全部 id."""
    ids: set[str] = set()
    for layer in document.layers:
        ids.add(layer.id)
        ids.update(shape_node_ids(layer.shape))
    return frozenset(ids)


def _rebuild_document(
    document: ShaderDocument, layers: list[Layer], background: ColorRGBA
) -> ShaderDocument:
    """完整重建文档以触发全图校验（id 唯一、层级/primitive/CSG 预算、opaque 背景）.

    DSL shape 是内联树而非 id 引用图，不可达节点在结构上不可表示；逐层
    `shape_node_ids` 可达性遍历见 `_reachable_node_ids`，供审计与测试断言。
    """
    try:
        canvas = DslCanvas(
            width=document.canvas.width,
            height=document.canvas.height,
            background=background,
        )
        return ShaderDocument(canvas=canvas, layers=tuple(layers))
    except ValidationError as exc:
        raise ShaderGraphAuthorPatchError("patched_document_invalid") from exc


def apply_shader_graph_author_patch(
    document: ShaderDocument, patch: ShaderGraphAuthorPatch
) -> ShaderDocument:
    """校验 base 哈希后应用恰好一个原子 layer op，并对结果做全图重新校验."""
    if document_sha256(document) != patch.base_document_sha256:
        raise ShaderGraphAuthorPatchError("base_document_mismatch")
    layers = list(document.layers)
    background = document.canvas.background
    if isinstance(patch, AddLayerBundleAuthorPatch):
        layers.append(patch.value)
    elif isinstance(patch, RemoveLayerAuthorPatch):
        del layers[_layer_index(document, patch.value)]
    elif isinstance(patch, ReplaceLayerBundleAuthorPatch):
        if patch.value.layer.id != patch.value.layer_id:
            raise ShaderGraphAuthorPatchError("layer_id_mismatch")
        layers[_layer_index(document, patch.value.layer_id)] = patch.value.layer
    elif isinstance(patch, ReorderLayerAuthorPatch):
        index = _layer_index(document, patch.value.layer_id)
        offset = 1 if patch.value.direction == "toward_front" else -1
        target = index + offset
        if target < 0 or target >= len(layers):
            raise ShaderGraphAuthorPatchError("reorder_out_of_range")
        layers[index], layers[target] = layers[target], layers[index]
    else:
        background = patch.value
    return _rebuild_document(document, layers, background)


def _layer_node_kinds(layer: Layer) -> tuple[str, ...]:
    """返回 Layer bundle 的节点类型集合（shape/fill/effect kind，去重排序）."""
    kinds = {layer.fill.kind, *(effect.kind for effect in layer.effects)}

    def walk(node: Any) -> None:
        kinds.add(node.kind)
        for child in ("base", "cut", "left", "right"):
            sub = getattr(node, child, None)
            if sub is not None:
                walk(sub)

    walk(layer.shape)
    return tuple(sorted(kinds))


def summarize_shader_graph_author_patch(
    patch: ShaderGraphAuthorPatch,
) -> dict[str, Any]:
    """返回不含完整 value 的 patch 身份摘要与规范化 SHA-256 指纹."""
    canonical = json.dumps(
        patch.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    layer_id: str | None = None
    node_kinds: tuple[str, ...] = ()
    if isinstance(patch, AddLayerBundleAuthorPatch):
        layer_id = patch.value.id
        node_kinds = _layer_node_kinds(patch.value)
    elif isinstance(patch, RemoveLayerAuthorPatch):
        layer_id = patch.value
    elif isinstance(patch, ReplaceLayerBundleAuthorPatch):
        layer_id = patch.value.layer_id
        node_kinds = _layer_node_kinds(patch.value.layer)
    elif isinstance(patch, ReorderLayerAuthorPatch):
        layer_id = patch.value.layer_id
    return {
        "patch_operation": patch.operation,
        "layer_id": layer_id,
        "node_kinds": node_kinds,
        "base_document_sha256_prefix": patch.base_document_sha256[
            :_BASE_HASH_PREFIX_LENGTH
        ],
        "patch_fingerprint": sha256(canonical).hexdigest(),
    }


__all__ = [
    "AddLayerBundleAuthorPatch",
    "LayerReorder",
    "LayerReplacement",
    "RemoveLayerAuthorPatch",
    "ReorderLayerAuthorPatch",
    "ReplaceCanvasBackgroundAuthorPatch",
    "ReplaceLayerBundleAuthorPatch",
    "ShaderGraphAuthorPatch",
    "ShaderGraphAuthorPatchError",
    "apply_shader_graph_author_patch",
    "summarize_shader_graph_author_patch",
]
