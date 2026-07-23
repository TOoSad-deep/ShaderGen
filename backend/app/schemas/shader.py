"""Shader API schema."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

GenerationMode = Literal["scene_mvp"]
QualityPresetName = Literal["fast", "balanced", "high", "manual"]


class ShaderMinPipelineSummary(BaseModel):
    """scene_mvp 最小流水线的公开运行摘要."""

    mae: float | None = None
    objective_loss: float | None = None
    metric_breakdown: dict[str, Any] = Field(default_factory=dict)
    template_version: str
    render_count: int = 0
    render_budget: int
    llm_call_count: int = 0
    llm_budget: int
    refine_budget: int
    run_classification: Literal["frozen_benchmark", "independent_experiment"]
    experiment_id: str | None = None
    config_fingerprint: str
    report_schema_version: str
    patch_candidate_draw_budget: int
    patch_evidence: list[dict[str, Any]] = Field(default_factory=list)
    renderer_path: Literal["prepared_uniforms_v1"]
    target_mae: float
    target_loss: float
    target_reached: bool
    prepare_duration_ms: float
    uniform_render_count: int
    uniform_render_p95_ms: float
    scene: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = Field(default_factory=list)


class ShaderResponse(BaseModel):
    """scene_mvp 产品生成响应."""

    project_id: UUID
    run_id: UUID
    glsl: str
    generation_mode: GenerationMode
    quality_preset: QualityPresetName
    stop_reason: str | None = None
    render_width: int | None = None
    render_height: int | None = None
    final_render_url: str | None = None
    metrics_url: str | None = None
    manifest_url: str | None = None
    min_pipeline: ShaderMinPipelineSummary


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


class MinRunProgressEvent(BaseModel):
    """scene_mvp 单节点进度事件（白名单，不含图片、Scene 或 GLSL）."""

    model_config = ConfigDict(extra="allow")

    seq: int
    node: str
    status: str
    phase: str | None = None
    elapsed_ms: float | None = None
    duration_ms: float | None = None
    budgets: dict[str, Any] = Field(default_factory=dict)
    counters: dict[str, int] = Field(default_factory=dict)
    best: dict[str, float] = Field(default_factory=dict)
    trace: list[dict[str, Any]] = Field(default_factory=list)
    next_action: str | None = None
    stop_reason: str | None = None


class MinRunProgressSnapshot(BaseModel):
    """运行进度快照：最新计数、质量、当前节点与渲染帧序号."""

    model_config = ConfigDict(extra="allow")

    budgets: dict[str, Any] = Field(default_factory=dict)
    counters: dict[str, int] = Field(default_factory=dict)
    best: dict[str, float] = Field(default_factory=dict)
    current_node: str | None = None
    render_seq: int = 0


class MinRunProgressResponse(BaseModel):
    """scene_mvp 运行进度增量读取响应；未知 run_id 返回 pending."""

    run_id: UUID
    status: Literal["pending", "running", "succeeded", "failed"]
    generation_mode: str | None = None
    quality_preset: str | None = None
    started_at: str | None = None
    latest_seq: int = 0
    events: list[MinRunProgressEvent] = Field(default_factory=list)
    snapshot: MinRunProgressSnapshot = Field(default_factory=MinRunProgressSnapshot)
