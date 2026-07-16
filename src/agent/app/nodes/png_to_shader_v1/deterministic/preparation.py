"""PNG 转 Shader V1 的运行初始化与目标准备节点."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from shaderforge.analysis import (
    measure_target,
    normalize_target_png,
)
from shaderforge.contracts import (
    DEFAULT_ACCEPTANCE_POLICY,
    WEBGL1_STATIC_NO_TEXTURE_V1,
    AcceptancePolicy,
    BudgetPolicy,
    QualityPreset,
    budget_for_preset,
)
from shaderforge.store import LocalArtifactStore

from .runtime import (
    Clock,
    RunNode,
    _run_store,
    logger,
)


def make_initialize_png_to_shader_v1_node(
    artifact_store: LocalArtifactStore,
    *,
    clock: Clock,
) -> RunNode:
    """创建规范化输入并冻结本次策略的初始化节点."""

    async def initialize(state: Mapping[str, Any]) -> dict[str, Any]:
        project_id = str(state.get("project_id", "")).strip()
        if not project_id:
            raise ValueError("project_id 不能为空。")
        image = state.get("image")
        if not isinstance(image, bytes) or not image:
            raise ValueError("image 必须是非空 bytes。")

        preset_value = str(state.get("quality_preset", QualityPreset.BALANCED.value))
        preset = QualityPreset(preset_value)
        raw_budget = state.get("budget_policy")
        budget = (
            raw_budget
            if isinstance(raw_budget, BudgetPolicy)
            else BudgetPolicy(**dict(raw_budget))
            if raw_budget is not None
            else budget_for_preset(preset)
        )
        high_ceiling = budget_for_preset(QualityPreset.HIGH)
        if (
            budget.max_visual_refinements > high_ceiling.max_visual_refinements
            or budget.max_compile_repairs > high_ceiling.max_compile_repairs
            or budget.max_model_calls > high_ceiling.max_model_calls
            or budget.max_wall_time_seconds > high_ceiling.max_wall_time_seconds
            or budget.max_shader_chars > high_ceiling.max_shader_chars
            or budget.renderer_replay_on_crash > high_ceiling.renderer_replay_on_crash
        ):
            raise ValueError("自定义 budget_policy 不得超过 V1 high 档硬上限。")
        raw_acceptance = state.get("acceptance_policy")
        acceptance = (
            raw_acceptance
            if isinstance(raw_acceptance, AcceptancePolicy)
            else AcceptancePolicy(**dict(raw_acceptance))
            if raw_acceptance is not None
            else DEFAULT_ACCEPTANCE_POLICY
        )
        run_id = str(state.get("run_id") or uuid4().hex)
        started_at = clock()
        reference = normalize_target_png(
            image,
            max_long_side=WEBGL1_STATIC_NO_TEXTURE_V1.max_long_side,
        )
        store = artifact_store.register_run(project_id, run_id)
        store.write_bytes("input/source.bin", image)
        reference_ref = store.write_bytes(
            "input/reference.png",
            reference,
            content_type="image/png",
        )
        store.write_json(
            "run-config.json",
            {
                "schema_version": 1,
                "project_id": project_id,
                "run_id": run_id,
                "quality_preset": preset.value,
                "render_contract": WEBGL1_STATIC_NO_TEXTURE_V1.to_dict(),
                "budget_policy": asdict(budget),
                "acceptance_policy": asdict(acceptance),
            },
        )
        logger.info(
            "shader.pipeline.initialized run_id=%s project_id=%s "
            "quality_preset=%s max_wall_time_seconds=%s max_model_calls=%s",
            run_id,
            project_id,
            preset.value,
            budget.max_wall_time_seconds,
            budget.max_model_calls,
        )
        return {
            "project_id": project_id,
            "run_id": run_id,
            "phase": "initialized",
            "quality_preset": preset.value,
            "iteration": 0,
            "current_candidate_id": "",
            "current_best_id": "",
            "current_best_glsl_sha256": "",
            "current_best_total_loss": 1.0,
            "current_best_score_summary": {},
            "compile_repair_count": 0,
            "visual_refinement_count": 0,
            "no_improvement_count": 0,
            "model_call_count": 0,
            "candidate_sequence": 0,
            "measurement_seed_attempted": False,
            "stop_reason": "",
            "cancelled": bool(state.get("cancelled", False)),
            "image": reference,
            "content_type": "image/png",
            "instruction": str(state.get("instruction", "")).strip(),
            "render_contract": WEBGL1_STATIC_NO_TEXTURE_V1.to_dict(),
            "budget_policy": asdict(budget),
            "acceptance_policy": asdict(acceptance),
            "started_at": started_at,
            "reference_ref": reference_ref.relative_path,
            "candidate_records": (),
            "current_best_record": None,
            "model_calls": (),
            "events": (
                {
                    "stage": "initialize",
                    "event_type": "run_initialized",
                    "payload": {
                        "run_id": run_id,
                        "quality_preset": preset.value,
                        "reference_sha256": reference_ref.sha256,
                    },
                },
            ),
            "logs": (),
            "memory_status": str(state.get("memory_status", "ephemeral")),
        }

    return initialize


def make_measure_target_node(artifact_store: LocalArtifactStore) -> RunNode:
    """创建确定性测量参考图并持久化结果的节点."""

    async def measure(state: Mapping[str, Any]) -> dict[str, Any]:
        measurements = measure_target(state["image"])
        store = _run_store(artifact_store, state)
        store.write_json("analysis/measurements.json", measurements)
        return {
            "phase": "measured",
            "target_measurements": measurements,
            "events": (
                *state.get("events", ()),
                {
                    "stage": "measure_target",
                    "event_type": "target_measured",
                    "payload": {
                        "width": measurements.analysis_width,
                        "height": measurements.analysis_height,
                        "image_sha256": measurements.image_sha256,
                    },
                },
            ),
        }

    return measure


def make_persist_visual_analysis_node(artifact_store: LocalArtifactStore) -> RunNode:
    """创建只持久化已通过 M2 Parser 的 VisualAnalysis 节点."""

    async def persist(state: Mapping[str, Any]) -> dict[str, Any]:
        store = _run_store(artifact_store, state)
        artifact = store.write_json(
            "analysis/visual-analysis.json",
            state["visual_analysis"],
        )
        return {
            "phase": "analyzed",
            "events": (
                *state.get("events", ()),
                {
                    "stage": "visual_analysis",
                    "event_type": "analysis_persisted",
                    "payload": {"artifact_ref": artifact.relative_path},
                },
            ),
        }

    return persist
