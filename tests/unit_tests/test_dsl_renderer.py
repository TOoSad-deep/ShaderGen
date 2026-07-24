from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from shaderforge.dsl import (
    MAX_CUSTOM_FRAGMENT_UNIFORM_VEC4,
    MAX_LAYERS,
    MAX_PRIMITIVES_PER_LAYER,
    MAX_TOTAL_PRIMITIVES,
    MIN_LINEAR_SPAN,
    MIN_POSITIVE_VALUE,
    MIN_SEGMENT_LENGTH,
    ManifestEntry,
    canonical_json,
    compile_dsl_shader,
    document_sha256,
    layer_fill_block,
    layer_geometry_block,
    pack_active_uniforms,
    parameter_manifest,
    parameter_manifest_sha256,
    parse_dsl_document,
    topology_sha256,
)
from shaderforge.validation import validate_shader


def _canvas() -> dict:
    return {
        "width": 192,
        "height": 192,
        "background": [1.0, 1.0, 1.0, 1.0],
        "color_space": "srgb_encoded_v1",
        "output_alpha": "opaque",
    }


def _layer(layer_id: str, shape: dict, **overrides) -> dict:
    layer = {
        "id": layer_id,
        "visible": True,
        "opacity": 1.0,
        "shape": shape,
        "fill": {"kind": "solid", "color": [1.0, 0.4, 0.6, 1.0]},
        "effects": [],
    }
    layer.update(overrides)
    return layer


def _document(*layers: dict) -> dict:
    return {
        "schema_version": "shader_graph_v1",
        "canvas": _canvas(),
        "layers": list(layers),
    }


def _circle(node_id: str, **overrides) -> dict:
    node = {"id": node_id, "kind": "circle", "radius": 0.5}
    node.update(overrides)
    return node


def _rich_document() -> dict:
    """覆盖全部节点类型、transform、fill、effect 与 CSG 的合法文档."""
    ring = {
        "id": "ring",
        "kind": "subtract",
        "base": _circle("outer", radius=0.6),
        "cut": _circle(
            "inner",
            radius=0.35,
            transform={
                "translate": [0.1, 0.0],
                "scale": [1.0, 0.5],
                "rotation": [0.0, 1.0],
            },
        ),
    }
    badge = {
        "id": "badge",
        "kind": "union",
        "left": {
            "id": "badge_box",
            "kind": "rounded_box",
            "half_size": [0.4, 0.25],
            "corner_radius": 0.08,
        },
        "right": {
            "id": "badge_stem",
            "kind": "segment",
            "from": [-0.3, -0.2],
            "to": [0.3, 0.2],
            "radius": 0.05,
        },
        "transform": {"translate": [0.0, -0.1], "scale": [0.8, 1.2]},
    }
    clip = {
        "id": "clip",
        "kind": "intersect",
        "left": _circle("clip_circle", radius=0.5),
        "right": {
            "id": "clip_ellipse",
            "kind": "ellipse",
            "radii": [0.5, 0.3],
        },
    }
    layers = [
        _layer(
            "back",
            ring,
            fill={
                "kind": "linear",
                "from": [-0.5, 0.0],
                "to": [0.5, 0.0],
                "start_color": [1.0, 0.2, 0.4, 1.0],
                "end_color": [1.0, 0.9, 0.95, 1.0],
                "spread": "clamp",
            },
            effects=[
                {
                    "kind": "rim",
                    "width": 0.05,
                    "softness": 0.01,
                    "color": [1.0, 1.0, 1.0, 0.8],
                },
                {
                    "kind": "shadow",
                    "offset": [0.05, -0.05],
                    "blur": 0.04,
                    "spread": 0.01,
                    "color": [0.0, 0.0, 0.0, 0.5],
                },
            ],
        ),
        _layer(
            "mid",
            badge,
            fill={
                "kind": "radial",
                "center": [-0.2, 0.3],
                "radius": 0.8,
                "inner_color": [1.0, 0.9, 0.95, 1.0],
                "outer_color": [1.0, 0.2, 0.4, 1.0],
                "spread": "clamp",
            },
            effects=[
                {
                    "kind": "glow",
                    "radius": 0.2,
                    "softness": 0.05,
                    "color": [1.0, 0.8, 0.2, 0.6],
                },
            ],
        ),
        _layer("front", clip, opacity=0.5),
    ]
    return _document(*layers)


def test_all_node_kinds_compile_and_pass_static_validation() -> None:
    document = parse_dsl_document(_rich_document())
    compiled = compile_dsl_shader(document)

    validation = validate_shader(compiled.fragment_source)
    assert validation.valid, validation.violations
    assert compiled.dsl_schema_version == "shader_graph_v1"
    assert compiled.compiler_version
    assert compiled.render_contract_id == "webgl1_static_no_texture_v1"
    assert compiled.uniform_values == {}
    assert compiled.uniform_schema == {}
    summary = compiled.resource_summary
    assert summary.layer_count == 3
    assert summary.visible_layer_count == 3
    assert summary.primitive_total == 6
    assert summary.max_primitives_per_layer == 2
    assert summary.max_csg_depth == 1
    assert summary.custom_fragment_uniform_vec4 == 0
    assert summary.fragment_uniform_vectors_total == 1
    assert summary.baked_parameter_count == len(compiled.parameter_manifest)
    assert summary.active_parameter_count == 0


def test_full_budget_document_compiles_within_resource_plan() -> None:
    layers = []
    for layer_index in range(MAX_LAYERS):
        shape = {
            "id": f"l{layer_index}_union",
            "kind": "union",
            "left": {
                "id": f"l{layer_index}_sub",
                "kind": "subtract",
                "base": _circle(f"l{layer_index}_a", radius=0.5),
                "cut": _circle(f"l{layer_index}_b", radius=0.3),
            },
            "right": {
                "id": f"l{layer_index}_pair",
                "kind": "union",
                "left": {
                    "id": f"l{layer_index}_cap",
                    "kind": "segment",
                    "from": [-0.4, 0.0],
                    "to": [0.4, 0.0],
                    "radius": 0.05,
                    "transform": {"rotation": [0.0, 1.0], "scale": [1.0, 1.5]},
                },
                "right": _circle(f"l{layer_index}_c", radius=0.2),
            },
        }
        layers.append(
            _layer(
                f"layer_{layer_index}",
                shape,
                opacity=0.9,
                effects=[
                    {
                        "kind": "shadow",
                        "offset": [0.02, -0.02],
                        "blur": 0.02,
                        "spread": 0.0,
                        "color": [0.0, 0.0, 0.0, 0.4],
                    },
                    {
                        "kind": "glow",
                        "radius": 0.1,
                        "softness": 0.02,
                        "color": [1.0, 1.0, 0.0, 0.5],
                    },
                    {
                        "kind": "rim",
                        "width": 0.03,
                        "softness": 0.01,
                        "color": [1.0, 1.0, 1.0, 0.7],
                    },
                ],
            )
        )
    document = parse_dsl_document(_document(*layers))
    compiled = compile_dsl_shader(document)

    assert compiled.resource_summary.primitive_total == MAX_TOTAL_PRIMITIVES
    assert (
        compiled.resource_summary.max_primitives_per_layer == MAX_PRIMITIVES_PER_LAYER
    )
    assert compiled.resource_summary.max_csg_depth == 2
    assert compiled.resource_summary.fragment_source_chars <= 30_000
    assert validate_shader(compiled.fragment_source).valid

    geometry_block = layer_geometry_block("layer_0")
    promoted = compile_dsl_shader(document, active_block=geometry_block)
    assert (
        promoted.resource_summary.custom_fragment_uniform_vec4
        <= MAX_CUSTOM_FRAGMENT_UNIFORM_VEC4
    )
    assert promoted.resource_summary.fragment_uniform_vectors_total <= 15


def test_hash_stability_across_key_order_and_recompile() -> None:
    payload = _rich_document()
    first = parse_dsl_document(payload)
    shuffled = json.loads(json.dumps(payload))
    shuffled["canvas"] = dict(reversed(list(shuffled["canvas"].items())))
    shuffled["layers"][0] = dict(reversed(list(shuffled["layers"][0].items())))
    second = parse_dsl_document(shuffled)

    assert canonical_json(first) == canonical_json(second)
    assert document_sha256(first) == document_sha256(second)
    assert topology_sha256(first) == topology_sha256(second)
    assert parameter_manifest_sha256(first) == parameter_manifest_sha256(second)

    compiled_first = compile_dsl_shader(first)
    compiled_second = compile_dsl_shader(second)
    assert compiled_first.fragment_source == compiled_second.fragment_source
    assert compiled_first.glsl_sha256 == compiled_second.glsl_sha256


def test_signed_zero_is_canonicalized_for_hash_and_glsl() -> None:
    positive_zero = parse_dsl_document(
        _document(
            _layer(
                "body",
                _circle(
                    "s1",
                    transform={"translate": [0.0, 0.0]},
                ),
            )
        )
    )
    negative_zero = parse_dsl_document(
        _document(
            _layer(
                "body",
                _circle(
                    "s1",
                    transform={"translate": [-0.0, 0.0]},
                ),
            )
        )
    )

    assert canonical_json(positive_zero) == canonical_json(negative_zero)
    assert document_sha256(positive_zero) == document_sha256(negative_zero)
    assert (
        compile_dsl_shader(positive_zero).fragment_source
        == compile_dsl_shader(negative_zero).fragment_source
    )


def test_mediump_unsafe_small_geometry_is_rejected() -> None:
    too_small_radius = _document(
        _layer("body", _circle("s1", radius=MIN_POSITIVE_VALUE / 2.0))
    )
    too_small_scale = _document(
        _layer(
            "body",
            _circle(
                "s1",
                transform={"scale": [MIN_POSITIVE_VALUE / 2.0, 1.0]},
            ),
        )
    )
    too_short_segment = _document(
        _layer(
            "body",
            {
                "id": "s1",
                "kind": "segment",
                "from": [0.0, 0.0],
                "to": [MIN_SEGMENT_LENGTH / 2.0, 0.0],
                "radius": 0.1,
            },
        )
    )
    too_short_linear = _document(
        _layer(
            "body",
            _circle("s1"),
            fill={
                "kind": "linear",
                "from": [0.0, 0.0],
                "to": [MIN_LINEAR_SPAN / 2.0, 0.0],
                "start_color": [1.0, 0.0, 0.0, 1.0],
                "end_color": [0.0, 0.0, 1.0, 1.0],
            },
        )
    )

    for payload in (
        too_small_radius,
        too_small_scale,
        too_short_segment,
        too_short_linear,
    ):
        with pytest.raises(ValidationError):
            parse_dsl_document(payload)


def test_compiler_preserves_minimum_positive_literal() -> None:
    document = parse_dsl_document(
        _document(
            _layer(
                "body",
                _circle("s1", radius=MIN_POSITIVE_VALUE),
            )
        )
    )

    source = compile_dsl_shader(document).fragment_source

    assert "0.01" in source
    assert "0.00000000" not in source


def test_document_and_topology_hash_semantics() -> None:
    base = parse_dsl_document(_document(_layer("body", _circle("s1"))))
    changed_param = parse_dsl_document(
        _document(_layer("body", _circle("s1", radius=0.7)))
    )
    changed_kind = parse_dsl_document(
        _document(_layer("body", {"id": "s1", "kind": "ellipse", "radii": [0.5, 0.3]}))
    )
    swapped = parse_dsl_document(
        _document(
            _layer("a", _circle("sa")),
            _layer("b", {"id": "sb", "kind": "ellipse", "radii": [0.5, 0.3]}),
        )
    )
    swapped_back = parse_dsl_document(
        _document(
            _layer("b", {"id": "sb", "kind": "ellipse", "radii": [0.5, 0.3]}),
            _layer("a", _circle("sa")),
        )
    )

    assert document_sha256(base) != document_sha256(changed_param)
    assert topology_sha256(base) == topology_sha256(changed_param)
    assert topology_sha256(base) != topology_sha256(changed_kind)
    assert document_sha256(swapped) != document_sha256(swapped_back)
    assert topology_sha256(swapped) != topology_sha256(swapped_back)


def test_manifest_paths_are_stable_and_id_based() -> None:
    document = parse_dsl_document(_rich_document())
    manifest = parameter_manifest(document)
    paths = [entry.path for entry in manifest]

    assert paths == sorted(paths)
    assert "node:ring.transform.translate.x" not in paths  # ring 无 transform
    assert "node:inner.transform.rotation.cos" in paths
    assert "node:badge.transform.scale.y" in paths
    assert "node:badge_stem.from.x" in paths
    assert "node:outer.radius" in paths
    assert "layer:back.opacity" in paths
    assert "layer:back.fill.from.x" in paths
    assert "layer:mid.fill.inner_color.r" in paths
    assert "layer:back.effect.shadow.offset.x" in paths
    assert "layer:back.effect.rim.width" in paths
    assert "layer:mid.effect.glow.color.a" in paths
    assert "canvas.background.r" in paths
    blocks = {entry.block for entry in manifest}
    assert "canvas" in blocks
    assert "layer:back.geometry" in blocks
    assert "layer:mid.effects" in blocks


@pytest.mark.parametrize(
    "mutate",
    [
        lambda doc: doc["layers"][0]["shape"].update(radius=float("nan")),
        lambda doc: doc["layers"][0]["shape"].update(radius=float("inf")),
        lambda doc: doc["layers"][0]["shape"].update(radius=0.0),
        lambda doc: doc["layers"][0].update(opacity=1.5),
        lambda doc: doc["layers"][0].update(opacity=-0.1),
        lambda doc: doc["canvas"].update(background=[1.0, 1.0, 1.0, 0.5]),
        lambda doc: doc["canvas"].update(width=8),
        lambda doc: doc.update(schema_version="shader_graph_v2"),
        lambda doc: doc["layers"][0].update(id="bad id!"),
    ],
)
def test_invalid_scalar_values_rejected(mutate) -> None:
    payload = _document(_layer("body", _circle("s1")))
    mutate(payload)
    with pytest.raises(ValidationError):
        parse_dsl_document(payload)


def test_canvas_respects_renderer_dimension_limit() -> None:
    payload = _document(_layer("body", _circle("s1")))
    payload["canvas"]["width"] = 1025

    with pytest.raises(ValidationError):
        parse_dsl_document(payload)


def test_duplicate_ids_rejected() -> None:
    two_layers_same_id = _document(
        _layer("dup", _circle("s1")), _layer("dup", _circle("s2"))
    )
    with pytest.raises(ValidationError, match="唯一"):
        parse_dsl_document(two_layers_same_id)

    node_id_collides_with_layer = _document(_layer("body", _circle("body")))
    with pytest.raises(ValidationError, match="唯一"):
        parse_dsl_document(node_id_collides_with_layer)


def test_layer_count_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        parse_dsl_document(_document())
    too_many = _document(
        *[_layer(f"layer_{index}", _circle(f"s{index}")) for index in range(9)]
    )
    with pytest.raises(ValidationError):
        parse_dsl_document(too_many)


def test_per_layer_primitive_budget_enforced() -> None:
    shape = {
        "id": "u1",
        "kind": "union",
        "left": {
            "id": "u2",
            "kind": "union",
            "left": _circle("a"),
            "right": _circle("b"),
        },
        "right": {
            "id": "u3",
            "kind": "union",
            "left": _circle("c"),
            "right": _circle("d"),
        },
    }
    assert parse_dsl_document(_document(_layer("body", shape)))
    shape["right"]["right"] = {
        "id": "u4",
        "kind": "union",
        "left": _circle("d"),
        "right": _circle("e"),
    }
    with pytest.raises(ValidationError, match="primitive"):
        parse_dsl_document(_document(_layer("body", shape)))


def test_csg_depth_budget_enforced() -> None:
    depth_three = {
        "id": "n1",
        "kind": "subtract",
        "base": {
            "id": "n2",
            "kind": "union",
            "left": _circle("a"),
            "right": {
                "id": "n3",
                "kind": "intersect",
                "left": _circle("b"),
                "right": _circle("c"),
            },
        },
        "cut": _circle("d"),
    }
    with pytest.raises(ValidationError, match="CSG"):
        parse_dsl_document(_document(_layer("body", depth_three)))


@pytest.mark.parametrize(
    "shape",
    [
        {
            "id": "s",
            "kind": "circle",
            "radius": 0.5,
            "transform": {"rotation": [2.0, 0.0]},
        },
        {
            "id": "s",
            "kind": "circle",
            "radius": 0.5,
            "transform": {"rotation": [0.0, 0.0]},
        },
        {
            "id": "s",
            "kind": "circle",
            "radius": 0.5,
            "transform": {"scale": [0.0, 1.0]},
        },
        {
            "id": "s",
            "kind": "circle",
            "radius": 0.5,
            "transform": {"scale": [-1.0, 1.0]},
        },
        {"id": "s", "kind": "ellipse", "radii": [0.5, 0.0]},
        {
            "id": "s",
            "kind": "rounded_box",
            "half_size": [0.5, 0.3],
            "corner_radius": 0.31,
        },
        {
            "id": "s",
            "kind": "segment",
            "from": [0.0, 0.0],
            "to": [0.0, 0.0],
            "radius": 0.05,
        },
        {
            "id": "s",
            "kind": "segment",
            "from": [0.0, 0.0],
            "to": [0.4, 0.0],
            "radius": -0.05,
        },
    ],
)
def test_invalid_shape_values_rejected(shape) -> None:
    with pytest.raises(ValidationError):
        parse_dsl_document(_document(_layer("body", shape)))


def test_effect_constraints_enforced() -> None:
    rim = {"kind": "rim", "width": 0.05, "color": [1.0, 1.0, 1.0, 0.8]}
    duplicated = _document(_layer("body", _circle("s1"), effects=[rim, dict(rim)]))
    with pytest.raises(ValidationError, match="最多一个"):
        parse_dsl_document(duplicated)

    four_effects = _document(
        _layer(
            "body",
            _circle("s1"),
            effects=[
                rim,
                {"kind": "shadow", "offset": [0.0, 0.0], "color": [0.0, 0.0, 0.0, 0.5]},
                {"kind": "glow", "radius": 0.1, "color": [1.0, 0.0, 0.0, 0.5]},
                {"kind": "glow", "radius": 0.2, "color": [0.0, 1.0, 0.0, 0.5]},
            ],
        )
    )
    with pytest.raises(ValidationError):
        parse_dsl_document(four_effects)

    zero_span_linear = _document(
        _layer(
            "body",
            _circle("s1"),
            fill={
                "kind": "linear",
                "from": [0.0, 0.0],
                "to": [0.0, 0.0],
                "start_color": [1.0, 0.0, 0.0, 1.0],
                "end_color": [0.0, 0.0, 1.0, 1.0],
            },
        )
    )
    with pytest.raises(ValidationError):
        parse_dsl_document(zero_span_linear)


def test_unknown_fields_and_kinds_rejected() -> None:
    payload = _document(_layer("body", _circle("s1")))
    payload["layers"][0]["blend_mode"] = "multiply"
    with pytest.raises(ValidationError):
        parse_dsl_document(payload)

    payload = _document(_layer("body", {"id": "s1", "kind": "polygon", "sides": 5}))
    with pytest.raises(ValidationError):
        parse_dsl_document(payload)


def test_layer_order_drives_source_over_sequence() -> None:
    document = parse_dsl_document(
        _document(
            _layer("back", _circle("sa"), opacity=0.5),
            _layer("front", _circle("sb")),
        )
    )
    compiled = compile_dsl_shader(document)
    source = compiled.fragment_source

    back_call = source.index("_sf_layer_0(p, aa)")
    front_call = source.index("_sf_layer_1(p, aa)")
    assert back_call < front_call
    composite = "_acc = _lay + _acc * (1.0 - _lay.a);"
    assert source.count(composite) == 2

    swapped = parse_dsl_document(
        _document(
            _layer("front", _circle("sb")),
            _layer("back", _circle("sa"), opacity=0.5),
        )
    )
    swapped_compiled = compile_dsl_shader(swapped)
    assert swapped_compiled.fragment_source != source
    assert swapped_compiled.document_sha256 != compiled.document_sha256


def test_premultiplied_source_over_and_opaque_output() -> None:
    document = parse_dsl_document(
        _document(
            _layer(
                "body",
                _circle("s1"),
                opacity=0.5,
                fill={"kind": "solid", "color": [1.0, 0.0, 0.0, 0.5]},
            )
        )
    )
    source = compile_dsl_shader(document).fragment_source

    assert "vec4(_fl.rgb * _fl_a, _fl_a)" in source  # premultiplied fill
    assert "_acc = _fp + _acc * (1.0 - _fp.a);" in source  # source-over
    assert "return _acc * (0.5);" in source  # premultiplied layer opacity
    assert source.rstrip().endswith("gl_FragColor = vec4(_acc.rgb, 1.0);\n}")
    assert "0.5" in source


def test_csg_operator_semantics_not_confused() -> None:
    union_doc = parse_dsl_document(
        _document(
            _layer(
                "body",
                {
                    "id": "op",
                    "kind": "union",
                    "left": _circle("a"),
                    "right": _circle("b"),
                },
            )
        )
    )
    subtract_doc = parse_dsl_document(
        _document(
            _layer(
                "body",
                {
                    "id": "op",
                    "kind": "subtract",
                    "base": _circle("a"),
                    "cut": _circle("b"),
                },
            )
        )
    )
    intersect_doc = parse_dsl_document(
        _document(
            _layer(
                "body",
                {
                    "id": "op",
                    "kind": "intersect",
                    "left": _circle("a"),
                    "right": _circle("b"),
                },
            )
        )
    )
    union_src = compile_dsl_shader(union_doc).fragment_source
    subtract_src = compile_dsl_shader(subtract_doc).fragment_source
    intersect_src = compile_dsl_shader(intersect_doc).fragment_source

    dist_union = union_src.split("float _sf_dist_0", 1)[1]
    dist_subtract = subtract_src.split("float _sf_dist_0", 1)[1]
    dist_intersect = intersect_src.split("float _sf_dist_0", 1)[1]
    assert "return min(" in dist_union
    assert "return max((length(p) - (0.5)), -((length(p) - (0.5))))" in dist_subtract
    assert "return max(" in dist_intersect
    assert "-(" not in dist_intersect
    assert topology_sha256(union_doc) != topology_sha256(subtract_doc)
    assert topology_sha256(subtract_doc) != topology_sha256(intersect_doc)


def test_effects_follow_fixed_intra_layer_order() -> None:
    effects = [
        {"kind": "rim", "width": 0.05, "color": [1.0, 1.0, 1.0, 0.8]},
        {"kind": "glow", "radius": 0.2, "color": [1.0, 0.8, 0.2, 0.6]},
        {"kind": "shadow", "offset": [0.05, -0.05], "color": [0.0, 0.0, 0.0, 0.5]},
    ]
    forward = parse_dsl_document(
        _document(_layer("body", _circle("s1"), effects=effects))
    )
    shuffled = parse_dsl_document(
        _document(_layer("body", _circle("s1"), effects=list(reversed(effects))))
    )
    assert canonical_json(forward) == canonical_json(shuffled)

    source = compile_dsl_shader(forward).fragment_source
    assert source.index("_sh_w") < source.index("_gl_w") < source.index("_fl_a")
    assert source.index("_fl_a") < source.index("_rm_w")


def test_aa_width_derived_from_resolution() -> None:
    document = parse_dsl_document(_document(_layer("body", _circle("s1"))))
    source = compile_dsl_shader(document).fragment_source
    assert "float aa = 2.00000000 / _unit;" in source
    assert "float _unit = min(u_resolution.x, u_resolution.y);" in source
    assert "fwidth" not in source
    assert "dFdx" not in source


def test_invisible_layer_skipped_but_still_hashed() -> None:
    visible_only = parse_dsl_document(_document(_layer("body", _circle("s1"))))
    with_hidden = parse_dsl_document(
        _document(
            _layer("body", _circle("s1")),
            _layer("ghost", _circle("s2"), visible=False),
        )
    )
    compiled = compile_dsl_shader(with_hidden)
    assert "_sf_dist_1" not in compiled.fragment_source
    assert compiled.resource_summary.visible_layer_count == 1
    assert compiled.resource_summary.layer_count == 2
    assert document_sha256(with_hidden) != document_sha256(visible_only)
    assert validate_shader(compiled.fragment_source).valid


def test_active_block_promotes_packed_vec4_uniforms() -> None:
    document = parse_dsl_document(_rich_document())
    block = layer_fill_block("back")
    compiled = compile_dsl_shader(document, active_block=block)

    active_entries = [
        entry for entry in compiled.parameter_manifest if entry.block == block
    ]
    assert len(active_entries) == 12  # from/to 各 2 + 两个颜色各 4
    assert compiled.resource_summary.active_parameter_count == 12
    assert compiled.resource_summary.custom_fragment_uniform_vec4 == 3
    assert len(compiled.uniform_values) == 3
    assert set(compiled.uniform_schema) == {
        "u_active_0",
        "u_active_1",
        "u_active_2",
    }
    assert "uniform vec4 u_active_0;" in compiled.fragment_source
    assert "u_active_0.x" in compiled.fragment_source
    baked = compile_dsl_shader(document)
    assert "u_active" not in baked.fragment_source
    assert baked.glsl_sha256 != compiled.glsl_sha256
    # 激活 uniform 的值与 manifest 数值一致（按路径排序打包）.
    ordered = sorted(active_entries, key=lambda entry: entry.path)
    flat = [entry.value for entry in ordered]
    packed = [value for vector in compiled.uniform_values.values() for value in vector]
    assert packed[: len(flat)] == flat
    assert validate_shader(compiled.fragment_source).valid


def test_unknown_active_block_rejected() -> None:
    document = parse_dsl_document(_document(_layer("body", _circle("s1"))))
    with pytest.raises(ValueError, match="active block"):
        compile_dsl_shader(document, active_block="layer:ghost.fill")


def test_uniform_resource_plan_guard() -> None:
    entries = tuple(
        ManifestEntry(f"node:s.f{index:02d}", "block", float(index))
        for index in range(4 * MAX_CUSTOM_FRAGMENT_UNIFORM_VEC4 + 1)
    )
    with pytest.raises(ValueError, match="资源计划"):
        pack_active_uniforms(entries)
    values, refs = pack_active_uniforms(entries[:5])
    assert len(values) == 2
    assert refs["node:s.f04"] == "u_active_1.x"


def test_compile_output_is_deterministic_for_same_document() -> None:
    document = parse_dsl_document(_rich_document())
    first = compile_dsl_shader(document)
    second = compile_dsl_shader(
        parse_dsl_document(json.loads(canonical_json(document)))
    )
    assert first == second
