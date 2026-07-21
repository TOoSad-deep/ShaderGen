"""V2 请求约束的确定性身份与 revision CAS。."""

from __future__ import annotations

from collections.abc import Iterable

from shaderforge.contracts import canonical_sha256
from shaderforge.intent.models import (
    Constraint,
    ConstraintConflict,
    RegionLockConstraintValue,
    RequestConstraintSet,
)

CONSTRAINT_HASH_VERSION = "constraint_hash_v1"
CONSTRAINT_SET_HASH_VERSION = "constraint_set_hash_v1"
MEASUREMENT_HARD_POLICY_VERSION = "measurement_hard_verified_only_v1"


def _artifact_semantics(ref: object) -> dict[str, object]:
    artifact = ref  # 保持投影代码易于审计，不依赖本地路径或 URI。
    return {
        "sha256": getattr(artifact, "sha256"),
        "kind": getattr(artifact, "kind"),
        "schema_version": getattr(artifact, "schema_version"),
    }


def _constraint_value_semantics(constraint: Constraint) -> object:
    value = constraint.value
    if not isinstance(value, RegionLockConstraintValue):
        return value
    projection = value.model_dump(mode="python")
    projection["mask_ref"] = _artifact_semantics(value.mask_ref)
    return projection


def constraint_semantic_projection(constraint: Constraint) -> dict[str, object]:
    """返回排除 record/revision/存储位置的完整约束语义。."""
    return {
        "kind": constraint.kind,
        "strength": constraint.strength,
        "scope": constraint.scope,
        "scope_ref": constraint.scope_ref,
        "value": _constraint_value_semantics(constraint),
        "source": constraint.source,
        "confidence": constraint.confidence,
        "verification_status": constraint.verification_status,
        "evidence": sorted(
            (_artifact_semantics(ref) for ref in constraint.evidence_refs),
            key=lambda item: (item["kind"], item["schema_version"], item["sha256"]),
        ),
    }


def compute_constraint_id(constraint: Constraint) -> str:
    """按 kind/strength/scope/source/value 生成稳定 constraint id。."""
    digest = canonical_sha256(
        {
            "hash_version": CONSTRAINT_HASH_VERSION,
            "kind": constraint.kind,
            "strength": constraint.strength,
            "scope": constraint.scope,
            "scope_ref": constraint.scope_ref,
            "source": constraint.source,
            "value": _constraint_value_semantics(constraint),
        }
    )
    return f"constraint_{digest}"


def with_constraint_id(constraint: Constraint) -> Constraint:
    """返回写入确定性 id 的新约束。."""
    return constraint.model_copy(
        update={"constraint_id": compute_constraint_id(constraint)}
    )


def compute_constraint_set_hash(constraint_set: RequestConstraintSet) -> str:
    """计算排除 set id 和所有 revision 的集合语义 hash。."""
    constraints = sorted(
        (
            {
                "constraint_id": constraint.constraint_id,
                "semantics": constraint_semantic_projection(constraint),
            }
            for constraint in constraint_set.constraints
        ),
        key=lambda item: str(item["constraint_id"]),
    )
    conflicts = sorted(
        (
            {
                "constraint_ids": sorted(conflict.constraint_ids),
                "status": conflict.status,
                "selected_constraint_id": conflict.selected_constraint_id,
                "resolution_policy": conflict.resolution_policy,
                "reason": conflict.reason,
            }
            for conflict in constraint_set.conflicts
        ),
        key=canonical_sha256,
    )
    return canonical_sha256(
        {
            "hash_version": CONSTRAINT_SET_HASH_VERSION,
            "target_sha256": constraint_set.target_sha256,
            "constraints": constraints,
            "conflicts": conflicts,
        }
    )


def validate_constraint_set_identity(constraint_set: RequestConstraintSet) -> None:
    """验证每条 id 与集合 hash，发现漂移时 fail closed。."""
    for constraint in constraint_set.constraints:
        if constraint.constraint_id != compute_constraint_id(constraint):
            raise ValueError(f"{constraint.constraint_id} 与约束语义不一致。")
    expected = compute_constraint_set_hash(constraint_set)
    if constraint_set.constraint_set_hash != expected:
        raise ValueError("constraint_set_hash 与集合语义不一致。")


def assert_intent_compatible_constraints(constraint_set: RequestConstraintSet) -> None:
    """拒绝将 unresolved conflict 或 rejected hard constraint 送入 Intent。."""
    validate_constraint_set_identity(constraint_set)
    if any(conflict.status == "unresolved" for conflict in constraint_set.conflicts):
        raise ValueError("存在 unresolved conflict，不能生成 hard constraint Intent。")
    if any(
        constraint.strength == "hard" and constraint.verification_status == "rejected"
        for constraint in constraint_set.constraints
    ):
        raise ValueError("rejected hard constraint 不能进入 Intent。")
    if any(
        constraint.source == "measurement"
        and constraint.strength == "hard"
        and constraint.verification_status != "verified"
        for constraint in constraint_set.constraints
    ):
        raise ValueError(
            "measurement hard constraint 必须由独立确认或策略晋升为 verified；"
            "confidence 不能直接晋升。"
        )


def compare_and_swap_constraint_set(
    current: RequestConstraintSet,
    *,
    expected_revision: int,
    constraints: Iterable[Constraint],
    conflicts: Iterable[ConstraintConflict] = (),
) -> RequestConstraintSet:
    """以 request_revision CAS 生成新集合，不原地修改旧 revision。."""
    if current.request_revision != expected_revision:
        raise RuntimeError("RequestConstraintSet revision CAS 冲突。")
    normalized_constraints = tuple(with_constraint_id(item) for item in constraints)
    raw = current.model_dump(mode="python")
    raw.update(
        {
            "request_revision": expected_revision + 1,
            "constraints": normalized_constraints,
            "conflicts": tuple(conflicts),
            "constraint_set_hash": "0" * 64,
        }
    )
    draft = RequestConstraintSet.model_validate(raw, strict=False)
    raw["constraint_set_hash"] = compute_constraint_set_hash(draft)
    result = RequestConstraintSet.model_validate(raw, strict=False)
    validate_constraint_set_identity(result)
    return result
