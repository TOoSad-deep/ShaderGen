"""ShaderGen 图的状态定义."""

from typing import Annotated, Any

from langgraph.channels import UntrackedValue
from typing_extensions import TypedDict


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
    fallback_shader_graph: Annotated[dict[str, Any], UntrackedValue]
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
