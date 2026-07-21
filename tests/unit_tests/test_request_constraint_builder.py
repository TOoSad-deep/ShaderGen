from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

import pytest

from shaderforge.intent.canonical import (
    compute_constraint_set_hash,
    validate_constraint_set_identity,
)
from shaderforge.intent.constraints_builder import (
    build_request_constraint_set,
    merge_request_constraint_set,
    validate_request_constraint_set_policy,
)
from shaderforge.intent.models import (
    Constraint,
    ConstraintSource,
    ContractConstraintValue,
    RequestConstraintSet,
    RequiredLayerConstraintValue,
    TopologyConstraintValue,
)
from shaderforge.store import ArtifactRefV2

TARGET_SHA256 = "a" * 64


def _evidence(digit: str = "e") -> ArtifactRefV2:
    return ArtifactRefV2(
        artifact_id=f"artifact-evidence-{digit}",
        sha256=digit * 64,
        kind="constraint_evidence",
        schema_version="constraint_evidence_v1",
        content_type="application/json",
        size_bytes=12,
    )


def _contract() -> Constraint:
    return Constraint(
        constraint_id="caller-id-is-normalized",
        kind="contract",
        strength="hard",
        scope="global",
        value=ContractConstraintValue(contract_id="webgl1-static-v1"),
        source="render_contract",
        source_revision=0,
        confidence=1.0,
        verification_status="verified",
    )


def _topology(
    topology: Literal["solid", "hollow", "ring", "open"],
    *,
    source: ConstraintSource,
    strength: Literal["hard", "soft"] = "soft",
    verification_status: Literal[
        "verified", "inferred", "unverified", "rejected"
    ] = "inferred",
    evidence_refs: tuple[ArtifactRefV2, ...] = (),
    source_revision: int = 0,
) -> Constraint:
    return Constraint(
        constraint_id="caller-id-is-normalized",
        kind="topology",
        strength=strength,
        scope="object",
        scope_ref="subject",
        value=TopologyConstraintValue(topology=topology),
        source=source,
        source_revision=source_revision,
        confidence=0.9,
        verification_status=verification_status,
        evidence_refs=evidence_refs,
    )


def _build(constraints: Iterable[Constraint]) -> RequestConstraintSet:
    return build_request_constraint_set(
        constraint_set_id="constraints-main",
        target_sha256=TARGET_SHA256,
        request_revision=3,
        constraints=constraints,
    )


def test_builder_normalizes_ids_deduplicates_and_is_order_deterministic() -> None:
    user = _topology("ring", source="user", source_revision=1)
    duplicate = user.model_copy(update={"source_revision": 7})
    forward = _build((_contract(), user, duplicate))
    reverse = _build((duplicate, user, _contract()))

    assert len(forward.constraints) == 2
    assert forward.constraint_set_hash == reverse.constraint_set_hash
    assert forward.conflicts == reverse.conflicts == ()
    assert tuple(item.constraint_id for item in forward.constraints) == tuple(
        sorted(item.constraint_id for item in forward.constraints)
    )
    normalized_user = next(item for item in forward.constraints if item.kind == "topology")
    assert normalized_user.constraint_id != "caller-id-is-normalized"
    assert normalized_user.source_revision == 7
    validate_constraint_set_identity(forward)


@pytest.mark.parametrize(
    ("higher", "lower"),
    (
        ("user", "project_memory"),
        ("project_memory", "measurement"),
        ("measurement", "model"),
    ),
)
def test_source_priority_resolves_conflicts_without_dropping_constraints(
    higher: ConstraintSource,
    lower: ConstraintSource,
) -> None:
    high = _topology(
        "ring",
        source=higher,
        verification_status="verified" if higher == "measurement" else "inferred",
        evidence_refs=(_evidence(),) if higher == "measurement" else (),
    )
    low = _topology(
        "solid",
        source=lower,
        verification_status="verified" if lower == "measurement" else "inferred",
        evidence_refs=(_evidence(),) if lower == "measurement" else (),
    )

    result = _build((_contract(), low, high))

    assert len(result.constraints) == 3
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.status == "resolved"
    selected = next(
        item for item in result.constraints if item.constraint_id == conflict.selected_constraint_id
    )
    assert selected.source == higher
    topology_ids = {
        item.constraint_id for item in result.constraints if item.kind == "topology"
    }
    assert set(conflict.constraint_ids) == topology_ids


def test_same_priority_conflict_is_unresolved_and_stable() -> None:
    solid = _topology("solid", source="user")
    ring = _topology("ring", source="user")
    first = _build((_contract(), solid, ring))
    second = _build((ring, _contract(), solid))

    assert first.constraint_set_hash == second.constraint_set_hash
    assert first.conflicts == second.conflicts
    assert first.conflicts[0].status == "unresolved"
    assert first.conflicts[0].selected_constraint_id is None


def test_policy_validator_rejects_rehashed_wrong_conflict_winner() -> None:
    high = _topology("ring", source="user")
    low = _topology("solid", source="model")
    valid = _build((_contract(), high, low))
    conflict = valid.conflicts[0]
    wrong_id = next(
        item.constraint_id
        for item in valid.constraints
        if item.kind == "topology" and item.source == "model"
    )
    wrong_conflict = conflict.model_copy(
        update={"selected_constraint_id": wrong_id}
    )
    draft = valid.model_copy(
        update={"conflicts": (wrong_conflict,), "constraint_set_hash": "0" * 64}
    )
    tampered = draft.model_copy(
        update={"constraint_set_hash": compute_constraint_set_hash(draft)}
    )

    with pytest.raises(ValueError, match="冻结合并策略"):
        validate_request_constraint_set_policy(tampered)


def test_soft_preference_never_eliminates_verified_hard_constraint() -> None:
    hard = _topology(
        "solid",
        source="measurement",
        strength="hard",
        verification_status="verified",
        evidence_refs=(_evidence(),),
    )
    soft = _topology("ring", source="user", strength="soft")

    result = _build((_contract(), soft, hard))

    assert result.conflicts == ()
    assert {item.strength for item in result.constraints if item.kind == "topology"} == {
        "hard",
        "soft",
    }


def test_unverified_measurement_does_not_outrank_model_inference() -> None:
    measurement = _topology(
        "ring",
        source="measurement",
        verification_status="unverified",
    )
    model = _topology("solid", source="model")

    result = _build((_contract(), measurement, model))

    conflict = result.conflicts[0]
    assert conflict.status == "resolved"
    selected = next(
        item for item in result.constraints if item.constraint_id == conflict.selected_constraint_id
    )
    assert selected.source == "model"


def test_rejected_soft_constraint_is_audit_only_and_never_wins_conflict() -> None:
    rejected = _topology(
        "ring",
        source="user",
        verification_status="rejected",
    )
    active = _topology("solid", source="model")

    result = _build((_contract(), rejected, active))

    assert result.conflicts == ()
    assert {item.verification_status for item in result.constraints} == {
        "verified",
        "rejected",
        "inferred",
    }


def test_required_layers_are_additive_and_do_not_conflict() -> None:
    layer_names: tuple[
        Literal["base_fill", "rim", "highlight"], ...
    ] = ("base_fill", "rim", "highlight")
    layers = tuple(
        Constraint(
            constraint_id="pending",
            kind="required_layer",
            strength="hard",
            scope="object",
            scope_ref="subject",
            value=RequiredLayerConstraintValue(layer=layer),
            source="user",
            source_revision=0,
            confidence=1.0,
            verification_status="verified",
        )
        for layer in layer_names
    )

    result = _build((_contract(), *layers))

    assert result.conflicts == ()
    assert {
        item.value.layer
        for item in result.constraints
        if isinstance(item.value, RequiredLayerConstraintValue)
    } == {
        "base_fill",
        "rim",
        "highlight",
    }


@pytest.mark.parametrize(
    "constraint",
    (
        _topology("solid", source="deployment", strength="soft"),
        _topology("solid", source="model", strength="hard"),
        _topology(
            "solid",
            source="measurement",
            strength="hard",
            verification_status="unverified",
            evidence_refs=(_evidence(),),
        ),
        _topology(
            "solid",
            source="measurement",
            strength="hard",
            verification_status="verified",
        ),
    ),
)
def test_source_policy_rejects_untrusted_hard_constraints(
    constraint: Constraint,
) -> None:
    with pytest.raises(ValueError):
        _build((_contract(), constraint))


def test_verified_measurement_hard_with_evidence_is_allowed() -> None:
    measurement = _topology(
        "ring",
        source="measurement",
        strength="hard",
        verification_status="verified",
        evidence_refs=(_evidence(),),
    )

    result = _build((_contract(), measurement))

    assert next(item for item in result.constraints if item.kind == "topology").source == (
        "measurement"
    )


@pytest.mark.parametrize(
    "constraints",
    (
        (),
        (
            _contract(),
            _contract().model_copy(
                update={
                    "constraint_id": "another",
                    "value": ContractConstraintValue(contract_id="webgl1-other-v1"),
                }
            ),
        ),
        (
            _contract().model_copy(
                update={"strength": "soft", "verification_status": "inferred"}
            ),
        ),
    ),
)
def test_builder_requires_exactly_one_verified_hard_render_contract(
    constraints: tuple[Constraint, ...],
) -> None:
    with pytest.raises(ValueError, match="contract"):
        _build(constraints)


def test_merge_uses_revision_cas_and_preserves_model_identity() -> None:
    current = _build((_contract(), _topology("solid", source="user")))

    with pytest.raises(RuntimeError, match="CAS"):
        merge_request_constraint_set(
            current,
            expected_revision=current.request_revision - 1,
            constraints=current.constraints,
        )

    updated = merge_request_constraint_set(
        current,
        expected_revision=current.request_revision,
        constraints=(_contract(), _topology("ring", source="user")),
    )

    assert updated.constraint_set_id == current.constraint_set_id
    assert updated.target_sha256 == current.target_sha256
    assert updated.request_revision == current.request_revision + 1
    assert updated.constraint_set_hash != current.constraint_set_hash
    validate_constraint_set_identity(updated)
