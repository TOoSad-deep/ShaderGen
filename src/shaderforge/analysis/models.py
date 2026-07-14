"""参考图片测量结果模型."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ColorSample:
    """量化调色板中的颜色及占比."""

    rgb: tuple[int, int, int]
    fraction: float


@dataclass(frozen=True)
class PixelProbe:
    """用于后续局部颜色比较的代表像素."""

    probe_id: str
    uv: tuple[float, float]
    rgb: tuple[int, int, int]
    purpose: str


@dataclass(frozen=True)
class EdgeSummary:
    """灰度边缘强度摘要."""

    mean_strength: float
    p90_strength: float
    edge_fraction: float
    strongest_uv: tuple[float, float]


@dataclass(frozen=True)
class RegionOfInterest:
    """后续评分和保护使用的归一化区域."""

    region_id: str
    bbox_uv: tuple[float, float, float, float]
    purpose: str
    confidence: float


@dataclass(frozen=True)
class TargetMeasurements:
    """参考图片的确定性测量结果."""

    schema_version: int
    image_sha256: str
    image_width: int
    image_height: int
    analysis_width: int
    analysis_height: int
    border_color_rgb: tuple[int, int, int]
    border_uniformity: float
    foreground_bbox_uv: tuple[float, float, float, float] | None
    foreground_fraction: float
    foreground_confidence: float
    palette: tuple[ColorSample, ...]
    representative_pixels: tuple[PixelProbe, ...]
    edge_summary: EdgeSummary
    roi_candidates: tuple[RegionOfInterest, ...]

    def to_dict(self) -> dict[str, Any]:
        """返回可写入 JSON 和 Prompt 的普通字典."""
        return asdict(self)
