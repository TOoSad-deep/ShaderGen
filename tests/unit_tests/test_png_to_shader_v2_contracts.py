from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from agent.app.states.png_to_shader_v2_state import (
    BudgetStateV2,
    PngToShaderV2State,
)
from shaderforge.analysis.models_v2 import (
    MeasuredRelation,
    TargetHypothesis,
    TargetMeasurementsV2,
    compute_target_hypothesis_hash,
)
from shaderforge.contracts.canonical import canonical_sha256
from shaderforge.evaluation.models_v2 import (
    CandidateAttemptRecord,
    CandidateRecordV2,
)
from shaderforge.genome.models import EffectGenome, GenomeProvenance, ParameterSpec
from shaderforge.intent.canonical import (
    assert_intent_compatible_constraints,
    compare_and_swap_constraint_set,
    compute_constraint_id,
    compute_constraint_set_hash,
    validate_constraint_set_identity,
    with_constraint_id,
)
from shaderforge.intent.models import (
    Constraint,
    ConstraintConflict,
    RegionLockConstraintValue,
    RequestConstraintSet,
    RequiredLayerConstraintValue,
    TopologyConstraintValue,
)
from shaderforge.store import ArtifactRefV2
from tests.fixtures.png_to_shader_v2_contracts import (
    artifact_ref,
    make_candidate,
    make_constraint_set,
    make_genome,
    make_state,
    make_target_measurements,
)

ROOT = Path(__file__).resolve().parents[2]
STATE_V4_GOLDEN = json.loads(
    (ROOT / "tests/fixtures/png_to_shader_v2/golden_hashes_v4.json").read_text(
        encoding="utf-8"
    )
)


def test_v2_schema_and_identity_golden_fixture_is_frozen() -> None:
    target = make_target_measurements()
    constraint_set = make_constraint_set()
    candidate = make_candidate()

    assert (
        target.target_hypotheses[0].hypothesis_hash
        == STATE_V4_GOLDEN["target_hypothesis_hash"]
    )
    assert [
        item.constraint_id for item in constraint_set.constraints
    ] == STATE_V4_GOLDEN["constraint_ids"]
    assert constraint_set.constraint_set_hash == STATE_V4_GOLDEN["constraint_set_hash"]
    assert candidate.record_hash == STATE_V4_GOLDEN["candidate_record_hash"]
    validate_constraint_set_identity(constraint_set)


def test_v2_pydantic_json_schema_hashes_are_compatibility_golden() -> None:
    models = (
        ArtifactRefV2,
        BudgetStateV2,
        CandidateAttemptRecord,
        Constraint,
        ParameterSpec,
        TargetHypothesis,
        TargetMeasurementsV2,
        RequestConstraintSet,
        EffectGenome,
        CandidateRecordV2,
        PngToShaderV2State,
    )

    assert {
        model.__name__: canonical_sha256(TypeAdapter(model).json_schema())
        for model in models
    } == STATE_V4_GOLDEN["schema_sha256"]


def test_v2_1_measurements_break_rejects_legacy_instance_without_geometry() -> None:
    raw = json.loads(make_target_measurements().model_dump_json())
    raw["schema_version"] = "target_measurements_v2_1"
    raw["target_hypotheses"][0]["schema_version"] = "target_hypothesis_v1"
    raw["target_hypotheses"][0].pop("instance_geometries")

    with pytest.raises(ValidationError, match="schema_version|instance_geometries"):
        TargetMeasurementsV2.model_validate_json(json.dumps(raw), strict=True)


def test_hypothesis_hash_binds_each_instance_geometry_field() -> None:
    measurements = make_target_measurements()
    hypothesis = measurements.target_hypotheses[0]
    geometry = hypothesis.instance_geometries[0]
    changed = hypothesis.model_copy(
        update={
            "instance_geometries": (
                geometry.model_copy(update={"center_uv": (0.51, 0.5)}),
            )
        }
    )

    assert compute_target_hypothesis_hash(measurements.target_sha256, changed) != (
        hypothesis.hypothesis_hash
    )


def test_hypothesis_hash_uses_content_not_artifact_id_or_evidence_location() -> None:
    target = make_target_measurements()
    hypothesis = target.target_hypotheses[0]
    renamed_mask = hypothesis.subject_mask_ref.__class__(
        artifact_id="artifact_renamed",
        sha256=hypothesis.subject_mask_ref.sha256,
        kind=hypothesis.subject_mask_ref.kind,
        schema_version=hypothesis.subject_mask_ref.schema_version,
        content_type=hypothesis.subject_mask_ref.content_type,
        size_bytes=hypothesis.subject_mask_ref.size_bytes,
    )
    renamed = hypothesis.model_copy(
        update={"subject_mask_ref": renamed_mask, "evidence_refs": ()}
    )
    changed_content = hypothesis.model_copy(
        update={
            "subject_mask_ref": hypothesis.subject_mask_ref.__class__(
                artifact_id="artifact_renamed",
                sha256="e" * 64,
                kind=hypothesis.subject_mask_ref.kind,
                schema_version=hypothesis.subject_mask_ref.schema_version,
                content_type=hypothesis.subject_mask_ref.content_type,
                size_bytes=hypothesis.subject_mask_ref.size_bytes,
            )
        }
    )

    assert compute_target_hypothesis_hash(target.target_sha256, renamed) == (
        hypothesis.hypothesis_hash
    )
    assert (
        compute_target_hypothesis_hash(target.target_sha256, changed_content)
        != hypothesis.hypothesis_hash
    )


def test_hypothesis_hash_binds_instance_index_and_relation_order_is_canonical() -> None:
    target = make_target_measurements()
    hypothesis = target.target_hypotheses[0]
    second_mask = artifact_ref(
        "mask-second",
        "d",
        kind="subject_mask",
        schema_version="subject_mask_v1",
        content_type="image/png",
        size_bytes=16,
    )
    first_relation = MeasuredRelation(
        relation_id="relation-0-1",
        kind="disjoint",
        subject_ref="instance_0000",
        object_ref="instance_0001",
        confidence=0.8,
    )
    multi = hypothesis.model_copy(
        update={
            "instance_mask_refs": (hypothesis.instance_mask_refs[0], second_mask),
            "component_count": 2,
            "instance_count": 2,
            "relations": (first_relation,),
        }
    )
    reordered_masks = multi.model_copy(
        update={"instance_mask_refs": tuple(reversed(multi.instance_mask_refs))}
    )

    assert compute_target_hypothesis_hash(
        target.target_sha256,
        multi,
    ) != compute_target_hypothesis_hash(target.target_sha256, reordered_masks)

    second_relation = MeasuredRelation(
        relation_id="relation-subject-0",
        kind="contains",
        subject_ref="subject",
        object_ref="instance_0000",
        confidence=0.9,
    )
    forward = multi.model_copy(update={"relations": (first_relation, second_relation)})
    reverse = multi.model_copy(update={"relations": (second_relation, first_relation)})
    assert compute_target_hypothesis_hash(
        target.target_sha256,
        forward,
    ) == compute_target_hypothesis_hash(target.target_sha256, reverse)


def test_constraint_payload_is_sealed_and_kind_must_match() -> None:
    raw = make_constraint_set().constraints[0].model_dump(mode="json")
    raw["value"]["unknown"] = True
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Constraint.model_validate_json(json.dumps(raw))


def test_required_layer_taxonomy_includes_glow() -> None:
    assert RequiredLayerConstraintValue(layer="glow").layer == "glow"

    raw = make_constraint_set().constraints[0].model_dump(mode="json")
    raw["kind"] = "topology"
    with pytest.raises(ValidationError, match="kind"):
        Constraint.model_validate_json(json.dumps(raw))


def test_constraint_hash_excludes_revisions_but_tracks_semantics() -> None:
    current = make_constraint_set()
    revision_only = current.model_copy(update={"request_revision": 999})
    source_revision_only = current.constraints[1].model_copy(
        update={"source_revision": 999}
    )

    assert compute_constraint_set_hash(revision_only) == current.constraint_set_hash
    assert (
        compute_constraint_id(source_revision_only)
        == current.constraints[1].constraint_id
    )

    changed = current.constraints[1].model_copy(
        update={"value": RequiredLayerConstraintValue(layer="rim")}
    )
    assert compute_constraint_id(changed) != current.constraints[1].constraint_id


def test_region_lock_identity_uses_mask_content_not_run_local_artifact_id() -> None:
    first_mask = artifact_ref(
        "mask-run-a",
        "e",
        kind="region_mask",
        schema_version="region_mask_v1",
        content_type="image/png",
    )
    second_mask = replace(first_mask, artifact_id="artifact_mask-run-b")

    def make_region_set(mask_ref: ArtifactRefV2) -> RequestConstraintSet:
        constraint = with_constraint_id(
            Constraint(
                constraint_id="pending",
                kind="region_lock",
                strength="hard",
                scope="region",
                scope_ref="subject",
                value=RegionLockConstraintValue(
                    region_id="subject",
                    mask_ref=mask_ref,
                ),
                source="user",
                source_revision=0,
                confidence=1.0,
                verification_status="verified",
            )
        )
        return RequestConstraintSet(
            constraint_set_id="region-locks",
            constraint_set_hash="0" * 64,
            target_sha256="a" * 64,
            request_revision=0,
            constraints=(constraint,),
        )

    first = make_region_set(first_mask)
    second = make_region_set(second_mask)

    assert first.constraints[0].constraint_id == second.constraints[0].constraint_id
    assert compute_constraint_set_hash(first) == compute_constraint_set_hash(second)


@pytest.mark.parametrize("confidence", (0.01, 1.0))
def test_measurement_hard_constraint_requires_verified_independent_of_confidence(
    confidence: float,
) -> None:
    current = make_constraint_set()
    measurement = Constraint(
        constraint_id="pending",
        kind="topology",
        strength="hard",
        scope="object",
        scope_ref="subject",
        value=TopologyConstraintValue(topology="solid"),
        source="measurement",
        source_revision=0,
        confidence=confidence,
        verification_status="unverified",
    )
    unverified = compare_and_swap_constraint_set(
        current,
        expected_revision=current.request_revision,
        constraints=(*current.constraints, measurement),
    )
    with pytest.raises(ValueError, match="measurement hard constraint.*verified"):
        assert_intent_compatible_constraints(unverified)

    verified_measurement = measurement.model_copy(
        update={"verification_status": "verified"}
    )
    verified = compare_and_swap_constraint_set(
        current,
        expected_revision=current.request_revision,
        constraints=(*current.constraints, verified_measurement),
    )
    assert_intent_compatible_constraints(verified)


def test_constraint_revision_cas_and_unresolved_conflict_fail_closed() -> None:
    current = make_constraint_set()
    with pytest.raises(RuntimeError, match="CAS"):
        compare_and_swap_constraint_set(
            current,
            expected_revision=current.request_revision - 1,
            constraints=current.constraints,
        )

    unresolved = ConstraintConflict(
        conflict_id="conflict-1",
        constraint_ids=tuple(item.constraint_id for item in current.constraints),
        status="unresolved",
        selected_constraint_id=None,
        resolution_policy="manual_required",
        reason="用户层约束与运行契约尚未完成裁决",
    )
    updated = compare_and_swap_constraint_set(
        current,
        expected_revision=current.request_revision,
        constraints=current.constraints,
        conflicts=(unresolved,),
    )

    assert updated.request_revision == current.request_revision + 1
    with pytest.raises(ValueError, match="unresolved"):
        assert_intent_compatible_constraints(updated)


def test_candidate_record_is_frozen_and_tamper_evident() -> None:
    candidate = make_candidate()
    with pytest.raises(ValidationError, match="frozen"):
        candidate.candidate_id = "changed"  # type: ignore[misc]

    raw = candidate.model_dump(mode="json")
    raw["candidate_id"] = "changed"
    with pytest.raises(ValidationError, match="record_hash"):
        CandidateRecordV2.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("field_name", ("render_refs", "evaluation_refs"))
def test_candidate_requires_render_and_evaluation_evidence(field_name: str) -> None:
    raw = make_candidate().model_dump(mode="json")
    raw[field_name] = []
    with pytest.raises(ValidationError, match=field_name):
        CandidateRecordV2.model_validate_json(json.dumps(raw), strict=True)


def test_attempt_requires_failure_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence_refs"):
        CandidateAttemptRecord(
            attempt_id="attempt-1",
            run_id="run-1",
            target_hypothesis_hash="1" * 64,
            semantic_genome_hash="2" * 64,
            status="compile_failed",
            error_code="compile_error",
            evidence_refs=(),
        )


def test_target_hypothesis_topology_instance_and_unique_hash_invariants() -> None:
    target = make_target_measurements()
    hypothesis_raw = target.target_hypotheses[0].model_dump(mode="json")
    hypothesis_raw["instance_count"] = 2
    with pytest.raises(ValidationError, match="instance_count"):
        TargetHypothesis.model_validate_json(json.dumps(hypothesis_raw), strict=True)

    hypothesis_raw = target.target_hypotheses[0].model_dump(mode="json")
    hypothesis_raw.update(fill_topology="ring", hole_count=0)
    with pytest.raises(ValidationError, match="ring/hollow"):
        TargetHypothesis.model_validate_json(json.dumps(hypothesis_raw), strict=True)

    hypothesis_raw = target.target_hypotheses[0].model_dump(mode="json")
    hypothesis_raw.update(fill_topology="solid", hole_count=1)
    with pytest.raises(ValidationError, match="solid"):
        TargetHypothesis.model_validate_json(json.dumps(hypothesis_raw), strict=True)

    target_raw = target.model_dump(mode="json")
    duplicate = dict(target_raw["target_hypotheses"][0])
    duplicate["hypothesis_id"] = "hypothesis-duplicate-content"
    target_raw["target_hypotheses"].append(duplicate)
    with pytest.raises(ValidationError, match="hypothesis_hash 不得重复"):
        TargetMeasurementsV2.model_validate_json(json.dumps(target_raw), strict=True)


@pytest.mark.parametrize(
    ("field_name", "bad_value"),
    (
        ("value", 1),
        ("min_value", 1),
        ("max_value", float("nan")),
    ),
)
def test_float_parameter_rejects_non_float_or_non_finite_values(
    field_name: str,
    bad_value: object,
) -> None:
    parameter = make_genome().parameters[1].model_dump(mode="python")
    parameter[field_name] = bad_value
    with pytest.raises(ValidationError):
        ParameterSpec.model_validate(parameter)


def test_parameter_vector_is_deeply_immutable_and_enforces_shape_and_range() -> None:
    parameter = make_genome().parameters[0]
    assert isinstance(parameter.value, tuple)
    with pytest.raises(TypeError):
        parameter.value[0] = 0.2  # type: ignore[index]

    for updates in (
        {"value": (0.5,)},
        {"value": (0, 1)},
        {"value": (1.1, 0.5)},
        {"min_value": (0.8, 0.0), "max_value": (0.2, 1.0)},
    ):
        raw = parameter.model_dump(mode="python")
        raw.update(updates)
        with pytest.raises(ValidationError):
            ParameterSpec.model_validate(raw)


def test_parameter_bool_forbids_range_and_int_forbids_bool() -> None:
    shared = {
        "path": "feature.enabled",
        "optimizable": False,
        "block": "feature",
        "affected_regions": ("subject",),
        "semantic_role": "feature_switch",
        "unit": "boolean",
        "coordinate_space": None,
        "color_space": None,
        "cyclic": False,
        "quantization": None,
    }
    with pytest.raises(ValidationError, match="bool 参数不得设置范围"):
        ParameterSpec(
            **shared,
            dtype="bool",
            value=True,
            min_value=False,
            max_value=None,
        )
    with pytest.raises(ValidationError, match="int 参数 value 必须是整数"):
        ParameterSpec(
            **shared,
            dtype="int",
            value=True,
            min_value=None,
            max_value=None,
        )


def test_genome_provenance_target_hash_is_sha256_hex() -> None:
    raw = make_genome().provenance.model_dump(mode="json")
    raw["target_hypothesis_hash"] = "hypothesis-hash"
    with pytest.raises(ValidationError, match="target_hypothesis_hash"):
        GenomeProvenance.model_validate_json(json.dumps(raw), strict=True)


def test_models_reject_unknown_schema_fields() -> None:
    raw = make_state().model_dump(mode="json")
    raw["large_inline_genome"] = {}

    with pytest.raises(ValidationError, match="extra_forbidden"):
        PngToShaderV2State.model_validate_json(json.dumps(raw))
