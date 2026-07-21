"""V2.0 schema/hash/state 测试共用的稳定 golden 输入。."""

from __future__ import annotations

from agent.app.states.png_to_shader_v2_state import (
    BudgetStateV2,
    BudgetVectorV2,
    PngToShaderV2State,
)
from shaderforge.analysis.models_v2 import (
    BBoxUv,
    InstanceGeometryV2,
    LabSample,
    RegionStatistics,
    SymmetryEvidence,
    TargetHypothesis,
    TargetMeasurementsV2,
    compute_target_hypothesis_hash,
)
from shaderforge.evaluation.models_v2 import (
    CandidateRecordV2,
    compute_candidate_record_hash,
)
from shaderforge.genome.canonical import compute_genome_hashes
from shaderforge.genome.models import (
    EFFECT_NODE_REGISTRY_V0,
    EffectEdge,
    EffectGenome,
    EffectNode,
    GenomeProvenance,
    ParameterBinding,
    ParameterSpec,
)
from shaderforge.intent.canonical import (
    compute_constraint_set_hash,
    with_constraint_id,
)
from shaderforge.intent.models import (
    Constraint,
    ContractConstraintValue,
    RequestConstraintSet,
    RequiredLayerConstraintValue,
)
from shaderforge.store.artifacts_v2 import ArtifactRefV2


def artifact_ref(
    name: str,
    digit: str,
    *,
    kind: str = "json",
    schema_version: str = "fixture_v1",
    content_type: str = "application/json",
    size_bytes: int = 10,
) -> ArtifactRefV2:
    """构造内容摘要可读的路径无关测试引用。."""
    return ArtifactRefV2(
        artifact_id=f"artifact_{name}",
        sha256=digit * 64,
        kind=kind,
        schema_version=schema_version,
        content_type=content_type,
        size_bytes=size_bytes,
    )


def make_target_measurements() -> TargetMeasurementsV2:
    """构造带已校验 hypothesis hash 的单假设测量。."""
    mask = artifact_ref(
        "mask",
        "1",
        kind="subject_mask",
        schema_version="subject_mask_v1",
        content_type="image/png",
        size_bytes=16,
    )
    evidence = artifact_ref("evidence", "2", kind="evidence")
    draft = TargetHypothesis(
        hypothesis_id="hypothesis-main",
        hypothesis_hash="0" * 64,
        subject_mask_ref=mask,
        instance_mask_refs=(mask,),
        instance_geometries=(
            InstanceGeometryV2(
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
        confidence=0.875,
        bbox_uv=BBoxUv(min_x=0.2, min_y=0.2, max_x=0.8, max_y=0.8),
        center_uv=(0.5, 0.5),
        area_ratio=0.28,
        axes_uv=(0.3, 0.3),
        orientation_rad=0.0,
        fill_topology="solid",
        component_count=1,
        instance_count=1,
        hole_count=0,
        evidence_refs=(evidence,),
    )
    hypothesis = draft.model_copy(
        update={"hypothesis_hash": compute_target_hypothesis_hash("a" * 64, draft)}
    )
    return TargetMeasurementsV2(
        target_sha256="a" * 64,
        image_size=(192, 192),
        target_hypotheses=(hypothesis,),
        palette_lab=(LabSample(lab=(60.0, 20.0, 5.0), weight=1.0),),
        region_statistics=(
            RegionStatistics(
                region_id="subject",
                bbox_uv=hypothesis.bbox_uv,
                area_ratio=0.28,
                mean_lab=(60.0, 20.0, 5.0),
            ),
        ),
        symmetry=SymmetryEvidence(horizontal=0.9, vertical=0.9, radial=0.8),
        radiality=0.8,
        gradient_evidence=(),
        edge_refs=(evidence,),
        evidence_index_ref=evidence,
    )


def make_constraint_set() -> RequestConstraintSet:
    """构造 id/hash 已物化的约束集合。."""
    evidence = artifact_ref("evidence", "2", kind="evidence")
    constraints = (
        with_constraint_id(
            Constraint(
                constraint_id="pending",
                kind="contract",
                strength="hard",
                scope="global",
                value=ContractConstraintValue(
                    contract_id="webgl1_static_no_texture_v1"
                ),
                source="render_contract",
                source_revision=0,
                confidence=1.0,
                verification_status="verified",
            )
        ),
        with_constraint_id(
            Constraint(
                constraint_id="pending",
                kind="required_layer",
                strength="hard",
                scope="object",
                scope_ref="subject",
                value=RequiredLayerConstraintValue(layer="highlight"),
                source="user",
                source_revision=3,
                confidence=1.0,
                verification_status="verified",
                evidence_refs=(evidence,),
            )
        ),
    )
    draft = RequestConstraintSet(
        constraint_set_id="constraints-main",
        constraint_set_hash="0" * 64,
        target_sha256="a" * 64,
        request_revision=3,
        constraints=constraints,
        evidence_refs=(evidence,),
    )
    return draft.model_copy(
        update={"constraint_set_hash": compute_constraint_set_hash(draft)}
    )


def _node(
    kind: str,
    *,
    node_id: str,
    semantic_role: str,
    bindings: tuple[ParameterBinding, ...] = (),
) -> EffectNode:
    spec = next(item for item in EFFECT_NODE_REGISTRY_V0 if item.kind == kind)
    return EffectNode(
        node_id=node_id,
        kind=spec.kind,
        semantic_role=semantic_role,
        sibling_ordinal=0,
        inputs=spec.inputs,
        outputs=spec.outputs,
        parameter_bindings=bindings,
    )


def make_genome() -> EffectGenome:
    """构造最小 circle→fill→output typed DAG。."""
    hypothesis_hash = make_target_measurements().target_hypotheses[0].hypothesis_hash
    return EffectGenome(
        genome_id="genome-record-1",
        contract_id="webgl1_static_no_texture_v1",
        strategy="minimal-solid-circle",
        nodes=(
            _node(
                "circle_sdf",
                node_id="geometry-original",
                semantic_role="subject_geometry",
                bindings=(
                    ParameterBinding(
                        binding_name="center", parameter_path="subject.center"
                    ),
                    ParameterBinding(
                        binding_name="radius", parameter_path="subject.radius"
                    ),
                ),
            ),
            _node(
                "solid_fill",
                node_id="fill-original",
                semantic_role="base_fill",
                bindings=(
                    ParameterBinding(binding_name="color", parameter_path="fill.color"),
                ),
            ),
            _node(
                "color_output",
                node_id="output-original",
                semantic_role="output",
            ),
        ),
        edges=(
            EffectEdge(
                source_node_id="geometry-original",
                source_port="sdf",
                target_node_id="fill-original",
                target_port="mask",
            ),
            EffectEdge(
                source_node_id="fill-original",
                source_port="color",
                target_node_id="output-original",
                target_port="color",
            ),
        ),
        parameters=(
            ParameterSpec(
                path="subject.center",
                dtype="vec2",
                value=(0.5, 0.5),
                min_value=(0.0, 0.0),
                max_value=(1.0, 1.0),
                optimizable=True,
                block="geometry",
                affected_regions=("subject",),
                semantic_role="position",
                unit="uv",
                coordinate_space="shader_uv_bottom_left",
                color_space=None,
                cyclic=False,
                quantization=0.0001,
            ),
            ParameterSpec(
                path="subject.radius",
                dtype="float",
                value=0.3,
                min_value=0.01,
                max_value=0.5,
                optimizable=True,
                block="geometry",
                affected_regions=("subject",),
                semantic_role="radius",
                unit="uv",
                coordinate_space="shader_uv_bottom_left",
                color_space=None,
                cyclic=False,
                quantization=0.0001,
            ),
            ParameterSpec(
                path="fill.color",
                dtype="vec4",
                value=(0.8, 0.2, 0.3, 1.0),
                min_value=(0.0, 0.0, 0.0, 0.0),
                max_value=(1.0, 1.0, 1.0, 1.0),
                optimizable=True,
                block="color",
                affected_regions=("subject",),
                semantic_role="base_color",
                unit="normalized",
                coordinate_space=None,
                color_space="linear_rgb",
                cyclic=False,
                quantization=0.0001,
            ),
        ),
        output_node_id="output-original",
        provenance=GenomeProvenance(
            source="rule",
            intent_id="intent-main",
            target_hypothesis_id="hypothesis-main",
            target_hypothesis_hash=hypothesis_hash,
            template_id="solid-circle",
            template_version="1",
            random_seed=7,
            evidence_refs=(artifact_ref("evidence", "2", kind="evidence"),),
        ),
    )


def make_candidate() -> CandidateRecordV2:
    """构造所有证据均绑定 hash 的不可变 Candidate。."""
    target = make_target_measurements()
    constraint_set = make_constraint_set()
    genome_hashes = compute_genome_hashes(make_genome())
    refs = {
        name: artifact_ref(name, digit, kind=name, schema_version=f"{name}_v1")
        for name, digit in zip(
            (
                "intent",
                "genome",
                "compilation",
                "diagnostic_compilation",
                "glsl",
                "render",
                "render_plan",
                "render_progress",
                "render_repeatability",
                "rendered_structure_evidence",
                "rendered_structure_verification",
                "constraint_evaluation",
                "evaluation",
                "provenance",
            ),
            "3456789bcdef01",
            strict=True,
        )
    }
    raw = {
        "schema_version": "candidate_record_v3",
        "candidate_id": "candidate-1",
        "run_id": "run-v2-golden",
        "parent_candidate_id": None,
        "target_hypothesis_id": "hypothesis-main",
        "target_hypothesis_hash": target.target_hypotheses[0].hypothesis_hash,
        "constraint_set_hash": constraint_set.constraint_set_hash,
        "intent_ref": refs["intent"],
        "genome_ref": refs["genome"],
        "topology_hash": genome_hashes.topology_hash,
        "parameter_layout_hash": genome_hashes.parameter_layout_hash,
        "semantic_genome_hash": genome_hashes.semantic_genome_hash,
        "compilation_ref": refs["compilation"],
        "diagnostic_compilation_ref": refs["diagnostic_compilation"],
        "glsl_ref": refs["glsl"],
        "render_refs": (refs["render"],) * 5,
        "render_plan_ref": refs["render_plan"],
        "render_progress_ref": refs["render_progress"],
        "render_repeatability_ref": refs["render_repeatability"],
        "rendered_structure_evidence_ref": refs["rendered_structure_evidence"],
        "rendered_structure_verification_ref": refs["rendered_structure_verification"],
        "constraint_evaluation_ref": refs["constraint_evaluation"],
        "evaluation_refs": (refs["evaluation"],) * 5,
        "provenance_ref": refs["provenance"],
    }
    record_hash = compute_candidate_record_hash(raw)
    return CandidateRecordV2(**raw, record_hash=record_hash)


def make_state() -> PngToShaderV2State:
    """构造可序列化恢复的空游标 State。."""
    zero = BudgetVectorV2(
        wall_time_ms=0,
        model_calls=0,
        model_tokens=0,
        render_calls=0,
        candidate_attempts=0,
        artifact_bytes=0,
        cost_usd_micros=0,
    )
    return PngToShaderV2State(
        checkpoint_namespace="png-to-shader-v2.4:run-v2-golden",
        project_id="project-v2",
        run_id="run-v2-golden",
        run_revision=0,
        phase="initialized",
        evaluation_revision=0,
        measurements_ref=artifact_ref("measurements", "c", kind="measurements"),
        visual_interpretation_ref=None,
        request_constraint_set_ref=artifact_ref(
            "constraint_set", "d", kind="constraint_set"
        ),
        hypothesis_branches=(),
        hypothesis_cursor=0,
        objective_best_id=None,
        candidate_summary_refs=(),
        budget_state=BudgetStateV2(
            policy_hash="f" * 64,
            revision=0,
            limits=BudgetVectorV2(
                wall_time_ms=60_000,
                model_calls=3,
                model_tokens=5_000,
                render_calls=10,
                candidate_attempts=5,
                artifact_bytes=1_000_000,
                cost_usd_micros=100_000,
            ),
            used=zero,
            reserved=zero,
            exhausted_dimensions=(),
        ),
        stop_reason=None,
    )
