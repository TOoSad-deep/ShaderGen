"""Basic Oracle 的评分模型."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MetricWeights:
    """V1 各评分分量的初始权重."""

    global_rmse: float = 0.35
    global_mae: float = 0.15
    edge: float = 0.15
    geometry: float = 0.15
    representative_pixels: float = 0.10
    roi: float = 0.10

    def __post_init__(self) -> None:
        """拒绝负权重或空目标."""
        values = (
            self.global_rmse,
            self.global_mae,
            self.edge,
            self.geometry,
            self.representative_pixels,
            self.roi,
        )
        if any(value < 0.0 for value in values):
            raise ValueError("Metric weight 不能小于 0。")
        if sum(values) <= 0.0:
            raise ValueError("Metric weights 不能全部为 0。")


@dataclass(frozen=True)
class ScoreBreakdownV1:
    """候选渲染的完整 V1 评分向量."""

    metric_version: str
    total_loss: float
    global_rmse: float
    global_mae: float
    edge_loss: float
    geometry_loss: float | None
    representative_pixel_loss: float
    roi_losses: tuple[tuple[str, float], ...]
    protected_region_losses: tuple[tuple[str, float], ...]
    effective_weights: tuple[tuple[str, float], ...]
    diagnostics: tuple[str, ...]

    @property
    def roi_loss_map(self) -> dict[str, float]:
        """以字典读取 ROI loss."""
        return dict(self.roi_losses)

    @property
    def protected_region_loss_map(self) -> dict[str, float]:
        """以字典读取 protection loss."""
        return dict(self.protected_region_losses)

    def to_dict(self) -> dict[str, Any]:
        """返回 API/Artifact 友好的普通字典."""
        return {
            "metric_version": self.metric_version,
            "total_loss": self.total_loss,
            "global_rmse": self.global_rmse,
            "global_mae": self.global_mae,
            "edge_loss": self.edge_loss,
            "geometry_loss": self.geometry_loss,
            "representative_pixel_loss": self.representative_pixel_loss,
            "roi_losses": self.roi_loss_map,
            "protected_region_losses": self.protected_region_loss_map,
            "effective_weights": dict(self.effective_weights),
            "diagnostics": list(self.diagnostics),
        }
