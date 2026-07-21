"""Shader API schema."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

MemoryStatus = Literal["durable", "ephemeral", "degraded"]
GenerationMode = Literal["procedural_v1"]
QualityPresetName = Literal["fast", "balanced", "high", "ultra"]


class ShaderReview(BaseModel):
    """Shader 渲染评审."""

    evaluation: str
    suggestions: list[str]


class ShaderScore(BaseModel):
    """V1 最佳候选的确定性评分摘要."""

    metric_version: str
    total_loss: float
    global_rmse: float
    global_mae: float
    edge_loss: float
    geometry_loss: float | None
    representative_pixel_loss: float
    roi_losses: dict[str, float]
    protected_region_losses: dict[str, float]
    effective_weights: dict[str, float]
    diagnostics: list[str]


class ShaderResponse(BaseModel):
    """PNG-to-Shader V1 生成响应."""

    project_id: UUID
    run_id: UUID
    glsl: str
    memory_status: MemoryStatus
    generation_mode: GenerationMode
    quality_preset: QualityPresetName | None = None
    iterations: int = 0
    stop_reason: str | None = None
    best_candidate_id: str | None = None
    unscored_fallback: bool = False
    render_width: int | None = None
    render_height: int | None = None
    final_render_url: str | None = None
    metrics_url: str | None = None
    manifest_url: str | None = None
    score: ShaderScore | None = None
    review: ShaderReview | None = None


class ShaderGenerationErrorDetail(BaseModel):
    """Shader 生成失败的稳定、可安全公开诊断."""

    message: str
    code: str
    run_id: UUID
    stage: str
    retryable: bool
    stop_reason: str | None = None


class ShaderGenerationErrorResponse(BaseModel):
    """兼容 FastAPI detail envelope 的生成失败响应."""

    detail: ShaderGenerationErrorDetail
