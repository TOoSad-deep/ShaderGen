"""Shader DSL 文档的最小 node-id 参数优化接口.

只提供三件确定性能力：从 Compiler 稳定参数清单派生可调参数的范围与步长、
按 ``node:<id>.<field>`` / ``layer:<id>.<field>`` / ``canvas.background.<channel>``
稳定地址做单参数 replace、replace 后重新通过完整文档契约校验。严格
improvement 判定、rebase、预算调度与结构搜索都留给上层；本模块不实现
CMA-ES、批量 patch 或任意字段路径。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterator

from shaderforge.dsl import ShaderDocument, parameter_manifest, parse_dsl_document

# 范围与步长是 Compiler manifest 之上的确定性派生表，单位与 DSL 契约一致：
# 颜色与 opacity 裁剪到 [0, 1]；位置使用画布短边归一化坐标；长度为归一化长度。
_COLOR_RANGE = (0.0, 1.0, 0.05)
_OPACITY_RANGE = (0.0, 1.0, 0.05)
_POSITION_RANGE = (-2.0, 2.0, 0.05)
_SCALE_RANGE = (0.05, 4.0, 0.05)
_ROTATION_RANGE = (-1.0, 1.0, 0.05)
_POSITIVE_LENGTH_RANGE = (0.001, 2.0, 0.02)
_NONNEGATIVE_LENGTH_RANGE = (0.0, 2.0, 0.02)

_LEAF_INDEX = {"x": 0, "y": 1, "cos": 0, "sin": 1, "r": 0, "g": 1, "b": 2, "a": 3}
_POSITIVE_LENGTH_LEAVES = frozenset({"radius", "width"})
_NONNEGATIVE_LENGTH_LEAVES = frozenset({"corner_radius", "blur", "spread", "softness"})
_POSITION_SEGMENTS = (".center.", ".from.", ".to.", ".offset.")


@dataclass(frozen=True)
class DslParameterSpec:
    """一个可调 DSL 参数的稳定地址、所属 block、当前值与确定性邻域."""

    path: str
    block: str
    value: float
    minimum: float
    maximum: float
    step: float


def _bounds_for_path(path: str) -> tuple[float, float, float]:
    """按稳定地址的语义返回 (minimum, maximum, step)，未知路径 fail closed."""
    leaf = path.rsplit(".", 1)[-1]
    if leaf == "opacity":
        return _OPACITY_RANGE
    if ".transform.scale." in path:
        return _SCALE_RANGE
    if ".transform.rotation." in path:
        return _ROTATION_RANGE
    if ".transform.translate." in path:
        return _POSITION_RANGE
    if ".color." in path or "_color." in path or path.startswith("canvas.background."):
        return _COLOR_RANGE
    if any(segment in path for segment in _POSITION_SEGMENTS):
        return _POSITION_RANGE
    if ".radii." in path or ".half_size." in path:
        return _POSITIVE_LENGTH_RANGE
    if leaf in _POSITIVE_LENGTH_LEAVES:
        return _POSITIVE_LENGTH_RANGE
    if leaf in _NONNEGATIVE_LENGTH_LEAVES:
        return _NONNEGATIVE_LENGTH_RANGE
    raise ValueError(f"未定义范围规则的参数路径：{path}。")


def dsl_parameter_specs(document: ShaderDocument) -> tuple[DslParameterSpec, ...]:
    """从 Compiler 稳定参数清单派生全部可调参数的范围与步长."""
    specs: list[DslParameterSpec] = []
    for entry in parameter_manifest(document):
        minimum, maximum, step = _bounds_for_path(entry.path)
        specs.append(
            DslParameterSpec(
                path=entry.path,
                block=entry.block,
                value=entry.value,
                minimum=minimum,
                maximum=maximum,
                step=step,
            )
        )
    return tuple(specs)


def _iter_shape_nodes(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for key in ("left", "right", "base", "cut"):
        child = node.get(key)
        if isinstance(child, dict):
            yield from _iter_shape_nodes(child)


def _find_node(payload: dict[str, Any], node_id: str) -> dict[str, Any]:
    for layer in payload["layers"]:
        for node in _iter_shape_nodes(layer["shape"]):
            if node.get("id") == node_id:
                return node
    raise ValueError(f"文档中不存在 shape 节点：{node_id}。")


def _find_layer(payload: dict[str, Any], layer_id: str) -> dict[str, Any]:
    for layer in payload["layers"]:
        if isinstance(layer, dict) and layer.get("id") == layer_id:
            return layer
    raise ValueError(f"文档中不存在 layer：{layer_id}。")


def _assign_nested(target: Any, dotted: str, value: float) -> None:
    """把 ``a.b.x`` 形式的相对路径写入 JSON payload 叶子."""
    tokens = dotted.split(".")
    for token in tokens[:-1]:
        if not isinstance(target, dict) or token not in target:
            raise ValueError(f"参数路径中段不存在：{dotted}。")
        target = target[token]
    leaf = tokens[-1]
    if isinstance(target, list):
        index = _LEAF_INDEX.get(leaf)
        if index is None or index >= len(target):
            raise ValueError(f"参数路径的向量分量无效：{dotted}。")
        target[index] = value
        return
    if isinstance(target, dict) and leaf in target:
        target[leaf] = value
        return
    raise ValueError(f"参数路径叶子不存在：{dotted}。")


def _assign(payload: dict[str, Any], path: str, value: float) -> None:
    if path.startswith("canvas.background."):
        channel = path.rsplit(".", 1)[-1]
        background = payload["canvas"]["background"]
        index = _LEAF_INDEX.get(channel)
        if index is None or index >= len(background):
            raise ValueError(f"canvas.background 分量无效：{path}。")
        background[index] = value
        return
    prefix, separator, rest = path.partition(".")
    if not separator:
        raise ValueError(f"参数路径缺少字段段：{path}。")
    if prefix.startswith("node:"):
        node = _find_node(payload, prefix[len("node:") :])
        _assign_nested(node, rest, value)
        return
    if prefix.startswith("layer:"):
        layer = _find_layer(payload, prefix[len("layer:") :])
        if rest == "opacity":
            layer["opacity"] = value
            return
        if rest.startswith("fill."):
            _assign_nested(layer["fill"], rest[len("fill.") :], value)
            return
        if rest.startswith("effect."):
            kind, separator, nested = rest[len("effect.") :].partition(".")
            if not separator:
                raise ValueError(f"effect 参数路径缺少字段段：{path}。")
            for effect in layer["effects"]:
                if effect.get("kind") == kind:
                    _assign_nested(effect, nested, value)
                    return
            raise ValueError(f"layer 中不存在 effect：{kind}。")
        raise ValueError(f"未知 layer 参数路径：{path}。")
    raise ValueError(f"未知参数路径前缀：{path}。")


def replace_dsl_parameter(
    document: ShaderDocument, path: str, value: float
) -> ShaderDocument:
    """对 canonical ShaderDocument 做单参数 replace 并重新通过完整契约.

    路径必须存在于 Compiler 稳定参数清单，value 必须是落在 spec 范围内的
    有限数；写回后整个文档重新校验（预算、单位 rotation、corner_radius 等
    联动约束一旦破坏即 fail closed），调用方再用 document_sha256 比较哈希。
    """
    if not math.isfinite(value):
        raise ValueError("参数值必须是有限数。")
    specs = {spec.path: spec for spec in dsl_parameter_specs(document)}
    spec = specs.get(path)
    if spec is None:
        raise ValueError(f"参数路径不在稳定清单中：{path}。")
    if not spec.minimum <= value <= spec.maximum:
        raise ValueError(
            f"参数值 {value} 超出 [{spec.minimum}, {spec.maximum}] 范围：{path}。"
        )
    payload = document.model_dump(mode="json", by_alias=True)
    _assign(payload, path, value)
    return parse_dsl_document(payload)


__all__ = [
    "DslParameterSpec",
    "dsl_parameter_specs",
    "replace_dsl_parameter",
]
