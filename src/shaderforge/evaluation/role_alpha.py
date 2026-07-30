"""Deterministic role-alpha masks decoded from packed RGB diagnostic renders."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from types import MappingProxyType
from typing import Any

import numpy as np
from PIL import Image

from shaderforge.program_spec.models import LayerRole

ROLE_ALPHA_MASK_VERSION = "role_alpha_mask_v1"
ROLE_ALPHA_MASK_PASSES: Mapping[int, tuple[LayerRole, ...]] = MappingProxyType(
    {
        1: ("subject", "highlight", "detail"),
        2: ("shadow", "glow", "background"),
    }
)


def _positive_dimension(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"诊断渲染 {name} 必须是正整数。")
    return value


def _validate_roles(roles: object) -> tuple[LayerRole, ...]:
    if not isinstance(roles, tuple):
        raise ValueError("role alpha roles 必须是固定 pass 的 tuple。")
    if roles not in ROLE_ALPHA_MASK_PASSES.values():
        raise ValueError("role alpha roles 必须完整匹配一个固定 diagnostic pass。")
    return roles


def encode_grayscale_png(alpha_bytes: bytes, width: int, height: int) -> bytes:
    """Encode one exact-size uint8 alpha plane as a deterministic grayscale PNG."""
    checked_width = _positive_dimension(width, "width")
    checked_height = _positive_dimension(height, "height")
    if (
        not isinstance(alpha_bytes, bytes)
        or len(alpha_bytes) != checked_width * checked_height
    ):
        raise ValueError("alpha 平面必须是恰好 width * height 字节的 bytes。")
    image = Image.frombytes("L", (checked_width, checked_height), alpha_bytes)
    output = BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


@dataclass(frozen=True)
class RoleAlphaMaskV1:
    """An immutable uint8 alpha plane and its deterministic grayscale PNG."""

    role: LayerRole
    width: int
    height: int
    alpha_bytes: bytes
    png_bytes: bytes
    schema_version: str = ROLE_ALPHA_MASK_VERSION

    def __post_init__(self) -> None:
        """Fail closed if a manually constructed mask violates the binary contract."""
        if self.role not in {
            role for roles in ROLE_ALPHA_MASK_PASSES.values() for role in roles
        }:
            raise ValueError("role alpha mask 包含未知角色。")
        expected_size = _positive_dimension(self.width, "width") * _positive_dimension(
            self.height, "height"
        )
        if (
            not isinstance(self.alpha_bytes, bytes)
            or len(self.alpha_bytes) != expected_size
        ):
            raise ValueError("alpha 平面必须是恰好 width * height 字节的 bytes。")
        if not isinstance(self.png_bytes, bytes) or not self.png_bytes:
            raise ValueError("role alpha mask PNG 必须是非空 bytes。")

    @property
    def alpha_sha256(self) -> str:
        """Return the hash of the semantic uint8 plane, not PNG container bytes."""
        return sha256(self.alpha_bytes).hexdigest()

    @property
    def nonzero_pixel_ratio(self) -> float:
        """Return the fraction of pixels whose alpha is nonzero."""
        return float(
            np.count_nonzero(np.frombuffer(self.alpha_bytes, dtype=np.uint8))
        ) / (self.width * self.height)

    def to_dict(self) -> dict[str, Any]:
        """Return safe metadata, intentionally omitting alpha and PNG byte fields."""
        return {
            "schema_version": self.schema_version,
            "role": self.role,
            "width": self.width,
            "height": self.height,
            "alpha_sha256": self.alpha_sha256,
            "nonzero_pixel_ratio": self.nonzero_pixel_ratio,
            "alpha_size_bytes": len(self.alpha_bytes),
            "png_sha256": sha256(self.png_bytes).hexdigest(),
            "png_size_bytes": len(self.png_bytes),
        }


def decode_role_alpha_masks(
    rgb_bytes: bytes,
    width: int,
    height: int,
    roles: tuple[LayerRole, ...],
) -> Mapping[LayerRole, RoleAlphaMaskV1]:
    """Split one strict RGB diagnostic frame into the supplied fixed-pass roles.

    ``ROLE_ALPHA_MASK_PASSES[1]`` binds RGB to subject/highlight/detail, while
    pass 2 binds RGB to shadow/glow/background. Returned mapping order is the
    canonical role order of the validated pass.
    """
    checked_width = _positive_dimension(width, "width")
    checked_height = _positive_dimension(height, "height")
    checked_roles = _validate_roles(roles)
    expected_size = checked_width * checked_height * 3
    if not isinstance(rgb_bytes, bytes) or len(rgb_bytes) != expected_size:
        raise ValueError("诊断 RGB 必须是恰好 width * height * 3 字节的 bytes。")
    rgb = np.frombuffer(rgb_bytes, dtype=np.uint8).reshape(
        checked_height, checked_width, 3
    )
    decoded = {
        role: RoleAlphaMaskV1(
            role=role,
            width=checked_width,
            height=checked_height,
            alpha_bytes=rgb[..., channel].tobytes(),
            png_bytes=encode_grayscale_png(
                rgb[..., channel].tobytes(), checked_width, checked_height
            ),
        )
        for channel, role in enumerate(checked_roles)
    }
    return MappingProxyType(decoded)


__all__ = [
    "ROLE_ALPHA_MASK_PASSES",
    "ROLE_ALPHA_MASK_VERSION",
    "RoleAlphaMaskV1",
    "decode_role_alpha_masks",
    "encode_grayscale_png",
]
