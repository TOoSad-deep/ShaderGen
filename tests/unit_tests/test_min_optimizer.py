from __future__ import annotations

import math

import pytest

from shaderforge.optimization import (
    MAX_CANDIDATES_PER_BATCH,
    MAX_PATCH_CANDIDATE_DRAWS,
    ScoredScene,
    accept_strict_mae_improvement,
    propose_min_scene_candidates,
    rebase_candidate_proposal,
)
from shaderforge.scene import (
    Canvas,
    Feature,
    LinearColorField,
    MinScene,
    Primitive,
    RadialColorField,
    SceneObject,
    SolidColorField,
)


def _scene() -> MinScene:
    return MinScene(
        canvas=Canvas(width=160, height=100, background=(0.2, 0.3, 0.4)),
        object=SceneObject(
            primitive=Primitive(type="ellipse", center=(0.1, -0.1), axes=(0.7, 0.5)),
            color_field=RadialColorField(
                model="radial",
                inner=(0.9, 0.5, 0.4),
                outer=(0.4, 0.1, 0.2),
                origin=(-0.35, 0.55),
                scale=1.25,
            ),
            features=(
                Feature(
                    id="highlight",
                    type="polar_arc",
                    center=(-0.2, 0.3),
                    axes=(0.3, 0.1),
                    color=(1.0, 0.8, 0.7),
                    intensity=0.4,
                ),
                Feature(id="rim", type="rim", color=(0.9, 0.6, 0.6), intensity=0.2),
                Feature(
                    id="shadow",
                    type="shadow",
                    center=(0.1, -0.6),
                    axes=(0.5, 0.1),
                    color=(0.1, 0.1, 0.1),
                    intensity=0.3,
                ),
            ),
        ),
    )


def test_base_candidates_cover_only_the_numeric_field_whitelist() -> None:
    proposals = propose_min_scene_candidates(
        _scene(), stage="base", remaining_draw_budget=100, batch_size=100
    )
    paths = {proposal.parameter.path for proposal in proposals}

    assert len(proposals) == MAX_CANDIDATES_PER_BATCH
    assert {
        "object.primitive.center[0]",
        "object.primitive.center[1]",
        "object.primitive.axes[0]",
        "object.primitive.axes[1]",
        "canvas.background[0]",
        "canvas.background[1]",
        "canvas.background[2]",
        "object.color_field.inner[0]",
        "object.color_field.inner[1]",
        "object.color_field.inner[2]",
        "object.color_field.outer[0]",
        "object.color_field.outer[1]",
        "object.color_field.outer[2]",
        "object.color_field.origin[0]",
        "object.color_field.origin[1]",
        "object.color_field.scale",
    } == paths
    assert all(proposal.stage == "base" for proposal in proposals)
    assert all(
        MinScene.model_validate(proposal.scene.model_dump()) for proposal in proposals
    )


@pytest.mark.parametrize(
    ("field", "expected_paths"),
    (
        (
            SolidColorField(model="solid", color=(0.5, 0.4, 0.3)),
            {f"object.color_field.color[{index}]" for index in range(3)},
        ),
        (
            LinearColorField(
                model="linear",
                start=(0.9, 0.2, 0.3),
                end=(1.0, 0.9, 0.9),
                direction=(0.0, -1.0),
                offset=0.5,
                scale=1.2,
            ),
            {
                *(f"object.color_field.start[{index}]" for index in range(3)),
                *(f"object.color_field.end[{index}]" for index in range(3)),
                "object.color_field.direction[0]",
                "object.color_field.direction[1]",
                "object.color_field.offset",
                "object.color_field.scale",
            },
        ),
    ),
)
def test_base_candidates_are_color_field_type_aware(field, expected_paths) -> None:
    scene = _scene().model_copy(
        update={"object": _scene().object.model_copy(update={"color_field": field})}
    )
    proposals = propose_min_scene_candidates(
        scene, stage="base", remaining_draw_budget=32, batch_size=32
    )
    paths = {item.parameter.path for item in proposals}

    assert expected_paths <= paths
    if field.model == "solid":
        assert not any("inner" in path or "direction" in path for path in paths)
    else:
        assert not any("inner" in path or ".color[" in path for path in paths)


@pytest.mark.parametrize(
    "field",
    (
        SolidColorField(model="solid", color=(0.5, 0.4, 0.3)),
        RadialColorField(
            model="radial",
            inner=(0.9, 0.5, 0.4),
            outer=(0.4, 0.1, 0.2),
            origin=(-0.35, 0.55),
            scale=1.25,
        ),
        LinearColorField(
            model="linear",
            start=(0.9, 0.2, 0.3),
            end=(1.0, 0.9, 0.9),
            direction=(0.0, -1.0),
            offset=0.5,
            scale=1.2,
        ),
    ),
)
def test_color_field_candidates_never_touch_geometry_or_background(field) -> None:
    scene = _scene().model_copy(
        update={"object": _scene().object.model_copy(update={"color_field": field})}
    )
    proposals = propose_min_scene_candidates(
        scene,
        stage="color_field",
        remaining_draw_budget=MAX_PATCH_CANDIDATE_DRAWS - 1,
        batch_size=MAX_PATCH_CANDIDATE_DRAWS - 1,
    )

    assert len(proposals) <= MAX_PATCH_CANDIDATE_DRAWS - 1
    assert proposals
    assert all(item.parameter.path.startswith("object.color_field.") for item in proposals)
    assert all(item.scene.canvas == scene.canvas for item in proposals)
    assert all(item.scene.object.primitive == scene.object.primitive for item in proposals)
    assert all(item.scene.object.features == scene.object.features for item in proposals)


def test_patch_candidate_draw_limit_reserves_one_raw_draw() -> None:
    local_draw_budget = MAX_PATCH_CANDIDATE_DRAWS - 1

    feature = propose_min_scene_candidates(
        _scene(),
        stage="feature",
        feature_id="highlight",
        remaining_draw_budget=local_draw_budget,
        batch_size=local_draw_budget,
    )
    color_field = propose_min_scene_candidates(
        _scene(),
        stage="color_field",
        remaining_draw_budget=local_draw_budget,
        batch_size=local_draw_budget,
    )

    assert MAX_PATCH_CANDIDATE_DRAWS == 12
    assert len(feature) == local_draw_budget
    assert len(color_field) == local_draw_budget
    assert 1 + len(feature) <= MAX_PATCH_CANDIDATE_DRAWS
    assert 1 + len(color_field) <= MAX_PATCH_CANDIDATE_DRAWS


@pytest.mark.parametrize("feature_id", ["highlight", "rim", "shadow"])
def test_feature_candidates_cover_existing_feature_numeric_fields(
    feature_id: str,
) -> None:
    proposals = propose_min_scene_candidates(
        _scene(),
        stage="feature",
        feature_id=feature_id,
        remaining_draw_budget=20,
        batch_size=20,
    )
    suffixes = {proposal.parameter.path.rsplit(".", 1)[-1] for proposal in proposals}

    assert {
        "center[0]",
        "center[1]",
        "axes[0]",
        "axes[1]",
        "color[0]",
        "color[1]",
        "color[2]",
        "intensity",
    } == suffixes
    assert all(proposal.feature_id == feature_id for proposal in proposals)


def test_candidates_are_budget_bounded_and_deterministic() -> None:
    scene = _scene()
    first = propose_min_scene_candidates(scene, stage="base", remaining_draw_budget=5)
    second = propose_min_scene_candidates(scene, stage="base", remaining_draw_budget=5)

    assert len(first) == 5
    assert first == second
    assert (
        propose_min_scene_candidates(scene, stage="base", remaining_draw_budget=0) == ()
    )
    assert (
        len(
            propose_min_scene_candidates(
                scene, stage="base", remaining_draw_budget=2_000, batch_size=2_000
            )
        )
        == MAX_CANDIDATES_PER_BATCH
    )


def test_boundary_clipping_skips_noops_and_keeps_schema_valid() -> None:
    scene = _scene().model_copy(
        update={
            "object": _scene().object.model_copy(
                update={
                    "color_field": _scene().object.color_field.model_copy(
                        update={"scale": 4.0}
                    )
                }
            )
        }
    )
    proposals = propose_min_scene_candidates(
        scene, stage="base", remaining_draw_budget=100, batch_size=100
    )
    scale = [item for item in proposals if item.parameter.path.endswith(".scale")]

    assert len(scale) == 1
    assert scale[0].direction == "decrease"
    assert 0.05 < scale[0].after < 4.0
    for proposal in proposals:
        assert proposal.scene != scene
        assert (
            proposal.parameter.minimum <= proposal.after <= proposal.parameter.maximum
        )
        assert math.isfinite(proposal.after)
        MinScene.model_validate(proposal.scene.model_dump())


def test_input_is_unchanged_and_mae_acceptance_is_strict_and_serial() -> None:
    scene = _scene()
    before = scene.model_dump(mode="python")
    proposals = propose_min_scene_candidates(
        scene, stage="base", remaining_draw_budget=3
    )
    current = ScoredScene(scene=scene, mae=0.5)

    unchanged = accept_strict_mae_improvement(current, proposals[0], 0.5)
    improved = accept_strict_mae_improvement(unchanged, proposals[1], 0.49)
    rejected = accept_strict_mae_improvement(improved, proposals[2], 0.51)

    assert scene.model_dump(mode="python") == before
    assert unchanged is current
    assert improved.scene == proposals[1].scene
    assert rejected is improved


def test_candidate_plan_rebases_onto_latest_best_for_cumulative_changes() -> None:
    scene = _scene()
    proposals = propose_min_scene_candidates(
        scene,
        stage="base",
        remaining_draw_budget=2,
        batch_size=2,
    )
    first = rebase_candidate_proposal(scene, proposals[0])
    assert first is not None
    second = rebase_candidate_proposal(first.scene, proposals[1])
    assert second is not None

    assert second.scene.object.primitive.center[0] == pytest.approx(0.07)
    assert second.scene.object.primitive.center[1] == pytest.approx(-0.13)
    assert scene.object.primitive.center == (0.1, -0.1)


def test_feature_stage_rejects_non_whitelisted_selection() -> None:
    with pytest.raises(ValueError, match="必须指定"):
        propose_min_scene_candidates(_scene(), stage="feature", remaining_draw_budget=1)
    with pytest.raises(ValueError, match="不存在"):
        propose_min_scene_candidates(
            _scene(),
            stage="feature",
            feature_id="unknown",
            remaining_draw_budget=1,
        )
    with pytest.raises(ValueError, match="不接受 feature_id"):
        propose_min_scene_candidates(
            _scene(),
            stage="color_field",
            feature_id="highlight",
            remaining_draw_budget=1,
        )


@pytest.mark.parametrize("budget", [-1, True, 1.5])
def test_budget_must_be_a_non_negative_integer(budget: object) -> None:
    with pytest.raises(ValueError, match="非负整数"):
        propose_min_scene_candidates(
            _scene(),
            stage="base",
            remaining_draw_budget=budget,  # type: ignore[arg-type]
        )
