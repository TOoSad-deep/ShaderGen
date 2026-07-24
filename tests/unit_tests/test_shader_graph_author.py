"""ShaderGraph Model Author 契约、Parser 与 Prompt 装配的单元测试."""

from __future__ import annotations

import json

import pytest

from agent.app.contracts.shader_graph_author import (
    AddLayerBundleAuthorPatch,
    RemoveLayerAuthorPatch,
    ShaderGraphAuthorPatchError,
    apply_shader_graph_author_patch,
    summarize_shader_graph_author_patch,
)
from agent.app.nodes.png_to_shader_min.shader_graph_author import (
    SHADER_GRAPH_AUTHOR_INITIAL_PROMPT,
    SHADER_GRAPH_AUTHOR_REFINE_PROMPT,
    shader_graph_author_patch_json_schema,
    shader_graph_document_json_schema,
)
from agent.app.parsers.shader_graph_author import (
    ShaderGraphAuthorParseError,
    parse_shader_graph_author_patch,
    parse_shader_graph_document,
)
from shaderforge.dsl import (
    CircleShape,
    DslCanvas,
    Layer,
    ShaderDocument,
    SolidFill,
    document_sha256,
)

_BASE = "a" * 64


def _layer(layer_id: str, node_id: str, *, radius: float = 0.5) -> Layer:
    return Layer(
        id=layer_id,
        shape=CircleShape(id=node_id, kind="circle", radius=radius),
        fill=SolidFill(kind="solid", color=(0.8, 0.2, 0.3, 1.0)),
    )


def _document(layer_count: int = 2) -> ShaderDocument:
    return ShaderDocument(
        canvas=DslCanvas(width=64, height=64, background=(1.0, 1.0, 1.0, 1.0)),
        layers=tuple(_layer(f"layer_{i}", f"node_{i}") for i in range(layer_count)),
    )


def _document_json(document: ShaderDocument) -> str:
    return json.dumps(document.model_dump(mode="json", by_alias=True))


def _patch_json(payload: dict[str, object]) -> str:
    return json.dumps(payload)


def _base(document: ShaderDocument) -> str:
    return document_sha256(document)


# --- Initial：完整 ShaderDocument ---


def test_parse_valid_initial_document() -> None:
    document = _document()

    parsed = parse_shader_graph_document(
        _document_json(document), expected_width=64, expected_height=64
    )

    assert parsed == document


def test_parse_initial_rejects_canvas_resize() -> None:
    with pytest.raises(ShaderGraphAuthorParseError) as raised:
        parse_shader_graph_document(
            _document_json(_document()), expected_width=128, expected_height=64
        )
    assert raised.value.code == "shader_graph_canvas_mismatch"


@pytest.mark.parametrize(
    "text",
    [
        json.dumps({"canvas": None, "layers": [], "unknown": 1}),
        '{"canvas": 1, "canvas": 2}',
        '{"canvas": NaN}',
    ],
)
def test_parse_initial_rejects_non_strict_json(text: str) -> None:
    with pytest.raises(ShaderGraphAuthorParseError) as raised:
        parse_shader_graph_document(text, expected_width=64, expected_height=64)
    assert raised.value.code == "invalid_shader_graph_document_json"


def test_parse_initial_rejects_oversize_output() -> None:
    document = _document()
    payload = json.loads(_document_json(document))
    payload["padding"] = "x" * 200_000

    with pytest.raises(ShaderGraphAuthorParseError) as raised:
        parse_shader_graph_document(
            json.dumps(payload), expected_width=64, expected_height=64
        )
    assert raised.value.code == "invalid_shader_graph_document_json"


def test_parse_initial_exposes_safe_validation_details_for_repair() -> None:
    payload = json.loads(_document_json(_document()))
    payload["layers"][0]["shape"]["radius"] = 0.0

    with pytest.raises(ShaderGraphAuthorParseError) as raised:
        parse_shader_graph_document(
            json.dumps(payload),
            expected_width=64,
            expected_height=64,
        )

    assert raised.value.details
    assert raised.value.details[0]["location"].endswith("radius")
    assert "input" not in raised.value.details[0]


# --- Refine：单个 typed layer patch 解析 ---


def test_parse_all_five_patch_operations() -> None:
    layer_payload = _layer("layer_new", "node_new").model_dump(
        mode="json", by_alias=True
    )
    cases = [
        {
            "base_document_sha256": _BASE,
            "operation": "add_layer_bundle",
            "value": layer_payload,
        },
        {
            "base_document_sha256": _BASE,
            "operation": "remove_layer",
            "value": "layer_0",
        },
        {
            "base_document_sha256": _BASE,
            "operation": "replace_layer_bundle",
            "value": {
                "layer_id": "layer_0",
                "layer": layer_payload | {"id": "layer_0"},
            },
        },
        {
            "base_document_sha256": _BASE,
            "operation": "reorder_layer",
            "value": {"layer_id": "layer_0", "direction": "toward_front"},
        },
        {
            "base_document_sha256": _BASE,
            "operation": "replace_canvas_background",
            "value": [0.1, 0.2, 0.3, 1.0],
        },
    ]
    operations = [
        parse_shader_graph_author_patch(_patch_json(case)).operation for case in cases
    ]
    assert operations == [
        "add_layer_bundle",
        "remove_layer",
        "replace_layer_bundle",
        "reorder_layer",
        "replace_canvas_background",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "remove_layer", "value": "layer_0"},
        {
            "base_document_sha256": "not-a-hash",
            "operation": "remove_layer",
            "value": "layer_0",
        },
        {"base_document_sha256": _BASE, "operation": "move_layer", "value": "layer_0"},
        {
            "base_document_sha256": _BASE,
            "operation": "remove_layer",
            "value": "layer_0",
            "extra": 1,
        },
    ],
)
def test_parse_patch_rejects_invalid_shape(payload: dict[str, object]) -> None:
    with pytest.raises(ShaderGraphAuthorParseError) as raised:
        parse_shader_graph_author_patch(_patch_json(payload))
    assert raised.value.code == "invalid_shader_graph_author_patch_json"


# --- Patch 应用：base 绑定、原子 op 与全图重新校验 ---


def test_apply_rejects_base_mismatch() -> None:
    patch = RemoveLayerAuthorPatch(
        base_document_sha256="b" * 64, operation="remove_layer", value="layer_0"
    )

    with pytest.raises(ShaderGraphAuthorPatchError) as raised:
        apply_shader_graph_author_patch(_document(), patch)
    assert raised.value.code == "base_document_mismatch"


def test_apply_add_layer_bundle_appends_front_most() -> None:
    document = _document()
    patch = AddLayerBundleAuthorPatch(
        base_document_sha256=_base(document),
        operation="add_layer_bundle",
        value=_layer("layer_top", "node_top"),
    )

    result = apply_shader_graph_author_patch(document, patch)

    assert [layer.id for layer in result.layers] == [
        "layer_0",
        "layer_1",
        "layer_top",
    ]
    assert document_sha256(result) != _base(document)


def test_apply_add_layer_bundle_rejects_duplicate_id_and_ninth_layer() -> None:
    document = _document()
    duplicate = AddLayerBundleAuthorPatch(
        base_document_sha256=_base(document),
        operation="add_layer_bundle",
        value=_layer("layer_0", "node_new"),
    )
    with pytest.raises(ShaderGraphAuthorPatchError) as raised:
        apply_shader_graph_author_patch(document, duplicate)
    assert raised.value.code == "patched_document_invalid"

    full = _document(layer_count=8)
    ninth = AddLayerBundleAuthorPatch(
        base_document_sha256=_base(full),
        operation="add_layer_bundle",
        value=_layer("layer_9", "node_9"),
    )
    with pytest.raises(ShaderGraphAuthorPatchError) as raised:
        apply_shader_graph_author_patch(full, ninth)
    assert raised.value.code == "patched_document_invalid"


def test_apply_remove_layer_and_missing_target() -> None:
    document = _document()
    remove = RemoveLayerAuthorPatch(
        base_document_sha256=_base(document),
        operation="remove_layer",
        value="layer_0",
    )
    result = apply_shader_graph_author_patch(document, remove)
    assert [layer.id for layer in result.layers] == ["layer_1"]

    missing = RemoveLayerAuthorPatch(
        base_document_sha256=_base(document),
        operation="remove_layer",
        value="layer_none",
    )
    with pytest.raises(ShaderGraphAuthorPatchError) as raised:
        apply_shader_graph_author_patch(document, missing)
    assert raised.value.code == "layer_not_found"


def test_apply_remove_last_remaining_layer_is_rejected() -> None:
    document = _document(layer_count=1)
    patch = RemoveLayerAuthorPatch(
        base_document_sha256=_base(document),
        operation="remove_layer",
        value="layer_0",
    )

    with pytest.raises(ShaderGraphAuthorPatchError) as raised:
        apply_shader_graph_author_patch(document, patch)
    assert raised.value.code == "patched_document_invalid"


def test_apply_replace_layer_bundle_requires_same_id() -> None:
    document = _document()
    payload = {
        "base_document_sha256": _base(document),
        "operation": "replace_layer_bundle",
        "value": {
            "layer_id": "layer_0",
            "layer": _layer("layer_renamed", "node_new").model_dump(
                mode="json", by_alias=True
            ),
        },
    }
    renamed = parse_shader_graph_author_patch(_patch_json(payload))
    with pytest.raises(ShaderGraphAuthorPatchError) as raised:
        apply_shader_graph_author_patch(document, renamed)
    assert raised.value.code == "layer_id_mismatch"

    payload["value"]["layer"]["id"] = "layer_0"  # type: ignore[index]
    same_id = parse_shader_graph_author_patch(_patch_json(payload))
    result = apply_shader_graph_author_patch(document, same_id)
    assert result.layers[0].id == "layer_0"
    assert result.layers[0].shape.id == "node_new"


def test_apply_reorder_layer_single_step_and_bounds() -> None:
    document = _document(layer_count=3)
    forward = parse_shader_graph_author_patch(
        _patch_json(
            {
                "base_document_sha256": _base(document),
                "operation": "reorder_layer",
                "value": {"layer_id": "layer_0", "direction": "toward_front"},
            }
        )
    )
    result = apply_shader_graph_author_patch(document, forward)
    assert [layer.id for layer in result.layers] == [
        "layer_1",
        "layer_0",
        "layer_2",
    ]

    out_of_range = parse_shader_graph_author_patch(
        _patch_json(
            {
                "base_document_sha256": _base(document),
                "operation": "reorder_layer",
                "value": {"layer_id": "layer_2", "direction": "toward_front"},
            }
        )
    )
    with pytest.raises(ShaderGraphAuthorPatchError) as raised:
        apply_shader_graph_author_patch(document, out_of_range)
    assert raised.value.code == "reorder_out_of_range"


def test_apply_replace_canvas_background_stays_opaque() -> None:
    document = _document()
    ok = parse_shader_graph_author_patch(
        _patch_json(
            {
                "base_document_sha256": _base(document),
                "operation": "replace_canvas_background",
                "value": [0.1, 0.2, 0.3, 1.0],
            }
        )
    )
    result = apply_shader_graph_author_patch(document, ok)
    assert result.canvas.background == (0.1, 0.2, 0.3, 1.0)
    assert result.canvas.width == 64

    translucent = parse_shader_graph_author_patch(
        _patch_json(
            {
                "base_document_sha256": _base(document),
                "operation": "replace_canvas_background",
                "value": [0.1, 0.2, 0.3, 0.5],
            }
        )
    )
    with pytest.raises(ShaderGraphAuthorPatchError) as raised:
        apply_shader_graph_author_patch(document, translucent)
    assert raised.value.code == "patched_document_invalid"


def test_summarize_patch_hides_full_value() -> None:
    document = _document()
    patch = AddLayerBundleAuthorPatch(
        base_document_sha256=_base(document),
        operation="add_layer_bundle",
        value=_layer("layer_top", "node_top"),
    )

    summary = summarize_shader_graph_author_patch(patch)

    assert summary["patch_operation"] == "add_layer_bundle"
    assert summary["layer_id"] == "layer_top"
    assert summary["node_kinds"] == ("circle", "solid")
    assert summary["base_document_sha256_prefix"] == _base(document)[:12]
    assert len(summary["patch_fingerprint"]) == 64
    assert "value" not in summary
    assert "color" not in json.dumps(summary)


# --- Prompt 与 Schema 装配 ---


def test_prompt_definitions_are_versioned() -> None:
    assert SHADER_GRAPH_AUTHOR_INITIAL_PROMPT.version
    assert SHADER_GRAPH_AUTHOR_REFINE_PROMPT.version
    assert "ShaderDocument" in SHADER_GRAPH_AUTHOR_INITIAL_PROMPT.prompt
    assert "base_document_sha256" in SHADER_GRAPH_AUTHOR_REFINE_PROMPT.prompt


def test_initial_prompt_binds_fallback_and_layer_decomposition_contract() -> None:
    prompt = SHADER_GRAPH_AUTHOR_INITIAL_PROMPT.prompt

    assert "fallback_shader_graph" in prompt
    assert "后到前" in prompt
    # 细线、柔和高光/暗斑、弧形条带的保守表达路径。
    for keyword in ("segment", "ellipse", "radial", "subtract"):
        assert keyword in prompt
    # 关键数值硬约束与虚构节点禁令。
    assert "0.01" in prompt
    assert "corner_radius" in prompt
    assert "arc" in prompt


def test_refine_prompt_binds_operation_choice_rules() -> None:
    prompt = SHADER_GRAPH_AUTHOR_REFINE_PROMPT.prompt

    for operation in (
        "add_layer_bundle",
        "remove_layer",
        "replace_layer_bundle",
        "reorder_layer",
        "replace_canvas_background",
    ):
        assert operation in prompt
    assert "recent_rejected_patch_summaries" in prompt
    assert "spatial_residual_summary" in prompt
    assert "segment" in prompt
    assert "8" in prompt


def test_json_schemas_are_bounded_and_readable() -> None:
    document_schema = shader_graph_document_json_schema()
    patch_schema = shader_graph_author_patch_json_schema()

    assert document_schema["type"] == "object"
    assert "layers" in json.dumps(document_schema)
    patch_text = json.dumps(patch_schema)
    for operation in (
        "add_layer_bundle",
        "remove_layer",
        "replace_layer_bundle",
        "reorder_layer",
        "replace_canvas_background",
    ):
        assert operation in patch_text
    assert "base_document_sha256" in patch_text
