"""scene_mvp 无模型诊断脚本的纯函数测试。"""

from __future__ import annotations

from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from scripts.run_scene_mvp_run_diagnostics import (
    bounded_best_circle,
    circle_mask,
    foreground_membership,
    geometry_mask_loss,
    interleave_proposal_directions,
    soft_geometry_loss,
)
from shaderforge.optimization import propose_min_scene_candidates
from shaderforge.perception import perceive_min_target


def test_geometry_helpers_match_hard_iou_and_soft_membership() -> None:
    reference = np.ones((4, 4, 3), dtype=np.float32)
    rendered = reference.copy()
    reference[1:3, 1:3] = (0.7, 0.7, 0.7)
    rendered[1:3, 2:4] = (0.7, 0.7, 0.7)

    reference_mask = foreground_membership(reference, (1.0, 1.0, 1.0), threshold=0.05)
    rendered_mask = foreground_membership(rendered, (1.0, 1.0, 1.0), threshold=0.05)

    assert geometry_mask_loss(reference_mask, rendered_mask) == pytest.approx(2.0 / 3.0)
    assert soft_geometry_loss(
        reference,
        rendered,
        (1.0, 1.0, 1.0),
        low=0.03,
        high=0.10,
    ) == pytest.approx(2.0 / 3.0)


def test_bounded_circle_search_recovers_known_circle() -> None:
    reference = circle_mask(32, 32, center=(0.0, 0.0), radius=0.5)

    result = bounded_best_circle(
        reference,
        center=(0.04, -0.04),
        radius=0.46,
        center_offsets=(-0.04, 0.0, 0.04),
        radius_offsets=(0.0, 0.04),
    )

    assert result["evaluated"] == 18
    assert result["best"]["center"] == [0.0, 0.0]
    assert result["best"]["radius"] == 0.5
    assert result["best"]["geometry_mask_loss"] == 0.0


def test_interleaved_order_gives_each_parameter_both_directions() -> None:
    image = Image.new("RGB", (32, 32), "white")
    pixels = image.load()
    assert pixels is not None
    for y in range(4, 28):
        for x in range(4, 28):
            pixels[x, y] = (245, 80, 130)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    scene = perceive_min_target(buffer.getvalue()).fallback_scene
    feature_id = scene.object.features[0].id
    proposals = propose_min_scene_candidates(
        scene,
        stage="feature",
        feature_id=feature_id,
        remaining_draw_budget=16,
        batch_size=16,
    )

    ordered = interleave_proposal_directions(proposals)

    assert ordered[0].parameter.path == ordered[1].parameter.path
    assert [ordered[0].direction, ordered[1].direction] == [
        "decrease",
        "increase",
    ]
    assert ordered[-2].parameter.path == ordered[-1].parameter.path
    assert [ordered[-2].direction, ordered[-1].direction] == [
        "decrease",
        "increase",
    ]
