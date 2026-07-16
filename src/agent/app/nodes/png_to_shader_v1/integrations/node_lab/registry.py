"""PNG-to-Shader V1 生产 Node 对 Node Lab 暴露的 descriptor 目录."""

from __future__ import annotations

from dataclasses import dataclass

from agent.app.lab.models import (
    ExecutionMode,
    ImplementationStatus,
    NodeDescriptor,
    NodeInputExample,
)


@dataclass(frozen=True)
class _NodeSpec:
    """构造 descriptor 所需的仓库内静态事实."""

    node_id: str
    category: str
    summary: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    side_effects: tuple[str, ...]
    source_ref: str
    requires_model: bool = False
    requires_browser: bool = False
    cold_start_sensitive: bool = False
    default_fixture_ids: tuple[str, ...] = ()


_DETERMINISTIC_ROOT = "src/agent/app/nodes/png_to_shader_v1/deterministic"
_MODEL_ROOT = "src/agent/app/nodes/png_to_shader_v1/model"
_PREPARATION_SOURCE = f"{_DETERMINISTIC_ROOT}/preparation.py"
_CANDIDATES_SOURCE = f"{_DETERMINISTIC_ROOT}/candidates.py"
_RENDER_EVALUATE_SOURCE = f"{_DETERMINISTIC_ROOT}/render_evaluate.py"
_SELECTION_SOURCE = f"{_DETERMINISTIC_ROOT}/selection.py"
_FINALIZATION_SOURCE = f"{_DETERMINISTIC_ROOT}/finalization.py"
_CONTEXT_SOURCE = f"{_DETERMINISTIC_ROOT}/context.py"
_PROMOTION_SOURCE = f"{_DETERMINISTIC_ROOT}/promotion.py"
_VISUAL_ANALYSIS_SOURCE = f"{_MODEL_ROOT}/visual_analysis.py"
_SHADER_AUTHOR_SOURCE = f"{_MODEL_ROOT}/shader_author.py"
_VISUAL_CRITIC_SOURCE = f"{_MODEL_ROOT}/visual_critic.py"
_ROUTING_SOURCE = "src/agent/app/graphs/png_to_shader_v1_routing.py"

_NODE_SPECS = (
    _NodeSpec(
        "initialize_run",
        "run_lifecycle",
        "规范化输入并冻结运行契约、预算和根 Artifact。",
        (
            "source_artifact_id",
            "project_id",
            "quality_preset",
            "budget_policy",
            "acceptance_policy",
            "instruction",
            "run_id",
            "cancelled",
            "memory_status",
        ),
        (
            "project_id",
            "run_id",
            "phase",
            "reference_artifact_id",
            "run_config_artifact_id",
            "render_contract",
            "budget_policy",
            "acceptance_policy",
            "started_at",
            "candidate_records",
            "events",
        ),
        ("artifact_write",),
        _PREPARATION_SOURCE,
    ),
    _NodeSpec(
        "prepare_context",
        "context",
        "从项目 Memory 构建受限 ContextPack。",
        (
            "project_id",
            "context_policy",
            "memory_strict",
            "current_best_glsl_sha256",
            "last_glsl_sha256",
            "phase",
            "iteration",
            "run_id",
            "memory_status",
        ),
        (
            "context_pack_artifact_id",
            "context_summary",
            "selected_memory_ids",
            "memory_status",
            "events",
        ),
        ("memory_read",),
        _CONTEXT_SOURCE,
    ),
    _NodeSpec(
        "measure_target",
        "analysis",
        "从规范化参考图提取确定性 TargetMeasurements。",
        ("reference_artifact_id", "max_long_side"),
        ("target_measurements", "measurements_artifact"),
        ("artifact_read", "artifact_write"),
        _PREPARATION_SOURCE,
    ),
    _NodeSpec(
        "visual_analysis",
        "model_role",
        "把参考图和测量证据转换为严格 VisualAnalysis。",
        (
            "reference_artifact_id",
            "target_measurements",
            "render_contract",
            "instruction",
            "context_pack",
            "context_pack_artifact_id",
            "image_artifact_id",
            "budget_policy",
            "started_at",
            "structured_output_max_attempts",
        ),
        (
            "preview",
            "visual_analysis",
            "visual_analysis_artifact_id",
            "visual_analysis_model",
            "model_calls",
            "model_call_count",
            "phase",
            "stop_reason",
            "events",
            "logs",
        ),
        ("model_call",),
        _VISUAL_ANALYSIS_SOURCE,
        requires_model=True,
        default_fixture_ids=(
            "visual-analysis-success-v1",
            "visual-analysis-parser-rejected-v1",
        ),
    ),
    _NodeSpec(
        "persist_visual_analysis",
        "artifact",
        "保存 VisualAnalysis 与其证据绑定。",
        ("visual_analysis_artifact_id",),
        ("visual_analysis_artifact_id", "phase", "events"),
        ("artifact_write",),
        _PREPARATION_SOURCE,
    ),
    _NodeSpec(
        "author_initial",
        "model_role",
        "根据分析和 Context 生成首个完整无贴图 WebGL1 Shader。",
        (
            "reference_artifact_id",
            "visual_analysis",
            "target_measurements",
            "render_contract",
            "instruction",
            "context_pack",
            "context_pack_artifact_id",
            "visual_analysis_artifact_id",
            "image_artifact_id",
            "budget_policy",
            "started_at",
            "structured_output_max_attempts",
        ),
        (
            "preview",
            "author_artifact_id",
            "author_summary",
            "glsl_artifact_id",
            "glsl_sha256",
            "glsl_chars",
            "candidate_provenance_artifact_id",
            "author_model",
            "model_calls",
            "model_call_count",
            "phase",
            "stop_reason",
            "events",
            "logs",
        ),
        ("model_call",),
        _SHADER_AUTHOR_SOURCE,
        requires_model=True,
        default_fixture_ids=(
            "author-initial-success-v1",
            "author-initial-parser-rejected-v1",
        ),
    ),
    _NodeSpec(
        "materialize_candidate",
        "candidate",
        "为模型 Author 或确定性 seed 分配候选并原子保存源码与 provenance。",
        (
            "author_artifact_id",
            "candidate_provenance_artifact_id",
            "glsl_artifact_id",
            "candidate_origin",
            "candidate_generator_version",
            "candidate_sequence",
            "current_candidate_id",
            "visual_refinement_count",
            "candidate_records",
        ),
        (
            "candidate_record",
            "current_candidate_id",
            "candidate_sequence",
            "candidate_manifest_artifact_id",
            "candidate_records",
            "phase",
            "render_status",
            "events",
        ),
        ("artifact_write",),
        _CANDIDATES_SOURCE,
    ),
    _NodeSpec(
        "render_and_evaluate",
        "fact_layer",
        "执行静态校验、真实 WebGL1 渲染和 Oracle 评分。",
        (
            "candidate_record",
            "candidate_records",
            "shader_artifact_id",
            "glsl_artifact_id",
            "reference_artifact_id",
            "target_measurements",
            "render_contract",
            "budget_policy",
            "width",
            "height",
        ),
        (
            "static_validation",
            "compile_result",
            "render_status",
            "score_breakdown",
            "rendered_image_artifact_id",
            "candidate_record",
            "candidate_records",
            "phase",
            "events",
            "stop_reason",
        ),
        ("browser", "artifact_write"),
        _RENDER_EVALUATE_SOURCE,
        requires_browser=True,
        cold_start_sensitive=True,
    ),
    _NodeSpec(
        "decide_after_render",
        "routing",
        "根据渲染事实、取消状态和预算选择下一动作。",
        (
            "render_status",
            "budget_policy",
            "cancelled",
            "stop_reason",
            "model_call_count",
            "compile_repair_count",
        ),
        ("next_action", "stop_reason"),
        (),
        _ROUTING_SOURCE,
        default_fixture_ids=("decide-after-render-success-v1",),
    ),
    _NodeSpec(
        "prepare_compile_repair",
        "state_transition",
        "为编译修复冻结旧 Author 结果和剩余预算。",
        ("author_artifact_id", "budget_policy", "compile_repair_count"),
        ("previous_author_artifact_id", "repair_budget", "phase"),
        (),
        _CANDIDATES_SOURCE,
    ),
    _NodeSpec(
        "author_compile_repair",
        "model_role",
        "根据绑定的编译诊断修复完整 GLSL，禁止视觉重写。",
        (
            "previous_author_artifact_id",
            "glsl_artifact_id",
            "static_validation",
            "compile_result",
            "repair_budget",
            "context_pack",
            "context_pack_artifact_id",
            "budget_policy",
            "started_at",
            "structured_output_max_attempts",
        ),
        (
            "preview",
            "author_artifact_id",
            "author_summary",
            "glsl_artifact_id",
            "glsl_sha256",
            "glsl_chars",
            "candidate_provenance_artifact_id",
            "author_model",
            "model_calls",
            "model_call_count",
            "compile_repair_count",
            "phase",
            "stop_reason",
            "events",
            "logs",
        ),
        ("model_call",),
        _SHADER_AUTHOR_SOURCE,
        requires_model=True,
        default_fixture_ids=(
            "author-compile-repair-success-v1",
            "author-compile-repair-parser-rejected-v1",
        ),
    ),
    _NodeSpec(
        "select_current_best",
        "selection",
        "按硬门禁、最小改善和保护区规则单调更新 current_best。",
        (
            "candidate_record",
            "acceptance_policy",
            "current_best_record",
            "no_improvement_count",
        ),
        (
            "selection_decision",
            "selection_artifact_id",
            "current_best_record",
            "current_best_id",
            "current_best_glsl_sha256",
            "current_best_total_loss",
            "current_best_score_summary",
            "no_improvement_count",
            "iteration",
            "phase",
            "events",
        ),
        ("artifact_write",),
        _SELECTION_SOURCE,
    ),
    _NodeSpec(
        "prepare_measurement_seed",
        "deterministic_generator",
        "根据规范化参考图和 TargetMeasurements 生成独立 affine 根候选。",
        (
            "reference_artifact_id",
            "target_measurements",
            "measurement_seed_attempted",
            "events",
        ),
        (
            "phase",
            "measurement_seed_attempted",
            "author_artifact_id",
            "author_summary",
            "glsl_artifact_id",
            "glsl_sha256",
            "glsl_chars",
            "candidate_provenance_artifact_id",
            "author_model",
            "candidate_origin",
            "candidate_generator_version",
            "events",
        ),
        ("artifact_read", "artifact_write"),
        _CANDIDATES_SOURCE,
    ),
    _NodeSpec(
        "decide_after_selection",
        "routing",
        "按质量阈值、停滞和预算决定 Critic 或 finalize。",
        (
            "current_best_total_loss",
            "budget_policy",
            "acceptance_policy",
            "no_improvement_count",
            "visual_refinement_count",
            "model_call_count",
            "cancelled",
            "stop_reason",
        ),
        ("next_action", "stop_reason"),
        (),
        _ROUTING_SOURCE,
    ),
    _NodeSpec(
        "load_current_best",
        "artifact",
        "按 SHA-256 绑定从 Artifact 重载 current_best 证据。",
        ("current_best_record",),
        (
            "candidate_record",
            "author_artifact_id",
            "glsl_artifact_id",
            "rendered_image_artifact_id",
            "score_breakdown",
            "render_evidence_binding",
            "current_candidate",
            "current_best_candidate",
            "residual_summary",
            "phase",
        ),
        ("artifact_read",),
        _SELECTION_SOURCE,
    ),
    _NodeSpec(
        "visual_critic",
        "model_role",
        "对绑定的 reference、render、GLSL 和 score 生成严格 Review。",
        (
            "current_candidate",
            "render_evidence_binding",
            "score_breakdown",
            "reference_artifact_id",
            "render_artifact_id",
            "rendered_image_artifact_id",
            "glsl_artifact_id",
            "target_measurements",
            "visual_analysis",
            "visual_analysis_artifact_id",
            "render_contract",
            "context_pack",
            "context_pack_artifact_id",
            "budget_policy",
            "started_at",
            "structured_output_max_attempts",
        ),
        (
            "preview",
            "visual_review",
            "visual_review_artifact_id",
            "visual_critic_model",
            "model_calls",
            "model_call_count",
            "phase",
            "stop_reason",
            "events",
            "logs",
        ),
        ("model_call",),
        _VISUAL_CRITIC_SOURCE,
        requires_model=True,
        default_fixture_ids=(
            "visual-critic-success-v1",
            "visual-critic-parser-rejected-v1",
        ),
    ),
    _NodeSpec(
        "persist_visual_review",
        "artifact",
        "保存与 current_best candidate id 一致的 VisualReview。",
        (
            "current_best_record",
            "candidate_records",
            "visual_review_artifact_id",
        ),
        (
            "review_artifact_id",
            "candidate_manifest_artifact_id",
            "current_best_record",
            "candidate_record",
            "candidate_records",
            "phase",
            "events",
        ),
        ("artifact_write",),
        _SELECTION_SOURCE,
    ),
    _NodeSpec(
        "author_visual_refine",
        "model_role",
        "基于 current_best 与 Critic 证据生成视觉修订候选。",
        (
            "current_best_candidate",
            "visual_review",
            "visual_review_artifact_id",
            "render_evidence_binding",
            "reference_artifact_id",
            "render_artifact_id",
            "rendered_image_artifact_id",
            "glsl_artifact_id",
            "target_measurements",
            "visual_analysis",
            "visual_analysis_artifact_id",
            "score_breakdown",
            "render_contract",
            "context_pack",
            "context_pack_artifact_id",
            "budget_policy",
            "started_at",
            "structured_output_max_attempts",
        ),
        (
            "preview",
            "author_artifact_id",
            "author_summary",
            "glsl_artifact_id",
            "glsl_sha256",
            "glsl_chars",
            "candidate_provenance_artifact_id",
            "author_model",
            "model_calls",
            "model_call_count",
            "visual_refinement_count",
            "phase",
            "stop_reason",
            "events",
            "logs",
        ),
        ("model_call",),
        _SHADER_AUTHOR_SOURCE,
        requires_model=True,
        default_fixture_ids=(
            "author-visual-refine-success-v1",
            "author-visual-refine-parser-rejected-v1",
        ),
    ),
    _NodeSpec(
        "finalize",
        "run_lifecycle",
        "只从 best 或明确 fallback Artifact 形成最终结果并清理 Renderer。",
        (
            "current_best_record",
            "candidate_records",
            "stop_reason",
            "target_measurements",
            "project_id",
            "run_id",
        ),
        (
            "final_result",
            "final_manifest_artifact_id",
            "phase",
            "stop_reason",
            "events",
        ),
        ("artifact_read", "artifact_write", "browser_cleanup"),
        _FINALIZATION_SOURCE,
        requires_browser=True,
        cold_start_sensitive=True,
    ),
    _NodeSpec(
        "promote_validated_strategy",
        "memory",
        "为已验证 current_best 生成策略 Memory 晋升预览。",
        ("project_id", "run_id", "current_best_record", "final_result"),
        ("memory_preview", "memory_status", "events"),
        ("memory_preview_only",),
        _PROMOTION_SOURCE,
    ),
)


_OPTIONAL_INPUT_FIELDS: dict[str, frozenset[str]] = {
    "initialize_run": frozenset(
        {
            "project_id",
            "quality_preset",
            "budget_policy",
            "acceptance_policy",
            "instruction",
            "run_id",
            "cancelled",
            "memory_status",
        }
    ),
    "prepare_context": frozenset(
        {
            "project_id",
            "context_policy",
            "memory_strict",
            "current_best_glsl_sha256",
            "last_glsl_sha256",
            "phase",
            "iteration",
            "run_id",
            "memory_status",
        }
    ),
    "measure_target": frozenset({"max_long_side"}),
    "visual_analysis": frozenset(
        {
            "render_contract",
            "instruction",
            "context_pack",
            "context_pack_artifact_id",
            "image_artifact_id",
            "budget_policy",
            "started_at",
            "structured_output_max_attempts",
        }
    ),
    "author_initial": frozenset(
        {
            "render_contract",
            "instruction",
            "context_pack",
            "context_pack_artifact_id",
            "visual_analysis",
            "image_artifact_id",
            "budget_policy",
            "started_at",
            "structured_output_max_attempts",
        }
    ),
    "materialize_candidate": frozenset(
        {
            "candidate_origin",
            "candidate_generator_version",
            "candidate_sequence",
            "current_candidate_id",
            "visual_refinement_count",
            "candidate_records",
        }
    ),
    "render_and_evaluate": frozenset(
        {
            "candidate_records",
            "glsl_artifact_id",
            "render_contract",
            "budget_policy",
            "width",
            "height",
        }
    ),
    "decide_after_render": frozenset(
        {"cancelled", "stop_reason", "model_call_count", "compile_repair_count"}
    ),
    "prepare_compile_repair": frozenset({"compile_repair_count"}),
    "author_compile_repair": frozenset(
        {
            "context_pack",
            "context_pack_artifact_id",
            "budget_policy",
            "started_at",
            "structured_output_max_attempts",
        }
    ),
    "select_current_best": frozenset(
        {"current_best_record", "acceptance_policy", "no_improvement_count"}
    ),
    "prepare_measurement_seed": frozenset({"measurement_seed_attempted", "events"}),
    "decide_after_selection": frozenset(
        {
            "no_improvement_count",
            "visual_refinement_count",
            "model_call_count",
            "cancelled",
            "stop_reason",
        }
    ),
    "visual_critic": frozenset(
        {
            "render_artifact_id",
            "visual_analysis",
            "context_pack",
            "context_pack_artifact_id",
            "render_contract",
            "budget_policy",
            "started_at",
            "structured_output_max_attempts",
        }
    ),
    "author_visual_refine": frozenset(
        {
            "render_artifact_id",
            "visual_review",
            "visual_analysis",
            "context_pack",
            "context_pack_artifact_id",
            "render_contract",
            "budget_policy",
            "started_at",
            "structured_output_max_attempts",
        }
    ),
    "finalize": frozenset(
        {
            "current_best_record",
            "candidate_records",
            "stop_reason",
            "project_id",
            "run_id",
        }
    ),
    "promote_validated_strategy": frozenset(
        {"project_id", "run_id", "current_best_record"}
    ),
}

_OPTIONAL_OUTPUT_FIELDS: dict[str, frozenset[str]] = {
    "visual_analysis": frozenset(
        field
        for field in (
            "preview",
            "visual_analysis",
            "visual_analysis_artifact_id",
            "visual_analysis_model",
            "model_calls",
            "model_call_count",
            "phase",
            "stop_reason",
            "events",
            "logs",
        )
    ),
    "author_initial": frozenset(
        field
        for field in (
            "preview",
            "author_artifact_id",
            "author_summary",
            "glsl_artifact_id",
            "glsl_sha256",
            "glsl_chars",
            "candidate_provenance_artifact_id",
            "author_model",
            "model_calls",
            "model_call_count",
            "phase",
            "stop_reason",
            "events",
            "logs",
        )
    ),
    "render_and_evaluate": frozenset(
        {
            "static_validation",
            "compile_result",
            "score_breakdown",
            "rendered_image_artifact_id",
            "candidate_record",
            "candidate_records",
            "events",
            "stop_reason",
        }
    ),
    "decide_after_render": frozenset({"stop_reason"}),
    "author_compile_repair": frozenset(
        {
            "preview",
            "author_artifact_id",
            "author_summary",
            "glsl_artifact_id",
            "glsl_sha256",
            "glsl_chars",
            "candidate_provenance_artifact_id",
            "author_model",
            "model_calls",
            "model_call_count",
            "compile_repair_count",
            "phase",
            "stop_reason",
            "events",
            "logs",
        }
    ),
    "select_current_best": frozenset(
        {
            "current_best_record",
            "current_best_id",
            "current_best_glsl_sha256",
            "current_best_total_loss",
            "current_best_score_summary",
            "iteration",
        }
    ),
    "decide_after_selection": frozenset({"stop_reason"}),
    "visual_critic": frozenset(
        {
            "preview",
            "visual_review",
            "visual_review_artifact_id",
            "visual_critic_model",
            "model_calls",
            "model_call_count",
            "phase",
            "stop_reason",
            "events",
            "logs",
        }
    ),
    "author_visual_refine": frozenset(
        {
            "preview",
            "author_artifact_id",
            "author_summary",
            "glsl_artifact_id",
            "glsl_sha256",
            "glsl_chars",
            "candidate_provenance_artifact_id",
            "author_model",
            "model_calls",
            "model_call_count",
            "visual_refinement_count",
            "phase",
            "stop_reason",
            "events",
            "logs",
        }
    ),
    "promote_validated_strategy": frozenset({"memory_status"}),
}

_ARTIFACT_ID_SCHEMA: dict[str, object] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
}


def _field_schema(field: str) -> dict[str, object]:
    """为 Lab Artifact 引用提供机器可读 id 约束，其余字段保持开放对象语义."""
    if field.endswith("_artifact_id"):
        return dict(_ARTIFACT_ID_SCHEMA)
    return {}


def _object_schema(
    fields: tuple[str, ...],
    *,
    optional_fields: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """生成稳定顶层 Schema，并显式区分必需前置证据与可选摘要."""
    return {
        "type": "object",
        "properties": {name: _field_schema(name) for name in fields},
        "required": [name for name in fields if name not in optional_fields],
        "additionalProperties": True,
    }


_MODEL_NODE_IDS = {
    "visual_analysis",
    "author_initial",
    "author_compile_repair",
    "visual_critic",
    "author_visual_refine",
}


def _execution_modes(spec: _NodeSpec) -> list[ExecutionMode]:
    """声明每个已接通节点允许的受控执行模式."""
    if spec.node_id in _MODEL_NODE_IDS:
        return ["fixture", "mock", "real"]
    modes: list[ExecutionMode] = ["deterministic"]
    if spec.default_fixture_ids and not spec.requires_model:
        modes.append("fixture")
    return modes


def _implementation_status(spec: _NodeSpec) -> ImplementationStatus:
    """当前 20 个生产图节点均已通过精确 Executor 接通."""
    return "available"


def _benchmark_profiles(spec: _NodeSpec) -> list[str]:
    """按副作用声明可分离统计的 benchmark profile."""
    profiles = ["micro", "node"]
    if spec.requires_browser:
        profiles.append("renderer_cold")
    if spec.requires_model:
        profiles.append("model_role")
    return profiles


def _benchmark_metrics(spec: _NodeSpec) -> list[str]:
    """先登记 correctness，再登记节点特有用量."""
    metrics = ["schema_pass", "invariant_pass", "duration_ms"]
    if spec.requires_browser:
        metrics.extend(["compile_success", "render_success", "pixel_sha256"])
    if spec.requires_model:
        metrics.extend(["parse_pass", "model_call_count", "token_usage"])
    if "artifact_write" in spec.side_effects:
        metrics.append("artifact_integrity_pass")
    return metrics


_BASE_STEP_NODE_IDS: dict[str, str | None] = {
    "initialize_run": None,
    "prepare_context": "initialize_run",
    "measure_target": "prepare_context",
    "visual_analysis": "measure_target",
    "persist_visual_analysis": "visual_analysis",
    "author_initial": "persist_visual_analysis",
    "materialize_candidate": "author_initial",
    "render_and_evaluate": "materialize_candidate",
    "decide_after_render": "render_and_evaluate",
    "prepare_compile_repair": "render_and_evaluate",
    "author_compile_repair": "prepare_compile_repair",
    "select_current_best": "render_and_evaluate",
    "prepare_measurement_seed": "select_current_best",
    "decide_after_selection": "select_current_best",
    "load_current_best": "select_current_best",
    "visual_critic": "load_current_best",
    "persist_visual_review": "visual_critic",
    "author_visual_refine": "persist_visual_review",
    "finalize": "decide_after_selection",
    "promote_validated_strategy": "finalize",
}


def _input_examples(spec: _NodeSpec) -> list[NodeInputExample]:
    """生成可机械解析的成功路径，以及模型 Parser 拒绝路径示例."""
    base_step_node_id = _BASE_STEP_NODE_IDS[spec.node_id]
    inputs: dict[str, object] = {}
    artifact_inputs: dict[str, str] = {}
    if spec.node_id == "initialize_run":
        inputs = {
            "source_artifact_id": "artifact-source-png",
            "quality_preset": "balanced",
            "instruction": "复刻参考图的主要形状、颜色和高光。",
        }
        artifact_inputs = {"source_artifact_id": "source_png"}
    mode: ExecutionMode = "fixture" if spec.requires_model else "deterministic"
    success_fixture_id = spec.default_fixture_ids[0] if spec.requires_model else None
    result = [
        NodeInputExample(
            example_id=f"{spec.node_id.replace('_', '-')}-success-v1",
            summary=(
                "创建 LabRun、上传所需 Artifact，并把指定父节点的 step_id "
                "作为 base_step_id 后执行。"
            ),
            execution_mode=mode,
            base_step_node_id=base_step_node_id,
            fixture_id=success_fixture_id,
            inputs=inputs,
            artifact_inputs=artifact_inputs,
        )
    ]
    if spec.requires_model:
        result.append(
            NodeInputExample(
                example_id=f"{spec.node_id.replace('_', '-')}-parser-rejected-v1",
                summary="重放两次非法结构化输出，验证真实 Parser 拒绝且不调用外部模型。",
                execution_mode="fixture",
                expected_outcome="stopped",
                base_step_node_id=base_step_node_id,
                fixture_id=spec.default_fixture_ids[1],
                inputs={},
            )
        )
    return result


def _node_input_schema(spec: _NodeSpec) -> dict[str, object]:
    """把顶层输入约束和 descriptor 调用示例放进同一 JSON Schema."""
    schema = _object_schema(
        spec.inputs,
        optional_fields=_OPTIONAL_INPUT_FIELDS.get(spec.node_id, frozenset()),
    )
    schema["examples"] = [example.inputs for example in _input_examples(spec)]
    return schema


def build_png_to_shader_v1_descriptors() -> tuple[NodeDescriptor, ...]:
    """构造精确覆盖生产 Graph 节点的 descriptor."""
    return tuple(
        [
            NodeDescriptor(
                node_id=spec.node_id,
                category=spec.category,
                summary=spec.summary,
                prerequisites=[
                    field
                    for field in spec.inputs
                    if field
                    not in _OPTIONAL_INPUT_FIELDS.get(spec.node_id, frozenset())
                ],
                side_effects=list(spec.side_effects),
                implementation_status=_implementation_status(spec),
                execution_modes=_execution_modes(spec),
                test_profiles=["unit", "integration"],
                benchmark_profiles=_benchmark_profiles(spec),
                default_fixture_ids=list(spec.default_fixture_ids),
                benchmark_metrics=_benchmark_metrics(spec),
                cold_start_sensitive=spec.cold_start_sensitive,
                requires_browser=spec.requires_browser,
                requires_model=spec.requires_model,
                source_ref=spec.source_ref,
                input_schema=_node_input_schema(spec),
                output_schema=_object_schema(
                    spec.outputs,
                    optional_fields=_OPTIONAL_OUTPUT_FIELDS.get(
                        spec.node_id,
                        frozenset(),
                    ),
                ),
                input_examples=_input_examples(spec),
            )
            for spec in _NODE_SPECS
        ]
    )
