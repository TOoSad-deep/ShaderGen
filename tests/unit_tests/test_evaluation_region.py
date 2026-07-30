"""Focused local ROI metric contract tests."""

from __future__ import annotations

import numpy as np
import pytest

from shaderforge.evaluation import evaluate_focused_region, normalize_uv_bbox


def test_top_and_bottom_uv_regions_follow_spatial_residual_v2_coordinates() -> None:
    reference = np.zeros((4, 4, 3), dtype=np.float32)
    rendered = reference.copy()
    rendered[0, 0] = 1.0
    top = evaluate_focused_region(
        reference,
        rendered,
        {"x": 0, "y": 0.75, "width": 0.25, "height": 0.25},
        background=(0, 0, 0),
        dilation_radius=0,
    )
    bottom = evaluate_focused_region(
        reference,
        rendered,
        {"x": 0, "y": 0, "width": 0.25, "height": 0.25},
        background=(0, 0, 0),
        dilation_radius=0,
    )
    assert top.roi_mae == pytest.approx(1.0)
    assert top.outside_roi_mae == pytest.approx(0.0)
    assert bottom.roi_mae == pytest.approx(0.0)
    assert bottom.outside_roi_mae == pytest.approx(1 / 15)


def test_normalize_uv_bbox_clips_boundaries() -> None:
    bbox = normalize_uv_bbox({"x": -0.2, "y": 0.8, "width": 0.5, "height": 0.4})
    assert bbox.to_dict() == pytest.approx(
        {"x": 0.0, "y": 0.8, "width": 0.3, "height": 0.2}
    )
    metrics = evaluate_focused_region(
        np.zeros((10, 10, 3), dtype=np.float32),
        np.zeros((10, 10, 3), dtype=np.float32),
        bbox,
        background=(0, 0, 0),
        dilation_radius=0,
    )
    assert metrics.roi_pixel_count == 6


def test_empty_and_full_roi_have_stable_complements() -> None:
    reference = np.zeros((3, 3, 3), dtype=np.float32)
    rendered = reference.copy()
    rendered[1, 1] = 1.0
    empty = evaluate_focused_region(
        reference,
        rendered,
        {"x": 0.5, "y": 0.5, "width": 0, "height": 0},
        background=(0, 0, 0),
    )
    full = evaluate_focused_region(
        reference,
        rendered,
        {"x": -1, "y": -1, "width": 3, "height": 3},
        background=(0, 0, 0),
        dilation_radius=0,
    )
    assert (empty.roi_pixel_count, empty.roi_mae, empty.outside_roi_mae) == (
        0,
        0.0,
        pytest.approx(1 / 9),
    )
    assert (full.outside_roi_pixel_count, full.outside_roi_mae) == (0, 0.0)
    assert full.roi_mae == pytest.approx(1 / 9)


def test_shape_change_is_reflected_in_geometry_and_edge_metrics() -> None:
    reference = np.zeros((8, 8, 3), dtype=np.float32)
    rendered = reference.copy()
    reference[2:5, 2:5] = 1.0
    rendered[2:5, 4:7] = 1.0
    metrics = evaluate_focused_region(
        reference,
        rendered,
        {"x": 0, "y": 0, "width": 1, "height": 1},
        background=(0, 0, 0),
        dilation_radius=0,
    )
    assert metrics.roi_geometry_mask_loss == pytest.approx(0.8)
    assert metrics.roi_edge_loss > 0.0


def test_outside_roi_mae_excludes_dilated_roi() -> None:
    reference = np.zeros((5, 5, 3), dtype=np.float32)
    rendered = reference.copy()
    rendered[2, 2] = 1.0
    metrics = evaluate_focused_region(
        reference,
        rendered,
        {"x": 0.4, "y": 0.4, "width": 0.2, "height": 0.2},
        background=(0, 0, 0),
        dilation_radius=1,
    )
    assert metrics.roi_pixel_count == 5
    assert metrics.roi_mae == pytest.approx(1 / 5)
    assert metrics.outside_roi_mae == pytest.approx(0.0)


def test_geometry_mask_uses_explicit_non_black_background() -> None:
    reference = np.full((4, 4, 3), (0.2, 0.4, 0.6), dtype=np.float32)
    rendered = reference.copy()
    reference[1:3, 1:3] = (0.8, 0.1, 0.1)
    rendered[1:3, 2:4] = (0.8, 0.1, 0.1)
    metrics = evaluate_focused_region(
        reference,
        rendered,
        {"x": 0, "y": 0, "width": 1, "height": 1},
        background=(0.2, 0.4, 0.6),
        dilation_radius=0,
    )
    assert metrics.roi_geometry_mask_loss == pytest.approx(2 / 3)
