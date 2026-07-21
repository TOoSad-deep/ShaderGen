"""跨 Analysis、Intent、Evaluation 与 Benchmark 共用的 V2 taxonomy。."""

from __future__ import annotations

from typing import Literal

REQUIRED_LAYER_TAXONOMY_VERSION: Literal["required_layer_taxonomy_v1"] = (
    "required_layer_taxonomy_v1"
)
RequiredLayerTaxon = Literal[
    "background",
    "shadow",
    "base_fill",
    "color_lobe",
    "haze",
    "rim",
    "outline",
    "highlight",
    "detail",
    "glow",
]
REQUIRED_LAYER_ORDER: tuple[RequiredLayerTaxon, ...] = (
    "background",
    "shadow",
    "base_fill",
    "color_lobe",
    "haze",
    "rim",
    "outline",
    "highlight",
    "detail",
    "glow",
)

__all__ = [
    "REQUIRED_LAYER_ORDER",
    "REQUIRED_LAYER_TAXONOMY_VERSION",
    "RequiredLayerTaxon",
]
