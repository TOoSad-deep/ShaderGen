"""渲染前的证据绑定、静态校验与安全确定性修复."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any

from shaderforge.evaluation import CandidateRecord
from shaderforge.rendering import CompileResult
from shaderforge.store import RunArtifactStore
from shaderforge.validation import (
    repair_constant_reversed_smoothsteps,
    validate_shader,
)

from .runtime import (
    Clock,
    NodeEvidenceError,
    _budget,
    _elapsed_seconds,
    _persist_deterministic_shader_repair,
    _replace_record,
    _validation_diagnostics,
    _write_candidate_manifest,
    logger,
)


@dataclass(frozen=True)
class ValidationStageOutcome:
    """进入真实渲染前的候选、事件和可选失败结果."""

    record: CandidateRecord
    glsl: str
    events: tuple[Any, ...]
    repair_update: dict[str, Any]
    failure_update: dict[str, Any] | None = None


def validate_candidate(
    store: RunArtifactStore,
    state: Mapping[str, Any],
    record: CandidateRecord,
    glsl: str,
    *,
    clock: Clock,
    run_id: str,
    project_id: str,
) -> ValidationStageOutcome:
    """校验证据和 GLSL，并只执行白名单内的确定性修复."""
    try:
        persisted_glsl = store.read_bytes(record.glsl_ref).decode("utf-8")
    except (FileNotFoundError, UnicodeDecodeError) as exc:
        raise NodeEvidenceError("CandidateRecord 引用的 GLSL 证据不可读取。") from exc
    if (
        persisted_glsl != glsl
        or sha256(glsl.encode("utf-8")).hexdigest() != record.glsl_sha256
    ):
        raise NodeEvidenceError("CandidateRecord 与 GLSL 证据绑定不一致。")

    logger.info(
        "shader.pipeline.render.started run_id=%s project_id=%s "
        "candidate_id=%s glsl_chars=%s",
        run_id,
        project_id,
        record.candidate_id,
        len(glsl),
    )
    budget = _budget(state)
    validation = validate_shader(glsl, max_shader_chars=budget.max_shader_chars)
    events = tuple(state.get("events", ()))
    repair_update: dict[str, Any] = {}
    blocking_codes = {item.code for item in validation.errors}
    if blocking_codes == {"reversed_smoothstep_edges"}:
        repair = repair_constant_reversed_smoothsteps(glsl)
        if repair is not None:
            repaired_validation = validate_shader(
                repair.source,
                max_shader_chars=budget.max_shader_chars,
            )
            if repaired_validation.valid:
                record, author, provenance, repair_audit = (
                    _persist_deterministic_shader_repair(
                        store,
                        state,
                        record,
                        repair,
                    )
                )
                glsl = repair.source
                validation = repaired_validation
                repair_update = {
                    "glsl": glsl,
                    "author_result": author,
                    "candidate_provenance": provenance,
                    "candidate_record": record,
                    "candidate_records": _replace_record(
                        tuple(state.get("candidate_records", ())),
                        record,
                    ),
                    "logs": (
                        *state.get("logs", ()),
                        {
                            "level": "warning",
                            "source": "shaderforge.validation",
                            "message": "常量倒序 smoothstep 已执行确定性修复并重验通过",
                            "context": repair_audit,
                        },
                    ),
                }
                events = (
                    *events,
                    {
                        "stage": "render",
                        "event_type": "shader_deterministically_repaired",
                        "payload": {
                            "candidate_id": record.candidate_id,
                            **repair_audit,
                            "elapsed_seconds": round(
                                _elapsed_seconds(state, clock),
                                3,
                            ),
                        },
                    },
                )
                logger.warning(
                    "shader.pipeline.local_repair run_id=%s project_id=%s "
                    "stage=static_validation candidate_id=%s strategy=%s "
                    "replacement_count=%s repaired_lines=%s",
                    run_id,
                    project_id,
                    record.candidate_id,
                    repair.strategy,
                    repair.replacement_count,
                    ",".join(str(line) for line in repair.repaired_lines),
                )

    if validation.valid:
        return ValidationStageOutcome(
            record=record,
            glsl=glsl,
            events=events,
            repair_update=repair_update,
        )

    logger.warning(
        "shader.pipeline.render.failed run_id=%s project_id=%s "
        "candidate_id=%s failure_stage=static_validation violation_codes=%s",
        run_id,
        project_id,
        record.candidate_id,
        ",".join(item.code for item in validation.violations),
    )
    compile_result = CompileResult(
        success=False,
        vertex_log="",
        fragment_log="",
        link_log="",
        draw_error="static_validation_failed",
        static_validation=validation,
    )
    prefix = f"candidates/{record.candidate_id}"
    compile_ref = store.write_json(f"{prefix}/compile.json", compile_result)
    failed = replace(record, compile_ref=compile_ref.relative_path)
    _write_candidate_manifest(store, failed)
    return ValidationStageOutcome(
        record=record,
        glsl=glsl,
        events=events,
        repair_update=repair_update,
        failure_update={
            "phase": "compile_failed",
            "candidate_record": failed,
            "candidate_records": _replace_record(
                tuple(state.get("candidate_records", ())),
                failed,
            ),
            "static_validation": validation.to_dict(),
            "compile_result": compile_result.to_dict(),
            "render_status": "compile_failed",
            "events": (
                *events,
                {
                    "stage": "render",
                    "event_type": "compile_failed",
                    "payload": {
                        "candidate_id": record.candidate_id,
                        "failure_stage": "static_validation",
                        **_validation_diagnostics(validation),
                        "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                    },
                },
            ),
        },
    )
