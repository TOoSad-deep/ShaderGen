"""Coordinate-contract tests for the Refine spatial residual."""

from __future__ import annotations

import numpy as np
import pytest

from shaderforge.evaluation import summarize_spatial_residual


def test_spatial_residual_v2_maps_image_top_to_high_webgl_uv() -> None:
    reference = np.zeros((5, 7, 3), dtype=np.float32)
    rendered = reference.copy()
    rendered[0, 0] = 1.0

    residual = summarize_spatial_residual(reference, rendered)

    assert residual["residual_version"] == "spatial_residual_v2"
    assert residual["coordinate_system"] == "webgl_uv_bottom_left"
    assert residual["source_row_origin"] == "image_top"
    worst = residual["worst_tiles"][0]
    assert worst["row"] == 0
    assert worst["column"] == 0
    assert worst["uv_bbox"] == pytest.approx(
        {
            "x": 0.0,
            "y": 0.6,
            "width": 2 / 7,
            "height": 0.4,
        }
    )


def test_spatial_residual_v2_maps_image_bottom_to_low_webgl_uv() -> None:
    reference = np.zeros((5, 7, 3), dtype=np.float32)
    rendered = reference.copy()
    rendered[-1, -1] = 1.0

    residual = summarize_spatial_residual(reference, rendered)

    worst = residual["worst_tiles"][0]
    assert worst["row"] == 3
    assert worst["column"] == 3
    assert worst["uv_bbox"] == pytest.approx(
        {
            "x": 6 / 7,
            "y": 0.0,
            "width": 1 / 7,
            "height": 0.2,
        }
    )
