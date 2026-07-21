"""RequestConstraintSet 的严格规范化、冲突裁决与 CAS 构建。."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from shaderforge.contracts import canonical_sha256
from shaderforge.intent.canonical import (
    compute_constraint_set_hash,
    constraint_semantic_projection,
    validate_constraint_set_identity,
    with_constraint_id,
)
from shaderforge.intent.models import (
    Constraint,
    ConstraintConflict,
    ConstraintSource,
    RequestConstraintSet,
)
from shaderforge.store import ArtifactRefV2

CONSTRAINT_MERGE_POLICY_VERSION: Final = "constraint_merge_policy_v1"

# 数字越小，来源优先级越高。该顺序是 V2.1 的冻结契约。
CONSTRAINT_SOURCE_PRIORITY: Final[dict[ConstraintSource, int]] = {
    "render_contract": 0,
    "deployment": 1,
    "user": 2,
    "project_memory": 3,
    "measurement": 4,
    "model": 5,
}
_UNVERIFIED_MEASUREMENT_PRIORITY: Final = 6


def _artifact_sort_key(ref: ArtifactRefV2) -> tuple[object, ...]:
    return (
        ref.kind,
        ref.schema_version,
        ref.sha256,
        ref.content_type,
        ref.size_bytes,
        ref.artifact_id,
    )


def _normalize_artifact_refs(
    refs: Iterable[ArtifactRefV2],
) -> tuple[ArtifactRefV2, ...]:
    """按内容语义去重 ArtifactRef，并稳定选择 run-local 代表。."""
    by_semantics: dict[tuple[str, str, str], ArtifactRefV2] = {}
    for ref in sorted(refs, key=_artifact_sort_key):
        key = (ref.kind, ref.schema_version, ref.sha256)
        by_semantics.setdefault(key, ref)
    return tuple(sorted(by_semantics.values(), key=_artifact_sort_key))


def _normalize_constraint(constraint: Constraint) -> Constraint:
    normalized = constraint.model_copy(
        update={
            "evidence_refs": _normalize_artifact_refs(constraint.evidence_refs),
        }
    )
    return with_constraint_id(normalized)


def _semantic_digest(constraint: Constraint) -> str:
    return canonical_sha256(constraint_semantic_projection(constraint))


def _normalize_constraints(constraints: Iterable[Constraint]) -> tuple[Constraint, ...]:
    by_id: dict[str, Constraint] = {}
    for constraint in constraints:
        normalized = _normalize_constraint(constraint)
        existing = by_id.get(normalized.constraint_id)
        if existing is None:
            by_id[normalized.constraint_id] = normalized
            continue
        if _semantic_digest(existing) != _semantic_digest(normalized):
            raise ValueError(
                "相同 constraint_id 出现不同 confidence、verification 或 evidence 语义。"
            )
        # source_revision 不参与身份或集合 hash；保留最新 revision 使完整记录稳定。
        if normalized.source_revision > existing.source_revision:
            by_id[normalized.constraint_id] = normalized
    return tuple(by_id[key] for key in sorted(by_id))


def _validate_source_policy(constraints: tuple[Constraint, ...]) -> None:
    for constraint in constraints:
        if constraint.source == "render_contract" and constraint.kind != "contract":
            raise ValueError("render_contract source 只能声明 contract constraint。")
        if constraint.source == "deployment" and not (
            constraint.strength == "hard"
            and constraint.kind in {"budget", "complexity"}
        ):
            raise ValueError(
                "deployment source 只允许声明 hard budget/complexity 安全上限。"
            )
        if constraint.source == "model" and constraint.strength == "hard":
            raise ValueError("model constraint 不得直接成为 hard constraint。")
        if constraint.source == "measurement" and constraint.strength == "hard":
            if constraint.verification_status != "verified":
                raise ValueError("measurement hard constraint 必须是 verified。")
            if not constraint.evidence_refs:
                raise ValueError("measurement hard constraint 必须绑定 evidence。")

    contracts = [item for item in constraints if item.kind == "contract"]
    if len(contracts) != 1:
        raise ValueError("RequestConstraintSet 必须恰好包含一条 contract constraint。")
    contract = contracts[0]
    if not (
        contract.source == "render_contract"
        and contract.strength == "hard"
        and contract.verification_status == "verified"
    ):
        raise ValueError(
            "唯一 contract constraint 必须来自 render_contract，且为 verified hard。"
        )


def _conflict_group_key(constraint: Constraint) -> tuple[str, str, str, str]:
    return (
        constraint.strength,
        constraint.kind,
        constraint.scope,
        constraint.scope_ref or "",
    )


def _constraint_priority(constraint: Constraint) -> int:
    if (
        constraint.source == "measurement"
        and constraint.verification_status != "verified"
    ):
        return _UNVERIFIED_MEASUREMENT_PRIORITY
    return CONSTRAINT_SOURCE_PRIORITY[constraint.source]


def _value_digest(constraint: Constraint) -> str:
    return canonical_sha256(constraint.value)


def _conflict_id(
    group_key: tuple[str, str, str, str],
    constraints: tuple[Constraint, ...],
) -> str:
    digest = canonical_sha256(
        {
            "policy_version": CONSTRAINT_MERGE_POLICY_VERSION,
            "group": group_key,
            "constraint_ids": sorted(item.constraint_id for item in constraints),
        }
    )
    return f"conflict_{digest}"


def _build_conflicts(
    constraints: tuple[Constraint, ...],
) -> tuple[ConstraintConflict, ...]:
    grouped: dict[tuple[str, str, str, str], list[Constraint]] = {}
    for constraint in constraints:
        if constraint.verification_status == "rejected":
            # rejected 只保留审计，不参与 winner 或下游 preference。
            continue
        if constraint.kind == "required_layer":
            # required layers 构成可加集合；不同 layer 不是互斥声明。
            continue
        grouped.setdefault(_conflict_group_key(constraint), []).append(constraint)

    conflicts: list[ConstraintConflict] = []
    for group_key, raw_group in grouped.items():
        group = tuple(sorted(raw_group, key=lambda item: item.constraint_id))
        if len({_value_digest(item) for item in group}) <= 1:
            continue

        best_priority = min(_constraint_priority(item) for item in group)
        top = tuple(
            item
            for item in group
            if _constraint_priority(item) == best_priority
        )
        top_values = {_value_digest(item) for item in top}
        conflict_id = _conflict_id(group_key, group)
        constraint_ids = tuple(item.constraint_id for item in group)
        if len(top_values) == 1:
            selected = min(top, key=lambda item: item.constraint_id)
            conflicts.append(
                ConstraintConflict(
                    conflict_id=conflict_id,
                    constraint_ids=constraint_ids,
                    status="resolved",
                    selected_constraint_id=selected.constraint_id,
                    resolution_policy=CONSTRAINT_MERGE_POLICY_VERSION,
                    reason="按冻结来源优先级选择唯一最高优先级语义。",
                )
            )
        else:
            conflicts.append(
                ConstraintConflict(
                    conflict_id=conflict_id,
                    constraint_ids=constraint_ids,
                    status="unresolved",
                    selected_constraint_id=None,
                    resolution_policy=CONSTRAINT_MERGE_POLICY_VERSION,
                    reason="同一最高来源优先级包含互斥语义，需要显式裁决。",
                )
            )
    return tuple(sorted(conflicts, key=lambda item: item.conflict_id))


def build_request_constraint_set(
    *,
    constraint_set_id: str,
    target_sha256: str,
    request_revision: int,
    constraints: Iterable[Constraint],
    evidence_refs: Iterable[ArtifactRefV2] = (),
) -> RequestConstraintSet:
    """从完整结构化约束快照构建规范、可审计的集合。."""
    normalized_constraints = _normalize_constraints(constraints)
    _validate_source_policy(normalized_constraints)
    conflicts = _build_conflicts(normalized_constraints)
    draft = RequestConstraintSet(
        constraint_set_id=constraint_set_id,
        constraint_set_hash="0" * 64,
        target_sha256=target_sha256,
        request_revision=request_revision,
        constraints=normalized_constraints,
        conflicts=conflicts,
        evidence_refs=_normalize_artifact_refs(evidence_refs),
    )
    result = draft.model_copy(
        update={"constraint_set_hash": compute_constraint_set_hash(draft)}
    )
    validate_constraint_set_identity(result)
    return result


def merge_request_constraint_set(
    current: RequestConstraintSet,
    *,
    expected_revision: int,
    constraints: Iterable[Constraint],
    evidence_refs: Iterable[ArtifactRefV2] | None = None,
) -> RequestConstraintSet:
    """以现有模型的 request_revision CAS 物化下一份完整约束快照。."""
    validate_constraint_set_identity(current)
    if current.request_revision != expected_revision:
        raise RuntimeError("RequestConstraintSet revision CAS 冲突。")
    return build_request_constraint_set(
        constraint_set_id=current.constraint_set_id,
        target_sha256=current.target_sha256,
        request_revision=expected_revision + 1,
        constraints=constraints,
        evidence_refs=(
            current.evidence_refs if evidence_refs is None else evidence_refs
        ),
    )


def validate_request_constraint_set_policy(
    constraint_set: RequestConstraintSet,
) -> None:
    """独立重建冻结合并策略，拒绝旧 CAS 或手工 conflict 绕过。."""
    validate_constraint_set_identity(constraint_set)
    rebuilt = build_request_constraint_set(
        constraint_set_id=constraint_set.constraint_set_id,
        target_sha256=constraint_set.target_sha256,
        request_revision=constraint_set.request_revision,
        constraints=constraint_set.constraints,
        evidence_refs=constraint_set.evidence_refs,
    )
    ordered_current = tuple(
        sorted(constraint_set.constraints, key=lambda item: item.constraint_id)
    )
    if ordered_current != rebuilt.constraints:
        raise ValueError("RequestConstraintSet constraints 未经冻结规范化策略生成。")
    if constraint_set.conflicts != rebuilt.conflicts:
        raise ValueError("RequestConstraintSet conflicts 与冻结合并策略不一致。")
    if constraint_set.evidence_refs != rebuilt.evidence_refs:
        raise ValueError("RequestConstraintSet evidence refs 未规范化。")


__all__ = [
    "CONSTRAINT_MERGE_POLICY_VERSION",
    "CONSTRAINT_SOURCE_PRIORITY",
    "build_request_constraint_set",
    "merge_request_constraint_set",
    "validate_request_constraint_set_policy",
]
