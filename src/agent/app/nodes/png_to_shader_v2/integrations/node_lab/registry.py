"""PNG-to-Shader V2 production nodes 的 Node Lab descriptor。."""

from __future__ import annotations

from dataclasses import dataclass

from agent.app.lab.models import ExecutionMode, NodeDescriptor, NodeInputExample
from agent.app.nodes.png_to_shader_v2.runtime import PNG_TO_SHADER_V2_NODE_IDS

PIPELINE_ID = "png_to_shader_v2"
_RUNTIME_SOURCE = "src/agent/app/nodes/png_to_shader_v2/runtime.py"

_STATE_FIELDS = (
    "state_schema_version",
    "graph_id",
    "graph_version",
    "checkpoint_schema_version",
    "checkpoint_namespace",
    "project_id",
    "run_id",
    "run_revision",
    "phase",
    "evaluation_revision",
    "measurements_ref",
    "visual_interpretation_ref",
    "request_constraint_set_ref",
    "hypothesis_branches",
    "hypothesis_cursor",
    "objective_best_id",
    "candidate_summary_refs",
    "active_seed_ref",
    "active_genome_ref",
    "active_compilation_ref",
    "active_diagnostic_compilation_ref",
    "active_render_plan_ref",
    "active_render_progress_ref",
    "active_render_repeatability_ref",
    "active_rendered_structure_evidence_ref",
    "active_rendered_structure_verification_ref",
    "active_evaluation_refs",
    "active_attempt_id",
    "active_semantic_genome_hash",
    "active_attempt_evidence_refs",
    "active_render_call_ordinal",
    "objective_best_ref",
    "budget_state",
    "stop_reason",
)


@dataclass(frozen=True)
class _NodeSpec:
    category: str
    summary: str
    prerequisites: tuple[str, ...]
    side_effects: tuple[str, ...] = ()
    requires_browser: bool = False
    requires_model: bool = False


_SPECS: dict[str, _NodeSpec] = {
    "initialize_run_v2": _NodeSpec(
        "run_lifecycle", "校验 run-scoped 依赖并推进初始化 revision。", ()
    ),
    "prepare_context_v2": _NodeSpec(
        "context", "恢复显式 Context 依赖，不隐式读取项目 Memory。", ()
    ),
    "ingest_target_v2": _NodeSpec(
        "artifact",
        "恢复目标测量与请求约束的完整性绑定。",
        ("measurements_ref", "request_constraint_set_ref"),
        ("artifact_read",),
    ),
    "measure_target_v2": _NodeSpec(
        "analysis",
        "严格恢复 production TargetMeasurementsV2。",
        ("measurements_ref",),
        ("artifact_read",),
    ),
    "analyze_visual_layers_v2": _NodeSpec(
        "model_role",
        "通过同一 production callable 恢复或生成 VisualInterpretationV2。",
        ("measurements_ref",),
        ("artifact_read", "artifact_write", "model_call"),
        requires_model=True,
    ),
    "build_intent_variants_v2": _NodeSpec(
        "intent",
        "从冻结输入构建并物化 hypothesis-bound Intent variants。",
        ("measurements_ref", "visual_interpretation_ref", "request_constraint_set_ref"),
        ("artifact_read", "artifact_write"),
    ),
    "dequeue_hypothesis_v2": _NodeSpec(
        "state_transition",
        "按有界游标激活下一个 hypothesis。",
        ("hypothesis_branches", "hypothesis_cursor"),
    ),
    "plan_strategy_v2": _NodeSpec(
        "strategy",
        "为 active hypothesis 物化确定性策略计划。",
        ("hypothesis_branches", "hypothesis_cursor"),
        ("artifact_read", "artifact_write"),
    ),
    "propose_seed_plans_v2": _NodeSpec(
        "seeding",
        "调用 production matcher 生成恰好三个 SeedPlan。",
        ("hypothesis_branches", "hypothesis_cursor"),
        ("artifact_read", "artifact_write"),
    ),
    "expand_validate_seeds_v2": _NodeSpec(
        "seeding",
        "调用 production expander 与 diversity gate 物化 typed genomes。",
        ("hypothesis_branches", "hypothesis_cursor"),
        ("artifact_read", "artifact_write"),
    ),
    "dequeue_seed_v2": _NodeSpec(
        "state_transition",
        "按 seed_cursor 激活下一个 typed genome。",
        ("hypothesis_branches", "hypothesis_cursor"),
        ("artifact_read",),
    ),
    "prepare_candidate_attempt_v2": _NodeSpec(
        "candidate",
        "冻结候选 attempt 身份并消费有界 attempt budget。",
        ("active_genome_ref", "budget_state"),
        ("artifact_write",),
    ),
    "compile_genome_v2": _NodeSpec(
        "compiler",
        "调用 Deterministic Compiler 并物化 typed CompilationBundle。",
        ("active_genome_ref",),
        ("artifact_read", "artifact_write"),
    ),
    "render_candidate_v2": _NodeSpec(
        "renderer",
        "使用 production Renderer 对当前 compilation 执行单次真实绘制。",
        ("active_compilation_ref", "budget_state"),
        ("artifact_read", "artifact_write", "browser"),
        requires_browser=True,
    ),
    "evaluate_structure_and_basic_score_v2": _NodeSpec(
        "evaluation",
        "运行 production structure/basic evaluator 并物化 typed evidence。",
        (
            "active_genome_ref",
            "active_render_plan_ref",
            "active_render_progress_ref",
            "active_render_repeatability_ref",
        ),
        ("artifact_read", "artifact_write"),
    ),
    "materialize_immutable_candidate_v2": _NodeSpec(
        "candidate",
        "从 typed refs 一次性物化不可变 CandidateRecordV2。",
        (
            "active_genome_ref",
            "active_compilation_ref",
            "active_diagnostic_compilation_ref",
            "active_render_plan_ref",
            "active_render_progress_ref",
            "active_render_repeatability_ref",
            "active_rendered_structure_evidence_ref",
            "active_rendered_structure_verification_ref",
            "active_evaluation_refs",
        ),
        ("artifact_read", "artifact_write"),
    ),
    "select_hypothesis_best_v2": _NodeSpec(
        "selection",
        "按 hypothesis-bound objective 更新 branch best。",
        ("candidate_summary_refs", "hypothesis_branches"),
        ("artifact_read",),
    ),
    "next_seed_v2": _NodeSpec(
        "state_transition",
        "清空 active refs 并进入下一个 seed。",
        ("hypothesis_branches", "hypothesis_cursor"),
    ),
    "next_hypothesis_v2": _NodeSpec(
        "state_transition",
        "完成当前 branch 并进入下一个 hypothesis。",
        ("hypothesis_branches", "hypothesis_cursor"),
    ),
    "select_cross_hypothesis_best_v2": _NodeSpec(
        "selection",
        "在同一目标与公共 profile 下选择 objective best。",
        ("hypothesis_branches", "candidate_summary_refs"),
        ("artifact_read",),
    ),
    "promote_or_skip_v2": _NodeSpec(
        "memory",
        "Node Lab 固定执行无项目写入的 promotion skip/preview。",
        ("objective_best_ref",),
        ("memory_preview_only",),
    ),
    "finalize_v2": _NodeSpec(
        "finalization",
        "从已确认 objective best 安全形成最终状态。",
        (),
        ("artifact_read",),
    ),
}

_REF_SCHEMA: dict[str, object] = {
    "type": ["object", "null"],
    "additionalProperties": False,
}


def _field_schema(field: str) -> dict[str, object]:
    if field.endswith("_ref"):
        return dict(_REF_SCHEMA)
    if field.endswith("_refs"):
        return {"type": "array"}
    return {}


def _state_schema(*, required: tuple[str, ...]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {field: _field_schema(field) for field in _STATE_FIELDS},
        "required": list(required),
        "additionalProperties": False,
    }


def _modes(spec: _NodeSpec) -> list[ExecutionMode]:
    return ["fixture", "mock", "real"] if spec.requires_model else ["deterministic"]


def _profiles(spec: _NodeSpec) -> list[str]:
    result = ["micro", "node", "v2_graph"]
    if spec.requires_browser:
        result.append("renderer_cold")
    if spec.requires_model:
        result.append("model_role")
    return result


def _metrics(spec: _NodeSpec) -> list[str]:
    result = ["schema_pass", "invariant_pass", "duration_ms"]
    if "artifact_write" in spec.side_effects:
        result.append("artifact_integrity_pass")
    if spec.requires_browser:
        result.extend(("compile_success", "render_success", "pixel_sha256"))
    if spec.requires_model:
        result.extend(("parse_pass", "model_call_count", "token_usage"))
    return result


def _example(node_id: str, spec: _NodeSpec) -> NodeInputExample:
    mode: ExecutionMode = "fixture" if spec.requires_model else "deterministic"
    return NodeInputExample(
        example_id=f"{node_id.replace('_', '-')}-success-v1",
        summary=(
            "创建 png_to_shader_v2 LabRun，把完整 PngToShaderV2State 及其不透明 "
            "Artifact 上传为初始快照，再调用同一 production node。"
        ),
        execution_mode=mode,
        fixture_id=(
            "visual-interpretation-v2-success-v1" if spec.requires_model else None
        ),
        inputs={},
    )


def build_png_to_shader_v2_descriptors() -> tuple[NodeDescriptor, ...]:
    """按 production tuple 顺序构造 descriptor；集合漂移在 import/test 时失败。."""
    if set(PNG_TO_SHADER_V2_NODE_IDS) != set(_SPECS):
        missing = sorted(set(PNG_TO_SHADER_V2_NODE_IDS).difference(_SPECS))
        stale = sorted(set(_SPECS).difference(PNG_TO_SHADER_V2_NODE_IDS))
        raise RuntimeError(
            f"V2 descriptor 与 production nodes 漂移：missing={missing}, stale={stale}"
        )
    descriptors: list[NodeDescriptor] = []
    for node_id in PNG_TO_SHADER_V2_NODE_IDS:
        spec = _SPECS[node_id]
        descriptors.append(
            NodeDescriptor(
                pipeline_id=PIPELINE_ID,
                node_id=node_id,
                category=spec.category,
                summary=spec.summary,
                prerequisites=list(spec.prerequisites),
                side_effects=list(spec.side_effects),
                implementation_status="available",
                execution_modes=_modes(spec),
                test_profiles=["unit", "integration"],
                benchmark_profiles=_profiles(spec),
                default_fixture_ids=(
                    ["visual-interpretation-v2-success-v1"]
                    if spec.requires_model
                    else []
                ),
                benchmark_metrics=_metrics(spec),
                cold_start_sensitive=spec.requires_browser,
                requires_browser=spec.requires_browser,
                requires_model=spec.requires_model,
                source_ref=_RUNTIME_SOURCE,
                input_schema=_state_schema(required=_STATE_FIELDS),
                output_schema=_state_schema(required=_STATE_FIELDS),
                input_examples=[_example(node_id, spec)],
            )
        )
    return tuple(descriptors)


__all__ = ["PIPELINE_ID", "build_png_to_shader_v2_descriptors"]
