"""Contracts for deterministic packed diagnostic role-alpha masks."""

from __future__ import annotations

from io import BytesIO

import numpy as np
import pytest
from PIL import Image

from shaderforge.evaluation import (
    ROLE_ALPHA_MASK_PASSES,
    decode_role_alpha_masks,
    encode_grayscale_png,
)


def test_pass_one_unpacks_rgb_in_fixed_role_order_and_encodes_grayscale_png() -> None:
    rgb = np.asarray(
        [[[1, 2, 3], [0, 5, 0]], [[7, 0, 9], [10, 11, 12]]], dtype=np.uint8
    )
    result = decode_role_alpha_masks(rgb.tobytes(), 2, 2, ROLE_ALPHA_MASK_PASSES[1])

    assert tuple(result) == ("subject", "highlight", "detail")
    assert result["highlight"].alpha_bytes == rgb[..., 1].tobytes()
    decoded = Image.open(BytesIO(result["detail"].png_bytes))
    assert decoded.mode == "L"
    assert np.array_equal(np.asarray(decoded), rgb[..., 2])


def test_pass_two_is_fixed_and_safe_metadata_does_not_embed_bytes() -> None:
    rgb = np.zeros((2, 2, 3), dtype=np.uint8)
    rgb[..., 0] = 255
    result = decode_role_alpha_masks(rgb.tobytes(), 2, 2, ROLE_ALPHA_MASK_PASSES[2])
    payload = result["shadow"].to_dict()

    assert tuple(result) == ("shadow", "glow", "background")
    assert payload["nonzero_pixel_ratio"] == pytest.approx(1.0)
    assert "alpha_bytes" not in payload
    assert "png_bytes" not in payload


@pytest.mark.parametrize(
    ("rgb_bytes", "width", "height", "roles"),
    [
        (b"", 1, 1, ROLE_ALPHA_MASK_PASSES[1]),
        (bytes(4), 1, 1, ROLE_ALPHA_MASK_PASSES[1]),
        (bytes(3), 0, 1, ROLE_ALPHA_MASK_PASSES[1]),
        (bytes(3), 1, 1, ("subject", "detail", "highlight")),
        (bytearray(3), 1, 1, ROLE_ALPHA_MASK_PASSES[1]),
    ],
)
def test_decode_rejects_invalid_frame_dimensions_bytes_or_roles(
    rgb_bytes: bytes, width: int, height: int, roles: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError):
        decode_role_alpha_masks(rgb_bytes, width, height, roles)  # type: ignore[arg-type]


def test_grayscale_png_encoding_is_deterministic_and_strict() -> None:
    alpha = bytes((0, 1, 2, 3))
    assert encode_grayscale_png(alpha, 2, 2) == encode_grayscale_png(alpha, 2, 2)
    with pytest.raises(ValueError):
        encode_grayscale_png(alpha, 3, 2)
