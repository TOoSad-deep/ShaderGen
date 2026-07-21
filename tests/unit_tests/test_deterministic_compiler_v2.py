from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Literal

import pytest

from shaderforge.compiler import (
    CompilerAst,
    CompilerDefectError,
    CompilerParameterTable,
    NodeLineSourceMap,
    compile_effect_genome,
    materialize_compilation,
)
from shaderforge.genome import (
    ArcHighlightNode,
    CircleSDFNode,
    ColorOutputNode,
    DifferenceMaskNode,
    EllipseSDFNode,
    GaussianColorLobeNode,
    GenomeProvenance,
    GlowNode,
    IntersectionMaskNode,
    LinearGradientNode,
    OutlineBandNode,
    OverBlendNode,
    ParameterSpec,
    RimBandNode,
    RoundedRectSDFNode,
    ShadowNode,
    SolidFillNode,
    TypedEffectEdge,
    TypedEffectGenome,
    UnionMaskNode,
    bindings,
    compute_semantic_genome_hash,
)
from shaderforge.store import LocalArtifactCatalog, LocalArtifactStore
from shaderforge.validation import validate_shader


def _parameter(
    path: str,
    dtype: Literal["float", "int", "bool", "vec2", "vec3", "vec4"],
    value: bool | int | float | tuple[float, ...],
    *,
    unit: str,
    coordinate_space: str | None = None,
    color_space: str | None = None,
) -> ParameterSpec:
    minimum: bool | int | float | tuple[float, ...] | None
    maximum: bool | int | float | tuple[float, ...] | None
    if dtype == "float":
        minimum, maximum = 0.0, 2.0
    elif dtype.startswith("vec"):
        length = int(dtype[-1])
        minimum = tuple(-2.0 for _ in range(length))
        maximum = tuple(2.0 for _ in range(length))
    else:  # pragma: no cover - 当前 Compiler fixture 只使用 float/vector
        minimum = maximum = None
    return ParameterSpec(
        path=path,
        dtype=dtype,
        value=value,
        min_value=minimum,
        max_value=maximum,
        optimizable=True,
        block="compiler-test",
        affected_regions=("subject",),
        semantic_role=path,
        unit=unit,
        coordinate_space=coordinate_space,
        color_space=color_space,
        cyclic=False,
        quantization=0.0001,
    )


def _p_uv(path: str, value: float) -> ParameterSpec:
    return _parameter(
        path,
        "float",
        value,
        unit="normalized",
        coordinate_space="shader_uv_bottom_left",
    )


def _p_uv2(path: str, value: tuple[float, float]) -> ParameterSpec:
    return _parameter(
        path,
        "vec2",
        value,
        unit="normalized",
        coordinate_space="shader_uv_bottom_left",
    )


def _p_color(path: str, value: tuple[float, float, float, float]) -> ParameterSpec:
    return _parameter(
        path,
        "vec4",
        value,
        unit="rgba",
        color_space="linear_rgb",
    )


def _p_ratio(path: str, value: float) -> ParameterSpec:
    return _parameter(path, "float", value, unit="ratio")


def _p_angle(path: str, value: float) -> ParameterSpec:
    return _parameter(path, "float", value, unit="radians")


def _p_direction(path: str, value: tuple[float, float]) -> ParameterSpec:
    return _parameter(
        path,
        "vec2",
        value,
        unit="unit_vector",
        coordinate_space="shader_uv_bottom_left",
    )


def _all_node_genome() -> TypedEffectGenome:
    parameters = (
        _p_uv2("circle.center", (0.5, 0.5)),
        _p_uv("circle.radius", 0.32),
        _p_uv2("ellipse.center", (0.48, 0.52)),
        _p_uv2("ellipse.radii", (0.34, 0.24)),
        _p_angle("ellipse.rotation", 0.2),
        _p_uv2("rect.center", (0.5, 0.5)),
        _p_uv2("rect.half_size", (0.36, 0.28)),
        _p_uv("rect.corner_radius", 0.08),
        _p_angle("rect.rotation", 0.1),
        _p_color("fill.color", (0.2, 0.4, 0.9, 1.0)),
        _p_uv2("gradient.start", (0.2, 0.2)),
        _p_uv2("gradient.end", (0.8, 0.8)),
        _p_color("gradient.start_color", (0.1, 0.2, 0.8, 1.0)),
        _p_color("gradient.end_color", (0.8, 0.2, 0.4, 1.0)),
        _p_uv2("lobe.center", (0.55, 0.45)),
        _p_uv2("lobe.sigma", (0.2, 0.14)),
        _p_color("lobe.color", (1.0, 0.4, 0.2, 0.7)),
        _p_ratio("lobe.intensity", 0.8),
        _p_uv2("shadow.offset", (0.02, 0.03)),
        _p_uv("shadow.blur", 0.08),
        _p_uv("shadow.spread", 0.01),
        _p_color("shadow.color", (0.0, 0.0, 0.0, 0.5)),
        _p_uv("glow.radius", 0.1),
        _p_ratio("glow.intensity", 0.7),
        _p_color("glow.color", (0.2, 0.6, 1.0, 0.6)),
        _p_uv("rim.width", 0.04),
        _p_uv("rim.softness", 0.01),
        _p_ratio("rim.intensity", 0.9),
        _p_color("rim.color", (0.7, 0.9, 1.0, 0.8)),
        _p_uv("outline.width", 0.025),
        _p_uv("outline.softness", 0.008),
        _p_color("outline.color", (0.05, 0.08, 0.2, 1.0)),
        _p_direction("arc.direction", (0.0, 1.0)),
        _p_angle("arc.angular_width", 0.8),
        _p_uv("arc.thickness", 0.04),
        _p_uv("arc.softness", 0.02),
        _p_ratio("arc.intensity", 0.8),
        _p_color("arc.color", (1.0, 1.0, 1.0, 0.8)),
        *(_p_ratio(f"blend.{index}.opacity", 0.85) for index in range(7)),
    )
    nodes = [
        CircleSDFNode(
            node_id="circle-record",
            semantic_role="geometry_circle",
            sibling_ordinal=0,
            parameter_bindings=bindings(
                center="circle.center",
                radius="circle.radius",
            ),
        ),
        EllipseSDFNode(
            node_id="ellipse-record",
            semantic_role="geometry_ellipse",
            sibling_ordinal=0,
            parameter_bindings=bindings(
                center="ellipse.center",
                radii="ellipse.radii",
                rotation="ellipse.rotation",
            ),
        ),
        RoundedRectSDFNode(
            node_id="rect-record",
            semantic_role="geometry_rect",
            sibling_ordinal=0,
            parameter_bindings=bindings(
                center="rect.center",
                half_size="rect.half_size",
                corner_radius="rect.corner_radius",
                rotation="rect.rotation",
            ),
        ),
        UnionMaskNode(
            node_id="union-record",
            semantic_role="mask_union",
            sibling_ordinal=0,
        ),
        IntersectionMaskNode(
            node_id="intersection-record",
            semantic_role="mask_intersection",
            sibling_ordinal=0,
        ),
        DifferenceMaskNode(
            node_id="difference-record",
            semantic_role="mask_difference",
            sibling_ordinal=0,
        ),
        SolidFillNode(
            node_id="fill-record",
            semantic_role="color_fill",
            sibling_ordinal=0,
            parameter_bindings=bindings(color="fill.color"),
        ),
        LinearGradientNode(
            node_id="gradient-record",
            semantic_role="color_gradient",
            sibling_ordinal=0,
            parameter_bindings=bindings(
                start="gradient.start",
                end="gradient.end",
                start_color="gradient.start_color",
                end_color="gradient.end_color",
            ),
        ),
        GaussianColorLobeNode(
            node_id="lobe-record",
            semantic_role="color_lobe",
            sibling_ordinal=0,
            parameter_bindings=bindings(
                center="lobe.center",
                sigma="lobe.sigma",
                color="lobe.color",
                intensity="lobe.intensity",
            ),
        ),
        ShadowNode(
            node_id="shadow-record",
            semantic_role="shadow",
            sibling_ordinal=0,
            parameter_bindings=bindings(
                offset="shadow.offset",
                blur="shadow.blur",
                spread="shadow.spread",
                color="shadow.color",
            ),
        ),
        GlowNode(
            node_id="glow-record",
            semantic_role="glow",
            sibling_ordinal=0,
            parameter_bindings=bindings(
                radius="glow.radius",
                intensity="glow.intensity",
                color="glow.color",
            ),
        ),
        RimBandNode(
            node_id="rim-record",
            semantic_role="rim",
            sibling_ordinal=0,
            parameter_bindings=bindings(
                width="rim.width",
                softness="rim.softness",
                intensity="rim.intensity",
                color="rim.color",
            ),
        ),
        OutlineBandNode(
            node_id="outline-record",
            semantic_role="outline",
            sibling_ordinal=0,
            parameter_bindings=bindings(
                width="outline.width",
                softness="outline.softness",
                color="outline.color",
            ),
        ),
        ArcHighlightNode(
            node_id="arc-record",
            semantic_role="highlight",
            sibling_ordinal=0,
            parameter_bindings=bindings(
                direction="arc.direction",
                angular_width="arc.angular_width",
                thickness="arc.thickness",
                softness="arc.softness",
                intensity="arc.intensity",
                color="arc.color",
            ),
        ),
    ]
    color_ids = [
        "fill-record",
        "gradient-record",
        "lobe-record",
        "shadow-record",
        "glow-record",
        "rim-record",
        "outline-record",
        "arc-record",
    ]
    for index in range(7):
        nodes.append(
            OverBlendNode(
                node_id=f"blend-record-{index}",
                semantic_role="blend",
                sibling_ordinal=index,
                parameter_bindings=bindings(opacity=f"blend.{index}.opacity"),
            )
        )
    nodes.append(
        ColorOutputNode(
            node_id="output-record",
            semantic_role="output",
            sibling_ordinal=0,
        )
    )

    def edge(
        source: str,
        target: str,
        target_port: str,
        *,
        source_port: str,
        convert: bool = False,
    ) -> TypedEffectEdge:
        return TypedEffectEdge(
            source_node_id=source,
            source_port=source_port,
            target_node_id=target,
            target_port=target_port,
            sdf_to_mask_conversion=("analytic_fixed_width_v1" if convert else None),
        )

    edges = [
        edge("circle-record", "union-record", "left", source_port="sdf", convert=True),
        edge("ellipse-record", "union-record", "right", source_port="sdf", convert=True),
        edge("union-record", "intersection-record", "left", source_port="mask"),
        edge("rect-record", "intersection-record", "right", source_port="sdf", convert=True),
        edge("intersection-record", "difference-record", "left", source_port="mask"),
        edge("circle-record", "difference-record", "right", source_port="sdf", convert=True),
        edge("circle-record", "fill-record", "mask", source_port="sdf", convert=True),
        edge("ellipse-record", "gradient-record", "mask", source_port="sdf", convert=True),
        edge("difference-record", "lobe-record", "mask", source_port="mask"),
        edge("circle-record", "shadow-record", "sdf", source_port="sdf"),
        edge("circle-record", "glow-record", "sdf", source_port="sdf"),
        edge("circle-record", "rim-record", "sdf", source_port="sdf"),
        edge("circle-record", "outline-record", "sdf", source_port="sdf"),
        edge("circle-record", "arc-record", "sdf", source_port="sdf"),
    ]
    previous = color_ids[0]
    for index, foreground in enumerate(color_ids[1:]):
        blend = f"blend-record-{index}"
        edges.extend(
            (
                edge(previous, blend, "background", source_port="color"),
                edge(foreground, blend, "foreground", source_port="color"),
            )
        )
        previous = blend
    edges.append(edge(previous, "output-record", "color", source_port="color"))
    return TypedEffectGenome(
        genome_id="compiler-all-nodes",
        contract_id="webgl1_static_no_texture_v1",
        strategy="all-node-conformance",
        nodes=tuple(nodes),  # type: ignore[arg-type]
        edges=tuple(edges),
        parameters=parameters,
        output_node_id="output-record",
        provenance=GenomeProvenance(
            source="rule",
            intent_id="intent-compiler",
            target_hypothesis_id="hypothesis-compiler",
            target_hypothesis_hash="a" * 64,
            template_id="all-node-template",
            template_version="1",
            random_seed=7,
        ),
    )


def test_compiler_covers_every_node_kind_and_emits_valid_deterministic_glsl() -> None:
    genome = _all_node_genome()

    first = compile_effect_genome(genome)
    second = compile_effect_genome(genome)

    assert first == second
    assert first.glsl_source.encode("utf-8") == second.glsl_source.encode("utf-8")
    assert first.glsl_sha256 == sha256(first.glsl_source.encode()).hexdigest()
    assert first.semantic_genome_hash == compute_semantic_genome_hash(genome)
    assert validate_shader(first.glsl_source).valid
    assert {node.kind for node in first.ast.nodes} == {
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
    }
    assert len(first.node_line_map.entries) == len(genome.nodes)
    assert len(first.compiler_parameter_table.entries) == len(genome.parameters)
    assert first.diagnostics == ("deterministic_compile_succeeded",)


def test_compiler_accepts_typed_genome_with_frozen_artifact_provenance(
    tmp_path: Path,
) -> None:
    genome = _all_node_genome()
    evidence_ref = LocalArtifactCatalog(
        LocalArtifactStore(tmp_path).start_run(
            "compiler-project", "compiler-run"
        ),
        run_id="compiler-run",
    ).put(
        run_id="compiler-run",
        kind="seed_evidence",
        schema_version="seed_evidence_v1",
        content_type="application/json",
        data=b"{}",
    )
    typed = genome.model_copy(
        update={
            "provenance": genome.provenance.model_copy(
                update={"evidence_refs": (evidence_ref,)}
            )
        }
    )

    assert compile_effect_genome(typed).semantic_genome_hash == (
        compute_semantic_genome_hash(typed)
    )


def test_compiler_output_ignores_record_node_ids_and_collection_order() -> None:
    genome = _all_node_genome()
    id_map = {node.node_id: f"renamed-{index}" for index, node in enumerate(genome.nodes)}
    renamed = TypedEffectGenome.model_validate(
        genome.model_dump(mode="python")
        | {
            "nodes": tuple(
                node.model_copy(update={"node_id": id_map[node.node_id]}).model_dump(
                    mode="python"
                )
                for node in reversed(genome.nodes)
            ),
            "edges": tuple(
                edge.model_copy(
                    update={
                        "source_node_id": id_map[edge.source_node_id],
                        "target_node_id": id_map[edge.target_node_id],
                    }
                ).model_dump(mode="python")
                for edge in reversed(genome.edges)
            ),
            "parameters": tuple(
                item.model_dump(mode="python") for item in reversed(genome.parameters)
            ),
            "output_node_id": id_map[genome.output_node_id],
        },
        strict=True,
    )

    baseline = compile_effect_genome(genome)
    reordered = compile_effect_genome(renamed)

    assert reordered.semantic_genome_hash == baseline.semantic_genome_hash
    assert reordered.glsl_source == baseline.glsl_source
    assert reordered.glsl_sha256 == baseline.glsl_sha256
    assert reordered.node_line_map == baseline.node_line_map


def test_compiler_rejects_generic_or_unsupported_payload_before_render() -> None:
    genome = _all_node_genome()
    circle = genome.nodes[0].model_copy(update={"parameter_bindings": ()})
    generic = genome.model_copy(update={"nodes": (circle, *genome.nodes[1:])})

    with pytest.raises(CompilerDefectError) as captured:
        compile_effect_genome(generic)

    assert captured.value.code == "invalid_or_unsupported_genome"
    assert str(captured.value).startswith("compiler_defect:")


def test_compilation_bundle_materializes_typed_content_addressed_artifacts(
    tmp_path: Path,
) -> None:
    product = compile_effect_genome(_all_node_genome())
    run = LocalArtifactStore(tmp_path).start_run("compiler-project", "compiler-run")
    catalog = LocalArtifactCatalog(run, run_id="compiler-run")

    bundle = materialize_compilation(
        product,
        catalog=catalog,
        run_id="compiler-run",
    )
    recovered = LocalArtifactCatalog(run, run_id="compiler-run")

    assert recovered.read_bytes(bundle.glsl_ref.artifact_id) == product.glsl_source.encode()
    assert bundle.glsl_sha256 == product.glsl_sha256
    assert bundle.semantic_genome_hash == product.semantic_genome_hash
    assert recovered.resolve(bundle.ast_ref.artifact_id) == bundle.ast_ref
    assert recovered.resolve(bundle.node_line_map_ref.artifact_id) == bundle.node_line_map_ref
    assert (
        recovered.resolve(bundle.compiler_parameter_table_ref.artifact_id)
        == bundle.compiler_parameter_table_ref
    )
    assert CompilerAst.model_validate_json(
        recovered.read_bytes(bundle.ast_ref.artifact_id), strict=True
    ) == product.ast
    assert NodeLineSourceMap.model_validate_json(
        recovered.read_bytes(bundle.node_line_map_ref.artifact_id), strict=True
    ) == product.node_line_map
    assert CompilerParameterTable.model_validate_json(
        recovered.read_bytes(bundle.compiler_parameter_table_ref.artifact_id),
        strict=True,
    ) == product.compiler_parameter_table
