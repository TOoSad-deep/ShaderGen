"""V2.3 State/Budget schema、revision transition 与 checkpoint 恢复原语。."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, model_validator

from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.store import ArtifactRefV2

STATE_SCHEMA_VERSION_V4 = "state_v4"
GRAPH_ID_V2 = "png_to_shader_v2"
GRAPH_VERSION_V2_4 = "2.4"
CHECKPOINT_SCHEMA_VERSION_V4 = "checkpoint_v4"

PngToShaderV2Phase = Literal[
    "initialized",
    "measured",
    "interpreted",
    "intent_built",
    "seeding",
    "compiling",
    "rendering",
    "evaluating",
    "selecting",
    "finalized",
]

_BUDGET_FIELDS = (
    "wall_time_ms",
    "model_calls",
    "model_tokens",
    "render_calls",
    "candidate_attempts",
    "artifact_bytes",
    "cost_usd_micros",
)

_IMMUTABLE_STATE_FIELDS = frozenset(
    {
        "state_schema_version",
        "graph_id",
        "graph_version",
        "checkpoint_schema_version",
        "checkpoint_namespace",
        "project_id",
        "run_id",
        "budget_state",
    }
)


class BudgetVectorV2(FrozenModel):
    """V2 全路径共用的七维非负预算向量。."""

    wall_time_ms: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    model_tokens: int = Field(ge=0)
    render_calls: int = Field(ge=0)
    candidate_attempts: int = Field(ge=0)
    artifact_bytes: int = Field(ge=0)
    cost_usd_micros: int = Field(ge=0)


class BudgetStateV2(FrozenModel):
    """带独立 revision 与 reservation 的预算状态。."""

    schema_version: Literal["budget_state_v2"] = "budget_state_v2"
    policy_hash: Sha256Hex
    revision: int = Field(ge=0)
    limits: BudgetVectorV2
    used: BudgetVectorV2
    reserved: BudgetVectorV2
    exhausted_dimensions: tuple[NonEmptyString, ...]

    @model_validator(mode="after")
    def _validate_accounting(self) -> BudgetStateV2:
        exhausted: list[str] = []
        for field_name in _BUDGET_FIELDS:
            limit = getattr(self.limits, field_name)
            consumed = getattr(self.used, field_name) + getattr(
                self.reserved, field_name
            )
            if consumed > limit:
                raise ValueError(f"预算维度 {field_name} 的 used + reserved 超限。")
            if consumed == limit:
                exhausted.append(field_name)
        if tuple(exhausted) != self.exhausted_dimensions:
            raise ValueError("exhausted_dimensions 必须与预算账本精确一致。")
        return self


class HypothesisBranchStateV2(FrozenModel):
    """checkpoint 中单个目标假设的有界游标与选择指针。."""

    target_hypothesis_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    intent_ref: ArtifactRefV2
    strategy_ref: ArtifactRefV2 | None
    seed_refs: tuple[ArtifactRefV2, ...]
    seed_cursor: int = Field(ge=0)
    hypothesis_best_id: NonEmptyString | None
    status: Literal["pending", "running", "completed", "failed"]

    @model_validator(mode="after")
    def _validate_cursor(self) -> HypothesisBranchStateV2:
        if self.seed_cursor > len(self.seed_refs):
            raise ValueError("seed_cursor 不能越过 seed_refs。")
        return self


class PngToShaderV2State(FrozenModel):
    """只保存版本、游标、小型分支状态和 ArtifactRef 的 V2 State。."""

    state_schema_version: Literal["state_v4"] = "state_v4"
    graph_id: Literal["png_to_shader_v2"] = "png_to_shader_v2"
    graph_version: Literal["2.4"] = "2.4"
    checkpoint_schema_version: Literal["checkpoint_v4"] = "checkpoint_v4"
    checkpoint_namespace: NonEmptyString
    project_id: NonEmptyString
    run_id: NonEmptyString
    run_revision: int = Field(ge=0)
    phase: PngToShaderV2Phase
    evaluation_revision: int = Field(ge=0)
    measurements_ref: ArtifactRefV2
    visual_interpretation_ref: ArtifactRefV2 | None
    request_constraint_set_ref: ArtifactRefV2
    hypothesis_branches: tuple[HypothesisBranchStateV2, ...]
    hypothesis_cursor: int = Field(ge=0)
    objective_best_id: NonEmptyString | None
    candidate_summary_refs: tuple[ArtifactRefV2, ...]
    active_seed_ref: ArtifactRefV2 | None = None
    active_genome_ref: ArtifactRefV2 | None = None
    active_compilation_ref: ArtifactRefV2 | None = None
    active_diagnostic_compilation_ref: ArtifactRefV2 | None = None
    active_render_plan_ref: ArtifactRefV2 | None = None
    active_render_progress_ref: ArtifactRefV2 | None = None
    active_render_repeatability_ref: ArtifactRefV2 | None = None
    active_rendered_structure_evidence_ref: ArtifactRefV2 | None = None
    active_rendered_structure_verification_ref: ArtifactRefV2 | None = None
    active_evaluation_refs: tuple[ArtifactRefV2, ...] = ()
    active_attempt_id: NonEmptyString | None = None
    active_semantic_genome_hash: Sha256Hex | None = None
    active_attempt_evidence_refs: tuple[ArtifactRefV2, ...] = ()
    active_render_call_ordinal: int | None = Field(default=None, ge=1, le=2)
    objective_best_ref: ArtifactRefV2 | None = None
    # Promotion 采用持久 outbox：operation_ref 是外部调用前的 intent，
    # receipt_ref 只在 sink execute/recover 可证明完成后写入。
    promotion_operation_ref: ArtifactRefV2 | None = None
    promotion_receipt_ref: ArtifactRefV2 | None = None
    budget_state: BudgetStateV2
    stop_reason: NonEmptyString | None

    @model_validator(mode="after")
    def _validate_checkpoint_identity(self) -> PngToShaderV2State:
        expected = build_checkpoint_namespace_v2(self.run_id)
        if self.checkpoint_namespace != expected:
            raise ValueError(f"checkpoint_namespace 必须等于 {expected}。")
        if self.hypothesis_cursor > len(self.hypothesis_branches):
            raise ValueError("hypothesis_cursor 不能越过 hypothesis_branches。")
        ids = [branch.target_hypothesis_id for branch in self.hypothesis_branches]
        hashes = [branch.target_hypothesis_hash for branch in self.hypothesis_branches]
        if len(set(ids)) != len(ids) or len(set(hashes)) != len(hashes):
            raise ValueError("State 中 hypothesis id/hash 不得重复。")
        if self.active_render_call_ordinal is not None and (
            self.phase != "rendering"
            or self.active_attempt_id is None
            or self.active_render_plan_ref is None
            or self.active_render_progress_ref is None
        ):
            raise ValueError("active render call intent 缺少 rendering/plan/progress/attempt。")
        if self.budget_state.reserved.render_calls not in {0, 1}:
            raise ValueError("State 同时最多保留一个 Renderer call reservation。")
        if (
            self.budget_state.reserved.render_calls == 1
            and self.active_render_call_ordinal is None
        ):
            raise ValueError("Renderer reservation 必须绑定持久 physical call intent。")
        render_refs = (
            self.active_render_plan_ref,
            self.active_render_progress_ref,
            self.active_render_repeatability_ref,
            self.active_rendered_structure_evidence_ref,
            self.active_rendered_structure_verification_ref,
        )
        if any(item is not None for item in render_refs) and (
            self.active_attempt_id is None
            or self.active_diagnostic_compilation_ref is None
        ):
            raise ValueError("render suite refs 不得脱离 attempt/diagnostic compilation。")
        if self.active_rendered_structure_verification_ref is not None and (
            self.active_rendered_structure_evidence_ref is None
            or self.active_render_repeatability_ref is None
        ):
            raise ValueError("structure verification 缺少 evidence/repeatability。")
        if len(self.active_evaluation_refs) > 5:
            raise ValueError("每个 Candidate 最多保存五次 beauty evaluation refs。")
        if self.active_evaluation_refs and (
            self.active_render_repeatability_ref is None
            or self.active_rendered_structure_verification_ref is None
        ):
            raise ValueError("Beauty evaluation refs 不得脱离 repeatability/structure closure。")
        if self.promotion_operation_ref is not None and self.objective_best_ref is None:
            raise ValueError("promotion operation 不得脱离 objective best。")
        if (
            self.promotion_receipt_ref is not None
            and self.promotion_operation_ref is None
        ):
            raise ValueError("promotion receipt 不得脱离 operation intent。")
        return self


def build_checkpoint_namespace_v2(run_id: str) -> str:
    """构造与总纲矩阵一致的 V2 checkpoint namespace。."""
    if not run_id.strip() or ":" in run_id:
        raise ValueError("run_id 不能为空或包含冒号。")
    return f"png-to-shader-v2.4:{run_id}"


def _vector_values(vector: BudgetVectorV2) -> dict[str, int]:
    return {field_name: getattr(vector, field_name) for field_name in _BUDGET_FIELDS}


def _exhausted(
    limits: dict[str, int], used: dict[str, int], reserved: dict[str, int]
) -> tuple[str, ...]:
    return tuple(
        field_name
        for field_name in _BUDGET_FIELDS
        if used[field_name] + reserved[field_name] == limits[field_name]
    )


def reserve_budget_v2(
    state: BudgetStateV2,
    delta: BudgetVectorV2,
    *,
    expected_revision: int,
) -> BudgetStateV2:
    """检查 Budget revision 后返回增加 reservation 的新对象。."""
    if state.revision != expected_revision:
        raise RuntimeError("BudgetStateV2 revision 不匹配。")
    limits = _vector_values(state.limits)
    used = _vector_values(state.used)
    reserved = _vector_values(state.reserved)
    for field_name, increment in _vector_values(delta).items():
        reserved[field_name] += increment
        if used[field_name] + reserved[field_name] > limits[field_name]:
            raise ValueError(f"预算维度 {field_name} reservation 超限。")
    return BudgetStateV2(
        policy_hash=state.policy_hash,
        revision=state.revision + 1,
        limits=state.limits,
        used=state.used,
        reserved=BudgetVectorV2(**reserved),
        exhausted_dimensions=_exhausted(limits, used, reserved),
    )


def commit_budget_v2(
    state: BudgetStateV2,
    *,
    reservation: BudgetVectorV2,
    used: BudgetVectorV2,
    expected_revision: int,
) -> BudgetStateV2:
    """检查 revision 后消费 reservation，并返回实际 used 记账新对象。."""
    if state.revision != expected_revision:
        raise RuntimeError("BudgetStateV2 revision 不匹配。")
    limits = _vector_values(state.limits)
    total_used = _vector_values(state.used)
    total_reserved = _vector_values(state.reserved)
    reservation_values = _vector_values(reservation)
    used_values = _vector_values(used)
    for field_name in _BUDGET_FIELDS:
        if reservation_values[field_name] > total_reserved[field_name]:
            raise ValueError(f"预算维度 {field_name} 没有足够 reservation。")
        if used_values[field_name] > reservation_values[field_name]:
            raise ValueError(f"预算维度 {field_name} 的实际 used 超过 reservation。")
        total_reserved[field_name] -= reservation_values[field_name]
        total_used[field_name] += used_values[field_name]
    return BudgetStateV2(
        policy_hash=state.policy_hash,
        revision=state.revision + 1,
        limits=state.limits,
        used=BudgetVectorV2(**total_used),
        reserved=BudgetVectorV2(**total_reserved),
        exhausted_dimensions=_exhausted(limits, total_used, total_reserved),
    )


def evolve_state_v2(
    state: PngToShaderV2State,
    *,
    expected_run_revision: int,
    **changes: Any,
) -> PngToShaderV2State:
    """校验期望 revision 后返回新 State；不提供持久化原子 CAS。."""
    if state.run_revision != expected_run_revision:
        raise RuntimeError("PngToShaderV2State run_revision 不匹配。")
    if "run_revision" in changes:
        raise ValueError("run_revision 由 transition helper 管理。")
    unknown = set(changes).difference(PngToShaderV2State.model_fields)
    if unknown:
        raise ValueError(
            f"State transition 包含未知字段：{', '.join(sorted(unknown))}。"
        )
    protected = set(changes).intersection(_IMMUTABLE_STATE_FIELDS)
    if protected:
        raise ValueError(
            "State identity/version/namespace/budget 不得通过 transition 修改："
            f"{', '.join(sorted(protected))}。"
        )
    candidate = state.model_copy(
        update={**changes, "run_revision": state.run_revision + 1}
    )
    return PngToShaderV2State.model_validate_json(
        candidate.model_dump_json(warnings="none"),
        strict=True,
    )


def serialize_state_v2(state: PngToShaderV2State) -> bytes:
    """序列化最后确认的 V2 State，不夹带对象实例。."""
    return state.model_dump_json().encode("utf-8")


def restore_state_v2(payload: bytes | str) -> PngToShaderV2State:
    """严格恢复 state_v4；旧 V3/V2/V1 State 不做原地升级。."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    try:
        decoded = json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValueError("V2 State 不是合法 JSON。") from exc
    if not isinstance(decoded, dict):
        raise ValueError("V2 State 必须是 JSON object。")
    return PngToShaderV2State.model_validate_json(payload, strict=True)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """拒绝 State 任意层级的重复 JSON key。."""
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"V2 State 包含重复 JSON key：{key}。")
        value[key] = item
    return value


def _reject_non_finite_json_constant(value: str) -> None:
    """拒绝 Python JSON decoder 默认接受的 NaN/Infinity。."""
    raise ValueError(f"V2 State 包含非法 JSON 常量：{value}。")
