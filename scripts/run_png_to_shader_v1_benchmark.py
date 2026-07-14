"""运行 F09 M5 AI-off/AI-on benchmark、门禁与盲评包生成."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from shaderforge.analysis import RegionOfInterest, measure_target
from shaderforge.benchmark import (
    build_ai_off_shader,
    evaluate_quality_gate,
    load_benchmark_suite,
    load_quality_gate_policy,
    write_blind_review_package,
)
from shaderforge.benchmark.models import BenchmarkCaseSpec, BenchmarkSuiteSpec
from shaderforge.contracts import AcceptancePolicy, QualityPreset, budget_for_preset
from shaderforge.evaluation import CandidateRecord, evaluate_render
from shaderforge.rendering import PlaywrightWebGL1Renderer
from shaderforge.validation import validate_shader

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "benchmarks/png_to_shader_v1/manifest.yaml"
GATE_POLICY_PATH = ROOT / "benchmarks/png_to_shader_v1/m5_gate.yaml"
DEFAULT_OUTPUT_ROOT = ROOT / "output/benchmarks/png-to-shader-v1"
BENCHMARK_ACCEPTANCE_POLICY = AcceptancePolicy(quality_threshold=0.0)


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
    return max(abs(left - right) for left, right in zip(reference, candidate, strict=True))


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
                measurements.foreground_bbox_uv,
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


def _traceability(
    store: Any,
    records: Sequence[CandidateRecord],
    state: Mapping[str, Any],
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    known: set[str] = set()
    for record in records:
        if record.parent_candidate_id is not None and record.parent_candidate_id not in known:
            errors.append(f"{record.candidate_id}:parent_missing")
        known.add(record.candidate_id)
        if not record.prompt_version or not record.model_ref:
            errors.append(f"{record.candidate_id}:prompt_or_model_missing")
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
    store = default_png_to_shader_v1_service.artifact_store.start_run(project_id, run_id)
    output = _case_root(suite_root, case.case_id) / "ai-on"
    output.mkdir(parents=True, exist_ok=True)
    records = sorted(
        (_record(item) for item in state.get("candidate_records", ())),
        key=lambda item: (item.iteration, item.candidate_id),
    )
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
    initial = successful_records[0] if successful_records else None
    best_raw = state.get("current_best_record")
    best = _record(best_raw) if best_raw is not None else None
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
    traceability_passed, traceability_errors = _traceability(store, records, state)
    evidence = {
        "schema_version": 1,
        "project_id": project_id,
        "run_id": run_id,
        "quality_preset": preset.value,
        "budget_policy": asdict(case_budget),
        "acceptance_policy": asdict(BENCHMARK_ACCEPTANCE_POLICY),
        "final_result": {key: value for key, value in final_value.items() if key != "glsl"},
        "candidate_records": [record.to_dict() for record in records],
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
        "initial_total_loss": (
            initial.score_summary.total_loss
            if initial is not None and initial.score_summary is not None
            else None
        ),
        "final_candidate_id": best.candidate_id if best else None,
        "current_best_candidate_id": str(state.get("current_best_id", "")) or None,
        "final_total_loss": (
            best.score_summary.total_loss
            if best is not None and best.score_summary is not None
            else None
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
        result.update(
            _copy_candidate_evidence(
                suite_root,
                output,
                store,
                "initial",
                initial,
            )
        )
    if best is None or best.score_summary is None or best.render_ref is None:
        result["failure_reason"] = str(
            final_value.get("stop_reason") or "no_validated_candidate"
        )
        _write_json(output / "failure.json", evidence)
        return result
    result.update(
        _copy_candidate_evidence(suite_root, output, store, "final", best)
    )
    final_glsl = store.read_bytes(best.glsl_ref).decode("utf-8")
    final_validation = validate_shader(final_glsl)
    result["final_static_passed"] = final_validation.valid
    reference = case.image_path.read_bytes()
    final_render = store.read_bytes(best.render_ref)
    measurements = measure_target(reference)
    benchmark_score = evaluate_render(
        reference,
        final_render,
        measurements=measurements,
        regions=_regions(case),
    )
    candidate_measurements = measure_target(final_render)
    result.update(
        {
            "global_rmse": benchmark_score.global_rmse,
            "bbox_max_error_uv": _bbox_error(
                measurements.foreground_bbox_uv,
                candidate_measurements.foreground_bbox_uv,
            ),
            "key_roi_losses": benchmark_score.roi_loss_map,
            "benchmark_total_loss": benchmark_score.total_loss,
        }
    )
    _write_json(output / "benchmark-metrics.json", benchmark_score.to_dict())
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

    def metric(key: str, digits: int = 2) -> str:
        value = summary.get(key)
        return f"{float(value):.{digits}f}" if isinstance(value, (int, float)) else "—"

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
        f"- quality gate: `{_section_status(report)}`",
        f"- config SHA-256: `{report.get('config_sha256')}`",
        f"- manifest SHA-256: `{report.get('manifest_sha256')}`",
        f"- gate SHA-256: `{report.get('gate_policy_sha256')}`",
        f"- human review SHA-256: `{report.get('human_review_sha256') or '—'}`",
        f"- AI-on compile: `{summary.get('ai_on_final_compile_pass_count', 0)}/{summary.get('case_count', 0)}`",
        f"- average model calls: `{metric('model_call_average')}`",
        f"- average elapsed seconds: `{metric('elapsed_seconds_average')}`",
        f"- average best updates: `{metric('best_update_average')}`",
        f"- improved cases: `{summary.get('improved_case_count', 0)}/{summary.get('improvement_comparable_case_count', 0)}`",
        f"- AI-on no worse than AI-off: `{summary.get('ai_on_beats_ai_off_count', 0)}/{summary.get('ai_on_ai_off_comparable_case_count', 0)}`",
        f"- input/output tokens: `{summary.get('input_tokens_total', 0)}/{summary.get('output_tokens_total', 0)}`",
        "",
        "| case | initial loss | final loss | delta | compile | calls | seconds |",
        "|---|---:|---:|---:|---|---:|---:|",
    ]
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
            "| {case} | {initial} | {final} | {delta} | {compile} | {calls} | {seconds} |".format(
                case=case.get("case_id"),
                initial=f"{float(initial):.6f}" if isinstance(initial, (int, float)) else "—",
                final=f"{float(final):.6f}" if isinstance(final, (int, float)) else "—",
                delta=f"{delta:.6f}" if delta is not None else "—",
                compile="yes" if ai_on.get("final_compile_passed") else "no",
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
        lines.extend(["", "## Quality Gate", "", "| check | pass | actual | expected |", "|---|---|---|---|"])
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
    lines.extend(["", "## Human Review", ""])
    if isinstance(review_count, (int, float)) and review_count > 0:
        preference_text = (
            f"{float(preference_rate):.3f}"
            if isinstance(preference_rate, (int, float))
            else "—"
        )
        lines.append(
            f"已载入 `{int(review_count)}` 项人工盲评；final 偏好率为 "
            f"`{preference_text}`。"
        )
    else:
        lines.append(
            "打开 `blind-review/index.html` 完成全部 A/B 选择，下载 JSON后使用 "
            "`--mode evaluate --human-review <file>` 重新计算最终门禁。"
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
        case["ai_on"]
        for case in case_results
        if isinstance(case.get("ai_on"), Mapping)
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
        "ai_on_success_count": sum(bool(value.get("success")) for value in ai_on_values),
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
    gate = (
        evaluate_quality_gate(
            case_results,
            policy,
            human_review=human_review,
            assignments=assignments,
        ).to_dict()
        if has_full_sections
        else None
    )
    return {
        "schema_version": 2,
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
            "schema_version": 2,
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
            "started_at": datetime.now(UTC).isoformat(),
        }
        config_path = suite_root / "config.json"
        if config_path.is_file():
            config = _load_json(config_path)
            config_schema = int(config.get("schema_version", 0))
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
            else:
                raise ValueError("恢复运行的 config schema_version 不受支持。")
            drifted = [
                field
                for field in frozen_fields
                if config.get(field) != expected_config.get(field)
            ]
            if drifted:
                raise ValueError(
                    "恢复运行的冻结配置已漂移：" + ", ".join(drifted)
                )
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
        write_blind_review_package(suite_root, suite_run_id, ordered)
    elif args.mode == "evaluate" and not (
        suite_root / "blind-review/assignments.private.json"
    ).is_file():
        raise ValueError("evaluate 找不到原运行的盲评 assignments。")
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
        sys.stderr.write("benchmark interrupted; completed case evidence was preserved.\n")
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
