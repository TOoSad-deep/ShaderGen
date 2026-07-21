"""尚未接入 real provider 的模型预算 receipt 基础设施。"""
# ruff: noqa: D102, D415

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from pydantic import Field

from agent.app.states.png_to_shader_v2_state import BudgetVectorV2, PngToShaderV2State
from agent.app.states.png_to_shader_v2_state_store import LocalPngToShaderV2StateStore
from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.intent import VisualInterpretationV2
from shaderforge.store import ArtifactRefV2, ArtifactResolver


class ModelCallReservationV1(FrozenModel):
    """调用前冻结的单次模型最坏情况上限。"""

    schema_version: Literal["png_to_shader_v2_model_reservation_v1"] = (
        "png_to_shader_v2_model_reservation_v1"
    )
    invocation_id: NonEmptyString
    provider_id: NonEmptyString
    model_id: NonEmptyString
    prompt_sha256: Sha256Hex
    request_sha256: Sha256Hex
    pricing_policy_sha256: Sha256Hex
    measurements_ref: ArtifactRefV2
    constraint_set_ref: ArtifactRefV2
    max_input_tokens: int = Field(ge=0)
    max_output_tokens: int = Field(gt=0)
    max_cost_usd_micros: int = Field(gt=0)
    max_output_artifact_bytes: int = Field(gt=0)

    @property
    def budget_vector(self) -> BudgetVectorV2:
        return BudgetVectorV2(
            wall_time_ms=0,
            model_calls=1,
            model_tokens=self.max_input_tokens + self.max_output_tokens,
            render_calls=0,
            candidate_attempts=0,
            artifact_bytes=self.max_output_artifact_bytes,
            cost_usd_micros=self.max_cost_usd_micros,
        )


class ModelCallReceiptV1(FrozenModel):
    """provider 返回的不可猜测实际 token/cost receipt。"""

    schema_version: Literal["png_to_shader_v2_model_receipt_v1"] = (
        "png_to_shader_v2_model_receipt_v1"
    )
    invocation_id: NonEmptyString
    provider_receipt_id: NonEmptyString
    provider_id: NonEmptyString
    model_id: NonEmptyString
    prompt_sha256: Sha256Hex
    request_sha256: Sha256Hex
    pricing_policy_sha256: Sha256Hex
    measurements_ref: ArtifactRefV2
    constraint_set_ref: ArtifactRefV2
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd_micros: int = Field(ge=0)
    interpretation_ref: ArtifactRefV2
    output_artifact_bytes: int | None = Field(default=None, ge=0)
    audit_ref: ArtifactRefV2 | None = None

    @property
    def budget_vector(self) -> BudgetVectorV2:
        return BudgetVectorV2(
            wall_time_ms=0,
            model_calls=1,
            model_tokens=self.input_tokens + self.output_tokens,
            render_calls=0,
            candidate_attempts=0,
            artifact_bytes=(
                self.interpretation_ref.size_bytes
                if self.output_artifact_bytes is None
                else self.output_artifact_bytes
            ),
            cost_usd_micros=self.cost_usd_micros,
        )


def reserve_model_call_v1(
    state_store: LocalPngToShaderV2StateStore,
    state: PngToShaderV2State,
    reservation: ModelCallReservationV1,
) -> PngToShaderV2State:
    """在 provider 副作用前持久化最坏 token/cost reservation。"""
    current = state_store.load_last_confirmed(state.run_id)
    if current != state:
        raise RuntimeError("模型 reservation 输入不是最后确认 State。")
    return state_store.reserve_budget(
        state.run_id,
        reservation.budget_vector,
        expected_budget_revision=state.budget_state.revision,
    )


def commit_model_call_receipt_v1(
    state_store: LocalPngToShaderV2StateStore,
    reserved_state: PngToShaderV2State,
    reservation: ModelCallReservationV1,
    receipt: ModelCallReceiptV1,
    *,
    resolver: ArtifactResolver,
) -> PngToShaderV2State:
    """验证 provider receipt/ref 后按实际用量提交；超 reservation fail closed。"""
    if receipt.invocation_id != reservation.invocation_id:
        raise ValueError("模型 receipt invocation_id 与 reservation 不一致。")
    for field_name in (
        "provider_id",
        "model_id",
        "prompt_sha256",
        "request_sha256",
        "pricing_policy_sha256",
        "measurements_ref",
        "constraint_set_ref",
    ):
        if getattr(receipt, field_name) != getattr(reservation, field_name):
            raise ValueError(f"模型 receipt {field_name} 与 reservation 不一致。")
    actual = receipt.budget_vector
    maximum = reservation.budget_vector
    if actual.model_tokens > maximum.model_tokens:
        raise ValueError("模型 receipt token 超过调用前 reservation。")
    if actual.cost_usd_micros > maximum.cost_usd_micros:
        raise ValueError("模型 receipt cost 超过调用前 reservation。")
    if actual.artifact_bytes > maximum.artifact_bytes:
        raise ValueError("模型 provider output Artifact bytes 超过调用前 reservation。")
    if resolver.resolve(receipt.interpretation_ref.artifact_id) != (
        receipt.interpretation_ref
    ):
        raise ValueError("模型 receipt 的 interpretation_ref 身份不一致。")
    payload = resolver.read_bytes(receipt.interpretation_ref.artifact_id)
    if (
        len(payload) != receipt.interpretation_ref.size_bytes
        or sha256(payload).hexdigest() != receipt.interpretation_ref.sha256
    ):
        raise ValueError("模型 receipt 的 interpretation Artifact size/SHA 不一致。")
    if (
        receipt.interpretation_ref.kind != "visual_interpretation"
        or receipt.interpretation_ref.schema_version != "visual_interpretation_v2_1"
        or receipt.interpretation_ref.content_type != "application/json"
    ):
        raise ValueError("模型 receipt output 不是冻结 VisualInterpretation Artifact。")
    VisualInterpretationV2.model_validate_json(payload, strict=True)
    if receipt.audit_ref is not None:
        if (
            receipt.audit_ref.kind != "visual_interpretation_call_audit"
            or receipt.audit_ref.schema_version != "visual_interpretation_call_audit_v2"
            or receipt.audit_ref.content_type != "application/json"
        ):
            raise ValueError("模型 receipt audit_ref 元数据无效。")
        if resolver.resolve(receipt.audit_ref.artifact_id) != receipt.audit_ref:
            raise ValueError("模型 receipt audit_ref 身份不一致。")
    current = state_store.load_last_confirmed(reserved_state.run_id)
    if current != reserved_state:
        raise RuntimeError("模型 receipt commit 输入不是最后确认 State。")
    return state_store.commit_budget(
        reserved_state.run_id,
        reservation=maximum,
        used=actual,
        expected_budget_revision=reserved_state.budget_state.revision,
    )


__all__ = [
    "ModelCallReceiptV1",
    "ModelCallReservationV1",
    "commit_model_call_receipt_v1",
    "reserve_model_call_v1",
]
