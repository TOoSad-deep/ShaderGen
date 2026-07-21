"""Effect Genome v0 与 typed port/parameter 冻结模型。."""

from __future__ import annotations

from collections import deque
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from shaderforge.contracts import (
    FiniteFloat,
    FrozenModel,
    NonEmptyString,
    Sha256Hex,
)
from shaderforge.store import ArtifactRefV2

EFFECT_NODE_REGISTRY_VERSION = "effect_node_registry_v0"
GENOME_HASH_VERSION = "genome_hash_v1"

PortType = Literal["sdf", "mask", "color"]
NodeKind = Literal[
    "circle_sdf",
    "ellipse_sdf",
    "rounded_rect_sdf",
    "solid_fill",
    "linear_gradient",
    "gaussian_color_lobe",
    "shadow",
    "glow",
    "rim_band",
    "outline_band",
    "arc_highlight",
    "union_mask",
    "intersection_mask",
    "difference_mask",
    "over_blend",
    "color_output",
]


class NodePort(FrozenModel):
    """一个具名 typed port。."""

    name: NonEmptyString
    port_type: PortType


class NodeKindSpec(FrozenModel):
    """Node registry v0 中冻结的端口签名。."""

    kind: NodeKind
    node_version: Literal["1"] = "1"
    inputs: tuple[NodePort, ...]
    outputs: tuple[NodePort, ...]


def _ports(*items: tuple[str, PortType]) -> tuple[NodePort, ...]:
    return tuple(NodePort(name=name, port_type=port_type) for name, port_type in items)


EFFECT_NODE_REGISTRY_V0: tuple[NodeKindSpec, ...] = (
    NodeKindSpec(kind="circle_sdf", inputs=(), outputs=_ports(("sdf", "sdf"))),
    NodeKindSpec(kind="ellipse_sdf", inputs=(), outputs=_ports(("sdf", "sdf"))),
    NodeKindSpec(kind="rounded_rect_sdf", inputs=(), outputs=_ports(("sdf", "sdf"))),
    NodeKindSpec(
        kind="solid_fill",
        inputs=_ports(("mask", "mask")),
        outputs=_ports(("color", "color")),
    ),
    NodeKindSpec(
        kind="linear_gradient",
        inputs=_ports(("mask", "mask")),
        outputs=_ports(("color", "color")),
    ),
    NodeKindSpec(
        kind="gaussian_color_lobe",
        inputs=_ports(("mask", "mask")),
        outputs=_ports(("color", "color")),
    ),
    NodeKindSpec(
        kind="shadow",
        inputs=_ports(("sdf", "sdf")),
        outputs=_ports(("color", "color")),
    ),
    NodeKindSpec(
        kind="glow",
        inputs=_ports(("sdf", "sdf")),
        outputs=_ports(("color", "color")),
    ),
    NodeKindSpec(
        kind="rim_band",
        inputs=_ports(("sdf", "sdf")),
        outputs=_ports(("color", "color")),
    ),
    NodeKindSpec(
        kind="outline_band",
        inputs=_ports(("sdf", "sdf")),
        outputs=_ports(("color", "color")),
    ),
    NodeKindSpec(
        kind="arc_highlight",
        inputs=_ports(("sdf", "sdf")),
        outputs=_ports(("color", "color")),
    ),
    NodeKindSpec(
        kind="union_mask",
        inputs=_ports(("left", "mask"), ("right", "mask")),
        outputs=_ports(("mask", "mask")),
    ),
    NodeKindSpec(
        kind="intersection_mask",
        inputs=_ports(("left", "mask"), ("right", "mask")),
        outputs=_ports(("mask", "mask")),
    ),
    NodeKindSpec(
        kind="difference_mask",
        inputs=_ports(("left", "mask"), ("right", "mask")),
        outputs=_ports(("mask", "mask")),
    ),
    NodeKindSpec(
        kind="over_blend",
        inputs=_ports(("background", "color"), ("foreground", "color")),
        outputs=_ports(("color", "color")),
    ),
    NodeKindSpec(
        kind="color_output",
        inputs=_ports(("color", "color")),
        outputs=_ports(("color", "color")),
    ),
)
_REGISTRY_BY_KIND = {spec.kind: spec for spec in EFFECT_NODE_REGISTRY_V0}


def _numeric_parameter_value(
    value: bool | int | float | tuple[float, ...] | None,
    *,
    field_name: str,
) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, tuple):
        raise ValueError(f"ParameterSpec {field_name} 必须是 numeric scalar。")
    return value


class ParameterBinding(FrozenModel):
    """Node 内部具名参数槽到全局唯一 path 的绑定。."""

    binding_name: NonEmptyString
    parameter_path: NonEmptyString


class EffectNode(FrozenModel):
    """V2.0 generic typed node；V2.2 才实现各 kind 的编译 union。."""

    node_id: NonEmptyString
    kind: NodeKind
    node_version: Literal["1"] = "1"
    semantic_role: NonEmptyString
    sibling_ordinal: int = Field(ge=0)
    inputs: tuple[NodePort, ...]
    outputs: tuple[NodePort, ...]
    parameter_bindings: tuple[ParameterBinding, ...] = ()

    @model_validator(mode="after")
    def _validate_registry_signature(self) -> EffectNode:
        expected = _REGISTRY_BY_KIND[self.kind]
        if self.node_version != expected.node_version:
            raise ValueError("node_version 与 effect_node_registry_v0 不一致。")
        if self.inputs != expected.inputs or self.outputs != expected.outputs:
            raise ValueError("Node ports 与 effect_node_registry_v0 不一致。")
        names = [binding.binding_name for binding in self.parameter_bindings]
        if len(set(names)) != len(names):
            raise ValueError("同一 Node 的 parameter binding name 不得重复。")
        return self


class EffectEdge(FrozenModel):
    """两个具名 typed port 之间的有向边。."""

    source_node_id: NonEmptyString
    source_port: NonEmptyString
    target_node_id: NonEmptyString
    target_port: NonEmptyString


class ParameterSpec(FrozenModel):
    """Genome 中参数值及其完整可搜索语义布局。."""

    path: NonEmptyString
    dtype: Literal["float", "int", "bool", "vec2", "vec3", "vec4"]
    value: bool | int | FiniteFloat | tuple[FiniteFloat, ...]
    min_value: bool | int | FiniteFloat | tuple[FiniteFloat, ...] | None
    max_value: bool | int | FiniteFloat | tuple[FiniteFloat, ...] | None
    optimizable: bool
    block: NonEmptyString
    affected_regions: tuple[NonEmptyString, ...]
    semantic_role: NonEmptyString
    unit: NonEmptyString
    coordinate_space: NonEmptyString | None
    color_space: NonEmptyString | None
    cyclic: bool
    quantization: FiniteFloat | None = Field(default=None, gt=0.0)

    @field_validator("value", "min_value", "max_value", mode="before")
    @classmethod
    def _validate_raw_dtype(cls, value: object, info: ValidationInfo) -> object:
        raw_dtype = info.data.get("dtype")
        dtype = raw_dtype if isinstance(raw_dtype, str) else ""
        expected_length = {"vec2": 2, "vec3": 3, "vec4": 4}.get(dtype)
        if info.field_name != "value" and value is None:
            return value
        if expected_length is not None:
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"{dtype} 参数 {info.field_name} 必须是数组。")
            if len(value) != expected_length:
                raise ValueError(
                    f"{dtype} 参数 {info.field_name} 必须是长度 "
                    f"{expected_length} 的数组。"
                )
            if any(type(item) is not float for item in value):
                raise ValueError(
                    f"{dtype} 参数 {info.field_name} 的元素必须是有限 float。"
                )
            return tuple(value)
        if dtype == "bool" and type(value) is not bool:
            raise ValueError("bool 参数 value 必须是 bool。")
        if dtype == "int" and type(value) is not int:
            raise ValueError(f"int 参数 {info.field_name} 必须是整数。")
        if dtype == "float" and type(value) is not float:
            raise ValueError(f"float 参数 {info.field_name} 必须是有限 float。")
        return value

    @model_validator(mode="after")
    def _validate_shape_and_range(self) -> ParameterSpec:
        expected_length = {"vec2": 2, "vec3": 3, "vec4": 4}.get(self.dtype)
        if expected_length is not None:
            value = self.value
            if not isinstance(value, tuple):  # pragma: no cover - schema 已保证
                raise ValueError(f"{self.dtype} value 必须冻结为 tuple。")
            minimum = self.min_value
            maximum = self.max_value
            if minimum is not None and not isinstance(minimum, tuple):
                raise ValueError(f"{self.dtype} min_value 必须冻结为 tuple。")
            if maximum is not None and not isinstance(maximum, tuple):
                raise ValueError(f"{self.dtype} max_value 必须冻结为 tuple。")
            for index, item in enumerate(value):
                lower = minimum[index] if isinstance(minimum, tuple) else None
                upper = maximum[index] if isinstance(maximum, tuple) else None
                if lower is not None and upper is not None and lower > upper:
                    raise ValueError("ParameterSpec min_value 不得大于 max_value。")
                if lower is not None and item < lower:
                    raise ValueError("ParameterSpec value 不得小于 min_value。")
                if upper is not None and item > upper:
                    raise ValueError("ParameterSpec value 不得大于 max_value。")
        elif self.dtype == "bool":
            if self.min_value is not None or self.max_value is not None:
                raise ValueError("bool 参数不得设置范围。")
        else:
            scalar_value = _numeric_parameter_value(self.value, field_name="value")
            scalar_minimum = _numeric_parameter_value(
                self.min_value,
                field_name="min_value",
            )
            scalar_maximum = _numeric_parameter_value(
                self.max_value,
                field_name="max_value",
            )
            if scalar_value is None:  # pragma: no cover - value 字段不允许 None
                raise ValueError("ParameterSpec value 不能为空。")
            if (
                scalar_minimum is not None
                and scalar_maximum is not None
                and scalar_minimum > scalar_maximum
            ):
                raise ValueError("ParameterSpec min_value 不得大于 max_value。")
            if scalar_minimum is not None and scalar_value < scalar_minimum:
                raise ValueError("ParameterSpec value 不得小于 min_value。")
            if scalar_maximum is not None and scalar_value > scalar_maximum:
                raise ValueError("ParameterSpec value 不得大于 max_value。")
        return self


class GenomeProvenance(FrozenModel):
    """不进入 semantic hash、但进入完整 record hash 的谱系。."""

    source: Literal["rule", "model", "memory", "legacy_adapter"]
    intent_id: NonEmptyString
    target_hypothesis_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    template_id: NonEmptyString
    template_version: NonEmptyString
    random_seed: int
    evidence_refs: tuple[ArtifactRefV2, ...] = ()


class EffectGenome(FrozenModel):
    """Effect Genome v0 不可变记录。."""

    schema_version: Literal["genome_v0"] = "genome_v0"
    hash_version: Literal["genome_hash_v1"] = "genome_hash_v1"
    node_registry_version: Literal["effect_node_registry_v0"] = (
        "effect_node_registry_v0"
    )
    mask_semantics: Literal["coverage_0_outside_1_inside_v1"] = (
        "coverage_0_outside_1_inside_v1"
    )
    sdf_semantics: Literal["negative_inside_v1"] = "negative_inside_v1"
    antialias_rule: Literal["analytic_fixed_width_v1"] = "analytic_fixed_width_v1"
    genome_id: NonEmptyString
    contract_id: NonEmptyString
    strategy: NonEmptyString
    nodes: tuple[EffectNode, ...]
    edges: tuple[EffectEdge, ...]
    parameters: tuple[ParameterSpec, ...]
    output_node_id: NonEmptyString
    provenance: GenomeProvenance

    @model_validator(mode="after")
    def _validate_graph(self) -> EffectGenome:
        if not self.nodes:
            raise ValueError("Genome nodes 不能为空。")
        node_by_id = {node.node_id: node for node in self.nodes}
        if len(node_by_id) != len(self.nodes):
            raise ValueError("node_id 不得重复。")
        stable_keys = [
            (node.semantic_role, node.kind, node.sibling_ordinal) for node in self.nodes
        ]
        if len(set(stable_keys)) != len(stable_keys):
            raise ValueError("canonical node stable key 不得重复。")
        if self.output_node_id not in node_by_id:
            raise ValueError("output_node_id 不存在。")
        if node_by_id[self.output_node_id].kind != "color_output":
            raise ValueError("output_node_id 必须指向 color_output。")
        paths = [parameter.path for parameter in self.parameters]
        if len(set(paths)) != len(paths):
            raise ValueError("ParameterSpec.path 不得重复。")
        known_paths = set(paths)
        for node in self.nodes:
            for binding in node.parameter_bindings:
                if binding.parameter_path not in known_paths:
                    raise ValueError("Node 绑定了不存在的 ParameterSpec.path。")
        incoming: dict[tuple[str, str], int] = {}
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_by_id}
        reverse_adjacency: dict[str, list[str]] = {
            node_id: [] for node_id in node_by_id
        }
        indegree = {node_id: 0 for node_id in node_by_id}
        for edge in self.edges:
            if (
                edge.source_node_id not in node_by_id
                or edge.target_node_id not in node_by_id
            ):
                raise ValueError("Edge 引用了不存在的 node_id。")
            source = node_by_id[edge.source_node_id]
            target = node_by_id[edge.target_node_id]
            source_ports = {port.name: port.port_type for port in source.outputs}
            target_ports = {port.name: port.port_type for port in target.inputs}
            if (
                edge.source_port not in source_ports
                or edge.target_port not in target_ports
            ):
                raise ValueError("Edge 引用了不存在的 port。")
            source_type = source_ports[edge.source_port]
            target_type = target_ports[edge.target_port]
            if source_type != target_type and not (
                source_type == "sdf" and target_type == "mask"
            ):
                raise ValueError("Edge port type 不兼容。")
            target_key = (edge.target_node_id, edge.target_port)
            incoming[target_key] = incoming.get(target_key, 0) + 1
            if incoming[target_key] > 1:
                raise ValueError("一个 input port 只能有一条入边。")
            adjacency[edge.source_node_id].append(edge.target_node_id)
            reverse_adjacency[edge.target_node_id].append(edge.source_node_id)
            indegree[edge.target_node_id] += 1
        for node in self.nodes:
            for input_port in node.inputs:
                if incoming.get((node.node_id, input_port.name), 0) != 1:
                    raise ValueError("每个 input port 必须恰好有一条入边。")
        if adjacency[self.output_node_id]:
            raise ValueError("output_node_id 必须是无出边的唯一输出汇点。")
        queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
        visited = 0
        while queue:
            node_id = queue.popleft()
            visited += 1
            for target_id in adjacency[node_id]:
                indegree[target_id] -= 1
                if indegree[target_id] == 0:
                    queue.append(target_id)
        if visited != len(self.nodes):
            raise ValueError("Genome 必须是 DAG。")
        reachable = {self.output_node_id}
        pending = [self.output_node_id]
        while pending:
            node_id = pending.pop()
            for source_id in reverse_adjacency[node_id]:
                if source_id not in reachable:
                    reachable.add(source_id)
                    pending.append(source_id)
        if len(reachable) != len(self.nodes):
            raise ValueError("Genome 的每个 node 都必须可达 output_node_id。")
        return self


class GenomeHashes(FrozenModel):
    """Genome v0 的四类冻结 hash。."""

    hash_version: Literal["genome_hash_v1"] = "genome_hash_v1"
    topology_hash: Sha256Hex
    parameter_layout_hash: Sha256Hex
    semantic_genome_hash: Sha256Hex
    record_hash: Sha256Hex
