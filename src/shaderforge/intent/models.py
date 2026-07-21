"""V2.0 请求约束集合的冻结模型。."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from shaderforge.contracts import (
    FiniteFloat,
    FrozenModel,
    NonEmptyString,
    Sha256Hex,
)
from shaderforge.contracts.taxonomy import RequiredLayerTaxon
from shaderforge.store import ArtifactRefV2

ConstraintKind = Literal[
    "contract",
    "topology",
    "instance_count",
    "hole_count",
    "required_layer",
    "region_lock",
    "color_lock",
    "complexity",
    "budget",
]
ConstraintSource = Literal[
    "render_contract",
    "user",
    "project_memory",
    "measurement",
    "model",
    "deployment",
]


class ContractConstraintValue(FrozenModel):
    """RenderContract 身份约束。."""

    kind: Literal["contract"] = "contract"
    contract_id: NonEmptyString


class TopologyConstraintValue(FrozenModel):
    """填充拓扑约束。."""

    kind: Literal["topology"] = "topology"
    topology: Literal["solid", "hollow", "ring", "open"]


class InstanceCountConstraintValue(FrozenModel):
    """实例数量约束。."""

    kind: Literal["instance_count"] = "instance_count"
    exact_count: int = Field(ge=1)


class HoleCountConstraintValue(FrozenModel):
    """孔洞数量约束。."""

    kind: Literal["hole_count"] = "hole_count"
    exact_count: int = Field(ge=0)


class RequiredLayerConstraintValue(FrozenModel):
    """必须保留的视觉层约束。."""

    kind: Literal["required_layer"] = "required_layer"
    layer: RequiredLayerTaxon


class RegionLockConstraintValue(FrozenModel):
    """区域 mask 锁定约束。."""

    kind: Literal["region_lock"] = "region_lock"
    region_id: NonEmptyString
    mask_ref: ArtifactRefV2


class ColorLockConstraintValue(FrozenModel):
    """区域 Lab 颜色及容差约束。."""

    kind: Literal["color_lock"] = "color_lock"
    region_id: NonEmptyString
    lab: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    max_delta_e: FiniteFloat = Field(ge=0.0)

    @model_validator(mode="after")
    def _validate_lab(self) -> ColorLockConstraintValue:
        lightness, axis_a, axis_b = self.lab
        if not 0.0 <= lightness <= 100.0:
            raise ValueError("Lab L 必须位于 0 到 100。")
        if not -128.0 <= axis_a <= 127.0 or not -128.0 <= axis_b <= 127.0:
            raise ValueError("Lab a/b 必须位于 -128 到 127。")
        return self


class ComplexityConstraintValue(FrozenModel):
    """Genome/Compiler 复杂度硬上限。."""

    kind: Literal["complexity"] = "complexity"
    max_nodes: int = Field(ge=1)
    max_estimated_ops: int = Field(ge=1)


class BudgetConstraintValue(FrozenModel):
    """请求层可只指定部分预算维度的上限。."""

    kind: Literal["budget"] = "budget"
    wall_time_ms: int | None = Field(default=None, ge=0)
    model_calls: int | None = Field(default=None, ge=0)
    model_tokens: int | None = Field(default=None, ge=0)
    render_calls: int | None = Field(default=None, ge=0)
    candidate_attempts: int | None = Field(default=None, ge=0)
    artifact_bytes: int | None = Field(default=None, ge=0)
    cost_usd_micros: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _require_one_limit(self) -> BudgetConstraintValue:
        values = self.model_dump(exclude={"kind"})
        if not any(value is not None for value in values.values()):
            raise ValueError("budget constraint 至少指定一个预算维度。")
        return self


ConstraintValue = Annotated[
    ContractConstraintValue
    | TopologyConstraintValue
    | InstanceCountConstraintValue
    | HoleCountConstraintValue
    | RequiredLayerConstraintValue
    | RegionLockConstraintValue
    | ColorLockConstraintValue
    | ComplexityConstraintValue
    | BudgetConstraintValue,
    Field(discriminator="kind"),
]


class Constraint(FrozenModel):
    """带来源、强度和 sealed payload 的单条约束。."""

    constraint_id: NonEmptyString
    kind: ConstraintKind
    strength: Literal["hard", "soft"]
    scope: Literal["global", "object", "region", "parameter"]
    scope_ref: NonEmptyString | None = None
    value: ConstraintValue
    source: ConstraintSource
    source_revision: int = Field(ge=0)
    confidence: FiniteFloat = Field(ge=0.0, le=1.0)
    verification_status: Literal["verified", "inferred", "unverified", "rejected"]
    evidence_refs: tuple[ArtifactRefV2, ...] = ()

    @model_validator(mode="after")
    def _validate_payload_and_scope(self) -> Constraint:
        if self.kind != self.value.kind:
            raise ValueError("Constraint.kind 必须与 value.kind 一致。")
        if self.scope == "global" and self.scope_ref is not None:
            raise ValueError("global constraint 不得设置 scope_ref。")
        if self.scope != "global" and self.scope_ref is None:
            raise ValueError("非 global constraint 必须设置 scope_ref。")
        return self


class ConstraintConflict(FrozenModel):
    """集合内约束冲突及其冻结处理结果。."""

    conflict_id: NonEmptyString
    constraint_ids: tuple[NonEmptyString, ...]
    status: Literal["resolved", "unresolved"]
    selected_constraint_id: NonEmptyString | None
    resolution_policy: NonEmptyString
    reason: NonEmptyString

    @model_validator(mode="after")
    def _validate_resolution(self) -> ConstraintConflict:
        if len(self.constraint_ids) < 2 or len(set(self.constraint_ids)) != len(
            self.constraint_ids
        ):
            raise ValueError("conflict 必须引用至少两条不同约束。")
        if self.status == "resolved":
            if self.selected_constraint_id not in self.constraint_ids:
                raise ValueError("resolved conflict 必须选择所引用的约束。")
        elif self.selected_constraint_id is not None:
            raise ValueError("unresolved conflict 不得设置 selected_constraint_id。")
        return self


class RequestConstraintSet(FrozenModel):
    """请求 revision 对应的完整约束集合。."""

    schema_version: Literal["request_constraint_set_v1"] = "request_constraint_set_v1"
    constraint_set_id: NonEmptyString
    constraint_set_hash: Sha256Hex
    target_sha256: Sha256Hex
    request_revision: int = Field(ge=0)
    constraints: tuple[Constraint, ...]
    conflicts: tuple[ConstraintConflict, ...] = ()
    evidence_refs: tuple[ArtifactRefV2, ...] = ()

    @model_validator(mode="after")
    def _validate_references(self) -> RequestConstraintSet:
        ids = [constraint.constraint_id for constraint in self.constraints]
        if len(set(ids)) != len(ids):
            raise ValueError("constraint_id 不得重复。")
        known = set(ids)
        if any(
            constraint_id not in known
            for conflict in self.conflicts
            for constraint_id in conflict.constraint_ids
        ):
            raise ValueError("conflict 引用了集合外的 constraint_id。")
        return self
