"""Shader API schema."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

GenerationMode = Literal["scene_mvp"]
QualityPresetName = Literal["fast", "balanced", "high", "manual"]
ShaderEngineId = Literal["shader_graph_v1", "direct_glsl_layerplan_v1"]
ShaderRepresentation = Literal["shader_document_v1", "shader_program_spec_v1"]


class ShaderEngineAttemptSummary(BaseModel):
    """父 run 可公开的单个 child attempt 安全引用."""

    attempt_id: str
    engine: ShaderEngineId
    representation: ShaderRepresentation
    status: Literal["succeeded", "failed"]
    failure_code: str | None = None


class ShaderShadowSubmissionSummary(BaseModel):
    """production shadow 的非权威提交结果；不代表 attempt 执行成功."""

    model_config = ConfigDict(extra="allow")

    status: str
    reason: str
    attempt_id: str | None = None


class ShaderEngineRunSummary(BaseModel):
    """父 run 的 policy、attempt 与 fallback 安全摘要."""

    model_config = ConfigDict(extra="allow")

    policy_id: str
    policy_sha256: str
    configured_stage: str
    stage: str
    bucket: int | None = None
    selected_attempt_id: str
    attempt_refs: list[ShaderEngineAttemptSummary] = Field(default_factory=list)
    fallback_from: ShaderEngineId | None = None
    fallback_reason: str | None = None
    promotion_authorization_sha256: str | None = None
    shadow_submission: ShaderShadowSubmissionSummary | None = None


class ShaderGraphShadowSummary(BaseModel):
    """不参与产品选择的 ShaderGraph shadow 纵向切片摘要."""

    status: Literal["rendered", "unsupported", "failed"]
    renderer_path: Literal["compiled_graph_program_cache_v1"] | None = None
    dsl_schema_version: str | None = None
    compiler_version: str | None = None
    document_sha256: str | None = None
    topology_sha256: str | None = None
    layer_count: int = 0
    primitive_count: int = 0
    compile_count: int = 0
    cache_hit_count: int = 0
    cache_size: int = 0
    render_duration_ms: float | None = None
    unsupported_features: list[str] = Field(default_factory=list)
    error_code: str | None = None
    resource_summary: dict[str, int] = Field(default_factory=dict)
    shader_graph: dict[str, Any] | None = None


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
    renderer_path: Literal[
        "prepared_uniforms_v1",
        "compiled_graph_program_cache_v1",
        "direct_program_spec_v1",
    ]
    target_mae: float
    target_loss: float
    target_reached: bool
    prepare_duration_ms: float
    uniform_render_count: int
    uniform_render_p95_ms: float
    scene: dict[str, Any] | None = None
    trace: list[dict[str, Any]] = Field(default_factory=list)
    shader_graph_shadow: ShaderGraphShadowSummary | None = None


class ShaderResponse(BaseModel):
    """scene_mvp 产品生成响应."""

    project_id: UUID
    run_id: UUID
    glsl: str
    generation_mode: GenerationMode
    quality_preset: QualityPresetName
    engine: ShaderEngineId | None = None
    representation: ShaderRepresentation | None = None
    engine_run: ShaderEngineRunSummary | None = None
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
