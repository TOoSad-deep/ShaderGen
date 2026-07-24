"""最小 Shader DSL V1 的确定性规范化、内容哈希与稳定参数清单."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from shaderforge.dsl.document import (
    CircleShape,
    EllipseShape,
    GlowEffect,
    Layer,
    LinearFill,
    RadialFill,
    RimEffect,
    RoundedBoxShape,
    SegmentShape,
    ShaderDocument,
    ShadowEffect,
    ShapeExpr,
    SolidFill,
    SubtractShape,
)

CANVAS_BLOCK = "canvas"


def node_param_path(node_id: str, field: str) -> str:
    """返回 shape 节点参数的稳定 manifest 路径."""
    return f"node:{node_id}.{field}"


def layer_param_path(layer_id: str, field: str) -> str:
    """返回 layer 级参数的稳定 manifest 路径."""
    return f"layer:{layer_id}.{field}"


def layer_geometry_block(layer_id: str) -> str:
    """返回 layer geometry 优化 block 名."""
    return f"layer:{layer_id}.geometry"


def layer_fill_block(layer_id: str) -> str:
    """返回 layer fill 优化 block 名."""
    return f"layer:{layer_id}.fill"


def layer_effects_block(layer_id: str) -> str:
    """返回 layer effects 优化 block 名."""
    return f"layer:{layer_id}.effects"


def layer_opacity_block(layer_id: str) -> str:
    """返回 layer opacity 优化 block 名."""
    return f"layer:{layer_id}.opacity"


def _canonical_payload(payload: Any) -> str:
    """把任意 JSON 可序列化结构编码为确定性 canonical JSON."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _normalize_signed_zero(payload: Any) -> Any:
    """递归把数值等价的 -0.0 规范化为 0.0."""
    if isinstance(payload, float) and payload == 0.0:
        return 0.0
    if isinstance(payload, list):
        return [_normalize_signed_zero(item) for item in payload]
    if isinstance(payload, tuple):
        return tuple(_normalize_signed_zero(item) for item in payload)
    if isinstance(payload, dict):
        return {key: _normalize_signed_zero(value) for key, value in payload.items()}
    return payload


def _sha256_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def canonical_json(document: ShaderDocument) -> str:
    """返回绑定结构、层序和全部参数的 canonical document JSON."""
    payload = document.model_dump(mode="json", by_alias=True)
    return _canonical_payload(_normalize_signed_zero(payload))


def document_sha256(document: ShaderDocument) -> str:
    """返回 canonical document 的 SHA-256；layers 数组顺序保持原样."""
    return _sha256_text(canonical_json(document))


def _shape_topology(node: ShapeExpr) -> dict[str, Any]:
    if isinstance(node, (CircleShape, EllipseShape, RoundedBoxShape, SegmentShape)):
        return {"kind": node.kind}
    if isinstance(node, SubtractShape):
        return {
            "kind": node.kind,
            "base": _shape_topology(node.base),
            "cut": _shape_topology(node.cut),
        }
    return {
        "kind": node.kind,
        "left": _shape_topology(node.left),
        "right": _shape_topology(node.right),
    }


def topology_json(document: ShaderDocument) -> str:
    """返回绑定 schema、节点类型、连接、材质/effect 类型和层序的 topology JSON."""
    payload = {
        "schema_version": document.schema_version,
        "layers": [
            {
                "shape": _shape_topology(layer.shape),
                "fill": layer.fill.kind,
                "effects": [effect.kind for effect in layer.effects],
            }
            for layer in document.layers
        ],
    }
    return _canonical_payload(payload)


def topology_sha256(document: ShaderDocument) -> str:
    """返回 topology skeleton 的 SHA-256，不随参数数值变化."""
    return _sha256_text(topology_json(document))


@dataclass(frozen=True)
class ManifestEntry:
    """稳定参数清单条目：路径、所属优化 block 与当前值."""

    path: str
    block: str
    value: float


def _node_manifest_entries(layer_id: str, node: ShapeExpr) -> list[ManifestEntry]:
    block = layer_geometry_block(layer_id)
    entries: list[ManifestEntry] = []

    def add(field: str, value: float) -> None:
        entries.append(ManifestEntry(node_param_path(node.id, field), block, value))

    if node.transform is not None:
        transform = node.transform
        add("transform.translate.x", transform.translate[0])
        add("transform.translate.y", transform.translate[1])
        add("transform.scale.x", transform.scale[0])
        add("transform.scale.y", transform.scale[1])
        add("transform.rotation.cos", transform.rotation[0])
        add("transform.rotation.sin", transform.rotation[1])
    if isinstance(node, CircleShape):
        add("radius", node.radius)
    elif isinstance(node, EllipseShape):
        add("radii.x", node.radii[0])
        add("radii.y", node.radii[1])
    elif isinstance(node, RoundedBoxShape):
        add("half_size.x", node.half_size[0])
        add("half_size.y", node.half_size[1])
        add("corner_radius", node.corner_radius)
    elif isinstance(node, SegmentShape):
        add("from.x", node.from_[0])
        add("from.y", node.from_[1])
        add("to.x", node.to[0])
        add("to.y", node.to[1])
        add("radius", node.radius)
    elif isinstance(node, SubtractShape):
        entries.extend(_node_manifest_entries(layer_id, node.base))
        entries.extend(_node_manifest_entries(layer_id, node.cut))
    else:
        entries.extend(_node_manifest_entries(layer_id, node.left))
        entries.extend(_node_manifest_entries(layer_id, node.right))
    return entries


def _color_entries(
    prefix: str, block: str, color: tuple[float, ...]
) -> list[ManifestEntry]:
    channels = ("r", "g", "b", "a")
    return [
        ManifestEntry(f"{prefix}.{channel}", block, color[index])
        for index, channel in enumerate(channels)
    ]


def _vec2_entries(
    prefix: str, block: str, value: tuple[float, float]
) -> list[ManifestEntry]:
    return [
        ManifestEntry(f"{prefix}.x", block, value[0]),
        ManifestEntry(f"{prefix}.y", block, value[1]),
    ]


def _fill_manifest_entries(layer: Layer) -> list[ManifestEntry]:
    block = layer_fill_block(layer.id)
    fill = layer.fill
    prefix = layer_param_path(layer.id, "fill")
    if isinstance(fill, SolidFill):
        return _color_entries(f"{prefix}.color", block, fill.color)
    if isinstance(fill, LinearFill):
        entries = _vec2_entries(f"{prefix}.from", block, fill.from_)
        entries.extend(_vec2_entries(f"{prefix}.to", block, fill.to))
        entries.extend(_color_entries(f"{prefix}.start_color", block, fill.start_color))
        entries.extend(_color_entries(f"{prefix}.end_color", block, fill.end_color))
        return entries
    if isinstance(fill, RadialFill):
        entries = _vec2_entries(f"{prefix}.center", block, fill.center)
        entries.append(ManifestEntry(f"{prefix}.radius", block, fill.radius))
        entries.extend(_color_entries(f"{prefix}.inner_color", block, fill.inner_color))
        entries.extend(_color_entries(f"{prefix}.outer_color", block, fill.outer_color))
        return entries
    raise TypeError(f"未知 fill 类型：{type(fill).__name__}。")


def _effect_manifest_entries(layer: Layer) -> list[ManifestEntry]:
    block = layer_effects_block(layer.id)
    entries: list[ManifestEntry] = []
    for effect in layer.effects:
        prefix = layer_param_path(layer.id, f"effect.{effect.kind}")
        if isinstance(effect, ShadowEffect):
            entries.extend(_vec2_entries(f"{prefix}.offset", block, effect.offset))
            entries.append(ManifestEntry(f"{prefix}.blur", block, effect.blur))
            entries.append(ManifestEntry(f"{prefix}.spread", block, effect.spread))
            entries.extend(_color_entries(f"{prefix}.color", block, effect.color))
        elif isinstance(effect, GlowEffect):
            entries.append(ManifestEntry(f"{prefix}.radius", block, effect.radius))
            entries.append(ManifestEntry(f"{prefix}.softness", block, effect.softness))
            entries.extend(_color_entries(f"{prefix}.color", block, effect.color))
        elif isinstance(effect, RimEffect):
            entries.append(ManifestEntry(f"{prefix}.width", block, effect.width))
            entries.append(ManifestEntry(f"{prefix}.softness", block, effect.softness))
            entries.extend(_color_entries(f"{prefix}.color", block, effect.color))
        else:
            raise TypeError(f"未知 effect 类型：{type(effect).__name__}。")
    return entries


def parameter_manifest(document: ShaderDocument) -> tuple[ManifestEntry, ...]:
    """返回按路径排序的稳定参数清单，不依赖任何数组位置."""
    entries: list[ManifestEntry] = []
    entries.extend(
        _color_entries("canvas.background", CANVAS_BLOCK, document.canvas.background)
    )
    for layer in document.layers:
        entries.extend(_node_manifest_entries(layer.id, layer.shape))
        entries.extend(_fill_manifest_entries(layer))
        entries.extend(_effect_manifest_entries(layer))
        entries.append(
            ManifestEntry(
                layer_param_path(layer.id, "opacity"),
                layer_opacity_block(layer.id),
                layer.opacity,
            )
        )
    paths = [entry.path for entry in entries]
    if len(set(paths)) != len(paths):
        raise ValueError("参数清单路径冲突，节点 id 必须文档级唯一。")
    return tuple(sorted(entries, key=lambda entry: entry.path))


def parameter_manifest_sha256(document: ShaderDocument) -> str:
    """返回参数清单身份（路径与 block，不含数值）的 SHA-256."""
    payload = [
        {"block": entry.block, "path": entry.path}
        for entry in parameter_manifest(document)
    ]
    return _sha256_text(_canonical_payload(payload))


__all__ = [
    "CANVAS_BLOCK",
    "ManifestEntry",
    "canonical_json",
    "document_sha256",
    "layer_effects_block",
    "layer_fill_block",
    "layer_geometry_block",
    "layer_opacity_block",
    "layer_param_path",
    "node_param_path",
    "parameter_manifest",
    "parameter_manifest_sha256",
    "topology_json",
    "topology_sha256",
]
