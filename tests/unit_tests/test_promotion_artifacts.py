from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from shaderforge.evaluation import (
    PromotionOperationV1,
    PromotionReceiptV1,
    PromotionSinkResultV1,
    compute_promotion_operation_id,
    load_promotion_operation,
    load_promotion_receipt,
    materialize_promotion_operation,
    materialize_promotion_receipt,
)
from shaderforge.store import LocalArtifactCatalog, LocalArtifactStore


def _put(catalog, *, kind: str):
    return catalog.put(
        run_id="promotion-artifact-run",
        kind=kind,
        schema_version=f"{kind}_v1",
        content_type="application/octet-stream",
        data=kind.encode(),
    )


def test_promotion_operation_and_receipt_round_trip_and_tamper(tmp_path) -> None:
    catalog = LocalArtifactCatalog(
        LocalArtifactStore(tmp_path).start_run(
            "promotion-artifact-project", "promotion-artifact-run"
        ),
        run_id="promotion-artifact-run",
    )
    candidate_ref = _put(catalog, kind="candidate_record")
    structure_ref = _put(catalog, kind="runtime_structure_envelope")
    fields = {
        "schema_version": "promotion_operation_v1",
        "hash_version": "promotion_operation_hash_v1",
        "run_id": "promotion-artifact-run",
        "candidate_ref": candidate_ref,
        "candidate_id": "candidate-v2-test",
        "candidate_glsl_sha256": "a" * 64,
        "candidate_render_sha256": "b" * 64,
        "candidate_provenance_ref": "provenance-v2-test",
        "structure_envelope_ref": structure_ref,
        "admission_policy_version": "measurement_seed_admission_v1",
    }
    operation = PromotionOperationV1(
        **fields,
        operation_id=compute_promotion_operation_id(fields),
    )
    operation_ref = materialize_promotion_operation(
        catalog=catalog, operation=operation
    )
    assert (
        load_promotion_operation(
            operation_ref, resolver=catalog, run_id=operation.run_id
        )
        == operation
    )

    receipt = PromotionReceiptV1(
        run_id=operation.run_id,
        operation_ref=operation_ref,
        operation_id=operation.operation_id,
        external_receipt_id="external-receipt-v1",
        external_receipt_sha256="c" * 64,
        sink_reason_code="completed",
    )
    receipt_ref = materialize_promotion_receipt(catalog=catalog, receipt=receipt)
    assert (
        load_promotion_receipt(
            receipt_ref,
            resolver=catalog,
            run_id=operation.run_id,
            operation_ref=operation_ref,
        )
        == receipt
    )

    with pytest.raises(ValueError, match="身份不一致"):
        load_promotion_operation(
            replace(operation_ref, sha256="f" * 64),
            resolver=catalog,
            run_id=operation.run_id,
        )


def test_sink_result_cannot_claim_completion_without_receipt() -> None:
    with pytest.raises(ValidationError, match="外部 receipt identity"):
        PromotionSinkResultV1(
            operation_id="a" * 64,
            status="completed",
            reason_code="invalid_missing_receipt",
        )
