from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from shaderforge.genome import (
    CircleSDFNode,
    ColorOutputNode,
    SolidFillNode,
    TypedEffectEdge,
    TypedEffectGenome,
    TypedEffectNode,
    UnionMaskNode,
    binding_contracts_for_kind,
    bindings,
    compute_genome_hashes,
)
from shaderforge.genome.models import GenomeProvenance, ParameterSpec

_ALL_BINDINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("circle_sdf", ("center", "radius")),
    ("ellipse_sdf", ("center", "radii", "rotation")),
    ("rounded_rect_sdf", ("center", "half_size", "corner_radius", "rotation")),
    ("solid_fill", ("color",)),
    ("linear_gradient", ("start", "end", "start_color", "end_color")),
    ("gaussian_color_lobe", ("center", "sigma", "color", "intensity")),
    ("shadow", ("offset", "blur", "spread", "color")),
    ("glow", ("radius", "intensity", "color")),
    ("rim_band", ("width", "softness", "intensity", "color")),
    ("outline_band", ("width", "softness", "color")),
    (
        "arc_highlight",
        ("direction", "angular_width", "thickness", "softness", "intensity", "color"),
    ),
    ("union_mask", ()),
    ("intersection_mask", ()),
    ("difference_mask", ()),
    ("over_blend", ("opacity",)),
    ("color_output", ()),
)


def _parameter(
    path: str,
    *,
    dtype: str,
    value: object,
    unit: str,
    coordinate_space: str | None = None,
    color_space: str | None = None,
    optimizable: bool = True,
) -> ParameterSpec:
    return ParameterSpec.model_validate(
        {
            "path": path,
            "dtype": dtype,
            "value": value,
            "min_value": None,
            "max_value": None,
            "optimizable": optimizable,
            "block": "subject",
            "affected_regions": ("subject",),
            "semantic_role": path,
            "unit": unit,
            "coordinate_space": coordinate_space,
            "color_space": color_space,
            "cyclic": False,
            "quantization": None,
        }
    )


def _circle_parameters(prefix: str = "shape") -> tuple[ParameterSpec, ...]:
    return (
        _parameter(
            f"{prefix}.center",
            dtype="vec2",
            value=(0.5, 0.5),
            unit="normalized",
            coordinate_space="shader_uv_bottom_left",
        ),
        _parameter(
            f"{prefix}.radius",
            dtype="float",
            value=0.3,
            unit="normalized",
            coordinate_space="shader_uv_bottom_left",
        ),
    )


def _typed_circle_genome() -> TypedEffectGenome:
    parameters = (
        *_circle_parameters(),
        _parameter(
            "fill.color",
            dtype="vec4",
            value=(0.8, 0.2, 0.4, 1.0),
            unit="rgba",
            color_space="linear_rgb",
        ),
    )
    return TypedEffectGenome(
        genome_id="typed-circle",
        contract_id="webgl1-no-texture-v1",
        strategy="typed-circle",
        nodes=(
            CircleSDFNode(
                node_id="shape",
                semantic_role="subject_shape",
                sibling_ordinal=0,
                parameter_bindings=bindings(
                    center="shape.center", radius="shape.radius"
                ),
            ),
            SolidFillNode(
                node_id="fill",
                semantic_role="base_fill",
                sibling_ordinal=0,
                parameter_bindings=bindings(color="fill.color"),
            ),
            ColorOutputNode(
                node_id="output",
                semantic_role="output",
                sibling_ordinal=0,
            ),
        ),
        edges=(
            TypedEffectEdge(
                source_node_id="shape",
                source_port="sdf",
                target_node_id="fill",
                target_port="mask",
                sdf_to_mask_conversion="analytic_fixed_width_v1",
            ),
            TypedEffectEdge(
                source_node_id="fill",
                source_port="color",
                target_node_id="output",
                target_port="color",
            ),
        ),
        parameters=parameters,
        output_node_id="output",
        provenance=GenomeProvenance(
            source="rule",
            intent_id="intent-1",
            target_hypothesis_id="hypothesis-1",
            target_hypothesis_hash="a" * 64,
            template_id="typed-circle",
            template_version="1",
            random_seed=7,
        ),
    )


def test_typed_genome_is_a_discriminated_sealed_union() -> None:
    genome = _typed_circle_genome()
    restored = TypedEffectGenome.model_validate_json(
        genome.model_dump_json(), strict=True
    )

    assert isinstance(restored.nodes[0], CircleSDFNode)
    assert isinstance(restored.nodes[1], SolidFillNode)
    assert restored.nodes[0].distance_semantics == "euclidean_negative_inside_v1"

    raw = genome.model_dump(mode="json")
    raw["nodes"][0]["kind"] = "not_a_kind"
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        TypedEffectGenome.model_validate_json(json.dumps(raw), strict=True)


@pytest.mark.parametrize(("kind", "binding_names"), _ALL_BINDINGS)
def test_all_sixteen_node_kinds_have_sealed_binding_payloads(
    kind: str, binding_names: tuple[str, ...]
) -> None:
    adapter: TypeAdapter[TypedEffectNode] = TypeAdapter(TypedEffectNode)
    raw = {
        "node_id": f"node-{kind}",
        "kind": kind,
        "semantic_role": kind,
        "sibling_ordinal": 0,
        "parameter_bindings": [
            {"binding_name": name, "parameter_path": f"{kind}.{name}"}
            for name in binding_names
        ],
    }
    node = adapter.validate_json(json.dumps(raw), strict=True)

    assert node.kind == kind
    assert set(binding_contracts_for_kind(node.kind)) == set(binding_names)


def test_typed_node_requires_exact_parameter_binding_payload() -> None:
    with pytest.raises(ValidationError, match="sealed payload"):
        CircleSDFNode(
            node_id="shape",
            semantic_role="shape",
            sibling_ordinal=0,
            parameter_bindings=bindings(center="shape.center"),
        )

    with pytest.raises(ValidationError, match="sealed payload"):
        CircleSDFNode(
            node_id="shape",
            semantic_role="shape",
            sibling_ordinal=0,
            parameter_bindings=bindings(
                center="shape.center",
                radius="shape.radius",
                surprise="shape.surprise",
            ),
        )


def test_typed_binding_contract_rejects_wrong_dtype_unit_and_space() -> None:
    genome = _typed_circle_genome()
    raw = genome.model_dump(mode="json")
    radius = next(item for item in raw["parameters"] if item["path"] == "shape.radius")
    radius.update(dtype="int", value=1, unit="pixels", coordinate_space="screen_px")

    with pytest.raises(ValidationError, match="circle_sdf.radius.*dtype"):
        TypedEffectGenome.model_validate_json(json.dumps(raw), strict=True)


def test_typed_binding_contract_uses_frozen_intent_spaces() -> None:
    raw = _typed_circle_genome().model_dump(mode="json")
    center = next(item for item in raw["parameters"] if item["path"] == "shape.center")
    center["coordinate_space"] = "reference_uv"
    with pytest.raises(ValidationError, match="shader_uv_bottom_left|coordinate_space"):
        TypedEffectGenome.model_validate_json(json.dumps(raw), strict=True)

    raw = _typed_circle_genome().model_dump(mode="json")
    color = next(item for item in raw["parameters"] if item["path"] == "fill.color")
    color["color_space"] = "linear_srgb"
    with pytest.raises(ValidationError, match="linear_rgb|color_space"):
        TypedEffectGenome.model_validate_json(json.dumps(raw), strict=True)


def test_sdf_to_mask_requires_explicit_analytic_aa_and_only_there() -> None:
    raw = _typed_circle_genome().model_dump(mode="json")
    raw["edges"][0]["sdf_to_mask_conversion"] = None
    with pytest.raises(ValidationError, match="SDF→mask.*AA"):
        TypedEffectGenome.model_validate_json(json.dumps(raw), strict=True)

    raw = _typed_circle_genome().model_dump(mode="json")
    raw["edges"][1]["sdf_to_mask_conversion"] = "analytic_fixed_width_v1"
    with pytest.raises(ValidationError, match="只有 SDF→mask"):
        TypedEffectGenome.model_validate_json(json.dumps(raw), strict=True)


def test_mask_algebra_has_frozen_coverage_semantics_and_closed_ports() -> None:
    node = UnionMaskNode(
        node_id="union",
        semantic_role="subject_union",
        sibling_ordinal=0,
    )
    assert node.operation == "coverage_max_v1"
    assert tuple((port.name, port.port_type) for port in node.inputs) == (
        ("left", "mask"),
        ("right", "mask"),
    )

    with pytest.raises(ValidationError, match="registry"):
        UnionMaskNode(
            node_id="union",
            semantic_role="subject_union",
            sibling_ordinal=0,
            inputs=(),
        )


def test_typed_genome_rejects_unbound_parameters() -> None:
    genome = _typed_circle_genome()
    raw = genome.model_dump(mode="json")
    raw["parameters"].append(
        _parameter(
            "unused.opacity", dtype="float", value=1.0, unit="ratio"
        ).model_dump(mode="json")
    )

    with pytest.raises(ValidationError, match="未绑定参数"):
        TypedEffectGenome.model_validate_json(json.dumps(raw), strict=True)


def test_typed_four_hashes_keep_layer_boundaries() -> None:
    genome = _typed_circle_genome()
    baseline = compute_genome_hashes(genome)

    changed_value = genome.model_copy(
        update={
            "parameters": tuple(
                parameter.model_copy(update={"value": 0.31})
                if parameter.path == "shape.radius"
                else parameter
                for parameter in genome.parameters
            )
        }
    )
    value_hashes = compute_genome_hashes(changed_value)
    assert value_hashes.topology_hash == baseline.topology_hash
    assert value_hashes.parameter_layout_hash == baseline.parameter_layout_hash
    assert value_hashes.semantic_genome_hash != baseline.semantic_genome_hash

    changed_layout = genome.model_copy(
        update={
            "parameters": tuple(
                parameter.model_copy(update={"optimizable": False})
                if parameter.path == "shape.radius"
                else parameter
                for parameter in genome.parameters
            )
        }
    )
    layout_hashes = compute_genome_hashes(changed_layout)
    assert layout_hashes.topology_hash == baseline.topology_hash
    assert layout_hashes.parameter_layout_hash != baseline.parameter_layout_hash
    assert layout_hashes.semantic_genome_hash != baseline.semantic_genome_hash

    provenance_hashes = compute_genome_hashes(
        genome.model_copy(
            update={
                "provenance": genome.provenance.model_copy(
                    update={"random_seed": genome.provenance.random_seed + 1}
                )
            }
        )
    )
    assert provenance_hashes.topology_hash == baseline.topology_hash
    assert provenance_hashes.parameter_layout_hash == baseline.parameter_layout_hash
    assert provenance_hashes.semantic_genome_hash == baseline.semantic_genome_hash
    assert provenance_hashes.record_hash != baseline.record_hash


def test_typed_topology_hash_is_record_id_and_order_independent() -> None:
    genome = _typed_circle_genome()
    baseline = compute_genome_hashes(genome)
    id_map = {"shape": "g", "fill": "f", "output": "o"}
    reordered = genome.model_copy(
        update={
            "nodes": tuple(
                node.model_copy(update={"node_id": id_map[node.node_id]})
                for node in reversed(genome.nodes)
            ),
            "edges": tuple(
                edge.model_copy(
                    update={
                        "source_node_id": id_map[edge.source_node_id],
                        "target_node_id": id_map[edge.target_node_id],
                    }
                )
                for edge in reversed(genome.edges)
            ),
            "parameters": tuple(reversed(genome.parameters)),
            "output_node_id": "o",
        }
    )
    reordered_hashes = compute_genome_hashes(reordered)

    assert reordered_hashes.topology_hash == baseline.topology_hash
    assert reordered_hashes.parameter_layout_hash == baseline.parameter_layout_hash
    assert reordered_hashes.semantic_genome_hash == baseline.semantic_genome_hash
    assert reordered_hashes.record_hash != baseline.record_hash
