"""scene_mvp typed Patch 安全证据测试。"""

import re

from agent.app.contracts.png_to_shader_min import (
    AddFeatureAuthorPatch,
    FeatureReplacement,
    RemoveFeatureAuthorPatch,
    ReplaceColorFieldAuthorPatch,
    ReplaceFeatureAuthorPatch,
    summarize_min_author_patch,
)
from shaderforge.public import Feature, SolidColorField


def _feature(*, intensity: float = 0.7) -> Feature:
    return Feature(
        id="highlight_1",
        type="gaussian_lobe",
        center=(0.2, 0.3),
        axes=(0.4, 0.2),
        color=(1.0, 0.8, 0.9),
        intensity=intensity,
    )


def test_patch_summaries_cover_all_typed_operations_without_value_leakage() -> None:
    patches = (
        AddFeatureAuthorPatch(
            operation="add", path="/object/features", value=_feature()
        ),
        RemoveFeatureAuthorPatch(
            operation="remove", path="/object/features", value="highlight_1"
        ),
        ReplaceFeatureAuthorPatch(
            operation="replace",
            path="/object/features",
            value=FeatureReplacement(
                feature_id="highlight_1",
                feature=_feature(),
            ),
        ),
        ReplaceColorFieldAuthorPatch(
            operation="replace",
            path="/object/color_field",
            value=SolidColorField(model="solid", color=(0.9, 0.4, 0.5)),
        ),
    )

    summaries = [summarize_min_author_patch(patch) for patch in patches]

    assert [item["patch_operation"] for item in summaries] == [
        "add_feature",
        "remove_feature",
        "replace_feature",
        "replace_color_field",
    ]
    assert summaries[0]["feature_id"] == "highlight_1"
    assert summaries[0]["feature_type"] == "gaussian_lobe"
    assert summaries[1]["feature_id"] == "highlight_1"
    assert summaries[1]["feature_type"] is None
    assert summaries[2]["feature_id"] == "highlight_1"
    assert summaries[2]["feature_type"] == "gaussian_lobe"
    assert summaries[3]["feature_id"] is None
    assert summaries[3]["feature_type"] is None
    for summary in summaries:
        assert set(summary) == {
            "patch_operation",
            "feature_id",
            "feature_type",
            "patch_fingerprint",
        }
        assert re.fullmatch(r"[0-9a-f]{64}", str(summary["patch_fingerprint"]))
        serialized = repr(summary)
        assert "'value':" not in serialized
        assert "'color':" not in serialized
        assert "'axes':" not in serialized
        assert "'intensity':" not in serialized
        assert "(0.9, 0.4, 0.5)" not in serialized


def test_patch_fingerprint_is_canonical_and_sensitive_to_parameter_changes() -> None:
    first = AddFeatureAuthorPatch(
        operation="add", path="/object/features", value=_feature()
    )
    same = AddFeatureAuthorPatch.model_validate(
        {
            "value": _feature().model_dump(mode="json"),
            "path": "/object/features",
            "operation": "add",
        }
    )
    changed = AddFeatureAuthorPatch(
        operation="add", path="/object/features", value=_feature(intensity=0.8)
    )

    first_summary = summarize_min_author_patch(first)

    assert first_summary["patch_fingerprint"] == summarize_min_author_patch(same)[
        "patch_fingerprint"
    ]
    assert first_summary["patch_fingerprint"] != summarize_min_author_patch(changed)[
        "patch_fingerprint"
    ]
