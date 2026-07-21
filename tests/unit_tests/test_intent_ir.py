from __future__ import annotations

import json
from dataclasses import replace

import pytest

from shaderforge.analysis import TargetMeasurementsV2, compute_target_hypothesis_hash
from shaderforge.contracts.taxonomy import REQUIRED_LAYER_ORDER
from shaderforge.intent.builder import (
    build_intent_variants,
    compute_intent_id,
    compute_intent_input_hash,
)
from shaderforge.intent.canonical import compare_and_swap_constraint_set
from shaderforge.intent.ir import (
    IntentBuildContext,
    LayerHypothesis,
    PrimitiveCandidate,
    RequiredLayerAssessment,
    StrategyHypothesis,
    VisualInterpretationV2,
)
from shaderforge.intent.models import Constraint, TopologyConstraintValue
from shaderforge.intent.parsing import (
    VisualInterpretationParseError,
    parse_visual_interpretation_v2,
)
from shaderforge.intent.validation import validate_intent_ir
from tests.fixtures.png_to_shader_v2_contracts import (
    artifact_ref,
    make_constraint_set,
    make_target_measurements,
)


def _interpretation() -> VisualInterpretationV2:
    evidence = artifact_ref("interpretation", "c", kind="visual_interpretation")
    return VisualInterpretationV2(
        summary="主体由基础填色和弧形高光组成。",
        layer_hypotheses=(
            LayerHypothesis(
                layer_id="layer-base",
                role="base_fill",
                order=0,
                confidence=0.95,
                region_description="主体内部",
                primitive_candidates=("solid_fill",),
                evidence_refs=(evidence,),
            ),
            LayerHypothesis(
                layer_id="layer-highlight",
                role="highlight",
                order=1,
                confidence=0.75,
                region_description="主体上缘",
                primitive_candidates=("arc_highlight",),
                evidence_refs=(evidence,),
            ),
        ),
        required_layer_assessments=tuple(
            RequiredLayerAssessment(
                layer=layer,
                status=(
                    "required"
                    if layer in {"base_fill", "highlight"}
                    else "not_required"
                ),
                confidence=0.9,
                rationale="测试闭集判断。",
                evidence_refs=(evidence,),
            )
            for layer in REQUIRED_LAYER_ORDER
        ),
        primitive_candidates=(
            PrimitiveCandidate(
                candidate_id="primitive-base",
                primitive_id="solid_fill",
                layer_id="layer-base",
                confidence=0.95,
                evidence_refs=(evidence,),
            ),
            PrimitiveCandidate(
                candidate_id="primitive-highlight",
                primitive_id="arc_highlight",
                layer_id="layer-highlight",
                confidence=0.75,
                evidence_refs=(evidence,),
            ),
        ),
        strategy_hypotheses=(
            StrategyHypothesis(
                strategy_id="strategy-layered",
                template_ids=("solid-circle-highlight",),
                required_layer_ids=("layer-base", "layer-highlight"),
                complexity="medium",
                confidence=0.8,
                evidence_refs=(evidence,),
            ),
        ),
        evidence_refs=(evidence,),
    )


def _context() -> IntentBuildContext:
    return IntentBuildContext(
        contract_id="webgl1_static_no_texture_v1",
        primitive_catalog_version="png_to_shader_expected_primitives_v1",
        primitive_catalog_sha256="d" * 64,
        template_catalog_version="png_to_shader_expected_primitives_v1",
        template_catalog_sha256="e" * 64,
        allowed_primitive_ids=("arc_highlight", "solid_fill"),
        allowed_template_ids=("solid-circle-highlight",),
        allowed_interpretation_evidence_refs=(
            artifact_ref("interpretation", "c", kind="visual_interpretation"),
        ),
    )


def test_visual_interpretation_parser_accepts_one_strict_json_object() -> None:
    interpretation = _interpretation()

    parsed = parse_visual_interpretation_v2(
        f"```json\n{interpretation.model_dump_json()}\n```"
    )

    assert parsed == interpretation


def test_visual_interpretation_parser_rejects_deterministic_or_duplicate_fields() -> (
    None
):
    raw = _interpretation().model_dump(mode="json")
    raw["target_sha256"] = "a" * 64
    with pytest.raises(VisualInterpretationParseError, match="Schema"):
        parse_visual_interpretation_v2(json.dumps(raw))

    text = _interpretation().model_dump_json()
    duplicated = text.replace(
        '"schema_version":',
        '"summary":"duplicate","schema_version":',
        1,
    )
    with pytest.raises(VisualInterpretationParseError, match="不得重复：summary"):
        parse_visual_interpretation_v2(duplicated)

    expanded_role = _interpretation().model_dump(mode="json")
    expanded_role["layer_hypotheses"][0]["role"] = "glow"
    with pytest.raises(VisualInterpretationParseError, match="Schema"):
        parse_visual_interpretation_v2(json.dumps(expanded_role))

    incomplete = _interpretation().model_dump(mode="json")
    incomplete["required_layer_assessments"] = incomplete[
        "required_layer_assessments"
    ][:-1]
    with pytest.raises(VisualInterpretationParseError, match="Schema"):
        parse_visual_interpretation_v2(json.dumps(incomplete))


def test_build_intent_variants_binds_hypothesis_constraints_and_required_layers() -> (
    None
):
    measurements = make_target_measurements()
    constraint_set = make_constraint_set()

    result = build_intent_variants(
        measurements,
        _interpretation(),
        constraint_set,
        _context(),
    )

    assert len(result.variants) == 1
    assert result.rejections == ()
    intent = result.variants[0]
    hypothesis = measurements.target_hypotheses[0]
    assert intent.target_hypothesis_id == hypothesis.hypothesis_id
    assert intent.target_hypothesis_hash == hypothesis.hypothesis_hash
    assert intent.constraint_set_hash == constraint_set.constraint_set_hash
    assert {item.role for item in intent.layers if item.required} == {
        "base_fill",
        "highlight",
    }
    validate_intent_ir(
        intent,
        measurements=measurements,
        interpretation=_interpretation(),
        constraint_set=constraint_set,
        context=_context(),
    )


def _with_assessment_status(
    interpretation: VisualInterpretationV2,
    layer: str,
    status: str,
) -> VisualInterpretationV2:
    raw = json.loads(interpretation.model_dump_json())
    for assessment in raw["required_layer_assessments"]:
        if assessment["layer"] == layer:
            assessment["status"] = status
    return VisualInterpretationV2.model_validate_json(json.dumps(raw), strict=True)


def test_assessment_required_layers_join_constraints_and_are_preserved() -> None:
    interpretation = _with_assessment_status(
        _interpretation(),
        "shadow",
        "required",
    )

    result = build_intent_variants(
        make_target_measurements(),
        interpretation,
        make_constraint_set(),
        _context(),
    )

    shadow = next(item for item in result.variants[0].layers if item.role == "shadow")
    assert shadow.required is True
    assert shadow.source == "model"
    assert shadow.confidence == 0.9
    assert shadow.evidence_refs
    assert shadow.required_by_constraint_ids == ()


def test_required_layer_unknown_fails_closed() -> None:
    with pytest.raises(ValueError, match="包含 unknown"):
        build_intent_variants(
            make_target_measurements(),
            _with_assessment_status(_interpretation(), "shadow", "unknown"),
            make_constraint_set(),
            _context(),
        )

def test_build_intent_variants_partitions_every_target_hypothesis() -> None:
    measurements = make_target_measurements()
    original = measurements.target_hypotheses[0]
    draft = original.model_copy(
        update={
            "hypothesis_id": "hypothesis-alternative",
            "hypothesis_hash": "0" * 64,
            "confidence": 0.5,
        }
    )
    alternative = draft.model_copy(
        update={
            "hypothesis_hash": compute_target_hypothesis_hash(
                measurements.target_sha256,
                draft,
            )
        }
    )
    multiple = TargetMeasurementsV2.model_validate_json(
        measurements.model_copy(
            update={"target_hypotheses": (original, alternative)}
        ).model_dump_json(),
        strict=True,
    )

    result = build_intent_variants(
        multiple,
        _interpretation(),
        make_constraint_set(),
        _context(),
    )

    assert len(result.variants) == 2
    assert {
        (item.target_hypothesis_id, item.target_hypothesis_hash)
        for item in result.variants
    } == {
        (item.hypothesis_id, item.hypothesis_hash)
        for item in multiple.target_hypotheses
    }


def test_hard_structure_mismatch_produces_branch_rejection_not_partial_intent() -> None:
    current = make_constraint_set()
    topology = Constraint(
        constraint_id="pending",
        kind="topology",
        strength="hard",
        scope="object",
        scope_ref="subject",
        value=TopologyConstraintValue(topology="ring"),
        source="user",
        source_revision=0,
        confidence=1.0,
        verification_status="verified",
    )
    changed = compare_and_swap_constraint_set(
        current,
        expected_revision=current.request_revision,
        constraints=(*current.constraints, topology),
    )

    result = build_intent_variants(
        make_target_measurements(),
        _interpretation(),
        changed,
        _context(),
    )

    assert result.variants == ()
    assert len(result.rejections) == 1
    assert result.rejections[0].reason_codes == ("topology_mismatch",)


def test_builder_rejects_unapproved_interpretation_evidence() -> None:
    context = IntentBuildContext(
        contract_id="webgl1_static_no_texture_v1",
        primitive_catalog_version="png_to_shader_expected_primitives_v1",
        primitive_catalog_sha256="d" * 64,
        template_catalog_version="png_to_shader_expected_primitives_v1",
        template_catalog_sha256="e" * 64,
        allowed_primitive_ids=("arc_highlight", "solid_fill"),
        allowed_template_ids=("solid-circle-highlight",),
        allowed_interpretation_evidence_refs=(
            artifact_ref("other", "d", kind="visual_interpretation"),
        ),
    )

    with pytest.raises(ValueError, match="未授权"):
        build_intent_variants(
            make_target_measurements(),
            _interpretation(),
            make_constraint_set(),
            context,
        )


def test_context_receipt_and_evidence_authorization_use_content_semantics() -> None:
    context = _context()
    original_ref = context.allowed_interpretation_evidence_refs[0]
    alias_ref = replace(original_ref, artifact_id="artifact_alias")
    alias_context = context.model_copy(
        update={"allowed_interpretation_evidence_refs": (alias_ref,)}
    )

    assert compute_intent_input_hash(alias_context) == compute_intent_input_hash(context)
    assert build_intent_variants(
        make_target_measurements(),
        _interpretation(),
        make_constraint_set(),
        alias_context,
    ).variants


def test_independent_intent_validator_rejects_tampered_id_or_required_layer() -> None:
    measurements = make_target_measurements()
    constraint_set = make_constraint_set()
    intent = build_intent_variants(
        measurements,
        _interpretation(),
        constraint_set,
        _context(),
    ).variants[0]

    with pytest.raises(ValueError, match="intent_id"):
        validate_intent_ir(
            intent.model_copy(update={"intent_id": "intent_tampered"}),
            measurements=measurements,
            interpretation=_interpretation(),
            constraint_set=constraint_set,
            context=_context(),
        )

    without_highlight = intent.model_copy(
        update={
            "layers": tuple(item for item in intent.layers if item.role != "highlight"),
            "intent_id": "pending",
        }
    )
    without_highlight = without_highlight.model_copy(
        update={"intent_id": compute_intent_id(without_highlight)}
    )
    with pytest.raises(ValueError, match="Builder variant"):
        validate_intent_ir(
            without_highlight,
            measurements=measurements,
            interpretation=_interpretation(),
            constraint_set=constraint_set,
            context=_context(),
        )


def test_validator_rebuild_rejects_canvas_or_constraint_closure_tamper() -> None:
    measurements = make_target_measurements()
    interpretation = _interpretation()
    constraint_set = make_constraint_set()
    context = _context()
    intent = build_intent_variants(
        measurements,
        interpretation,
        constraint_set,
        context,
    ).variants[0]
    tampered = intent.model_copy(
        update={
            "canvas": intent.canvas.model_copy(update={"image_size": (1, 1)}),
            "hard_constraints": (),
            "intent_id": "pending",
        }
    )
    tampered = tampered.model_copy(
        update={"intent_id": compute_intent_id(tampered)}
    )

    with pytest.raises(ValueError, match="Builder variant"):
        validate_intent_ir(
            tampered,
            measurements=measurements,
            interpretation=interpretation,
            constraint_set=constraint_set,
            context=context,
        )


def test_builder_requires_the_frozen_verified_render_contract() -> None:
    current = make_constraint_set()
    without_contract = compare_and_swap_constraint_set(
        current,
        expected_revision=current.request_revision,
        constraints=tuple(item for item in current.constraints if item.kind != "contract"),
    )

    with pytest.raises(ValueError, match="contract constraint"):
        build_intent_variants(
            make_target_measurements(),
            _interpretation(),
            without_contract,
            _context(),
        )


def test_builder_rejects_legacy_cas_model_hard_constraint() -> None:
    current = make_constraint_set()
    model_hard = Constraint(
        constraint_id="pending",
        kind="topology",
        strength="hard",
        scope="object",
        scope_ref="subject",
        value=TopologyConstraintValue(topology="solid"),
        source="model",
        source_revision=0,
        confidence=0.9,
        verification_status="inferred",
    )
    bypassed = compare_and_swap_constraint_set(
        current,
        expected_revision=current.request_revision,
        constraints=(*current.constraints, model_hard),
    )

    with pytest.raises(ValueError, match="model constraint"):
        build_intent_variants(
            make_target_measurements(),
            _interpretation(),
            bypassed,
            _context(),
        )
