"""PNG-to-Shader V2.3 production node 公共入口。"""
# ruff: noqa: D415

from agent.app.nodes.png_to_shader_v2.runtime import (
    PNG_TO_SHADER_V2_NODE_IDS,
    BasicMetricVectorV2,
    PngToShaderV2NodeRuntime,
    PromotionSinkOutcomeUncertain,
    V2Renderer,
    V2StateStore,
    build_png_to_shader_v2_fixture_runtime,
    build_png_to_shader_v2_node_callables,
    make_basic_metric_evaluator_v2,
    recover_reserved_budget_v2,
)

__all__ = [
    "BasicMetricVectorV2",
    "PNG_TO_SHADER_V2_NODE_IDS",
    "PngToShaderV2NodeRuntime",
    "PromotionSinkOutcomeUncertain",
    "V2Renderer",
    "V2StateStore",
    "build_png_to_shader_v2_fixture_runtime",
    "build_png_to_shader_v2_node_callables",
    "make_basic_metric_evaluator_v2",
    "recover_reserved_budget_v2",
]
