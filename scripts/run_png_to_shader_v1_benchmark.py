"""运行 F09 M5 AI-off/AI-on benchmark、门禁与盲评包生成."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from shaderforge.analysis import RegionOfInterest, measure_target
from shaderforge.benchmark import (
    BLIND_REVIEW_EVIDENCE_SCHEMA,
    build_ai_off_shader,
    evaluate_quality_gate,
    load_benchmark_suite,
    load_quality_gate_policy,
    verify_blind_review_package,
    verify_legacy_blind_review_package,
    write_blind_review_package,
)
from shaderforge.benchmark.models import BenchmarkCaseSpec, BenchmarkSuiteSpec
from shaderforge.contracts import AcceptancePolicy, QualityPreset, budget_for_preset
from shaderforge.evaluation import CandidateRecord, ScoreBreakdownV1, evaluate_render
from shaderforge.rendering import PlaywrightWebGL1Renderer
from shaderforge.validation import validate_shader

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "benchmarks/png_to_shader_v1/manifest.yaml"
GATE_POLICY_PATH = ROOT / "benchmarks/png_to_shader_v1/m5_gate.yaml"
DEFAULT_OUTPUT_ROOT = ROOT / "output/benchmarks/png-to-shader-v1"
BENCHMARK_ACCEPTANCE_POLICY = AcceptancePolicy(quality_threshold=0.0)
M5_OBJECTIVE_VERSION = "manifest_key_rois_v1"
INITIAL_SELECTION_POLICY = "first_successful_model_origin_v1"


@dataclass(frozen=True)
class _CandidateGeneration:
    """候选生成来源；旧证据缺 origin 时按模型候选兼容."""

    origin: str
    generator_version: str | None


@dataclass(frozen=True)
class _ObjectiveMetrics:
    """冻结 manifest objective 下的候选指标."""

    score: ScoreBreakdownV1
    bbox_max_error_uv: float | None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("ai-off", "ai-on", "all", "evaluate"),
        default="ai-off",
    )
    parser.add_argument(
        "--quality-preset",
        choices=tuple(item.value for item in QualityPreset),
        default=QualityPreset.BALANCED.value,
    )
    parser.add_argument("--cases", help="逗号分隔的 case id；默认完整 10 例。")
    parser.add_argument("--suite-run-id", help="新运行 ID；默认使用 UTC 时间。")
    parser.add_argument("--output-dir", type=Path, help="指定或恢复 suite 输出目录。")
    parser.add_argument(
        "--allow-model-calls",
        action="store_true",
        help="显式允许按量真实模型调用；ai-on/all 必须提供。",
    )
    parser.add_argument(
        "--model-call-budget",
        type=int,
        default=80,
        help="整套 benchmark 的模型调用硬上限。",
    )
    parser.add_argument(
        "--instruction",
        default="",
        help="统一附加约束；正式基线默认留空，避免按 case 泄露答案。",
    )
    parser.add_argument("--human-review", type=Path, help="人工盲评下载的 JSON。")
    parser.add_argument(
        "--require-gate-passed",
        action="store_true",
        help="门禁非 passed 时返回非零；人工评审前通常不启用。",
    )
    return parser.parse_args()


def _utc_run_id() -> str:
    return "m5-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _write_json(path: Path, value: Any) -> None:
    _write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _progress(message: str) -> None:
    sys.stdout.write(message + "\n")
    sys.stdout.flush()


def _structured_model_routing_snapshot() -> dict[str, Any]:
    """在调用模型前冻结三个结构化角色的真实调用配置."""
    from agent.app.nodes.shader_author_node import SHADER_AUTHOR_MODEL_CONFIG
    from agent.app.nodes.visual_analysis_node import VISUAL_ANALYSIS_MODEL_CONFIG
    from agent.app.nodes.visual_critic_node import VISUAL_CRITIC_MODEL_CONFIG

    def node_snapshot(config: Any) -> dict[str, Any]:
        return {
            **asdict(config.call),
            "print_reasoning": bool(config.print_reasoning),
        }

    return {
        "visual_analysis": node_snapshot(VISUAL_ANALYSIS_MODEL_CONFIG),
        "shader_author": node_snapshot(SHADER_AUTHOR_MODEL_CONFIG),
        "visual_critic": node_snapshot(VISUAL_CRITIC_MODEL_CONFIG),
        "json_repair": {
            "strategy": "inherit_source_role",
            "temperature": 0,
            "thinking": "off",
            "capture_reasoning": False,
            "response_format": "json_object",
        },
    }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 根节点必须是 object。")
    return value


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _selected_cases(
    suite: BenchmarkSuiteSpec,
    requested: str | None,
) -> tuple[BenchmarkCaseSpec, ...]:
    if not requested:
        return suite.cases
    requested_ids = tuple(item.strip() for item in requested.split(",") if item.strip())
    known = {case.case_id: case for case in suite.cases}
    unknown = sorted(set(requested_ids) - set(known))
    if unknown:
        raise ValueError("未知 benchmark case：" + ", ".join(unknown))
    return tuple(known[case_id] for case_id in requested_ids)


def _regions(case: BenchmarkCaseSpec) -> tuple[RegionOfInterest, ...]:
    return tuple(
        RegionOfInterest(
            region_id=roi.region_id,
            bbox_uv=roi.bbox_uv,
            purpose=roi.purpose,
            confidence=1.0,
        )
        for roi in case.key_rois
    )


def _bbox_error(
    reference: tuple[float, float, float, float] | None,
    candidate: tuple[float, float, float, float] | None,
) -> float | None:
    if reference is None or candidate is None:
        return None
    return max(
        abs(left - right) for left, right in zip(reference, candidate, strict=True)
    )


def _case_root(suite_root: Path, case_id: str) -> Path:
    return suite_root / "cases" / case_id


def _load_existing_results(
    suite_root: Path,
    cases: Sequence[BenchmarkCaseSpec],
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for case in cases:
        path = _case_root(suite_root, case.case_id) / "result.json"
        if path.is_file():
            results[case.case_id] = _load_json(path)
    return results


def _save_case_result(
    suite_root: Path,
    case: BenchmarkCaseSpec,
    result: dict[str, Any],
) -> None:
    _write_json(_case_root(suite_root, case.case_id) / "result.json", result)


def _safe_model_audits(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in state.get("model_calls", ()):
        if not isinstance(raw, Mapping):
            continue
        result.append(
            {
                key: value
                for key, value in raw.items()
                if key
                in {
                    "role",
                    "mode",
                    "attempt",
                    "requested_model_ref",
                    "model_ref",
                    "model_identity_source",
                    "response_format",
                    "prompt_version",
                    "repair_prompt_version",
                    "latency_ms",
                    "output_sha256",
                    "parse_status",
                    "error_codes",
                    "validation_issues",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                }
            }
        )
    return result


def _safe_events(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in state.get("events", ()):
        if not isinstance(raw, Mapping):
            continue
        payload = raw.get("payload")
        result.append(
            {
                "stage": str(raw.get("stage", "")),
                "event_type": str(raw.get("event_type", "")),
                "payload": dict(payload) if isinstance(payload, Mapping) else {},
            }
        )
    return result


async def _run_ai_off_case(
    case: BenchmarkCaseSpec,
    suite_root: Path,
    renderer: PlaywrightWebGL1Renderer,
) -> dict[str, Any]:
    case_root = _case_root(suite_root, case.case_id)
    output = case_root / "ai-off"
    output.mkdir(parents=True, exist_ok=True)
    reference = case.image_path.read_bytes()
    shutil.copyfile(case.image_path, case_root / "reference.png")
    measurements = measure_target(reference)
    shader = build_ai_off_shader(measurements)
    _write_text(output / "baseline.frag", shader)
    validation = validate_shader(shader)
    result: dict[str, Any] = {
        "static_passed": validation.valid,
        "compile_passed": False,
        "total_loss": None,
        "failure_reason": None,
        "shader_path": _relative(suite_root, output / "baseline.frag"),
    }
    _write_json(output / "static-validation.json", validation.to_dict())
    if not validation.valid:
        result["failure_reason"] = "static_validation_failed"
        return result
    try:
        render = await renderer.render(
            shader,
            measurements.analysis_width,
            measurements.analysis_height,
        )
    except Exception as exc:
        result["failure_reason"] = type(exc).__name__
        return result
    _write_json(output / "compile.json", render.compile.to_dict())
    result["compile_passed"] = bool(render.success and render.compile.success)
    if not result["compile_passed"] or render.image_bytes is None:
        result["failure_reason"] = "webgl_compile_or_draw_failed"
        return result
    render_path = output / "render.png"
    render_path.write_bytes(render.image_bytes)
    score = evaluate_render(
        reference,
        render.image_bytes,
        measurements=measurements,
        regions=_regions(case),
    )
    _write_json(output / "metrics.json", score.to_dict())
    candidate_measurements = measure_target(render.image_bytes)
    result.update(
        {
            "total_loss": score.total_loss,
            "global_rmse": score.global_rmse,
            "bbox_max_error_uv": _bbox_error(
                case.expected_foreground_bbox_uv,
                candidate_measurements.foreground_bbox_uv,
            ),
            "key_roi_losses": score.roi_loss_map,
            "render_path": _relative(suite_root, render_path),
            "metrics_path": _relative(suite_root, output / "metrics.json"),
        }
    )
    return result


def _record(value: Any) -> CandidateRecord:
    if isinstance(value, CandidateRecord):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("candidate record 必须是 object。")
    return CandidateRecord.from_dict(dict(value))


def _read_object(store: Any, relative_path: str) -> dict[str, Any]:
    value = json.loads(store.read_bytes(relative_path))
    if not isinstance(value, dict):
        raise ValueError(f"{relative_path} 根节点必须是 object。")
    return value


def _candidate_generation(
    store: Any,
    raw_record: Any,
    record: CandidateRecord,
) -> _CandidateGeneration:
    """读取候选来源；兼容旧 CandidateRecord 没有 origin 的证据."""
    raw = raw_record if isinstance(raw_record, Mapping) else {}
    try:
        provenance: Mapping[str, Any] = _read_object(store, record.provenance_ref)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        provenance = {}

    origin_values = tuple(
        str(value).strip()
        for value in (raw.get("origin"), provenance.get("origin"), record.origin)
        if value is not None and str(value).strip()
    )
    if len(set(origin_values)) > 1:
        raise ValueError(f"{record.candidate_id} 的 origin 证据不一致。")
    origin = origin_values[0] if origin_values else "model"

    version_values = tuple(
        str(value).strip()
        for value in (
            raw.get("generator_version"),
            provenance.get("generator_version"),
            record.generator_version,
        )
        if value is not None and str(value).strip()
    )
    if len(set(version_values)) > 1:
        raise ValueError(f"{record.candidate_id} 的 generator_version 证据不一致。")
    generator_version = version_values[0] if version_values else None
    return _CandidateGeneration(
        origin=origin,
        generator_version=generator_version or None,
    )


def _is_deterministic_origin(origin: str) -> bool:
    return origin == "deterministic"


def _select_model_initial(
    successful_records: Sequence[CandidateRecord],
    generations: Mapping[str, _CandidateGeneration],
) -> CandidateRecord | None:
    """返回首个成功模型候选；确定性 seed 不得冒充 initial."""
    return next(
        (
            record
            for record in successful_records
            if generations[record.candidate_id].origin == "model"
        ),
        None,
    )


def _evaluate_objective_candidate(
    case: BenchmarkCaseSpec,
    reference: bytes,
    candidate_render: bytes,
) -> _ObjectiveMetrics:
    """用冻结 manifest key ROI 和 expected bbox 评估单个候选."""
    measurements = measure_target(reference)
    score = evaluate_render(
        reference,
        candidate_render,
        measurements=measurements,
        regions=_regions(case),
    )
    candidate_measurements = measure_target(candidate_render)
    return _ObjectiveMetrics(
        score=score,
        bbox_max_error_uv=_bbox_error(
            case.expected_foreground_bbox_uv,
            candidate_measurements.foreground_bbox_uv,
        ),
    )


def _objective_pair_fields(
    initial: CandidateRecord | None,
    final: CandidateRecord | None,
    initial_objective: _ObjectiveMetrics | None,
    final_objective: _ObjectiveMetrics | None,
) -> dict[str, Any]:
    """构造 gate 使用的同口径 pair，并保留清晰命名的内部 loss."""
    initial_total = (
        initial_objective.score.total_loss if initial_objective is not None else None
    )
    final_total = (
        final_objective.score.total_loss if final_objective is not None else None
    )
    return {
        "objective_version": M5_OBJECTIVE_VERSION,
        "initial_selection_policy": INITIAL_SELECTION_POLICY,
        "objective_comparable": initial_total is not None and final_total is not None,
        "initial_total_loss": initial_total,
        "final_total_loss": final_total,
        "initial_objective_total_loss": initial_total,
        "final_objective_total_loss": final_total,
        "initial_internal_total_loss": (
            initial.score_summary.total_loss
            if initial is not None and initial.score_summary is not None
            else None
        ),
        "final_internal_total_loss": (
            final.score_summary.total_loss
            if final is not None and final.score_summary is not None
            else None
        ),
    }


def _objective_metrics_dict(metrics: _ObjectiveMetrics) -> dict[str, Any]:
    """返回同时携带 objective 身份和 manifest bbox 误差的证据."""
    return {
        "objective_version": M5_OBJECTIVE_VERSION,
        **metrics.score.to_dict(),
        "bbox_max_error_uv": metrics.bbox_max_error_uv,
    }


def _traceability(
    store: Any,
    records: Sequence[CandidateRecord],
    generations: Mapping[str, _CandidateGeneration],
    state: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    known: set[str] = set()
    for record in records:
        if (
            record.parent_candidate_id is not None
            and record.parent_candidate_id not in known
        ):
            errors.append(f"{record.candidate_id}:parent_missing")
        known.add(record.candidate_id)
        generation = generations.get(record.candidate_id)
        if generation is None:
            errors.append(f"{record.candidate_id}:origin_missing")
        else:
            if generation.origin != record.origin:
                errors.append(f"{record.candidate_id}:origin_record_mismatch")
            if generation.generator_version != record.generator_version:
                errors.append(
                    f"{record.candidate_id}:generator_version_record_mismatch"
                )
            if generation.origin == "model":
                if not record.prompt_version or not record.model_ref:
                    errors.append(f"{record.candidate_id}:prompt_or_model_missing")
            elif _is_deterministic_origin(generation.origin):
                if generation.generator_version is None:
                    errors.append(f"{record.candidate_id}:generator_version_missing")
            else:
                errors.append(f"{record.candidate_id}:origin_unsupported")
        for field_name, artifact_ref in (
            ("glsl", record.glsl_ref),
            ("author", record.author_ref),
            ("provenance", record.provenance_ref),
            ("compile", record.compile_ref),
        ):
            if artifact_ref is None:
                errors.append(f"{record.candidate_id}:{field_name}_ref_missing")
                continue
            try:
                store.read_bytes(artifact_ref)
            except (FileNotFoundError, ValueError):
                errors.append(f"{record.candidate_id}:{field_name}_artifact_missing")
        try:
            glsl = store.read_bytes(record.glsl_ref)
            if sha256(glsl).hexdigest() != record.glsl_sha256:
                errors.append(f"{record.candidate_id}:glsl_hash_mismatch")
        except (FileNotFoundError, ValueError):
            pass
        if record.hard_constraints_passed:
            if record.render_ref is None or record.metrics_ref is None:
                errors.append(f"{record.candidate_id}:render_or_metrics_ref_missing")
            else:
                try:
                    rendered = store.read_bytes(record.render_ref)
                    store.read_bytes(record.metrics_ref)
                    if sha256(rendered).hexdigest() != record.render_sha256:
                        errors.append(f"{record.candidate_id}:render_hash_mismatch")
                except (FileNotFoundError, ValueError):
                    errors.append(f"{record.candidate_id}:render_or_metrics_missing")
    for index, audit in enumerate(_safe_model_audits(state)):
        if not audit.get("model_ref") or not audit.get("prompt_version"):
            errors.append(f"model_call_{index}:identity_or_prompt_missing")
        output_hash = audit.get("output_sha256")
        if not isinstance(output_hash, str) or len(output_hash) != 64:
            errors.append(f"model_call_{index}:output_hash_missing")
    final = state.get("final_result")
    if isinstance(final, Mapping) and final.get("success"):
        for artifact_ref in (
            "final/shader.frag",
            "final/render.png",
            "final/metrics.json",
            "final/manifest.json",
        ):
            try:
                store.read_bytes(artifact_ref)
            except (FileNotFoundError, ValueError):
                errors.append(f"final:{artifact_ref}_missing")
    return not errors, errors


def _copy_candidate_evidence(
    suite_root: Path,
    output: Path,
    store: Any,
    label: str,
    record: CandidateRecord,
) -> dict[str, str]:
    glsl_path = output / f"{label}.frag"
    render_path = output / f"{label}.png"
    metrics_path = output / f"{label}.metrics.json"
    glsl_path.write_bytes(store.read_bytes(record.glsl_ref))
    if record.render_ref is None or record.metrics_ref is None:
        raise ValueError(f"{label} 候选缺少 render/metrics。")
    render_path.write_bytes(store.read_bytes(record.render_ref))
    metrics_path.write_bytes(store.read_bytes(record.metrics_ref))
    return {
        f"{label}_glsl_path": _relative(suite_root, glsl_path),
        f"{label}_render_path": _relative(suite_root, render_path),
        f"{label}_metrics_path": _relative(suite_root, metrics_path),
    }


async def _run_ai_on_case(
    case: BenchmarkCaseSpec,
    suite_root: Path,
    suite_run_id: str,
    preset: QualityPreset,
    instruction: str,
    remaining_model_calls: int,
) -> dict[str, Any]:
    from agent.app.services.png_to_shader_v1 import (
        default_png_to_shader_v1_service,
    )

    preset_budget = budget_for_preset(preset)
    if remaining_model_calls < 2:
        return {
            "success": False,
            "failure_reason": "global_model_budget_exhausted",
            "model_call_count": 0,
            "final_compile_passed": False,
            "final_static_passed": False,
            "final_matches_current_best": False,
            "best_updates_monotonic": False,
            "traceability_passed": False,
        }
    case_budget = replace(
        preset_budget,
        max_model_calls=min(preset_budget.max_model_calls, remaining_model_calls),
    )
    project_id = f"benchmark-{suite_run_id}-{case.case_id}"
    run_id = f"{suite_run_id}-{case.case_id}"
    state = await default_png_to_shader_v1_service.invoke(
        project_id,
        {
            "project_id": project_id,
            "run_id": run_id,
            "image": case.image_path.read_bytes(),
            "content_type": "image/png",
            "quality_preset": preset.value,
            "instruction": instruction,
            "budget_policy": case_budget,
            "acceptance_policy": asdict(BENCHMARK_ACCEPTANCE_POLICY),
            "memory_status": "ephemeral",
            "model_calls": (),
            "events": (),
            "logs": (),
        },
    )
    store = default_png_to_shader_v1_service.artifact_store.start_run(
        project_id, run_id
    )
    output = _case_root(suite_root, case.case_id) / "ai-on"
    output.mkdir(parents=True, exist_ok=True)
    raw_records = tuple(state.get("candidate_records", ()))
    record_pairs = tuple((_record(item), item) for item in raw_records)
    records = sorted(
        (record for record, _raw in record_pairs),
        key=lambda item: (item.iteration, item.candidate_id),
    )
    raw_by_id = {record.candidate_id: raw for record, raw in record_pairs}
    if len(raw_by_id) != len(record_pairs):
        raise ValueError("candidate_records 的 candidate_id 不得重复。")
    generations = {
        record.candidate_id: _candidate_generation(
            store,
            raw_by_id[record.candidate_id],
            record,
        )
        for record in records
    }
    final = state.get("final_result")
    final_value = dict(final) if isinstance(final, Mapping) else {}
    successful_records = [
        record
        for record in records
        if record.hard_constraints_passed
        and record.score_summary is not None
        and record.render_ref is not None
        and record.metrics_ref is not None
    ]
    initial = _select_model_initial(successful_records, generations)
    best_raw = state.get("current_best_record")
    best = _record(best_raw) if best_raw is not None else None
    if best is not None and best.candidate_id not in generations:
        generations[best.candidate_id] = _candidate_generation(store, best_raw, best)
    events = _safe_events(state)
    audits = _safe_model_audits(state)
    score_by_id = {
        record.candidate_id: record.score_summary.total_loss
        for record in successful_records
        if record.score_summary is not None
    }
    best_update_ids = [
        str(event["payload"].get("candidate_id"))
        for event in events
        if event["event_type"] == "current_best_updated"
    ]
    best_update_losses = [
        score_by_id[candidate_id]
        for candidate_id in best_update_ids
        if candidate_id in score_by_id
    ]
    monotonic = bool(best_update_losses) and all(
        later <= earlier + 1e-12
        for earlier, later in zip(best_update_losses, best_update_losses[1:])
    )
    reference = case.image_path.read_bytes()
    initial_objective = (
        _evaluate_objective_candidate(
            case,
            reference,
            store.read_bytes(initial.render_ref),
        )
        if initial is not None and initial.render_ref is not None
        else None
    )
    final_objective = (
        _evaluate_objective_candidate(
            case,
            reference,
            store.read_bytes(best.render_ref),
        )
        if best is not None
        and best.hard_constraints_passed
        and best.score_summary is not None
        and best.render_ref is not None
        else None
    )
    objective_fields = _objective_pair_fields(
        initial,
        best,
        initial_objective,
        final_objective,
    )
    initial_objective_path: Path | None = None
    if initial_objective is not None:
        initial_objective_path = output / "initial.objective-metrics.json"
        _write_json(initial_objective_path, _objective_metrics_dict(initial_objective))
    final_objective_path: Path | None = None
    if final_objective is not None:
        final_objective_path = output / "final.objective-metrics.json"
        _write_json(final_objective_path, _objective_metrics_dict(final_objective))

    traceability_passed, traceability_errors = _traceability(
        store,
        records,
        generations,
        state,
    )

    def record_evidence(record: CandidateRecord) -> dict[str, Any]:
        generation = generations[record.candidate_id]
        return {
            **record.to_dict(),
            "origin": generation.origin,
            "generator_version": generation.generator_version,
        }

    initial_generation = generations.get(initial.candidate_id) if initial else None
    final_generation = generations.get(best.candidate_id) if best else None
    evidence = {
        "schema_version": 1,
        "project_id": project_id,
        "run_id": run_id,
        "quality_preset": preset.value,
        "budget_policy": asdict(case_budget),
        "acceptance_policy": asdict(BENCHMARK_ACCEPTANCE_POLICY),
        "final_result": {
            key: value for key, value in final_value.items() if key != "glsl"
        },
        "candidate_records": [record_evidence(record) for record in records],
        "benchmark_objective": {
            **objective_fields,
            "expected_foreground_bbox_uv": list(case.expected_foreground_bbox_uv),
            "key_roi_ids": [roi.region_id for roi in case.key_rois],
            "initial_candidate_id": initial.candidate_id if initial else None,
            "initial_origin": (
                initial_generation.origin if initial_generation is not None else None
            ),
            "initial_generator_version": (
                initial_generation.generator_version
                if initial_generation is not None
                else None
            ),
            "final_candidate_id": best.candidate_id if best else None,
            "final_origin": (
                final_generation.origin if final_generation is not None else None
            ),
            "final_generator_version": (
                final_generation.generator_version
                if final_generation is not None
                else None
            ),
        },
        "model_calls": audits,
        "events": events,
        "traceability_errors": traceability_errors,
    }
    _write_json(output / "run-evidence.json", evidence)
    result: dict[str, Any] = {
        "success": bool(final_value.get("success") and best is not None),
        "failure_reason": None,
        "run_id": run_id,
        "project_id": project_id,
        "quality_preset": preset.value,
        "stop_reason": final_value.get("stop_reason"),
        "candidate_count": len(records),
        "model_call_count": int(final_value.get("model_call_count", len(audits))),
        "input_tokens": sum(int(item.get("input_tokens") or 0) for item in audits),
        "output_tokens": sum(int(item.get("output_tokens") or 0) for item in audits),
        "total_tokens": sum(int(item.get("total_tokens") or 0) for item in audits),
        "elapsed_seconds": float(final_value.get("elapsed_seconds", 0.0)),
        "best_update_count": len(best_update_losses),
        "best_update_losses": best_update_losses,
        "best_updates_monotonic": monotonic,
        "traceability_passed": traceability_passed,
        "traceability_errors": traceability_errors,
        "initial_candidate_id": initial.candidate_id if initial else None,
        "initial_origin": (
            initial_generation.origin if initial_generation is not None else None
        ),
        "initial_generator_version": (
            initial_generation.generator_version
            if initial_generation is not None
            else None
        ),
        "final_candidate_id": best.candidate_id if best else None,
        "final_origin": (
            final_generation.origin if final_generation is not None else None
        ),
        "final_generator_version": (
            final_generation.generator_version if final_generation is not None else None
        ),
        "current_best_candidate_id": str(state.get("current_best_id", "")) or None,
        **objective_fields,
        "initial_objective_metrics_path": (
            _relative(suite_root, initial_objective_path)
            if initial_objective_path is not None
            else None
        ),
        "final_objective_metrics_path": (
            _relative(suite_root, final_objective_path)
            if final_objective_path is not None
            else None
        ),
        "benchmark_total_loss": (
            final_objective.score.total_loss if final_objective is not None else None
        ),
        "bbox_max_error_uv": (
            final_objective.bbox_max_error_uv if final_objective is not None else None
        ),
        "global_rmse": (
            final_objective.score.global_rmse if final_objective is not None else None
        ),
        "key_roi_losses": (
            final_objective.score.roi_loss_map if final_objective is not None else {}
        ),
        "final_compile_passed": bool(best and best.hard_constraints_passed),
        "final_static_passed": False,
        "final_matches_current_best": bool(
            best is not None
            and final_value.get("candidate_id") == best.candidate_id
            and state.get("current_best_id") == best.candidate_id
        ),
        "evidence_path": _relative(suite_root, output / "run-evidence.json"),
    }
    if initial is not None:
        initial_paths = _copy_candidate_evidence(
            suite_root,
            output,
            store,
            "initial",
            initial,
        )
        result.update(initial_paths)
        result["initial_internal_metrics_path"] = initial_paths["initial_metrics_path"]
    if best is None or best.score_summary is None or best.render_ref is None:
        result["failure_reason"] = str(
            final_value.get("stop_reason") or "no_validated_candidate"
        )
        _write_json(output / "failure.json", evidence)
        return result
    final_paths = _copy_candidate_evidence(suite_root, output, store, "final", best)
    result.update(final_paths)
    result["final_internal_metrics_path"] = final_paths["final_metrics_path"]
    final_glsl = store.read_bytes(best.glsl_ref).decode("utf-8")
    final_validation = validate_shader(final_glsl)
    result["final_static_passed"] = final_validation.valid
    return result


def _case_results_in_order(
    cases: Sequence[BenchmarkCaseSpec],
    results: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        results.get(
            case.case_id,
            {"case_id": case.case_id, "level": case.level, "ai_off": {}, "ai_on": {}},
        )
        for case in cases
    ]


def _report_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary")
    summary = summary if isinstance(summary, Mapping) else {}
    mode = str(report.get("mode", ""))

    def metric(key: str, digits: int = 2) -> str:
        value = summary.get(key)
        return f"{float(value):.{digits}f}" if isinstance(value, (int, float)) else "—"

    def six_decimal(value: Any) -> str:
        return (
            f"{float(value):.6f}"
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else "—"
        )

    actual_models = report.get("actual_model_refs")
    actual_model_text = (
        ", ".join(str(item) for item in actual_models)
        if isinstance(actual_models, list) and actual_models
        else "—"
    )
    requested_models = report.get("requested_model_refs")
    requested_model_text = (
        ", ".join(str(item) for item in requested_models)
        if isinstance(requested_models, list) and requested_models
        else "—"
    )
    config_schema = report.get("config_schema_version")
    configured_label = (
        "configured model snapshot (legacy)"
        if config_schema == 1
        else "configured model snapshot"
    )
    lines = [
        "# PNG-to-Shader V1 M5 Benchmark",
        "",
        f"- suite run: `{report.get('suite_run_id')}`",
        f"- mode: `{report.get('mode')}`",
        f"- {configured_label}: `{report.get('model_ref')}`",
        f"- audited requested models: `{requested_model_text}`",
        f"- audited actual models: `{actual_model_text}`",
        f"- quality preset: `{report.get('quality_preset')}`",
        f"- objective: `{report.get('objective_version') or 'legacy_mixed'}`",
        f"- initial selection: `{report.get('initial_selection_policy') or 'legacy_first_successful'}`",
        f"- quality gate: `{_section_status(report)}`",
        f"- config SHA-256: `{report.get('config_sha256')}`",
        f"- manifest SHA-256: `{report.get('manifest_sha256')}`",
        f"- gate SHA-256: `{report.get('gate_policy_sha256')}`",
        f"- human review SHA-256: `{report.get('human_review_sha256') or '—'}`",
        f"- AI-off static: `{summary.get('ai_off_static_pass_count', 0)}/{summary.get('case_count', 0)}`",
        f"- AI-off compile: `{summary.get('ai_off_compile_pass_count', 0)}/{summary.get('case_count', 0)}`",
    ]
    if mode == "ai-off":
        lines.extend(
            [
                "",
                "| case | static | compile | total loss | global RMSE | bbox error |",
                "|---|---|---|---:|---:|---:|",
            ]
        )
        for case in report.get("cases", []):
            if not isinstance(case, Mapping):
                continue
            ai_off = case.get("ai_off")
            ai_off = ai_off if isinstance(ai_off, Mapping) else {}
            lines.append(
                "| {case} | {static} | {compile} | {loss} | {rmse} | {bbox} |".format(
                    case=case.get("case_id"),
                    static="yes" if ai_off.get("static_passed") else "no",
                    compile="yes" if ai_off.get("compile_passed") else "no",
                    loss=six_decimal(ai_off.get("total_loss")),
                    rmse=six_decimal(ai_off.get("global_rmse")),
                    bbox=six_decimal(ai_off.get("bbox_max_error_uv")),
                )
            )
    else:
        lines.extend(
            [
                f"- AI-on compile: `{summary.get('ai_on_final_compile_pass_count', 0)}/{summary.get('case_count', 0)}`",
                f"- average model calls: `{metric('model_call_average')}`",
                f"- average elapsed seconds: `{metric('elapsed_seconds_average')}`",
                f"- average best updates: `{metric('best_update_average')}`",
                f"- improved cases: `{summary.get('improved_case_count', 0)}/{summary.get('improvement_comparable_case_count', 0)}`",
                f"- AI-on no worse than AI-off: `{summary.get('ai_on_beats_ai_off_count', 0)}/{summary.get('ai_on_ai_off_comparable_case_count', 0)}`",
                f"- input/output tokens: `{summary.get('input_tokens_total', 0)}/{summary.get('output_tokens_total', 0)}`",
                "",
                "| case | initial origin | final origin | initial objective | final objective | delta | compile | calls | seconds |",
                "|---|---|---|---:|---:|---:|---|---:|---:|",
            ]
        )
        for case in report.get("cases", []):
            if not isinstance(case, Mapping):
                continue
            ai_on = case.get("ai_on")
            ai_on = ai_on if isinstance(ai_on, Mapping) else {}
            initial = ai_on.get("initial_total_loss")
            final = ai_on.get("final_total_loss")
            delta = (
                float(initial) - float(final)
                if isinstance(initial, (int, float)) and isinstance(final, (int, float))
                else None
            )
            lines.append(
                "| {case} | {initial_origin} | {final_origin} | {initial} | {final} | {delta} | {compile} | {calls} | {seconds} |".format(
                    case=case.get("case_id"),
                    initial_origin=ai_on.get("initial_origin") or "—",
                    final_origin=(
                        "{origin}@{version}".format(
                            origin=ai_on.get("final_origin"),
                            version=ai_on.get("final_generator_version"),
                        )
                        if ai_on.get("final_origin")
                        and ai_on.get("final_generator_version")
                        else ai_on.get("final_origin") or "—"
                    ),
                    initial=(
                        f"{float(initial):.6f}"
                        if isinstance(initial, (int, float))
                        else "—"
                    ),
                    final=(
                        f"{float(final):.6f}"
                        if isinstance(final, (int, float))
                        else "—"
                    ),
                    delta=f"{delta:.6f}" if delta is not None else "—",
                    compile=(
                        "yes"
                        if ai_on.get("final_compile_passed")
                        else "no"
                        if ai_on
                        else "—"
                    ),
                    calls=ai_on.get("model_call_count", "—"),
                    seconds=(
                        f"{float(ai_on['elapsed_seconds']):.2f}"
                        if isinstance(ai_on.get("elapsed_seconds"), (int, float))
                        else "—"
                    ),
                )
            )
    gate = report.get("quality_gate")
    if isinstance(gate, Mapping):
        lines.extend(
            [
                "",
                "## Quality Gate",
                "",
                "| check | pass | actual | expected |",
                "|---|---|---|---|",
            ]
        )
        for check in gate.get("checks", []):
            if isinstance(check, Mapping):
                lines.append(
                    f"| {check.get('check_id')} | {check.get('passed')} | "
                    f"`{check.get('actual')}` | `{check.get('expected')}` |"
                )
    gate_summary = gate.get("summary") if isinstance(gate, Mapping) else None
    review_count = (
        gate_summary.get("human_review_count")
        if isinstance(gate_summary, Mapping)
        else 0
    )
    preference_rate = (
        gate_summary.get("human_final_preference_rate")
        if isinstance(gate_summary, Mapping)
        else None
    )
    final_win_count = (
        gate_summary.get("final_win_count", 0)
        if isinstance(gate_summary, Mapping)
        else 0
    )
    initial_win_count = (
        gate_summary.get("initial_win_count", 0)
        if isinstance(gate_summary, Mapping)
        else 0
    )
    tie_count = (
        gate_summary.get("tie_count", 0) if isinstance(gate_summary, Mapping) else 0
    )
    distinct_pair_count = (
        gate_summary.get("distinct_pair_count")
        if isinstance(gate_summary, Mapping)
        else None
    )
    bit_identical_case_ids = (
        gate_summary.get("bit_identical_case_ids")
        if isinstance(gate_summary, Mapping)
        else []
    )
    lines.extend(["", "## Human Review", ""])
    if isinstance(distinct_pair_count, int):
        identical_text = (
            ", ".join(str(case_id) for case_id in bit_identical_case_ids)
            if isinstance(bit_identical_case_ids, list) and bit_identical_case_ids
            else "—"
        )
        lines.append(
            f"候选图对中 `{distinct_pair_count}` 项不同；bit-identical case："
            f"`{identical_text}`。"
        )
    has_complete_pair_refs = bool(report.get("cases")) and all(
        isinstance(case, Mapping)
        and isinstance(case.get("ai_on"), Mapping)
        and isinstance(case["ai_on"].get("initial_render_path"), str)
        and bool(case["ai_on"].get("initial_render_path"))
        and isinstance(case["ai_on"].get("final_render_path"), str)
        and bool(case["ai_on"].get("final_render_path"))
        for case in report.get("cases", [])
    )
    if mode == "ai-off":
        lines.append("AI-off smoke 不生成模型候选图对或人工盲评包。")
    elif isinstance(review_count, (int, float)) and review_count > 0:
        preference_text = (
            f"{float(preference_rate):.3f}"
            if isinstance(preference_rate, (int, float))
            else "—"
        )
        lines.append(
            f"已载入 `{int(review_count)}` 项人工盲评；final 偏好率为 "
            f"`{preference_text}`；final/initial/tie 为 "
            f"`{int(final_win_count)}/{int(initial_win_count)}/{int(tie_count)}`。"
        )
    elif has_complete_pair_refs:
        reviewer_path = str(
            report.get("blind_review_reviewer_path") or "blind-review/index.html"
        )
        lines.append(
            f"打开 `{reviewer_path}` 完成全部 A/B 选择，下载 JSON后使用 "
            "`--mode evaluate --human-review <file>` 重新计算最终门禁。"
        )
    else:
        lines.append(
            "至少一个 case 缺少成功的 model initial/final 图对，本次未生成盲评包。"
        )
    lines.append("")
    return "\n".join(lines)


def _section_status(report: Mapping[str, Any]) -> str:
    gate = report.get("quality_gate")
    return str(gate.get("status")) if isinstance(gate, Mapping) else "not-evaluated"


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _build_summary(
    case_results: Sequence[Mapping[str, Any]],
    *,
    minimum_improvement: float,
) -> dict[str, Any]:
    ai_off_values = [
        case["ai_off"]
        for case in case_results
        if isinstance(case.get("ai_off"), Mapping)
    ]
    ai_on_values = [
        case["ai_on"] for case in case_results if isinstance(case.get("ai_on"), Mapping)
    ]
    calls = [float(value.get("model_call_count", 0)) for value in ai_on_values]
    elapsed = [float(value.get("elapsed_seconds", 0.0)) for value in ai_on_values]
    best_updates = [float(value.get("best_update_count", 0)) for value in ai_on_values]
    initial_losses = [
        float(value["initial_total_loss"])
        for value in ai_on_values
        if isinstance(value.get("initial_total_loss"), (int, float))
    ]
    final_losses = [
        float(value["final_total_loss"])
        for value in ai_on_values
        if isinstance(value.get("final_total_loss"), (int, float))
    ]
    improved = 0
    comparable = 0
    ai_on_beats_ai_off = 0
    ai_comparable = 0
    for case in case_results:
        ai_on = case.get("ai_on")
        ai_off = case.get("ai_off")
        if not isinstance(ai_on, Mapping):
            continue
        initial = ai_on.get("initial_total_loss")
        final = ai_on.get("final_total_loss")
        if isinstance(initial, (int, float)) and isinstance(final, (int, float)):
            comparable += 1
            if float(initial) - float(final) >= minimum_improvement:
                improved += 1
        if not isinstance(ai_off, Mapping):
            continue
        ai_off_loss = ai_off.get("total_loss")
        ai_on_loss = ai_on.get("benchmark_total_loss")
        if isinstance(ai_off_loss, (int, float)) and isinstance(
            ai_on_loss, (int, float)
        ):
            ai_comparable += 1
            if float(ai_on_loss) <= float(ai_off_loss):
                ai_on_beats_ai_off += 1
    return {
        "case_count": len(case_results),
        "ai_off_compile_pass_count": sum(
            bool(value.get("compile_passed")) for value in ai_off_values
        ),
        "ai_off_static_pass_count": sum(
            bool(value.get("static_passed")) for value in ai_off_values
        ),
        "ai_on_success_count": sum(
            bool(value.get("success")) for value in ai_on_values
        ),
        "ai_on_final_compile_pass_count": sum(
            bool(value.get("final_compile_passed")) for value in ai_on_values
        ),
        "ai_on_final_static_pass_count": sum(
            bool(value.get("final_static_passed")) for value in ai_on_values
        ),
        "model_call_total": int(sum(calls)),
        "model_call_average": _mean(calls),
        "elapsed_seconds_total": sum(elapsed),
        "elapsed_seconds_average": _mean(elapsed),
        "best_update_total": int(sum(best_updates)),
        "best_update_average": _mean(best_updates),
        "input_tokens_total": sum(
            int(value.get("input_tokens", 0)) for value in ai_on_values
        ),
        "output_tokens_total": sum(
            int(value.get("output_tokens", 0)) for value in ai_on_values
        ),
        "initial_total_loss_average": _mean(initial_losses),
        "final_total_loss_average": _mean(final_losses),
        "minimum_improvement": minimum_improvement,
        "improved_case_count": improved,
        "improvement_comparable_case_count": comparable,
        "improvement_rate": improved / comparable if comparable else None,
        "ai_on_beats_ai_off_count": ai_on_beats_ai_off,
        "ai_on_ai_off_comparable_case_count": ai_comparable,
        "ai_on_beats_ai_off_rate": (
            ai_on_beats_ai_off / ai_comparable if ai_comparable else None
        ),
    }


def _audited_model_summary(
    suite_root: Path,
    case_results: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """只从已保存的安全模型审计聚合请求/实际身份."""
    requested: set[str] = set()
    actual: set[str] = set()
    identity_sources: set[str] = set()
    suite_resolved = suite_root.resolve()
    for case in case_results:
        ai_on = case.get("ai_on")
        if not isinstance(ai_on, Mapping):
            continue
        evidence_ref = ai_on.get("evidence_path")
        if not isinstance(evidence_ref, str) or not evidence_ref:
            continue
        evidence_path = (suite_root / evidence_ref).resolve()
        try:
            evidence_path.relative_to(suite_resolved)
        except ValueError:
            continue
        if not evidence_path.is_file():
            continue
        evidence = _load_json(evidence_path)
        audits = evidence.get("model_calls")
        if not isinstance(audits, list):
            continue
        for audit in audits:
            if not isinstance(audit, Mapping):
                continue
            if value := audit.get("requested_model_ref"):
                requested.add(str(value))
            if value := audit.get("model_ref"):
                actual.add(str(value))
            if value := audit.get("model_identity_source"):
                identity_sources.add(str(value))
    return {
        "requested_model_refs": sorted(requested),
        "actual_model_refs": sorted(actual),
        "model_identity_sources": sorted(identity_sources),
    }


def _bit_identical_case_ids(
    suite_root: Path,
    case_results: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    """基于冻结 PNG 字节计算 initial/final 完全相同的 case."""
    root = suite_root.resolve()
    identical: list[str] = []
    for case in case_results:
        case_id = str(case.get("case_id", ""))
        ai_on = case.get("ai_on")
        if not case_id or not isinstance(ai_on, Mapping):
            raise ValueError("计算盲评图对摘要时 case 证据不完整。")
        digests: list[str] = []
        for field in ("initial_render_path", "final_render_path"):
            relative_path = ai_on.get(field)
            if not isinstance(relative_path, str) or not relative_path:
                raise ValueError(f"{case_id} 缺少 {field}。")
            artifact_path = (root / relative_path).resolve()
            try:
                artifact_path.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"{case_id} 的 {field} 越过 suite 输出目录。") from exc
            if not artifact_path.is_file():
                raise ValueError(f"{case_id} 的 {field} 不存在。")
            digests.append(sha256(artifact_path.read_bytes()).hexdigest())
        if digests[0] == digests[1]:
            identical.append(case_id)
    return tuple(identical)


def _build_report(
    *,
    suite: BenchmarkSuiteSpec,
    suite_root: Path,
    suite_run_id: str,
    mode: str,
    preset: QualityPreset,
    model_ref: str | None,
    model_call_budget: int,
    case_results: Sequence[Mapping[str, Any]],
    human_review_path: Path | None,
) -> dict[str, Any]:
    policy = load_quality_gate_policy(GATE_POLICY_PATH)
    config_path = suite_root / "config.json"
    config = _load_json(config_path)
    config_schema = int(config.get("schema_version", 0))
    objective_version = str(config.get("objective_version") or "legacy_mixed")
    initial_selection_policy = str(
        config.get("initial_selection_policy") or "legacy_first_successful"
    )
    if config_schema == 3:
        for case in case_results:
            ai_on = case.get("ai_on")
            if not isinstance(ai_on, Mapping) or not bool(ai_on.get("success")):
                continue
            if ai_on.get("objective_version") != objective_version:
                raise ValueError(
                    f"{case.get('case_id')} 的 objective_version 与 config 不一致。"
                )
            if ai_on.get("initial_selection_policy") != initial_selection_policy:
                raise ValueError(
                    f"{case.get('case_id')} 的 initial_selection_policy 与 config 不一致。"
                )
    model_summary = _audited_model_summary(suite_root, case_results)
    assignments_path = suite_root / "blind-review/assignments.private.json"
    assignments = _load_json(assignments_path) if assignments_path.is_file() else None
    human_review = _load_json(human_review_path) if human_review_path else None
    has_full_sections = len(case_results) == policy.required_case_count and all(
        isinstance(case.get("ai_off"), Mapping)
        and bool(case.get("ai_off"))
        and isinstance(case.get("ai_on"), Mapping)
        and bool(case.get("ai_on"))
        for case in case_results
    )
    has_complete_pair_refs = all(
        isinstance(case.get("ai_on"), Mapping)
        and isinstance(case["ai_on"].get("initial_render_path"), str)
        and bool(case["ai_on"].get("initial_render_path"))
        and isinstance(case["ai_on"].get("final_render_path"), str)
        and bool(case["ai_on"].get("final_render_path"))
        for case in case_results
    )
    if human_review is not None and has_full_sections and not has_complete_pair_refs:
        raise ValueError(
            "人工盲评要求每个 case 都有可比较的 model initial/final 图对。"
        )
    identical_case_ids = (
        _bit_identical_case_ids(suite_root, case_results)
        if has_full_sections and has_complete_pair_refs
        else None
    )
    gate = (
        evaluate_quality_gate(
            case_results,
            policy,
            human_review=human_review,
            assignments=assignments,
            expected_suite_run_id=suite_run_id,
            bit_identical_case_ids=identical_case_ids,
        ).to_dict()
        if has_full_sections
        else None
    )
    blind_manifest_path = suite_root / "blind-review/evidence-manifest.json"
    blind_evidence_schema = config.get("blind_review_evidence_schema")
    return {
        "schema_version": 3,
        "suite_run_id": suite_run_id,
        "suite_id": suite.suite_id,
        "config_schema_version": config.get("schema_version"),
        "config_sha256": sha256(config_path.read_bytes()).hexdigest(),
        "manifest_sha256": suite.manifest_sha256,
        "gate_policy_id": policy.policy_id,
        "gate_policy_sha256": sha256(GATE_POLICY_PATH.read_bytes()).hexdigest(),
        "mode": mode,
        "model_ref": model_ref,
        "model_routing": config.get("model_routing"),
        **model_summary,
        "quality_preset": preset.value,
        "objective_version": objective_version,
        "initial_selection_policy": initial_selection_policy,
        "blind_review_evidence_schema": blind_evidence_schema,
        "blind_review_manifest_path": (
            _relative(suite_root, blind_manifest_path)
            if blind_manifest_path.is_file()
            else None
        ),
        "blind_review_manifest_sha256": (
            sha256(blind_manifest_path.read_bytes()).hexdigest()
            if blind_manifest_path.is_file()
            else None
        ),
        "blind_review_reviewer_path": (
            "blind-review/reviewer/index.html"
            if blind_manifest_path.is_file()
            and blind_evidence_schema == BLIND_REVIEW_EVIDENCE_SCHEMA
            else (
                "blind-review/index.html"
                if (suite_root / "blind-review/assignments.private.json").is_file()
                and (suite_root / "blind-review/index.html").is_file()
                else None
            )
        ),
        "model_call_budget": model_call_budget,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": _build_summary(
            case_results,
            minimum_improvement=policy.min_total_improvement,
        ),
        "cases": list(case_results),
        "quality_gate": gate,
        "human_review_path": str(human_review_path) if human_review_path else None,
        "human_review_sha256": (
            sha256(human_review_path.read_bytes()).hexdigest()
            if human_review_path
            else None
        ),
    }


def _verify_frozen_blind_review_anchor(
    *,
    suite_root: Path,
    config_path: Path,
    config: Mapping[str, Any],
    suite_run_id: str,
) -> None:
    """用首次报告锚定 manifest；验证失败时不得读取人工选择或覆盖报告."""
    report_path = suite_root / "report.json"
    if not report_path.is_file():
        raise ValueError("evaluate 找不到锚定盲评 manifest 的原运行 report.json。")
    frozen_report = _load_json(report_path)
    expected_pairs = {
        "suite_run_id": suite_run_id,
        "suite_id": config.get("suite_id"),
        "config_schema_version": config.get("schema_version"),
        "config_sha256": sha256(config_path.read_bytes()).hexdigest(),
        "manifest_sha256": config.get("manifest_sha256"),
        "gate_policy_sha256": config.get("gate_policy_sha256"),
        "blind_review_evidence_schema": BLIND_REVIEW_EVIDENCE_SCHEMA,
        "blind_review_manifest_path": "blind-review/evidence-manifest.json",
        "blind_review_reviewer_path": "blind-review/reviewer/index.html",
    }
    drifted = [
        field
        for field, expected in expected_pairs.items()
        if frozen_report.get(field) != expected
    ]
    if drifted:
        raise ValueError(
            "原运行 report.json 的盲评冻结锚点已漂移：" + ", ".join(drifted)
        )
    expected_manifest_sha256 = frozen_report.get("blind_review_manifest_sha256")
    if (
        not isinstance(expected_manifest_sha256, str)
        or len(expected_manifest_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in expected_manifest_sha256
        )
    ):
        raise ValueError("原运行 report.json 缺少合法的盲评 manifest SHA-256。")
    manifest_path = suite_root / "blind-review/evidence-manifest.json"
    if not manifest_path.is_file():
        raise ValueError("evaluate 找不到冻结的盲评 evidence manifest。")
    if sha256(manifest_path.read_bytes()).hexdigest() != expected_manifest_sha256:
        raise ValueError("盲评 evidence manifest 与原运行 report.json 锚点不一致。")
    verify_blind_review_package(
        suite_root,
        expected_suite_run_id=suite_run_id,
    )


async def _run(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    suite = load_benchmark_suite(MANIFEST_PATH)
    policy = load_quality_gate_policy(GATE_POLICY_PATH)
    if policy.suite_id != suite.suite_id:
        raise ValueError("gate policy 与 benchmark suite_id 不一致。")
    requested_run_id = args.suite_run_id or _utc_run_id()
    suite_root = (
        args.output_dir.resolve()
        if args.output_dir
        else (DEFAULT_OUTPUT_ROOT / requested_run_id).resolve()
    )
    suite_root.mkdir(parents=True, exist_ok=True)
    if args.mode == "evaluate":
        if args.human_review is None:
            raise ValueError("evaluate 必须提供 --human-review。")
        if args.cases:
            raise ValueError("evaluate 必须使用原运行冻结的完整 case 集合。")
        config_path = suite_root / "config.json"
        if not config_path.is_file():
            raise ValueError("evaluate 找不到原运行 config.json。")
        config = _load_json(config_path)
        config_schema = int(config.get("schema_version", 0))
        if config_schema not in {1, 2, 3}:
            raise ValueError("原运行 config schema_version 不受支持。")
        if config_schema == 3 and (
            config.get("objective_version") != M5_OBJECTIVE_VERSION
            or config.get("initial_selection_policy") != INITIAL_SELECTION_POLICY
        ):
            raise ValueError("原运行 config 的 benchmark objective 已漂移。")
        blind_evidence_schema = config.get("blind_review_evidence_schema")
        if blind_evidence_schema is not None and (
            type(blind_evidence_schema) is not int
            or blind_evidence_schema != BLIND_REVIEW_EVIDENCE_SCHEMA
        ):
            raise ValueError("原运行 config 的 blind review evidence schema 不受支持。")
        suite_run_id = str(config.get("suite_run_id", ""))
        if not suite_run_id:
            raise ValueError("原运行 config.json 缺少 suite_run_id。")
        if args.suite_run_id and args.suite_run_id != suite_run_id:
            raise ValueError("--suite-run-id 与原运行 config.json 不一致。")
        if config.get("suite_id") != suite.suite_id:
            raise ValueError("原运行 config.json 的 suite_id 已漂移。")
        if config.get("manifest_sha256") != suite.manifest_sha256:
            raise ValueError("原运行 config.json 的 manifest SHA-256 已漂移。")
        expected_gate_sha256 = sha256(GATE_POLICY_PATH.read_bytes()).hexdigest()
        if config.get("gate_policy_sha256") != expected_gate_sha256:
            raise ValueError("原运行 config.json 的 gate policy SHA-256 已漂移。")
        frozen_report_path = suite_root / "report.json"
        frozen_report = (
            _load_json(frozen_report_path) if frozen_report_path.is_file() else {}
        )
        report_blind_evidence_schema = frozen_report.get("blind_review_evidence_schema")
        strict_blind_evidence = (
            blind_evidence_schema is not None
            or report_blind_evidence_schema is not None
        )
        if strict_blind_evidence:
            if (
                blind_evidence_schema != BLIND_REVIEW_EVIDENCE_SCHEMA
                or report_blind_evidence_schema != BLIND_REVIEW_EVIDENCE_SCHEMA
            ):
                raise ValueError(
                    "config/report 的 blind review evidence schema 不一致。"
                )
            _verify_frozen_blind_review_anchor(
                suite_root=suite_root,
                config_path=config_path,
                config=config,
                suite_run_id=suite_run_id,
            )
        raw_case_ids = config.get("case_ids")
        if not isinstance(raw_case_ids, list) or not all(
            isinstance(case_id, str) for case_id in raw_case_ids
        ):
            raise ValueError("原运行 config.json 的 case_ids 非法。")
        cases = _selected_cases(suite, ",".join(raw_case_ids))
        preset = QualityPreset(str(config["quality_preset"]))
        effective_model_call_budget = int(config["model_call_budget"])
    else:
        cases = _selected_cases(suite, args.cases)
        suite_run_id = requested_run_id
        preset = QualityPreset(args.quality_preset)
        effective_model_call_budget = args.model_call_budget
        model_routing = (
            _structured_model_routing_snapshot()
            if args.mode in {"ai-on", "all"}
            else None
        )
        configured_model_ref = (
            str(model_routing["shader_author"]["model_ref"])
            if model_routing is not None
            else None
        )
        expected_config = {
            "schema_version": 3,
            "suite_run_id": suite_run_id,
            "suite_id": suite.suite_id,
            "manifest_sha256": suite.manifest_sha256,
            "gate_policy_sha256": sha256(GATE_POLICY_PATH.read_bytes()).hexdigest(),
            "mode": args.mode,
            "quality_preset": preset.value,
            "case_ids": [case.case_id for case in cases],
            "instruction": args.instruction,
            "model_call_budget": effective_model_call_budget,
            "acceptance_policy": asdict(BENCHMARK_ACCEPTANCE_POLICY),
            "model_ref": configured_model_ref,
            "model_routing": model_routing,
            "objective_version": M5_OBJECTIVE_VERSION,
            "initial_selection_policy": INITIAL_SELECTION_POLICY,
            "blind_review_evidence_schema": BLIND_REVIEW_EVIDENCE_SCHEMA,
            "started_at": datetime.now(UTC).isoformat(),
        }
        config_path = suite_root / "config.json"
        if config_path.is_file():
            config = _load_json(config_path)
            config_schema = int(config.get("schema_version", 0))
            frozen_fields: tuple[str, ...]
            if config_schema == 1:
                legacy_results = _load_existing_results(suite_root, cases)
                has_complete_ai_on = all(
                    bool(legacy_results.get(case.case_id, {}).get("ai_on"))
                    for case in cases
                )
                if args.mode in {"ai-on", "all"} and not has_complete_ai_on:
                    raise ValueError(
                        "旧版 config 未可靠冻结 dotenv 模型；部分 AI-on 运行不能继续，"
                        "请使用新的 suite-run-id。"
                    )
                expected_config["schema_version"] = 1
                expected_config["model_ref"] = config.get("model_ref")
                expected_config.pop("model_routing", None)
                expected_config.pop("objective_version", None)
                expected_config.pop("initial_selection_policy", None)
                expected_config.pop("blind_review_evidence_schema", None)
                frozen_fields = (
                    "schema_version",
                    "suite_run_id",
                    "suite_id",
                    "manifest_sha256",
                    "gate_policy_sha256",
                    "mode",
                    "quality_preset",
                    "case_ids",
                    "instruction",
                    "model_call_budget",
                    "acceptance_policy",
                    "model_ref",
                )
            elif config_schema == 2:
                legacy_results = _load_existing_results(suite_root, cases)
                has_complete_ai_on = all(
                    bool(legacy_results.get(case.case_id, {}).get("ai_on"))
                    for case in cases
                )
                if args.mode in {"ai-on", "all"} and not has_complete_ai_on:
                    raise ValueError(
                        "旧版 config 使用混合 benchmark objective；部分 AI-on 运行不能继续，"
                        "请使用新的 suite-run-id。"
                    )
                expected_config["schema_version"] = 2
                expected_config.pop("objective_version", None)
                expected_config.pop("initial_selection_policy", None)
                expected_config.pop("blind_review_evidence_schema", None)
                frozen_fields = (
                    "schema_version",
                    "suite_run_id",
                    "suite_id",
                    "manifest_sha256",
                    "gate_policy_sha256",
                    "mode",
                    "quality_preset",
                    "case_ids",
                    "instruction",
                    "model_call_budget",
                    "acceptance_policy",
                    "model_ref",
                    "model_routing",
                )
            elif config_schema == 3:
                if "blind_review_evidence_schema" not in config:
                    expected_config.pop("blind_review_evidence_schema", None)
                frozen_fields = (
                    "schema_version",
                    "suite_run_id",
                    "suite_id",
                    "manifest_sha256",
                    "gate_policy_sha256",
                    "mode",
                    "quality_preset",
                    "case_ids",
                    "instruction",
                    "model_call_budget",
                    "acceptance_policy",
                    "model_ref",
                    "model_routing",
                    "objective_version",
                    "initial_selection_policy",
                    *(
                        ("blind_review_evidence_schema",)
                        if "blind_review_evidence_schema" in config
                        else ()
                    ),
                )
            else:
                raise ValueError("恢复运行的 config schema_version 不受支持。")
            drifted = [
                field
                for field in frozen_fields
                if config.get(field) != expected_config.get(field)
            ]
            if drifted:
                raise ValueError("恢复运行的冻结配置已漂移：" + ", ".join(drifted))
        else:
            config = expected_config
            _write_json(config_path, config)
    results = _load_existing_results(suite_root, cases)
    for case in cases:
        results.setdefault(
            case.case_id,
            {"case_id": case.case_id, "level": case.level, "ai_off": {}, "ai_on": {}},
        )
    modes_with_ai_off = args.mode in {"ai-off", "all"}
    modes_with_ai_on = args.mode in {"ai-on", "all"}
    if modes_with_ai_on and not args.allow_model_calls:
        raise ValueError("ai-on/all 必须显式提供 --allow-model-calls。")
    if effective_model_call_budget <= 0:
        raise ValueError("--model-call-budget 必须大于 0。")

    if modes_with_ai_off:
        renderer = PlaywrightWebGL1Renderer()
        try:
            for index, case in enumerate(cases, 1):
                if results[case.case_id].get("ai_off"):
                    _progress(f"[ai-off {index}/{len(cases)}] {case.case_id}: resume")
                    continue
                _progress(f"[ai-off {index}/{len(cases)}] {case.case_id}: running")
                results[case.case_id]["ai_off"] = await _run_ai_off_case(
                    case,
                    suite_root,
                    renderer,
                )
                _save_case_result(suite_root, case, results[case.case_id])
        finally:
            await renderer.close()

    if modes_with_ai_on:
        consumed = sum(
            int(result.get("ai_on", {}).get("model_call_count", 0))
            for result in results.values()
            if isinstance(result.get("ai_on"), Mapping)
        )
        for index, case in enumerate(cases, 1):
            if results[case.case_id].get("ai_on"):
                _progress(f"[ai-on {index}/{len(cases)}] {case.case_id}: resume")
                continue
            remaining = effective_model_call_budget - consumed
            _progress(
                f"[ai-on {index}/{len(cases)}] {case.case_id}: running "
                f"(remaining global calls={remaining})"
            )
            try:
                ai_on = await _run_ai_on_case(
                    case,
                    suite_root,
                    suite_run_id,
                    preset,
                    args.instruction,
                    remaining,
                )
            except asyncio.CancelledError:
                _write_json(
                    _case_root(suite_root, case.case_id) / "ai-on/interrupted.json",
                    {
                        "error_type": "CancelledError",
                        "recorded_at": datetime.now(UTC).isoformat(),
                    },
                )
                raise
            except Exception as exc:
                ai_on = {
                    "success": False,
                    "failure_reason": type(exc).__name__,
                    "model_call_count": 0,
                    "final_compile_passed": False,
                    "final_static_passed": False,
                    "final_matches_current_best": False,
                    "best_updates_monotonic": False,
                    "traceability_passed": False,
                }
                _write_json(
                    _case_root(suite_root, case.case_id) / "ai-on/failure.json",
                    {"error_type": type(exc).__name__},
                )
            results[case.case_id]["ai_on"] = ai_on
            consumed += int(ai_on.get("model_call_count", 0))
            _save_case_result(suite_root, case, results[case.case_id])
            _progress(
                f"[ai-on {index}/{len(cases)}] {case.case_id}: "
                f"success={ai_on.get('success')} calls={ai_on.get('model_call_count')} "
                f"initial={ai_on.get('initial_total_loss')} final={ai_on.get('final_total_loss')}"
            )

    ordered = _case_results_in_order(cases, results)
    if modes_with_ai_on:
        has_complete_pair_refs = all(
            isinstance(case.get("ai_on"), Mapping)
            and isinstance(case["ai_on"].get("initial_render_path"), str)
            and bool(case["ai_on"].get("initial_render_path"))
            and isinstance(case["ai_on"].get("final_render_path"), str)
            and bool(case["ai_on"].get("final_render_path"))
            for case in ordered
        )
        if has_complete_pair_refs:
            if (
                config.get("blind_review_evidence_schema")
                == BLIND_REVIEW_EVIDENCE_SCHEMA
            ):
                write_blind_review_package(suite_root, suite_run_id, ordered)
            elif (suite_root / "blind-review/assignments.private.json").is_file():
                verify_legacy_blind_review_package(suite_root, suite_run_id, ordered)
            else:
                raise ValueError(
                    "旧式运行没有盲评证据；为避免原地改写冻结产物，请使用新的 "
                    "suite-run-id。"
                )
        else:
            _progress(
                "[blind-review] skipped: 至少一个 case 缺少成功的 model initial，"
                "initial/final 不可比较。"
            )
    elif args.mode == "evaluate":
        if config.get("blind_review_evidence_schema") is None:
            verify_legacy_blind_review_package(suite_root, suite_run_id, ordered)
    report = _build_report(
        suite=suite,
        suite_root=suite_root,
        suite_run_id=suite_run_id,
        mode=args.mode,
        preset=preset,
        model_ref=config["model_ref"],
        model_call_budget=effective_model_call_budget,
        case_results=ordered,
        human_review_path=args.human_review,
    )
    _write_json(suite_root / "report.json", report)
    _write_text(suite_root / "report.md", _report_markdown(report))
    return suite_root, report


def main() -> int:
    """执行 benchmark 并按显式门禁选项返回退出码."""
    args = _parse_args()
    try:
        suite_root, report = asyncio.run(_run(args))
    except KeyboardInterrupt:
        sys.stderr.write(
            "benchmark interrupted; completed case evidence was preserved.\n"
        )
        return 130
    except Exception as exc:
        sys.stderr.write(f"benchmark failed: {type(exc).__name__}: {exc}\n")
        return 2
    sys.stdout.write(f"benchmark report: {suite_root / 'report.md'}\n")
    gate_status = _section_status(report)
    sys.stdout.write(f"quality gate: {gate_status}\n")
    if args.mode == "ai-off":
        ai_off_passed = all(
            bool(case.get("ai_off", {}).get("static_passed"))
            and bool(case.get("ai_off", {}).get("compile_passed"))
            for case in report.get("cases", [])
            if isinstance(case, Mapping)
        )
        return 0 if ai_off_passed else 2
    if args.require_gate_passed and gate_status != "passed":
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
