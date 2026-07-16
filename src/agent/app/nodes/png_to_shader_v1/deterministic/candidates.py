"""PNG 转 Shader V1 的候选物化与修复准备节点."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Any, Literal

from agent.app.contracts.png_to_shader_v1 import (
    CandidateProvenance,
    ShaderAuthorResult,
)
from shaderforge.analysis import (
    TargetMeasurements,
)
from shaderforge.evaluation import (
    CandidateRecord,
)
from shaderforge.generation import build_measurement_affine_seed
from shaderforge.store import LocalArtifactStore

from .runtime import (
    NodeEvidenceError,
    RunNode,
    _budget,
    _run_store,
    _write_candidate_manifest,
)


def make_materialize_candidate_node(artifact_store: LocalArtifactStore) -> RunNode:
    """创建把 Author 输出冻结为新候选及 provenance 的节点."""

    async def materialize(state: Mapping[str, Any]) -> dict[str, Any]:
        sequence = int(state.get("candidate_sequence", 0)) + 1
        candidate_id = f"candidate-{sequence:04d}"
        author = dict(state["author_result"])
        provenance = dict(state["candidate_provenance"])
        glsl = str(state["glsl"])
        glsl_sha256 = sha256(glsl.encode("utf-8")).hexdigest()
        raw_origin = str(state.get("candidate_origin", "model"))
        origin: Literal["model", "deterministic"]
        if raw_origin == "model":
            origin = "model"
        elif raw_origin == "deterministic":
            origin = "deterministic"
        else:
            raise ValueError("candidate_origin 必须是 model 或 deterministic。")
        generator_version = state.get("candidate_generator_version")
        if origin == "deterministic" and not generator_version:
            raise ValueError("确定性候选必须绑定 generator_version。")
        if origin == "deterministic":
            expected_model_ref = f"deterministic:{generator_version}"
            if (
                author.get("author_version") != generator_version
                or author.get("mode") != "measurement_seed"
                or author.get("base_candidate_id") is not None
                or author.get("glsl") != glsl
                or author.get("changed_problem_domain") != "initial_build"
                or provenance.get("role") != "deterministic_generator"
                or provenance.get("origin") != "deterministic"
                or provenance.get("generator_version") != generator_version
                or provenance.get("prompt_version") != generator_version
                or provenance.get("model_ref") != expected_model_ref
                or provenance.get("requested_model_ref") != expected_model_ref
                or provenance.get("glsl_sha256") != glsl_sha256
            ):
                raise NodeEvidenceError(
                    "确定性 Author、provenance 与 GLSL 证据绑定不一致。"
                )
            mode = "measurement_seed"
        else:
            parsed_author = ShaderAuthorResult.model_validate(author)
            parsed_provenance = CandidateProvenance.model_validate(provenance)
            expected_author_versions = {
                "initial": "shader_author_initial_v1_1",
                "compile_repair": "shader_author_compile_repair_v1_1",
                "visual_refine": "shader_author_visual_refine_v1",
            }
            mode_value = parsed_author.mode.value
            expected_author_version = expected_author_versions[mode_value]
            if (
                generator_version is not None
                or parsed_author.author_version != expected_author_version
                or parsed_author.glsl != glsl
                or parsed_provenance.glsl_sha256 != glsl_sha256
                or parsed_author.mode != parsed_provenance.mode
                or parsed_provenance.prompt_version != expected_author_version
                or (
                    state.get("author_model") is not None
                    and str(state["author_model"]) != parsed_provenance.model_ref
                )
            ):
                raise NodeEvidenceError("Author、provenance 与 GLSL 证据绑定不一致。")
            if mode_value == "initial" and (
                parsed_author.base_candidate_id is not None
                or parsed_author.changed_problem_domain != "initial_build"
                or parsed_author.changed_parameters
                or parsed_author.protected_regions
            ):
                raise NodeEvidenceError("initial Author 的根候选证据绑定不一致。")
            if mode_value == "compile_repair" and (
                parsed_author.changed_problem_domain != "runtime_compile"
            ):
                raise NodeEvidenceError("compile repair Author 的修改域不合法。")
            if mode_value == "visual_refine":
                base_candidate_id = parsed_author.base_candidate_id
                expected_base = state.get("current_best_id")
                if not base_candidate_id or (
                    expected_base is not None
                    and str(expected_base)
                    and base_candidate_id != str(expected_base)
                ):
                    raise NodeEvidenceError(
                        "visual refine Author 未绑定 current_best。"
                    )
            mode = mode_value
        if origin == "deterministic" or mode == "initial":
            parent_id = None
        elif mode == "visual_refine":
            parent_id = str(author["base_candidate_id"])
        else:
            parent_id = str(state["current_candidate_id"])

        store = _run_store(artifact_store, state)
        prefix = f"candidates/{candidate_id}"
        glsl_ref = store.write_text(
            f"{prefix}/shader.frag",
            glsl,
            content_type="text/x-glsl; charset=utf-8",
        )
        author_ref = store.write_json(f"{prefix}/author.json", author)
        provenance_ref = store.write_json(
            f"{prefix}/provenance.json",
            provenance,
        )
        record = CandidateRecord(
            candidate_id=candidate_id,
            parent_candidate_id=parent_id,
            glsl_sha256=glsl_ref.sha256,
            glsl_ref=glsl_ref.relative_path,
            author_ref=author_ref.relative_path,
            provenance_ref=provenance_ref.relative_path,
            compile_ref=None,
            render_ref=None,
            render_sha256=None,
            metrics_ref=None,
            review_ref=None,
            iteration=int(state.get("visual_refinement_count", 0)),
            changed_problem_domain=str(author["changed_problem_domain"]),
            prompt_version=str(provenance["prompt_version"]),
            model_ref=str(provenance["model_ref"]),
            score_summary=None,
            hard_constraints_passed=False,
            origin=origin,
            generator_version=(
                str(generator_version) if generator_version is not None else None
            ),
        )
        _write_candidate_manifest(store, record)
        return {
            "phase": "candidate_materialized",
            "candidate_sequence": sequence,
            "current_candidate_id": candidate_id,
            "candidate_record": record,
            "candidate_records": (*state.get("candidate_records", ()), record),
            "render_status": "pending",
            "events": (
                *state.get("events", ()),
                {
                    "stage": "candidate",
                    "event_type": "candidate_created",
                    "payload": {
                        "candidate_id": candidate_id,
                        "parent_candidate_id": parent_id,
                        "mode": mode,
                        "origin": origin,
                        "generator_version": record.generator_version,
                        "glsl_sha256": record.glsl_sha256,
                    },
                },
            ),
        }

    return materialize


def make_prepare_measurement_seed_node() -> RunNode:
    """创建与 model initial 并列的确定性 measurement affine 根候选."""

    async def prepare(state: Mapping[str, Any]) -> dict[str, Any]:
        if bool(state.get("measurement_seed_attempted", False)):
            raise RuntimeError("measurement seed 每个 run 只能准备一次。")
        reference = state.get("image")
        measurements = state.get("target_measurements")
        if not isinstance(reference, bytes) or not reference:
            raise TypeError("measurement seed 需要规范化 reference bytes。")
        if not isinstance(measurements, TargetMeasurements):
            raise TypeError("measurement seed 需要 TargetMeasurements。")
        if measurements.image_sha256 != sha256(reference).hexdigest():
            raise NodeEvidenceError(
                "TargetMeasurements 与规范化 reference 证据绑定不一致。"
            )
        seed = build_measurement_affine_seed(reference, measurements)
        generator_version = seed.provenance.generator_version
        model_ref = f"deterministic:{generator_version}"
        source = {
            "author_version": generator_version,
            "mode": "measurement_seed",
            "base_candidate_id": None,
            "glsl": seed.glsl,
            "strategy_summary": (
                "用主体 bbox 椭圆和前景 RGB affine plane 构造紧凑无贴图根候选。"
            ),
            "implemented_layers": ["background", "measurement_affine_subject"],
            "parameter_manifest": [],
            "changed_problem_domain": "initial_build",
            "changed_parameters": [],
            "protected_regions": [],
            "expected_metric_changes": ["提供可由 Selector 独立验收的测量基线。"],
            "known_limitations": ["V1 seed 只表达单主体椭圆和一阶连续颜色场。"],
        }
        provenance = {
            **seed.provenance.to_dict(),
            "role": "deterministic_generator",
            "origin": "deterministic",
            "model_ref": model_ref,
            "requested_model_ref": model_ref,
            "model_identity_source": "deterministic_generator",
            # CandidateRecord v1 保留 prompt_version 字段；确定性候选在此字段中
            # 记录 generator version，另由 origin/generator_version 消除歧义。
            "prompt_version": generator_version,
        }
        return {
            "phase": "measurement_seed_prepared",
            "measurement_seed_attempted": True,
            "author_result": source,
            "glsl": seed.glsl,
            "author_model": model_ref,
            "candidate_provenance": provenance,
            "candidate_origin": "deterministic",
            "candidate_generator_version": generator_version,
            "events": (
                *state.get("events", ()),
                {
                    "stage": "candidate",
                    "event_type": "measurement_seed_prepared",
                    "payload": {
                        "generator_version": generator_version,
                        "strategy": seed.provenance.strategy,
                        "glsl_sha256": seed.provenance.glsl_sha256,
                        "fit_pixel_count": seed.provenance.fit_pixel_count,
                        "fit_rmse": seed.provenance.fit_rmse,
                        "fallback_reason": seed.provenance.fallback_reason,
                    },
                },
            ),
        }

    return prepare


def make_prepare_compile_repair_node() -> RunNode:
    """创建把失败候选和剩余 compile budget 绑定给 Author 的节点."""

    async def prepare(state: Mapping[str, Any]) -> dict[str, Any]:
        budget = _budget(state)
        used = int(state.get("compile_repair_count", 0))
        return {
            "phase": "compile_repair_prepared",
            "previous_author_result": dict(state["author_result"]),
            "repair_budget": {
                "used": used,
                "remaining": max(0, budget.max_compile_repairs - used),
                "maximum": budget.max_compile_repairs,
            },
        }

    return prepare
