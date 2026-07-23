"""ShaderGen 图的状态定义."""

from typing import Annotated, Any, Literal

from langgraph.channels import UntrackedValue
from typing_extensions import TypedDict


class PngToShaderV1State(TypedDict, total=False):
    """PNG 转无贴图 Shader V1 有界闭环状态."""

    project_id: str
    phase: str
    quality_preset: str
    iteration: int
    current_candidate_id: str
    current_best_id: str
    current_best_glsl_sha256: str
    current_best_total_loss: float
    current_best_score_summary: dict[str, Any]
    compile_repair_count: int
    visual_refinement_count: int
    no_improvement_count: int
    model_call_count: int
    candidate_sequence: int
    measurement_seed_attempted: bool
    stop_reason: str
    cancelled: bool

    run_id: Annotated[str, UntrackedValue]
    image: Annotated[bytes, UntrackedValue]
    content_type: Annotated[str, UntrackedValue]
    instruction: Annotated[str, UntrackedValue]
    render_contract: Annotated[dict[str, Any], UntrackedValue]
    budget_policy: Annotated[dict[str, Any], UntrackedValue]
    acceptance_policy: Annotated[dict[str, Any], UntrackedValue]
    started_at: Annotated[float, UntrackedValue]
    reference_ref: Annotated[str, UntrackedValue]
    target_measurements: Annotated[Any, UntrackedValue]
    visual_analysis: Annotated[dict[str, Any], UntrackedValue]
    visual_analysis_model: Annotated[str, UntrackedValue]
    author_result: Annotated[dict[str, Any], UntrackedValue]
    previous_author_result: Annotated[dict[str, Any], UntrackedValue]
    author_model: Annotated[str, UntrackedValue]
    candidate_provenance: Annotated[dict[str, Any], UntrackedValue]
    candidate_origin: Annotated[Literal["model", "deterministic"], UntrackedValue]
    candidate_generator_version: Annotated[str | None, UntrackedValue]
    glsl: Annotated[str, UntrackedValue]
    static_validation: Annotated[dict[str, Any], UntrackedValue]
    compile_result: Annotated[dict[str, Any], UntrackedValue]
    render_status: Annotated[str, UntrackedValue]
    rendered_image: Annotated[bytes, UntrackedValue]
    rendered_content_type: Annotated[str, UntrackedValue]
    score_breakdown: Annotated[Any, UntrackedValue]
    residual_summary: Annotated[dict[str, Any], UntrackedValue]
    candidate_record: Annotated[Any, UntrackedValue]
    current_best_record: Annotated[Any, UntrackedValue]
    candidate_records: Annotated[tuple[Any, ...], UntrackedValue]
    current_candidate: Annotated[dict[str, Any], UntrackedValue]
    current_best_candidate: Annotated[dict[str, Any], UntrackedValue]
    render_evidence_binding: Annotated[dict[str, Any], UntrackedValue]
    visual_review: Annotated[dict[str, Any], UntrackedValue]
    visual_critic_model: Annotated[str, UntrackedValue]
    repair_budget: Annotated[dict[str, Any], UntrackedValue]
    structured_output_max_attempts: Annotated[int, UntrackedValue]
    next_action: Annotated[str, UntrackedValue]
    selection_decision: Annotated[dict[str, Any], UntrackedValue]
    selection_ref: Annotated[str, UntrackedValue]
    final_result: Annotated[dict[str, Any], UntrackedValue]
    final_manifest_ref: Annotated[str, UntrackedValue]
    context_pack: Annotated[dict[str, Any], UntrackedValue]
    selected_memory_ids: Annotated[tuple[str, ...], UntrackedValue]
    memory_status: Annotated[str, UntrackedValue]
    model_calls: Annotated[tuple[dict[str, Any], ...], UntrackedValue]
    events: Annotated[tuple[dict[str, Any], ...], UntrackedValue]
    logs: Annotated[tuple[dict[str, Any], ...], UntrackedValue]


class PngToShaderMinState(TypedDict, total=False):
    """scene_mvp 的轻量路由状态与 run 级大对象边界。."""

    project_id: str
    phase: str
    status: str
    stop_reason: str
    quality_preset: str
    run_classification: str
    experiment_id: str | None
    config_fingerprint: str
    report_schema_version: str
    render_count: int
    render_budget: int
    llm_call_count: int
    llm_budget: int
    refine_count: int
    refine_budget: int
    target_mae: float
    target_loss: float
    current_best_mae: float
    current_best_loss: float
    feature_queue: tuple[str, ...]
    refine_branch_resolved: bool

    run_id: Annotated[str, UntrackedValue]
    image: Annotated[bytes, UntrackedValue]
    content_type: Annotated[str, UntrackedValue]
    instruction: Annotated[str, UntrackedValue]
    perception: Annotated[Any, UntrackedValue]
    target_rgb: Annotated[Any, UntrackedValue]
    metric_background: Annotated[Any, UntrackedValue]
    fallback_scene: Annotated[dict[str, Any], UntrackedValue]
    scene: Annotated[dict[str, Any], UntrackedValue]
    materialized: Annotated[Any, UntrackedValue]
    current_glsl: Annotated[str, UntrackedValue]
    current_render: Annotated[bytes, UntrackedValue]
    current_mae: Annotated[float, UntrackedValue]
    current_best: Annotated[dict[str, Any], UntrackedValue]
    residual_summary: Annotated[dict[str, Any], UntrackedValue]
    pending_patch_summary: Annotated[dict[str, Any] | None, UntrackedValue]
    recent_rejected_patch_summaries: Annotated[
        tuple[dict[str, Any], ...], UntrackedValue
    ]
    patch_evidence: Annotated[tuple[dict[str, Any], ...], UntrackedValue]
    author_model: Annotated[str | None, UntrackedValue]
    author_error: Annotated[str | None, UntrackedValue]
    next_action: Annotated[str, UntrackedValue]
    trace: Annotated[tuple[dict[str, Any], ...], UntrackedValue]
    final_result: Annotated[dict[str, Any], UntrackedValue]
    final_manifest_ref: Annotated[str, UntrackedValue]
    error: Annotated[str | None, UntrackedValue]
