from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from shaderforge.analysis import BBoxUv
from shaderforge.compiler import compile_diagnostic_passes
from shaderforge.contracts.taxonomy import REQUIRED_LAYER_ORDER
from shaderforge.genome import TypedEffectGenome
from shaderforge.intent import (
    CanvasIntent,
    InstanceIntent,
    IntentIR,
    ObjectIntent,
    PrimitiveCandidate,
    RegionIntent,
    RelationIntent,
    StrategyHypothesis,
    VisualLayerIntent,
)
from shaderforge.seeding import (
    AllowedOverrideV1,
    SeedPlanV1,
    assess_seed_diversity,
    build_seed_plans,
    expand_seed_plan,
    expand_seed_plans,
    match_seed_templates,
)
from shaderforge.store import ArtifactRefV2


def _artifact_ref(
    name: str,
    digit: str,
    *,
    kind: str,
    schema_version: str,
    content_type: str = "application/json",
    size_bytes: int = 10,
) -> ArtifactRefV2:
    return ArtifactRefV2(
        artifact_id=f"artifact_{name}",
        sha256=digit * 64,
        kind=kind,
        schema_version=schema_version,
        content_type=content_type,
        size_bytes=size_bytes,
    )


def _intent() -> IntentIR:
    mask = _artifact_ref(
        "seed_mask",
        "1",
        kind="subject_mask",
        schema_version="subject_mask_v1",
        content_type="image/png",
        size_bytes=16,
    )
    evidence = _artifact_ref(
        "seed_evidence",
        "2",
        kind="visual_interpretation",
        schema_version="visual_interpretation_v2_1",
    )
    return IntentIR(
        intent_id="intent-seed-v2",
        target_sha256="a" * 64,
        target_hypothesis_id="hypothesis-seed-v2",
        target_hypothesis_hash="b" * 64,
        constraint_set_hash="c" * 64,
        canvas=CanvasIntent(
            contract_id="webgl1_static_no_texture_v1",
            image_size=(192, 192),
        ),
        objects=(
            ObjectIntent(
                object_id="subject",
                subject_mask_ref=mask,
                instances=(
                    InstanceIntent(
                        instance_id="instance_0000",
                        instance_index=0,
                        mask_ref=mask,
                        bbox_uv=BBoxUv(min_x=0.2, min_y=0.2, max_x=0.8, max_y=0.8),
                        center_uv=(0.5, 0.5),
                        area_ratio=0.28,
                        axes_uv=(0.3, 0.3),
                        orientation_rad=0.0,
                        fill_topology="solid",
                        component_count=1,
                        hole_count=0,
                    ),
                ),
                bbox_uv=BBoxUv(min_x=0.2, min_y=0.2, max_x=0.8, max_y=0.8),
                center_uv=(0.5, 0.5),
                area_ratio=0.28,
                axes_uv=(0.3, 0.3),
                orientation_rad=0.0,
                topology="solid",
                component_count=1,
                instance_count=1,
                hole_count=0,
                confidence=0.95,
                evidence_refs=(evidence,),
            ),
        ),
        layers=(
            VisualLayerIntent(
                layer_id="layer-base",
                role="base_fill",
                order=0,
                object_ref="subject",
                required=True,
                source="policy",
                confidence=1.0,
                region_description="主体内部",
                primitive_candidate_ids=("primitive-base",),
            ),
            VisualLayerIntent(
                layer_id="layer-highlight",
                role="highlight",
                order=1,
                object_ref="subject",
                required=True,
                source="model",
                confidence=0.8,
                region_description="主体上缘",
                primitive_candidate_ids=("primitive-highlight",),
                evidence_refs=(evidence,),
            ),
        ),
        relations=(),
        regions=(
            RegionIntent(
                region_id="subject",
                bbox_uv=BBoxUv(min_x=0.2, min_y=0.2, max_x=0.8, max_y=0.8),
                area_ratio=0.28,
                mean_lab=(60.0, 20.0, 5.0),
            ),
        ),
        probes=(),
        hard_constraints=(),
        soft_preferences=(),
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
                confidence=0.8,
                evidence_refs=(evidence,),
            ),
        ),
        strategy_hypotheses=(
            StrategyHypothesis(
                strategy_id="strategy-layered",
                template_ids=("layered-shape",),
                required_layer_ids=("layer-base", "layer-highlight"),
                complexity="medium",
                confidence=0.85,
                evidence_refs=(evidence,),
            ),
        ),
        uncertainties=(),
        evidence_refs=(mask, evidence),
    )


def _all_layer_intent() -> IntentIR:
    intent = _intent()
    evidence = next(
        ref for ref in intent.evidence_refs if ref.kind == "visual_interpretation"
    )
    layers = tuple(
        VisualLayerIntent(
            layer_id=f"layer-{role}",
            role=role,
            order=index,
            object_ref=None if role == "background" else "subject",
            required=True,
            source="policy" if role == "base_fill" else "model",
            confidence=1.0 if role == "base_fill" else 0.8,
            region_description=f"{role} region",
            evidence_refs=() if role == "base_fill" else (evidence,),
        )
        for index, role in enumerate(REQUIRED_LAYER_ORDER)
    )
    strategy = StrategyHypothesis(
        strategy_id="strategy-all-layers",
        template_ids=("layered-shape",),
        required_layer_ids=tuple(item.layer_id for item in layers),
        complexity="high",
        confidence=0.8,
        evidence_refs=(evidence,),
    )
    changed = intent.model_copy(
        update={
            "layers": layers,
            "primitive_candidates": (),
            "strategy_hypotheses": (strategy,),
        }
    )
    return IntentIR.model_validate_json(changed.model_dump_json(), strict=True)


def test_matcher_builds_exactly_three_deterministic_closed_seed_plans() -> None:
    intent = _intent()

    matches = match_seed_templates(intent)
    plans = build_seed_plans(intent, random_seed=41)
    replay = build_seed_plans(intent, random_seed=41)

    assert tuple(item.seed_role for item in matches) == (
        "minimum_complexity",
        "semantic_enhancement",
        "alternate_structure",
    )
    assert tuple(item.seed_role for item in plans) == tuple(
        item.seed_role for item in matches
    )
    assert tuple(item.random_seed for item in plans) == (41, 42, 43)
    assert plans == replay
    assert tuple(item.model_dump_json() for item in plans) == tuple(
        item.model_dump_json() for item in replay
    )
    for plan in plans:
        assert plan.intent_id == intent.intent_id
        assert plan.target_hypothesis_hash == intent.target_hypothesis_hash
        assert {item.layer_id for item in plan.layer_bindings if item.enabled} == {
            "layer-base",
            "layer-highlight",
        }


def test_expander_returns_three_typed_genomes_with_real_diversity() -> None:
    intent = _intent()

    result = expand_seed_plans(intent, random_seed=7)

    assert result.diversity.gate_passed is True
    assert result.diversity.diversity_exception is None
    assert len(set(result.diversity.semantic_genome_hashes)) == 3
    assert result.diversity.distinct_structural_signatures == 3
    assert all(
        isinstance(item.genome, TypedEffectGenome) for item in result.expanded_seeds
    )


def test_expander_covers_the_complete_required_layer_taxonomy() -> None:
    result = expand_seed_plans(_all_layer_intent())

    assert result.diversity.gate_passed is True
    for expanded in result.expanded_seeds:
        semantic_roles = {node.semantic_role for node in expanded.genome.nodes}
        assert semantic_roles >= {f"layer_{role}" for role in REQUIRED_LAYER_ORDER}
    assert all(
        {node.semantic_role for node in item.genome.nodes}
        >= {"layer_base_fill", "layer_highlight", "output"}
        for item in result.expanded_seeds
    )
    assert all(
        edge.sdf_to_mask_conversion == "analytic_fixed_width_v1"
        for item in result.expanded_seeds
        for edge in item.genome.edges
        if edge.target_port == "mask"
    )


def test_random_seed_and_provenance_never_create_fake_semantic_diversity() -> None:
    intent = _intent()
    first = expand_seed_plans(intent, random_seed=1)
    replay = expand_seed_plans(intent, random_seed=100)

    assert tuple(
        item.genome_hashes.semantic_genome_hash for item in first.expanded_seeds
    ) == tuple(
        item.genome_hashes.semantic_genome_hash for item in replay.expanded_seeds
    )
    assert tuple(
        item.genome_hashes.record_hash for item in first.expanded_seeds
    ) != tuple(item.genome_hashes.record_hash for item in replay.expanded_seeds)


def test_diversity_gate_fails_closed_with_explicit_exception_on_duplicates() -> None:
    result = expand_seed_plans(_intent())
    plans = tuple(item.plan for item in result.expanded_seeds)
    hashes = tuple(item.genome_hashes for item in result.expanded_seeds)

    assessment = assess_seed_diversity(plans, (hashes[0], hashes[0], hashes[0]))

    assert assessment.gate_passed is False
    assert assessment.diversity_exception == "semantic_genome_hash_not_unique"


def test_expander_rejects_identity_tampering_and_unconsumed_override() -> None:
    intent = _intent()
    plan = build_seed_plans(intent)[0]
    tampered = plan.model_copy(update={"intent_id": "another-intent"})
    with pytest.raises(ValueError, match="身份不一致"):
        expand_seed_plan(intent, tampered)

    unused = plan.model_copy(
        update={
            "parameter_overrides": (
                AllowedOverrideV1(
                    layer_id="layer-base",
                    parameter_name="opacity",
                    value=0.5,
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="未消费"):
        expand_seed_plan(intent, unused)


def test_seed_plan_schema_rejects_arbitrary_override_and_duplicate_layers() -> None:
    plan = build_seed_plans(_intent())[0]
    raw = json.loads(plan.model_dump_json())
    raw["parameter_overrides"] = [
        {
            "layer_id": "layer-base",
            "parameter_name": "arbitrary_glsl",
            "value": 0.5,
        }
    ]
    with pytest.raises(ValidationError, match="parameter_name"):
        SeedPlanV1.model_validate_json(json.dumps(raw), strict=True)

    raw = json.loads(plan.model_dump_json())
    raw["layer_bindings"].append(raw["layer_bindings"][0])
    with pytest.raises(ValidationError, match="layer_id 不得重复"):
        SeedPlanV1.model_validate_json(json.dumps(raw), strict=True)


def test_diagnostic_compiler_is_node_derived_and_byte_deterministic() -> None:
    genome = expand_seed_plans(_intent()).expanded_seeds[0].genome

    first = compile_diagnostic_passes(genome)
    replay = compile_diagnostic_passes(genome)

    assert first == replay
    assert {item.pass_id for item in first.passes} >= {
        "instance_0000_visible_delta",
        "layer_base_fill_visible_delta",
    }
    assert all(item.glsl_sha256 for item in first.passes)
    assert all("target_mask" not in item.glsl_source for item in first.passes)
    assert all("manifest" not in item.glsl_source.lower() for item in first.passes)


def test_expander_uses_typed_per_instance_geometry_for_multi_instance() -> None:
    intent = _intent()
    subject = intent.objects[0]
    second_mask = _artifact_ref(
        "seed_mask_second",
        "3",
        kind="instance_mask",
        schema_version="binary_mask_v1",
        content_type="image/png",
        size_bytes=16,
    )
    first = subject.instances[0].model_copy(
        update={
            "center_uv": (0.3, 0.5),
            "axes_uv": (0.12, 0.18),
            "bbox_uv": BBoxUv(min_x=0.18, min_y=0.32, max_x=0.42, max_y=0.68),
        }
    )
    second = first.model_copy(
        update={
            "instance_id": "instance_0001",
            "instance_index": 1,
            "mask_ref": second_mask,
            "center_uv": (0.72, 0.48),
            "axes_uv": (0.08, 0.14),
            "bbox_uv": BBoxUv(min_x=0.64, min_y=0.34, max_x=0.8, max_y=0.62),
        }
    )
    multi_subject = subject.model_copy(
        update={
            "instances": (first, second),
            "instance_count": 2,
            "component_count": 2,
        }
    )
    multi = intent.model_copy(
        update={
            "objects": (multi_subject,),
            "relations": (
                RelationIntent(
                    relation_id="instance-pair-disjoint",
                    kind="disjoint",
                    subject_ref="instance_0000",
                    object_ref="instance_0001",
                    confidence=1.0,
                ),
            ),
            "evidence_refs": (*intent.evidence_refs, second_mask),
        }
    )

    genome = expand_seed_plans(multi).expanded_seeds[0].genome
    parameter_values = {item.path: item.value for item in genome.parameters}
    diagnostics = compile_diagnostic_passes(genome)

    assert parameter_values["shape.instance_0000.center"] == (0.3, 0.5)
    assert parameter_values["shape.instance_0001.center"] == (0.72, 0.48)
    assert any(node.kind == "union_mask" for node in genome.nodes)
    assert [
        item.pass_id
        for item in diagnostics.passes
        if item.pass_kind == "instance_visible_delta"
    ] == ["instance_0000_visible_delta", "instance_0001_visible_delta"]
    instance_passes = tuple(
        item
        for item in diagnostics.passes
        if item.pass_kind == "instance_visible_delta"
    )
    assert diagnostics.schema_version == "diagnostic_compilation_product_v3"
    assert diagnostics.ownership_policy_version == (
        "stable_instance_ordinal_first_match_v1"
    )
    assert "sf_earlier_instance_member = 0.0;" in instance_passes[0].glsl_source
    assert "sf_earlier_instance_member = max(0.0, step(" in (
        instance_passes[1].glsl_source
    )
    assert all("sf_instance_owner" in item.glsl_source for item in instance_passes)

    missing_relations = multi.model_copy(update={"relations": ()})
    with pytest.raises(ValueError, match="relations.*\u7cbe\u786e覆盖"):
        expand_seed_plans(missing_relations)

    for unsupported_kind in ("overlap", "contains", "subtracts"):
        unsupported = multi.model_copy(
            update={
                "relations": (
                    RelationIntent(
                        relation_id=f"instance-pair-{unsupported_kind}",
                        kind=unsupported_kind,
                        subject_ref="instance_0000",
                        object_ref="instance_0001",
                        confidence=1.0,
                    ),
                )
            }
        )
        with pytest.raises(ValueError, match="暂不支持 relation"):
            expand_seed_plans(unsupported)


def test_semantic_ring_instances_do_not_inherit_subject_ring_topology() -> None:
    intent = _intent()
    subject = intent.objects[0]
    second_mask = _artifact_ref(
        "semantic_ring_second",
        "4",
        kind="instance_mask",
        schema_version="binary_mask_v1",
        content_type="image/png",
        size_bytes=16,
    )
    first = subject.instances[0].model_copy(
        update={"center_uv": (0.35, 0.5), "axes_uv": (0.10, 0.20)}
    )
    second = first.model_copy(
        update={
            "instance_id": "instance_0001",
            "instance_index": 1,
            "mask_ref": second_mask,
            "center_uv": (0.65, 0.5),
        }
    )
    semantic_subject = subject.model_copy(
        update={
            "instances": (first, second),
            "topology": "ring",
            "component_count": 1,
            "instance_count": 2,
            "hole_count": 1,
        }
    )
    semantic = intent.model_copy(
        update={
            "objects": (semantic_subject,),
            "relations": (
                RelationIntent(
                    relation_id="semantic-ring-touch",
                    kind="touches",
                    subject_ref="instance_0000",
                    object_ref="instance_0001",
                    confidence=1.0,
                ),
            ),
            "evidence_refs": (*intent.evidence_refs, second_mask),
        }
    )

    genome = expand_seed_plans(semantic).expanded_seeds[0].genome

    assert not any("_inner" in node.semantic_role for node in genome.nodes)
    assert not any(
        node.kind == "difference_mask" and node.semantic_role.startswith("instance_")
        for node in genome.nodes
    )


@pytest.mark.parametrize("topology", ["ring", "hollow", "open"])
def test_expander_emits_explicit_mask_algebra_for_complex_topology(
    topology: str,
) -> None:
    intent = _intent()
    subject = intent.objects[0]
    instance = subject.instances[0].model_copy(
        update={
            "fill_topology": topology,
            "hole_count": 0 if topology == "open" else 1,
        }
    )
    complex_subject = subject.model_copy(
        update={
            "instances": (instance,),
            "topology": topology,
            "hole_count": 0 if topology == "open" else 1,
        }
    )
    complex_intent = intent.model_copy(update={"objects": (complex_subject,)})

    genome = expand_seed_plans(complex_intent).expanded_seeds[0].genome
    diagnostics = compile_diagnostic_passes(genome)

    assert any(node.kind == "difference_mask" for node in genome.nodes)
    assert any(
        item.pass_id == "instance_0000_visible_delta"
        and item.node_output_type == "mask"
        for item in diagnostics.passes
    )
