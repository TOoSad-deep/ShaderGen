"""V2 production promotion 的可恢复 outbox 与 typed receipt。"""  # noqa: D415
# ruff: noqa: D103, D415

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any, Literal

from pydantic import model_validator

from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.canonical import canonical_sha256
from shaderforge.store import ArtifactCatalog, ArtifactRefV2, ArtifactResolver

PROMOTION_OPERATION_ARTIFACT_KIND = "promotion_operation"
PROMOTION_OPERATION_SCHEMA_VERSION = "promotion_operation_v1"
PROMOTION_RECEIPT_ARTIFACT_KIND = "promotion_receipt"
PROMOTION_RECEIPT_SCHEMA_VERSION = "promotion_receipt_v1"
_JSON_CONTENT_TYPE = "application/json"


class PromotionOperationV1(FrozenModel):
    """在调用外部 sink 前必须持久化的稳定 promotion intent。"""

    schema_version: Literal["promotion_operation_v1"] = "promotion_operation_v1"
    hash_version: Literal["promotion_operation_hash_v1"] = "promotion_operation_hash_v1"
    run_id: NonEmptyString
    candidate_ref: ArtifactRefV2
    candidate_id: NonEmptyString
    candidate_glsl_sha256: Sha256Hex
    candidate_render_sha256: Sha256Hex
    candidate_provenance_ref: NonEmptyString
    structure_envelope_ref: ArtifactRefV2
    admission_policy_version: NonEmptyString
    operation_id: Sha256Hex

    @model_validator(mode="after")
    def _validate_operation_id(self) -> PromotionOperationV1:
        if self.operation_id != compute_promotion_operation_id(self):
            raise ValueError("Promotion operation_id 与冻结字段不一致。")
        return self


class PromotionSinkResultV1(FrozenModel):
    """sink execute/recover 的保守结果；unknown 永远不得触发重放。"""

    schema_version: Literal["promotion_sink_result_v1"] = "promotion_sink_result_v1"
    operation_id: Sha256Hex
    status: Literal["completed", "not_executed", "unknown"]
    external_receipt_id: NonEmptyString | None = None
    external_receipt_sha256: Sha256Hex | None = None
    reason_code: NonEmptyString

    @model_validator(mode="after")
    def _validate_completion_receipt(self) -> PromotionSinkResultV1:
        has_receipt = (
            self.external_receipt_id is not None
            and self.external_receipt_sha256 is not None
        )
        if self.status == "completed" and not has_receipt:
            raise ValueError("completed sink result 必须包含外部 receipt identity。")
        if self.status != "completed" and (
            self.external_receipt_id is not None
            or self.external_receipt_sha256 is not None
        ):
            raise ValueError("未完成 sink result 不得伪造外部 receipt identity。")
        return self


class PromotionReceiptV1(FrozenModel):
    """由 recoverable sink completion 生成的本地 commit receipt。"""

    schema_version: Literal["promotion_receipt_v1"] = "promotion_receipt_v1"
    run_id: NonEmptyString
    operation_ref: ArtifactRefV2
    operation_id: Sha256Hex
    external_receipt_id: NonEmptyString
    external_receipt_sha256: Sha256Hex
    sink_reason_code: NonEmptyString


def compute_promotion_operation_id(
    value: PromotionOperationV1 | dict[str, Any],
) -> str:
    payload = (
        value.model_dump(mode="python", exclude={"operation_id"})
        if isinstance(value, PromotionOperationV1)
        else {key: item for key, item in value.items() if key != "operation_id"}
    )
    return canonical_sha256(payload)


def materialize_promotion_operation(
    *, catalog: ArtifactCatalog, operation: PromotionOperationV1
) -> ArtifactRefV2:
    return catalog.put(
        run_id=operation.run_id,
        kind=PROMOTION_OPERATION_ARTIFACT_KIND,
        schema_version=PROMOTION_OPERATION_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
        data=operation.model_dump_json().encode("utf-8"),
    )


def load_promotion_operation(
    ref: ArtifactRefV2, *, resolver: ArtifactResolver, run_id: str
) -> PromotionOperationV1:
    value = _load_exact(
        ref,
        resolver=resolver,
        model_type=PromotionOperationV1,
        kind=PROMOTION_OPERATION_ARTIFACT_KIND,
        schema_version=PROMOTION_OPERATION_SCHEMA_VERSION,
    )
    assert isinstance(value, PromotionOperationV1)
    if value.run_id != run_id:
        raise ValueError("Promotion operation 不属于当前 run。")
    return value


def materialize_promotion_receipt(
    *, catalog: ArtifactCatalog, receipt: PromotionReceiptV1
) -> ArtifactRefV2:
    operation = load_promotion_operation(
        receipt.operation_ref,
        resolver=catalog,
        run_id=receipt.run_id,
    )
    if receipt.operation_id != operation.operation_id:
        raise ValueError("Promotion receipt 与 operation identity 不一致。")
    return catalog.put(
        run_id=receipt.run_id,
        kind=PROMOTION_RECEIPT_ARTIFACT_KIND,
        schema_version=PROMOTION_RECEIPT_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
        data=receipt.model_dump_json().encode("utf-8"),
    )


def load_promotion_receipt(
    ref: ArtifactRefV2,
    *,
    resolver: ArtifactResolver,
    run_id: str,
    operation_ref: ArtifactRefV2,
) -> PromotionReceiptV1:
    value = _load_exact(
        ref,
        resolver=resolver,
        model_type=PromotionReceiptV1,
        kind=PROMOTION_RECEIPT_ARTIFACT_KIND,
        schema_version=PROMOTION_RECEIPT_SCHEMA_VERSION,
    )
    assert isinstance(value, PromotionReceiptV1)
    operation = load_promotion_operation(
        operation_ref, resolver=resolver, run_id=run_id
    )
    if (
        value.run_id != run_id
        or value.operation_ref != operation_ref
        or value.operation_id != operation.operation_id
    ):
        raise ValueError("Promotion receipt 与 run/operation identity 不一致。")
    return value


def _load_exact(
    ref: ArtifactRefV2,
    *,
    resolver: ArtifactResolver,
    model_type: type[PromotionOperationV1] | type[PromotionReceiptV1],
    kind: str,
    schema_version: str,
) -> PromotionOperationV1 | PromotionReceiptV1:
    if (
        ref.kind != kind
        or ref.schema_version != schema_version
        or ref.content_type != _JSON_CONTENT_TYPE
    ):
        raise ValueError("Promotion ArtifactRef 元数据不符合冻结契约。")
    if resolver.resolve(ref.artifact_id) != ref:
        raise ValueError("Promotion Artifact resolver 身份不一致。")
    payload = resolver.read_bytes(ref.artifact_id)
    if len(payload) != ref.size_bytes or sha256(payload).hexdigest() != ref.sha256:
        raise ValueError("Promotion Artifact bytes 与 ref 不一致。")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"Promotion Artifact 包含重复 JSON key：{key}。")
            value[key] = item
        return value

    json.loads(
        payload,
        object_pairs_hook=reject_duplicates,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"Promotion Artifact 包含非法常量：{value}。")
        ),
    )
    return model_type.model_validate_json(payload, strict=True)


__all__ = [
    "PROMOTION_OPERATION_ARTIFACT_KIND",
    "PROMOTION_OPERATION_SCHEMA_VERSION",
    "PROMOTION_RECEIPT_ARTIFACT_KIND",
    "PROMOTION_RECEIPT_SCHEMA_VERSION",
    "PromotionOperationV1",
    "PromotionReceiptV1",
    "PromotionSinkResultV1",
    "compute_promotion_operation_id",
    "load_promotion_operation",
    "load_promotion_receipt",
    "materialize_promotion_operation",
    "materialize_promotion_receipt",
]
