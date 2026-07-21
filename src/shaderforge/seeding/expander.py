"""SeedPlan 到 EffectGenome 的无模型确定性展开器。."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import TypeAdapter

from shaderforge.contracts import canonical_sha256
from shaderforge.genome import (
    EFFECT_NODE_REGISTRY_V0,
    GenomeHashes,
    GenomeProvenance,
    ParameterBinding,
    ParameterSpec,
    TypedEffectEdge,
    TypedEffectGenome,
    TypedEffectNode,
    compute_genome_hashes,
)
from shaderforge.genome.models import NodeKind
from shaderforge.intent import IntentIR, RelationIntent

from .matcher import build_seed_plans, match_seed_templates
from .models import (
    AllowedOverrideV1,
    DiversityException,
    ExpandedSeedV1,
    GeometryKind,
    LayerBindingV1,
    OverrideParameterName,
    OverrideValue,
    SeedDiversityAssessmentV1,
    SeedExpansionResultV2,
    SeedPlanV1,
    TemplateMatchV1,
)

_SPEC_BY_KIND = {item.kind: item for item in EFFECT_NODE_REGISTRY_V0}
_NODE_ADAPTER: TypeAdapter[TypedEffectNode] = TypeAdapter(TypedEffectNode)


def _node(
    kind: NodeKind,
    *,
    node_id: str,
    semantic_role: str,
    sibling_ordinal: int,
    bindings: tuple[ParameterBinding, ...] = (),
) -> TypedEffectNode:
    spec = _SPEC_BY_KIND[kind]
    return _NODE_ADAPTER.validate_python(
        {
            "node_id": node_id,
            "kind": kind,
            "semantic_role": semantic_role,
            "sibling_ordinal": sibling_ordinal,
            "inputs": spec.inputs,
            "outputs": spec.outputs,
            "parameter_bindings": bindings,
        }
    )


def _float_parameter(
    *,
    path: str,
    value: float,
    minimum: float,
    maximum: float,
    block: str,
    region: str,
    role: str,
    unit: str,
    coordinate_space: str | None = None,
    color_space: str | None = None,
    cyclic: bool = False,
) -> ParameterSpec:
    return ParameterSpec(
        path=path,
        dtype="float",
        value=value,
        min_value=minimum,
        max_value=maximum,
        optimizable=True,
        block=block,
        affected_regions=(region,),
        semantic_role=role,
        unit=unit,
        coordinate_space=coordinate_space,
        color_space=color_space,
        cyclic=cyclic,
        quantization=0.0001,
    )


def _vector_parameter(
    *,
    path: str,
    value: tuple[float, ...],
    minimum: tuple[float, ...],
    maximum: tuple[float, ...],
    block: str,
    region: str,
    role: str,
    unit: str,
    coordinate_space: str | None = None,
    color_space: str | None = None,
) -> ParameterSpec:
    dtype_by_length: dict[int, Literal["vec2", "vec3", "vec4"]] = {
        2: "vec2",
        3: "vec3",
        4: "vec4",
    }
    dtype = dtype_by_length.get(len(value))
    if dtype is None:
        raise ValueError("Seed parameter vector 只允许 vec2/vec3/vec4。")
    return ParameterSpec(
        path=path,
        dtype=dtype,
        value=value,
        min_value=minimum,
        max_value=maximum,
        optimizable=True,
        block=block,
        affected_regions=(region,),
        semantic_role=role,
        unit=unit,
        coordinate_space=coordinate_space,
        color_space=color_space,
        cyclic=False,
        quantization=0.0001,
    )


def _as_float(value: OverrideValue, *, field: str) -> float:
    if type(value) is not float:
        raise ValueError(f"Override {field} 必须是 finite float。")
    return value


def _as_vector(
    value: OverrideValue,
    *,
    field: str,
    length: int,
) -> tuple[float, ...]:
    if (
        not isinstance(value, tuple)
        or len(value) != length
        or any(type(item) is not float for item in value)
    ):
        raise ValueError(f"Override {field} 必须是长度 {length} 的 float tuple。")
    return value


class _Overrides:
    def __init__(self, values: tuple[AllowedOverrideV1, ...]) -> None:
        self._values = {
            (item.layer_id, item.parameter_name): item.value for item in values
        }
        self._consumed: set[tuple[str, OverrideParameterName]] = set()

    def scalar(
        self,
        layer_id: str,
        name: OverrideParameterName,
        default: float,
    ) -> float:
        key = (layer_id, name)
        value = self._values.get(key)
        if value is None:
            return default
        self._consumed.add(key)
        return _as_float(value, field=f"{layer_id}.{name}")

    def vector(
        self,
        layer_id: str,
        name: OverrideParameterName,
        default: tuple[float, ...],
    ) -> tuple[float, ...]:
        key = (layer_id, name)
        value = self._values.get(key)
        if value is None:
            return default
        self._consumed.add(key)
        return _as_vector(value, field=f"{layer_id}.{name}", length=len(default))

    def assert_all_consumed(self) -> None:
        unused = sorted(set(self._values) - self._consumed)
        if unused:
            joined = ", ".join(f"{layer}.{name}" for layer, name in unused)
            raise ValueError(f"SeedPlan 含模板未消费的 parameter override：{joined}")


def _lab_to_linear_rgba(lab: tuple[float, float, float]) -> tuple[float, ...]:
    lightness, a_axis, b_axis = lab
    fy = (lightness + 16.0) / 116.0
    fx = fy + a_axis / 500.0
    fz = fy - b_axis / 200.0

    def inverse_lab(value: float) -> float:
        cube = value**3
        return cube if cube > 216.0 / 24389.0 else (116.0 * value - 16.0) / 903.3

    x = 0.95047 * inverse_lab(fx)
    y = inverse_lab(fy)
    z = 1.08883 * inverse_lab(fz)
    red = 3.2404542 * x - 1.5371385 * y - 0.4985314 * z
    green = -0.969266 * x + 1.8760108 * y + 0.041556 * z
    blue = 0.0556434 * x - 0.2040259 * y + 1.0572252 * z
    return (
        min(1.0, max(0.0, red)),
        min(1.0, max(0.0, green)),
        min(1.0, max(0.0, blue)),
        1.0,
    )


def _base_color(intent: IntentIR) -> tuple[float, ...]:
    if not intent.regions:
        return (0.5, 0.5, 0.5, 1.0)
    region = min(intent.regions, key=lambda item: (-item.area_ratio, item.region_id))
    return _lab_to_linear_rgba(region.mean_lab)


def _mix_color(
    color: tuple[float, ...],
    target: float,
    amount: float,
) -> tuple[float, ...]:
    return tuple(
        component + (target - component) * amount for component in color[:3]
    ) + (color[3],)


def _validate_plan(intent: IntentIR, plan: SeedPlanV1) -> TemplateMatchV1:
    if (
        plan.intent_id != intent.intent_id
        or plan.target_hypothesis_id != intent.target_hypothesis_id
        or plan.target_hypothesis_hash != intent.target_hypothesis_hash
    ):
        raise ValueError("SeedPlan 与 Intent/target hypothesis 身份不一致。")
    expected_matches = {item.seed_role: item for item in match_seed_templates(intent)}
    expected = expected_matches[plan.seed_role]
    if (
        plan.template_id != expected.template_id
        or plan.template_version != expected.template_version
    ):
        raise ValueError("SeedPlan template 不属于该 Intent 的冻结匹配结果。")
    intent_layers = {
        item.layer_id: (item.order, item.role, item.object_ref, item.required)
        for item in intent.layers
    }
    actual_layers = {
        item.layer_id: (
            item.layer_order,
            item.role,
            item.object_ref,
            item.enabled,
        )
        for item in plan.layer_bindings
    }
    if actual_layers != intent_layers:
        raise ValueError("SeedPlan layer bindings 未精确闭包 Intent required layers。")
    expected_evidence = tuple(
        sorted(
            set(intent.evidence_refs),
            key=lambda ref: (
                ref.sha256,
                ref.kind,
                ref.schema_version,
                ref.content_type,
                ref.size_bytes,
                ref.artifact_id,
            ),
        )
    )
    if plan.evidence_refs != expected_evidence:
        raise ValueError("SeedPlan evidence refs 未精确绑定 Intent evidence。")
    return expected


def _geometry(
    intent: IntentIR,
    binding: LayerBindingV1,
    geometry_kind: GeometryKind,
    overrides: _Overrides,
) -> tuple[TypedEffectNode, tuple[ParameterSpec, ...]]:
    subject = min(intent.objects, key=lambda item: (-item.area_ratio, item.object_id))
    node_id = "geometry_subject"
    center = overrides.vector(binding.layer_id, "center", subject.center_uv)
    bindings = [ParameterBinding(binding_name="center", parameter_path="shape.center")]
    parameters = [
        _vector_parameter(
            path="shape.center",
            value=center,
            minimum=(0.0, 0.0),
            maximum=(1.0, 1.0),
            block="geometry",
            region=subject.object_id,
            role="position",
            unit="normalized",
            coordinate_space="shader_uv_bottom_left",
        )
    ]
    axes = tuple(max(0.005, min(0.75, float(value))) for value in subject.axes_uv)
    if geometry_kind == "circle_sdf":
        radius = overrides.scalar(binding.layer_id, "radius", sum(axes) / 2.0)
        bindings.append(
            ParameterBinding(binding_name="radius", parameter_path="shape.radius")
        )
        parameters.append(
            _float_parameter(
                path="shape.radius",
                value=radius,
                minimum=0.005,
                maximum=0.75,
                block="geometry",
                region=subject.object_id,
                role="radius",
                unit="normalized",
                coordinate_space="shader_uv_bottom_left",
            )
        )
    elif geometry_kind == "ellipse_sdf":
        radii = overrides.vector(binding.layer_id, "radii", axes)
        rotation = overrides.scalar(
            binding.layer_id, "rotation", float(subject.orientation_rad)
        )
        bindings.extend(
            (
                ParameterBinding(binding_name="radii", parameter_path="shape.radii"),
                ParameterBinding(
                    binding_name="rotation", parameter_path="shape.rotation"
                ),
            )
        )
        parameters.extend(
            (
                _vector_parameter(
                    path="shape.radii",
                    value=radii,
                    minimum=(0.005, 0.005),
                    maximum=(0.75, 0.75),
                    block="geometry",
                    region=subject.object_id,
                    role="radii",
                    unit="normalized",
                    coordinate_space="shader_uv_bottom_left",
                ),
                _float_parameter(
                    path="shape.rotation",
                    value=rotation,
                    minimum=-math.pi,
                    maximum=math.pi,
                    block="geometry",
                    region=subject.object_id,
                    role="rotation",
                    unit="radians",
                    cyclic=True,
                ),
            )
        )
    else:
        half_size = overrides.vector(binding.layer_id, "half_size", axes)
        corner_default = max(0.005, min(half_size) * 0.25)
        corner = overrides.scalar(binding.layer_id, "corner_radius", corner_default)
        rotation = overrides.scalar(
            binding.layer_id, "rotation", float(subject.orientation_rad)
        )
        bindings.extend(
            (
                ParameterBinding(
                    binding_name="half_size", parameter_path="shape.half_size"
                ),
                ParameterBinding(
                    binding_name="corner_radius",
                    parameter_path="shape.corner_radius",
                ),
                ParameterBinding(
                    binding_name="rotation", parameter_path="shape.rotation"
                ),
            )
        )
        parameters.extend(
            (
                _vector_parameter(
                    path="shape.half_size",
                    value=half_size,
                    minimum=(0.005, 0.005),
                    maximum=(0.75, 0.75),
                    block="geometry",
                    region=subject.object_id,
                    role="half_size",
                    unit="normalized",
                    coordinate_space="shader_uv_bottom_left",
                ),
                _float_parameter(
                    path="shape.corner_radius",
                    value=corner,
                    minimum=0.0,
                    maximum=0.5,
                    block="geometry",
                    region=subject.object_id,
                    role="corner_radius",
                    unit="normalized",
                    coordinate_space="shader_uv_bottom_left",
                ),
                _float_parameter(
                    path="shape.rotation",
                    value=rotation,
                    minimum=-math.pi,
                    maximum=math.pi,
                    block="geometry",
                    region=subject.object_id,
                    role="rotation",
                    unit="radians",
                    cyclic=True,
                ),
            )
        )
    return (
        _node(
            geometry_kind,
            node_id=node_id,
            semantic_role="subject_geometry",
            sibling_ordinal=0,
            bindings=tuple(bindings),
        ),
        tuple(parameters),
    )


@dataclass(frozen=True, slots=True)
class _StructuralGeometry:
    nodes: tuple[TypedEffectNode, ...]
    edges: tuple[TypedEffectEdge, ...]
    parameters: tuple[ParameterSpec, ...]
    subject_mask_node_id: str
    subject_mask_port: str
    subject_mask_is_sdf: bool
    primary_sdf_node_id: str


def _relation_composition(
    intent: IntentIR,
    *,
    prior_instance_ids: tuple[str, ...],
    next_instance_id: str,
) -> tuple[Literal["union_mask", "difference_mask"], bool]:
    """将 Intent 的逐实例 relation 闭包为确定性 mask 代数.

    V2.4 production 的 instance mask 是互斥 partition，因此当前只允许
    可由 visible-delta partition 证明的 touches/disjoint。其余 relation
    保留在上游 schema 供未来 raw-instance diagnostics 使用，但不得编译。
    """
    relation_by_pair: dict[frozenset[str], RelationIntent] = {}
    for relation in intent.relations:
        endpoints = frozenset((relation.subject_ref, relation.object_ref))
        if relation.subject_ref == relation.object_ref or endpoints in relation_by_pair:
            raise ValueError("Intent instance relations 必须按 pair 唯一。")
        relation_by_pair[endpoints] = relation
    selected: list[RelationIntent] = []
    for prior_id in prior_instance_ids:
        matched_relation = relation_by_pair.get(frozenset((prior_id, next_instance_id)))
        if matched_relation is None:
            raise ValueError("多实例 Seed 必须显式闭包每一对 instance relation。")
        selected.append(matched_relation)
    kinds = {item.kind for item in selected}
    unsupported = kinds - {"disjoint", "touches"}
    if unsupported:
        raise ValueError(
            "V2.4 production instance partition 暂不支持 relation: "
            + ",".join(sorted(unsupported))
        )
    return "union_mask", False


def _renamed_geometry(
    node: TypedEffectNode,
    parameters: tuple[ParameterSpec, ...],
    *,
    node_id: str,
    semantic_role: str,
    prefix: str,
    center: tuple[float, float],
    axes: tuple[float, float],
    orientation_rad: float,
    size_scale: float,
) -> tuple[TypedEffectNode, tuple[ParameterSpec, ...]]:
    """把 aggregate geometry 变成一个显式 instance geometry。."""
    path_map = {
        item.path: f"{prefix}.{item.path.removeprefix('shape.')}" for item in parameters
    }
    bindings = tuple(
        ParameterBinding(
            binding_name=item.binding_name,
            parameter_path=path_map[item.parameter_path],
        )
        for item in node.parameter_bindings
    )
    renamed = _node(
        node.kind,
        node_id=node_id,
        semantic_role=semantic_role,
        sibling_ordinal=int(prefix.rsplit("_", 1)[-1])
        if prefix.rsplit("_", 1)[-1].isdigit()
        else 0,
        bindings=bindings,
    )
    values: list[ParameterSpec] = []
    for item in parameters:
        value = item.value
        minimum = item.min_value
        maximum = item.max_value
        if item.semantic_role == "position":
            value = center
        elif item.semantic_role == "radius":
            value = (axes[0] + axes[1]) * 0.5 * size_scale
        elif item.semantic_role in {"radii", "half_size"}:
            value = (axes[0] * size_scale, axes[1] * size_scale)
        elif item.semantic_role == "corner_radius":
            value = min(axes) * size_scale * 0.25
        elif item.semantic_role == "rotation":
            value = orientation_rad
        values.append(
            item.model_copy(
                update={
                    "path": path_map[item.path],
                    "value": value,
                    "min_value": minimum,
                    "max_value": maximum,
                }
            )
        )
    return renamed, tuple(values)


def _structural_geometry(
    intent: IntentIR,
    binding: LayerBindingV1,
    geometry_kind: GeometryKind,
    overrides: _Overrides,
) -> _StructuralGeometry:
    subject = min(intent.objects, key=lambda item: (-item.area_ratio, item.object_id))
    aggregate, aggregate_parameters = _geometry(
        intent, binding, geometry_kind, overrides
    )
    instances = subject.instances
    count = len(instances)
    instance_ids = tuple(item.instance_id for item in instances)
    expected_relation_pairs = {
        frozenset((instance_ids[left], instance_ids[right]))
        for left in range(count)
        for right in range(left + 1, count)
    }
    actual_relation_pairs = {
        frozenset((item.subject_ref, item.object_ref)) for item in intent.relations
    }
    if actual_relation_pairs != expected_relation_pairs or len(
        actual_relation_pairs
    ) != len(intent.relations):
        raise ValueError(
            "Seed Expander 要求 relations 精确覆盖 subject instance pair 闭集。"
        )
    if (
        subject.instance_count == 1
        and subject.topology == "solid"
        and subject.hole_count == 0
    ):
        # 保留 V2.2 solid Genome hash；Compiler 的兼容规则仍能导出 instance pass。
        return _StructuralGeometry(
            nodes=(aggregate,),
            edges=(),
            parameters=aggregate_parameters,
            subject_mask_node_id=aggregate.node_id,
            subject_mask_port="sdf",
            subject_mask_is_sdf=True,
            primary_sdf_node_id=aggregate.node_id,
        )

    nodes: list[TypedEffectNode] = []
    edges: list[TypedEffectEdge] = []
    parameters: list[ParameterSpec] = []
    instance_outputs: list[tuple[str, str, bool]] = []
    primary_sdf = ""
    for index, instance in enumerate(instances):
        center = (float(instance.center_uv[0]), float(instance.center_uv[1]))
        axes = (float(instance.axes_uv[0]), float(instance.axes_uv[1]))
        outer_id = f"geometry_instance_{index:04d}"
        outer, outer_parameters = _renamed_geometry(
            aggregate,
            aggregate_parameters,
            node_id=outer_id,
            semantic_role=f"instance_{index:04d}_geometry",
            prefix=f"shape.instance_{index:04d}",
            center=center,
            axes=axes,
            orientation_rad=float(instance.orientation_rad),
            size_scale=1.0,
        )
        nodes.append(outer)
        parameters.extend(outer_parameters)
        if not primary_sdf:
            primary_sdf = outer_id
        output_id = outer_id
        output_port = "sdf"
        output_is_sdf = True
        if (
            instance.fill_topology in {"ring", "hollow", "open"}
            or instance.hole_count > 0
        ):
            inner_id = f"geometry_instance_{index:04d}_inner"
            inner_scale = 0.68 if instance.fill_topology == "ring" else 0.48
            inner, inner_parameters = _renamed_geometry(
                aggregate,
                aggregate_parameters,
                node_id=inner_id,
                semantic_role=f"instance_{index:04d}_inner_geometry",
                prefix=f"shape.instance_{index:04d}_inner",
                center=center,
                axes=axes,
                orientation_rad=float(instance.orientation_rad),
                size_scale=inner_scale,
            )
            mask_id = f"mask_instance_{index:04d}"
            difference = _node(
                "difference_mask",
                node_id=mask_id,
                semantic_role=f"instance_{index:04d}_mask",
                sibling_ordinal=index,
            )
            nodes.extend((inner, difference))
            parameters.extend(inner_parameters)
            edges.extend(
                (
                    TypedEffectEdge(
                        source_node_id=outer_id,
                        source_port="sdf",
                        target_node_id=mask_id,
                        target_port="left",
                        sdf_to_mask_conversion="analytic_fixed_width_v1",
                    ),
                    TypedEffectEdge(
                        source_node_id=inner_id,
                        source_port="sdf",
                        target_node_id=mask_id,
                        target_port="right",
                        sdf_to_mask_conversion="analytic_fixed_width_v1",
                    ),
                )
            )
            output_id, output_port, output_is_sdf = mask_id, "mask", False
            if instance.fill_topology == "open":
                cutter_id = f"geometry_instance_{index:04d}_opening"
                cutter_center = (
                    min(0.98, center[0] + axes[0]),
                    center[1],
                )
                cutter, cutter_parameters = _renamed_geometry(
                    aggregate,
                    aggregate_parameters,
                    node_id=cutter_id,
                    semantic_role=f"instance_{index:04d}_opening_geometry",
                    prefix=f"shape.instance_{index:04d}_opening",
                    center=cutter_center,
                    axes=axes,
                    orientation_rad=float(instance.orientation_rad),
                    size_scale=0.38,
                )
                open_id = f"mask_instance_{index:04d}_open"
                open_mask = _node(
                    "difference_mask",
                    node_id=open_id,
                    semantic_role=f"instance_{index:04d}_mask",
                    sibling_ordinal=index + count,
                )
                # 只让最终 open mask 作为 instance diagnostic identity。
                difference = difference.model_copy(
                    update={"semantic_role": f"instance_{index:04d}_closed_mask"}
                )
                nodes[-1] = difference
                nodes.extend((cutter, open_mask))
                parameters.extend(cutter_parameters)
                edges.extend(
                    (
                        TypedEffectEdge(
                            source_node_id=mask_id,
                            source_port="mask",
                            target_node_id=open_id,
                            target_port="left",
                        ),
                        TypedEffectEdge(
                            source_node_id=cutter_id,
                            source_port="sdf",
                            target_node_id=open_id,
                            target_port="right",
                            sdf_to_mask_conversion="analytic_fixed_width_v1",
                        ),
                    )
                )
                output_id = open_id
        instance_outputs.append((output_id, output_port, output_is_sdf))

    current_id, current_port, current_is_sdf = instance_outputs[0]
    for index, (next_id, next_port, next_is_sdf) in enumerate(
        instance_outputs[1:], start=1
    ):
        composition_kind, reverse = _relation_composition(
            intent,
            prior_instance_ids=tuple(item.instance_id for item in instances[:index]),
            next_instance_id=instances[index].instance_id,
        )
        composition_id = f"mask_subject_relation_{index:04d}"
        composition = _node(
            composition_kind,
            node_id=composition_id,
            semantic_role="subject_relation_mask",
            sibling_ordinal=index - 1,
        )
        nodes.append(composition)
        left = (
            (next_id, next_port, next_is_sdf)
            if reverse
            else (
                current_id,
                current_port,
                current_is_sdf,
            )
        )
        right = (
            (current_id, current_port, current_is_sdf)
            if reverse
            else (
                next_id,
                next_port,
                next_is_sdf,
            )
        )
        edges.extend(
            (
                TypedEffectEdge(
                    source_node_id=left[0],
                    source_port=left[1],
                    target_node_id=composition_id,
                    target_port="left",
                    sdf_to_mask_conversion=(
                        "analytic_fixed_width_v1" if left[2] else None
                    ),
                ),
                TypedEffectEdge(
                    source_node_id=right[0],
                    source_port=right[1],
                    target_node_id=composition_id,
                    target_port="right",
                    sdf_to_mask_conversion=(
                        "analytic_fixed_width_v1" if right[2] else None
                    ),
                ),
            )
        )
        current_id, current_port, current_is_sdf = composition_id, "mask", False
    return _StructuralGeometry(
        nodes=tuple(nodes),
        edges=tuple(edges),
        parameters=tuple(parameters),
        subject_mask_node_id=current_id,
        subject_mask_port=current_port,
        subject_mask_is_sdf=current_is_sdf,
        primary_sdf_node_id=primary_sdf,
    )


def _color_parameter(
    path: str,
    value: tuple[float, ...],
    *,
    region: str,
    role: str,
) -> ParameterSpec:
    return _vector_parameter(
        path=path,
        value=value,
        minimum=(0.0, 0.0, 0.0, 0.0),
        maximum=(1.0, 1.0, 1.0, 1.0),
        block="color",
        region=region,
        role=role,
        unit="rgba",
        color_space="linear_rgb",
    )


def _layer_node(
    binding: LayerBindingV1,
    *,
    base_color: tuple[float, ...],
    center: tuple[float, ...],
    overrides: _Overrides,
) -> tuple[TypedEffectNode, tuple[ParameterSpec, ...]]:
    node_id = f"layer_{binding.layer_order:02d}_{binding.role}"
    prefix = f"layers.{binding.layer_id}"
    region = binding.object_ref or "canvas"
    kind: NodeKind
    values: list[ParameterSpec] = []
    bindings: list[ParameterBinding] = []

    def add(name: str, parameter: ParameterSpec) -> None:
        bindings.append(
            ParameterBinding(binding_name=name, parameter_path=parameter.path)
        )
        values.append(parameter)

    if binding.role == "base_fill" and binding.primitive_id == "linear_gradient":
        kind = "linear_gradient"
        start = overrides.vector(binding.layer_id, "start", (center[0], 0.15))
        end = overrides.vector(binding.layer_id, "end", (center[0], 0.85))
        start_color = overrides.vector(
            binding.layer_id, "start_color", _mix_color(base_color, 1.0, 0.18)
        )
        end_color = overrides.vector(
            binding.layer_id, "end_color", _mix_color(base_color, 0.0, 0.18)
        )
        add(
            "start",
            _vector_parameter(
                path=f"{prefix}.start",
                value=start,
                minimum=(0.0, 0.0),
                maximum=(1.0, 1.0),
                block="color",
                region=region,
                role="gradient_start",
                unit="normalized",
                coordinate_space="shader_uv_bottom_left",
            ),
        )
        add(
            "end",
            _vector_parameter(
                path=f"{prefix}.end",
                value=end,
                minimum=(0.0, 0.0),
                maximum=(1.0, 1.0),
                block="color",
                region=region,
                role="gradient_end",
                unit="normalized",
                coordinate_space="shader_uv_bottom_left",
            ),
        )
        add(
            "start_color",
            _color_parameter(
                f"{prefix}.start_color", start_color, region=region, role="start_color"
            ),
        )
        add(
            "end_color",
            _color_parameter(
                f"{prefix}.end_color", end_color, region=region, role="end_color"
            ),
        )
    elif binding.role == "base_fill":
        kind = "solid_fill"
        color = overrides.vector(binding.layer_id, "color", base_color)
        add(
            "color",
            _color_parameter(
                f"{prefix}.color", color, region=region, role="base_color"
            ),
        )
    elif binding.role == "shadow":
        kind = "shadow"
        shadow_specs: tuple[
            tuple[OverrideParameterName, float, float, float, str], ...
        ] = (
            ("blur", 0.035, 0.001, 0.25, "normalized"),
            ("spread", 0.008, -0.1, 0.25, "normalized"),
        )
        for name, default, minimum, maximum, unit in shadow_specs:
            add(
                name,
                _float_parameter(
                    path=f"{prefix}.{name}",
                    value=overrides.scalar(binding.layer_id, name, default),
                    minimum=minimum,
                    maximum=maximum,
                    block="layer",
                    region=region,
                    role=name,
                    unit=unit,
                    coordinate_space="shader_uv_bottom_left",
                ),
            )
        offset = overrides.vector(binding.layer_id, "offset", (0.02, -0.025))
        offset_parameter = _vector_parameter(
            path=f"{prefix}.offset",
            value=offset,
            minimum=(-0.5, -0.5),
            maximum=(0.5, 0.5),
            block="layer",
            region=region,
            role="offset",
            unit="normalized",
            coordinate_space="shader_uv_bottom_left",
        )
        bindings.insert(
            0,
            ParameterBinding(
                binding_name="offset", parameter_path=offset_parameter.path
            ),
        )
        values.insert(0, offset_parameter)
        color = overrides.vector(binding.layer_id, "color", (0.0, 0.0, 0.0, 0.55))
        add(
            "color",
            _color_parameter(
                f"{prefix}.color", color, region=region, role="shadow_color"
            ),
        )
    elif binding.role in {"glow", "haze"}:
        kind = "glow"
        radius = overrides.scalar(
            binding.layer_id, "radius", 0.06 if binding.role == "glow" else 0.12
        )
        intensity = overrides.scalar(
            binding.layer_id, "intensity", 0.7 if binding.role == "glow" else 0.3
        )
        color = overrides.vector(
            binding.layer_id, "color", _mix_color(base_color, 1.0, 0.25)
        )
        add(
            "radius",
            _float_parameter(
                path=f"{prefix}.radius",
                value=radius,
                minimum=0.001,
                maximum=0.5,
                block="layer",
                region=region,
                role="radius",
                unit="normalized",
                coordinate_space="shader_uv_bottom_left",
            ),
        )
        add(
            "intensity",
            _float_parameter(
                path=f"{prefix}.intensity",
                value=intensity,
                minimum=0.0,
                maximum=2.0,
                block="layer",
                region=region,
                role="intensity",
                unit="ratio",
            ),
        )
        add(
            "color",
            _color_parameter(
                f"{prefix}.color", color, region=region, role="glow_color"
            ),
        )
    elif binding.role in {"rim", "outline"}:
        kind = "rim_band" if binding.role == "rim" else "outline_band"
        width = overrides.scalar(binding.layer_id, "width", 0.018)
        softness = overrides.scalar(binding.layer_id, "softness", 0.006)
        color = overrides.vector(
            binding.layer_id, "color", _mix_color(base_color, 1.0, 0.35)
        )
        add(
            "width",
            _float_parameter(
                path=f"{prefix}.width",
                value=width,
                minimum=0.001,
                maximum=0.25,
                block="layer",
                region=region,
                role="width",
                unit="normalized",
                coordinate_space="shader_uv_bottom_left",
            ),
        )
        add(
            "softness",
            _float_parameter(
                path=f"{prefix}.softness",
                value=softness,
                minimum=0.0001,
                maximum=0.25,
                block="layer",
                region=region,
                role="softness",
                unit="normalized",
                coordinate_space="shader_uv_bottom_left",
            ),
        )
        if binding.role == "rim":
            intensity = overrides.scalar(binding.layer_id, "intensity", 0.8)
            add(
                "intensity",
                _float_parameter(
                    path=f"{prefix}.intensity",
                    value=intensity,
                    minimum=0.0,
                    maximum=2.0,
                    block="layer",
                    region=region,
                    role="intensity",
                    unit="ratio",
                ),
            )
        add(
            "color",
            _color_parameter(
                f"{prefix}.color", color, region=region, role=f"{binding.role}_color"
            ),
        )
    elif binding.role in {"highlight", "detail"}:
        kind = "arc_highlight"
        direction = overrides.vector(
            binding.layer_id, "direction", (-0.32328956686350335, 0.9463000876874145)
        )
        angular_width = overrides.scalar(
            binding.layer_id,
            "angular_width",
            1.2 if binding.role == "highlight" else 0.45,
        )
        thickness = overrides.scalar(
            binding.layer_id,
            "thickness",
            0.025 if binding.role == "highlight" else 0.012,
        )
        softness = overrides.scalar(binding.layer_id, "softness", 0.008)
        intensity = overrides.scalar(
            binding.layer_id, "intensity", 0.9 if binding.role == "highlight" else 0.55
        )
        color = overrides.vector(binding.layer_id, "color", (1.0, 1.0, 1.0, 0.85))
        for name, value, minimum, maximum, unit, cyclic in (
            ("angular_width", angular_width, 0.01, math.tau, "radians", False),
            ("thickness", thickness, 0.001, 0.25, "normalized", False),
            ("softness", softness, 0.0001, 0.25, "normalized", False),
            ("intensity", intensity, 0.0, 2.0, "ratio", False),
        ):
            add(
                name,
                _float_parameter(
                    path=f"{prefix}.{name}",
                    value=value,
                    minimum=minimum,
                    maximum=maximum,
                    block="layer",
                    region=region,
                    role=name,
                    unit=unit,
                    coordinate_space=(
                        "shader_uv_bottom_left" if unit == "normalized" else None
                    ),
                    cyclic=cyclic,
                ),
            )
        add(
            "direction",
            _vector_parameter(
                path=f"{prefix}.direction",
                value=direction,
                minimum=(-1.0, -1.0),
                maximum=(1.0, 1.0),
                block="layer",
                region=region,
                role="direction",
                unit="unit_vector",
                coordinate_space="shader_uv_bottom_left",
            ),
        )
        add(
            "color",
            _color_parameter(
                f"{prefix}.color", color, region=region, role=f"{binding.role}_color"
            ),
        )
    else:
        kind = "gaussian_color_lobe"
        lobe_center = overrides.vector(
            binding.layer_id, "center", (center[0] - 0.08, center[1] + 0.08)
        )
        sigma_default = 0.12 if binding.role == "haze" else 0.075
        sigma = overrides.vector(
            binding.layer_id, "sigma", (sigma_default, sigma_default)
        )
        intensity = overrides.scalar(binding.layer_id, "intensity", 0.45)
        color = overrides.vector(
            binding.layer_id, "color", _mix_color(base_color, 1.0, 0.3)
        )
        add(
            "center",
            _vector_parameter(
                path=f"{prefix}.center",
                value=lobe_center,
                minimum=(0.0, 0.0),
                maximum=(1.0, 1.0),
                block="layer",
                region=region,
                role="position",
                unit="normalized",
                coordinate_space="shader_uv_bottom_left",
            ),
        )
        add(
            "sigma",
            _vector_parameter(
                path=f"{prefix}.sigma",
                value=sigma,
                minimum=(0.001, 0.001),
                maximum=(0.5, 0.5),
                block="layer",
                region=region,
                role="sigma",
                unit="normalized",
                coordinate_space="shader_uv_bottom_left",
            ),
        )
        add(
            "color",
            _color_parameter(
                f"{prefix}.color", color, region=region, role="lobe_color"
            ),
        )
        add(
            "intensity",
            _float_parameter(
                path=f"{prefix}.intensity",
                value=intensity,
                minimum=0.0,
                maximum=2.0,
                block="layer",
                region=region,
                role="intensity",
                unit="ratio",
            ),
        )
    return (
        _node(
            kind,
            node_id=node_id,
            semantic_role=f"layer_{binding.role}",
            sibling_ordinal=0,
            bindings=tuple(bindings),
        ),
        tuple(values),
    )


def expand_seed_plan(intent: IntentIR, plan: SeedPlanV1) -> TypedEffectGenome:
    """验证 plan 闭包并确定性展开为一个合法 EffectGenome。."""
    match = _validate_plan(intent, plan)
    overrides = _Overrides(plan.parameter_overrides)
    base_binding = next(
        item for item in plan.layer_bindings if item.role == "base_fill"
    )
    geometry = _structural_geometry(
        intent, base_binding, match.geometry_kind, overrides
    )
    nodes: list[TypedEffectNode] = list(geometry.nodes)
    edges: list[TypedEffectEdge] = list(geometry.edges)
    parameters: list[ParameterSpec] = list(geometry.parameters)
    enabled = [item for item in plan.layer_bindings if item.enabled]
    enabled.sort(
        key=lambda item: (
            item.role != "background",
            item.role != "base_fill",
            item.layer_order,
        )
    )
    colors: list[str] = []
    base_color = _base_color(intent)
    center = tuple(float(value) for value in _primary_center(intent))
    for binding in enabled:
        layer_node, layer_parameters = _layer_node(
            binding,
            base_color=base_color,
            center=center,
            overrides=overrides,
        )
        nodes.append(layer_node)
        parameters.extend(layer_parameters)
        input_port = layer_node.inputs[0].name
        if layer_node.inputs[0].port_type == "mask":
            source_node_id = geometry.subject_mask_node_id
            source_port = geometry.subject_mask_port
            conversion: Literal["analytic_fixed_width_v1"] | None = (
                "analytic_fixed_width_v1" if geometry.subject_mask_is_sdf else None
            )
        else:
            source_node_id = geometry.primary_sdf_node_id
            source_port = "sdf"
            conversion = None
        edges.append(
            TypedEffectEdge(
                source_node_id=source_node_id,
                source_port=source_port,
                target_node_id=layer_node.node_id,
                target_port=input_port,
                sdf_to_mask_conversion=conversion,
            )
        )
        colors.append(layer_node.node_id)
    current = colors[0]
    for index, foreground in enumerate(colors[1:], start=1):
        blend_id = f"composite_{index:02d}"
        opacity_path = f"composite.{index:02d}.opacity"
        blend = _node(
            "over_blend",
            node_id=blend_id,
            semantic_role=f"composite_{index:02d}",
            sibling_ordinal=index - 1,
            bindings=(
                ParameterBinding(binding_name="opacity", parameter_path=opacity_path),
            ),
        )
        nodes.append(blend)
        parameters.append(
            _float_parameter(
                path=opacity_path,
                value=1.0,
                minimum=0.0,
                maximum=1.0,
                block="composite",
                region="canvas",
                role="opacity",
                unit="ratio",
            )
        )
        edges.extend(
            (
                TypedEffectEdge(
                    source_node_id=current,
                    source_port="color",
                    target_node_id=blend_id,
                    target_port="background",
                ),
                TypedEffectEdge(
                    source_node_id=foreground,
                    source_port="color",
                    target_node_id=blend_id,
                    target_port="foreground",
                ),
            )
        )
        current = blend_id
    output = _node(
        "color_output",
        node_id="output_color",
        semantic_role="output",
        sibling_ordinal=0,
    )
    nodes.append(output)
    edges.append(
        TypedEffectEdge(
            source_node_id=current,
            source_port="color",
            target_node_id=output.node_id,
            target_port="color",
        )
    )
    overrides.assert_all_consumed()
    return TypedEffectGenome(
        genome_id=f"genome_{canonical_sha256(plan)[:24]}",
        contract_id=intent.canvas.contract_id,
        strategy=plan.template_id,
        nodes=tuple(nodes),
        edges=tuple(edges),
        parameters=tuple(parameters),
        output_node_id=output.node_id,
        provenance=GenomeProvenance(
            source=plan.source,
            intent_id=plan.intent_id,
            target_hypothesis_id=plan.target_hypothesis_id,
            target_hypothesis_hash=plan.target_hypothesis_hash,
            template_id=plan.template_id,
            template_version=plan.template_version,
            random_seed=plan.random_seed,
            evidence_refs=plan.evidence_refs,
        ),
    )


def _primary_center(intent: IntentIR) -> tuple[float, float]:
    subject = min(intent.objects, key=lambda item: (-item.area_ratio, item.object_id))
    return (float(subject.center_uv[0]), float(subject.center_uv[1]))


def assess_seed_diversity(
    plans: Sequence[SeedPlanV1],
    genome_hashes: Sequence[GenomeHashes],
) -> SeedDiversityAssessmentV1:
    """评估真实 semantic hash 与结构签名；失败时返回显式 exception。."""
    if len(plans) != 3 or len(genome_hashes) != 3:
        raise ValueError("Seed diversity 只接受恰好三个 plan/genome。")
    semantic_hashes = tuple(item.semantic_genome_hash for item in genome_hashes)
    structural = {
        (
            plan.template_id,
            hashes.topology_hash,
            tuple(item.layer_id for item in plan.layer_bindings if item.enabled),
        )
        for plan, hashes in zip(plans, genome_hashes, strict=True)
    }
    semantic_ok = len(set(semantic_hashes)) == 3
    structural_ok = len(structural) >= 2
    if semantic_ok and structural_ok:
        exception: DiversityException | None = None
    elif not semantic_ok and not structural_ok:
        exception = "semantic_and_structural_diversity_missing"
    elif not semantic_ok:
        exception = "semantic_genome_hash_not_unique"
    else:
        exception = "no_template_topology_or_enabled_layer_difference"
    return SeedDiversityAssessmentV1(
        gate_passed=semantic_ok and structural_ok,
        semantic_genome_hashes=(
            semantic_hashes[0],
            semantic_hashes[1],
            semantic_hashes[2],
        ),
        distinct_structural_signatures=len(structural),
        diversity_exception=exception,
    )


def expand_seed_plans(
    intent: IntentIR,
    *,
    plans: tuple[SeedPlanV1, SeedPlanV1, SeedPlanV1] | None = None,
    random_seed: int = 0,
) -> SeedExpansionResultV2:
    """展开冻结顺序的三计划，并生成不可绕过的 diversity gate。."""
    selected = (
        plans
        if plans is not None
        else build_seed_plans(intent, random_seed=random_seed)
    )
    genomes = tuple(expand_seed_plan(intent, plan) for plan in selected)
    hashes = tuple(compute_genome_hashes(genome) for genome in genomes)
    diversity = assess_seed_diversity(selected, hashes)
    return SeedExpansionResultV2(
        expanded_seeds=(
            ExpandedSeedV1(
                plan=selected[0], genome=genomes[0], genome_hashes=hashes[0]
            ),
            ExpandedSeedV1(
                plan=selected[1], genome=genomes[1], genome_hashes=hashes[1]
            ),
            ExpandedSeedV1(
                plan=selected[2], genome=genomes[2], genome_hashes=hashes[2]
            ),
        ),
        diversity=diversity,
    )


__all__ = ["assess_seed_diversity", "expand_seed_plan", "expand_seed_plans"]
