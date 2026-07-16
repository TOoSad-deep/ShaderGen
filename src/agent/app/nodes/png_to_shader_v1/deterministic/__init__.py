"""PNG 转 Shader V1 确定性 Node 的包内公开入口."""

from .candidates import (
    make_materialize_candidate_node,
    make_prepare_compile_repair_node,
    make_prepare_measurement_seed_node,
)
from .context import (
    PrepareContextNode,
    ProjectMemoryReader,
    make_prepare_context_node,
)
from .finalization import make_finalize_png_to_shader_v1_node
from .preparation import (
    make_initialize_png_to_shader_v1_node,
    make_measure_target_node,
    make_persist_visual_analysis_node,
)
from .promotion import (
    PreviewValidatedStrategyNode,
    PromoteValidatedStrategyNode,
    make_preview_validated_strategy_node,
    make_promote_validated_strategy_node,
)
from .render_evaluate import make_render_and_evaluate_node
from .runtime import (
    Clock,
    NodeEvidenceError,
    RendererFactory,
    RenderEvaluator,
    RunNode,
    RunRendererRegistry,
    ShaderRenderer,
)
from .selection import (
    make_load_current_best_node,
    make_persist_visual_review_node,
    make_select_current_best_node,
)

__all__ = [
    "Clock",
    "NodeEvidenceError",
    "PrepareContextNode",
    "PreviewValidatedStrategyNode",
    "ProjectMemoryReader",
    "PromoteValidatedStrategyNode",
    "RenderEvaluator",
    "RendererFactory",
    "RunNode",
    "RunRendererRegistry",
    "ShaderRenderer",
    "make_finalize_png_to_shader_v1_node",
    "make_initialize_png_to_shader_v1_node",
    "make_load_current_best_node",
    "make_materialize_candidate_node",
    "make_measure_target_node",
    "make_persist_visual_analysis_node",
    "make_persist_visual_review_node",
    "make_prepare_context_node",
    "make_prepare_compile_repair_node",
    "make_prepare_measurement_seed_node",
    "make_render_and_evaluate_node",
    "make_select_current_best_node",
    "make_preview_validated_strategy_node",
    "make_promote_validated_strategy_node",
]
