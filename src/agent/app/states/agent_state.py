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
