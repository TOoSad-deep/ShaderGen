from __future__ import annotations

import pytest
from pydantic import ValidationError

from shaderforge.dsl import (
    document_sha256,
    parameter_manifest,
    parse_dsl_document,
    topology_sha256,
)
from shaderforge.optimization import (
    DslParameterSpec,
    dsl_parameter_specs,
    replace_dsl_parameter,
)


def _document() -> dict:
    return {
        "schema_version": "shader_graph_v1",
        "canvas": {
            "width": 192,
            "height": 192,
            "background": [1.0, 1.0, 1.0, 1.0],
        },
        "layers": [
            {
                "id": "back",
                "visible": True,
                "opacity": 0.8,
                "shape": {
                    "id": "ring",
                    "kind": "subtract",
                    "base": {"id": "outer", "kind": "circle", "radius": 0.6},
                    "cut": {
                        "id": "inner",
                        "kind": "circle",
                        "radius": 0.35,
                        "transform": {
                            "translate": [0.1, 0.0],
                            "scale": [1.0, 0.5],
                            "rotation": [1.0, 0.0],
                        },
                    },
                },
                "fill": {
                    "kind": "linear",
                    "from": [-0.5, 0.0],
                    "to": [0.5, 0.0],
                    "start_color": [1.0, 0.2, 0.4, 1.0],
                    "end_color": [1.0, 0.9, 0.95, 1.0],
                },
                "effects": [
                    {
                        "kind": "shadow",
                        "offset": [0.05, -0.05],
                        "blur": 0.04,
                        "spread": 0.01,
                        "color": [0.0, 0.0, 0.0, 0.5],
                    },
                    {
                        "kind": "rim",
                        "width": 0.05,
                        "softness": 0.01,
                        "color": [1.0, 1.0, 1.0, 0.8],
                    },
                ],
            },
            {
                "id": "front",
                "shape": {
                    "id": "stem",
                    "kind": "segment",
                    "from": [-0.3, -0.2],
                    "to": [0.3, 0.2],
                    "radius": 0.05,
                },
                "fill": {"kind": "solid", "color": [0.2, 0.6, 1.0, 1.0]},
            },
        ],
    }


def test_specs_cover_entire_compiler_manifest() -> None:
    document = parse_dsl_document(_document())
    manifest = parameter_manifest(document)
    specs = dsl_parameter_specs(document)

    assert len(specs) == len(manifest)
    by_path = {spec.path: spec for spec in specs}
    for entry in manifest:
        spec = by_path[entry.path]
        assert spec.block == entry.block
        assert spec.value == entry.value
        assert spec.minimum <= spec.value <= spec.maximum
        assert spec.step > 0.0


def test_spec_bounds_follow_parameter_semantics() -> None:
    document = parse_dsl_document(_document())
    by_path = {spec.path: spec for spec in dsl_parameter_specs(document)}

    assert (
        by_path["canvas.background.r"].minimum,
        by_path["canvas.background.r"].maximum,
    ) == (0.0, 1.0)
    assert (
        by_path["layer:back.opacity"].minimum,
        by_path["layer:back.opacity"].maximum,
    ) == (0.0, 1.0)
    assert by_path["node:inner.transform.scale.y"].minimum == 0.05
    assert by_path["node:inner.transform.scale.y"].maximum == 4.0
    assert by_path["node:inner.transform.rotation.cos"].minimum == -1.0
    assert by_path["node:inner.transform.rotation.cos"].maximum == 1.0
    assert by_path["node:inner.transform.translate.x"].minimum == -2.0
    assert by_path["node:outer.radius"].minimum == 0.001
    assert by_path["layer:back.effect.rim.width"].minimum == 0.001
    assert by_path["layer:back.effect.shadow.blur"].minimum == 0.0
    assert by_path["layer:back.effect.shadow.color.a"].maximum == 1.0
    assert by_path["node:stem.from.x"].minimum == -2.0
    assert by_path["layer:back.fill.start_color.g"].maximum == 1.0
    assert isinstance(by_path["node:outer.radius"], DslParameterSpec)


def test_replace_single_parameter_revalidates_and_rehashes() -> None:
    document = parse_dsl_document(_document())
    replaced = replace_dsl_parameter(document, "node:outer.radius", 0.7)

    assert document.layers[0].shape.kind == "subtract"
    base = replaced.layers[0].shape
    assert base.kind == "subtract"
    assert base.base.kind == "circle"
    assert base.base.radius == 0.7
    # 输入文档保持冻结不变.
    original_base = document.layers[0].shape
    assert original_base.kind == "subtract"
    assert original_base.base.kind == "circle"
    assert original_base.base.radius == 0.6
    assert document_sha256(replaced) != document_sha256(document)
    assert topology_sha256(replaced) == topology_sha256(document)
    specs = {spec.path: spec for spec in dsl_parameter_specs(replaced)}
    assert specs["node:outer.radius"].value == 0.7


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("layer:back.opacity", 0.5),
        ("canvas.background.g", 0.25),
        ("node:inner.transform.translate.y", -0.4),
        ("node:inner.transform.scale.x", 1.5),
        ("node:stem.from.x", -0.1),
        ("node:stem.radius", 0.08),
        ("layer:back.fill.from.y", 0.3),
        ("layer:back.fill.end_color.b", 0.5),
        ("layer:back.effect.shadow.offset.x", -0.2),
        ("layer:back.effect.shadow.color.a", 0.3),
        ("layer:back.effect.rim.softness", 0.05),
    ],
)
def test_replace_representative_parameter_kinds(path: str, value: float) -> None:
    document = parse_dsl_document(_document())
    replaced = replace_dsl_parameter(document, path, value)
    assert document_sha256(replaced) != document_sha256(document)
    specs = {spec.path: spec for spec in dsl_parameter_specs(replaced)}
    assert specs[path].value == value


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("node:outer.radius", float("nan")),
        ("node:outer.radius", float("inf")),
        ("node:outer.radius", 5.0),  # 超出 spec 范围
        ("layer:back.opacity", 1.5),
        ("layer:back.opacity", -0.1),
        ("node:ghost.radius", 0.5),  # 未知节点
        ("layer:ghost.opacity", 0.5),  # 未知 layer
        ("node:outer.color", 0.5),  # 不在稳定清单
        ("object.primitive.axes", 0.5),  # 旧 MinScene 路径不属于 DSL 清单
    ],
)
def test_replace_rejects_invalid_requests(path: str, value: float) -> None:
    document = parse_dsl_document(_document())
    with pytest.raises(ValueError):
        replace_dsl_parameter(document, path, value)


def test_replace_revalidates_coupled_constraints() -> None:
    document = parse_dsl_document(_document())
    # 单独改 rotation.cos 会破坏单位长度约束.
    with pytest.raises(ValidationError):
        replace_dsl_parameter(document, "node:inner.transform.rotation.cos", 0.5)
    # corner_radius 超过 half_size 时重新校验 fail closed.
    box_document = parse_dsl_document(
        {
            "schema_version": "shader_graph_v1",
            "canvas": {"width": 192, "height": 192, "background": [1.0, 1.0, 1.0, 1.0]},
            "layers": [
                {
                    "id": "body",
                    "shape": {
                        "id": "box",
                        "kind": "rounded_box",
                        "half_size": [0.5, 0.3],
                        "corner_radius": 0.1,
                    },
                    "fill": {"kind": "solid", "color": [1.0, 0.0, 0.0, 1.0]},
                }
            ],
        }
    )
    with pytest.raises(ValidationError):
        replace_dsl_parameter(box_document, "node:box.corner_radius", 0.5)
    # 合法联动调整可以通过.
    replaced = replace_dsl_parameter(box_document, "node:box.corner_radius", 0.2)
    shape = replaced.layers[0].shape
    assert shape.kind == "rounded_box"
    assert shape.corner_radius == 0.2


def test_replace_preserves_structure_and_layer_order() -> None:
    document = parse_dsl_document(_document())
    replaced = replace_dsl_parameter(document, "node:inner.radius", 0.2)

    assert [layer.id for layer in replaced.layers] == ["back", "front"]
    replaced_shape = replaced.layers[0].shape
    assert replaced_shape.kind == "subtract"
    assert replaced_shape.cut.kind == "circle"
    assert replaced_shape.cut.radius == 0.2
    effects = [effect.kind for effect in replaced.layers[0].effects]
    assert effects == ["shadow", "rim"]
