from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from agent.app.contracts.png_to_shader_v1 import AuthorMode, CandidateProvenance
from agent.app.lab.models import (
    ArtifactDescriptor,
    LabRunRecord,
    NodeExecutionResult,
    NodeLabError,
    StepExecutionRequest,
)
from agent.app.memory.models import MEMORY_SCHEMA_VERSION, MemoryItem
from agent.app.nodes.png_to_shader_v1.integrations.node_lab import (
    build_png_to_shader_v1_registry,
)
from agent.app.nodes.png_to_shader_v1.integrations.node_lab.deterministic import (
    SUPPORTED_NODE_IDS,
    DeterministicNodeExecutor,
)
from shaderforge.public import (
    MEASUREMENT_AFFINE_SEED_VERSION,
    BudgetPolicy,
    CandidateRecord,
    ScoreBreakdownV1,
    measure_target,
    normalize_target_png,
)
from shaderforge.rendering import CompileResult, RenderResult
from shaderforge.validation import validate_shader
from tests.fixtures.png_to_shader_v1_samples import (
    GOLDEN_GLSL,
    analysis_payload,
    author_payload,
    review_payload,
)

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_IMAGE = ROOT / "benchmarks/png_to_shader_v1/images/pink_gel.png"


class FakeArtifactAccess:
    def __init__(self, *, project_id: str = "project-stage-c") -> None:
        self.run = LabRunRecord(
            lab_run_id="lab-stage-c",
            project_id=project_id,
            created_at="2026-07-14T00:00:00Z",
            root_state_sha256="0" * 64,
        )
        self.values: dict[str, tuple[ArtifactDescriptor, bytes]] = {}
        self.counter = 0

    def get_run(self, lab_run_id: str) -> LabRunRecord:
        assert lab_run_id == self.run.lab_run_id
        return self.run

    def upload_artifact(
        self,
        *,
        lab_run_id: str,
        kind: str,
        content_type: str,
        data: bytes,
    ) -> ArtifactDescriptor:
        assert lab_run_id == self.run.lab_run_id
        self.counter += 1
        descriptor = ArtifactDescriptor(
            artifact_id=f"artifact-{self.counter:04d}",
            lab_run_id=lab_run_id,
            kind=kind,
            content_type=content_type,
            sha256=sha256(data).hexdigest(),
            size_bytes=len(data),
            created_at="2026-07-14T00:00:00Z",
        )
        self.values[descriptor.artifact_id] = (descriptor, data)
        return descriptor

    def read_artifact(
        self,
        lab_run_id: str,
        artifact_id: str,
    ) -> tuple[ArtifactDescriptor, bytes]:
        descriptor, data = self.values[artifact_id]
        if descriptor.lab_run_id != lab_run_id:
            raise NodeLabError(
                "artifact_not_found",
                "未找到同一 LabRun Artifact。",
                stage="artifact_read",
            )
        return descriptor, data

    def put_json(self, kind: str, value: object) -> ArtifactDescriptor:
        return self.upload_artifact(
            lab_run_id=self.run.lab_run_id,
            kind=kind,
            content_type="application/json; charset=utf-8",
            data=(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode(),
        )

    def put_glsl(self, source: str = GOLDEN_GLSL) -> ArtifactDescriptor:
        return self.upload_artifact(
            lab_run_id=self.run.lab_run_id,
            kind="glsl",
            content_type="text/x-glsl; charset=utf-8",
            data=source.encode(),
        )


class ScriptedMemoryReader:
    def __init__(self, values: list[MemoryItem] | Exception) -> None:
        self.values = values
        self.project_ids: list[str] = []

    async def list_project_memories(
        self,
        project_id: str,
        *,
        limit: int,
    ) -> list[MemoryItem]:
        self.project_ids.append(project_id)
        if isinstance(self.values, Exception):
            raise self.values
        return self.values[:limit]


class TrackingRenderer:
    """验证单步生产 Render Node 不把浏览器生命周期泄漏给 LabRun."""

    def __init__(self, image: bytes) -> None:
        self.image = image
        self.closed = False

    async def render(
        self,
        fragment_source: str,
        width: int,
        height: int,
    ) -> RenderResult:
        validation = validate_shader(fragment_source)
        return RenderResult(
            success=validation.valid,
            image_bytes=self.image if validation.valid else None,
            width=width,
            height=height,
            compile=CompileResult(
                success=validation.valid,
                vertex_log="",
                fragment_log="",
                link_log="",
                draw_error=None,
                static_validation=validation,
            ),
            console_errors=(),
            metadata=None,
            duration_ms=1.0,
        )

    async def close(self) -> None:
        self.closed = True


def request(node_id: str, *, effect_mode: str = "lab_commit") -> StepExecutionRequest:
    return StepExecutionRequest(
        lab_run_id="lab-stage-c",
        node_id=node_id,
        execution_mode="deterministic",
        effect_mode=effect_mode,  # type: ignore[arg-type]
    )


async def execute(
    executor: DeterministicNodeExecutor,
    node_id: str,
    state: dict[str, object],
    *,
    effect_mode: str = "lab_commit",
) -> NodeExecutionResult:
    descriptor = build_png_to_shader_v1_registry().get(node_id)
    return await executor.execute(
        descriptor,
        request(node_id, effect_mode=effect_mode),
        state,
    )


def model_artifacts(
    artifacts: FakeArtifactAccess,
) -> tuple[ArtifactDescriptor, ArtifactDescriptor, ArtifactDescriptor]:
    author = author_payload()
    glsl = artifacts.put_glsl(author["glsl"])
    provenance = CandidateProvenance(
        mode=AuthorMode.INITIAL,
        model_ref="fake:model",
        requested_model_ref="fake:model",
        model_identity_source="configured_fallback",
        prompt_version="shader_author_initial_v1_1",
        final_attempt=1,
        repair_prompt_version=None,
        output_sha256="1" * 64,
        glsl_sha256=glsl.sha256,
    )
    return (
        artifacts.put_json("author", author),
        artifacts.put_json(
            "candidate_provenance",
            provenance.to_dict(),
        ),
        glsl,
    )


def scored_candidate(artifacts: FakeArtifactAccess) -> CandidateRecord:
    author, provenance, glsl = model_artifacts(artifacts)
    render = artifacts.upload_artifact(
        lab_run_id=artifacts.run.lab_run_id,
        kind="render_png",
        content_type="image/png",
        data=b"rendered-image",
    )
    score = ScoreBreakdownV1(
        metric_version="unit-v1",
        total_loss=0.2,
        global_rmse=0.2,
        global_mae=0.2,
        edge_loss=0.2,
        geometry_loss=0.2,
        representative_pixel_loss=0.2,
        roi_losses=(("highlight", 0.3),),
        protected_region_losses=(("protected_center", 0.1),),
        effective_weights=(("global_rmse", 1.0),),
        diagnostics=(),
    )
    metrics = artifacts.put_json("score_metrics", score.to_dict())
    return CandidateRecord(
        candidate_id="candidate-0001",
        parent_candidate_id=None,
        glsl_sha256=glsl.sha256,
        glsl_ref=glsl.artifact_id,
        author_ref=author.artifact_id,
        provenance_ref=provenance.artifact_id,
        compile_ref=None,
        render_ref=render.artifact_id,
        render_sha256=render.sha256,
        metrics_ref=metrics.artifact_id,
        review_ref=None,
        iteration=0,
        changed_problem_domain="initial_build",
        prompt_version="shader_author_initial_v1_1",
        model_ref="fake:model",
        score_summary=score,
        hard_constraints_passed=True,
    )


@pytest.mark.anyio
async def test_stage_c_executor_allowlist_and_initialize_use_only_lab_artifacts() -> (
    None
):
    artifacts = FakeArtifactAccess()
    source = artifacts.upload_artifact(
        lab_run_id=artifacts.run.lab_run_id,
        kind="source_image",
        content_type="image/png",
        data=REFERENCE_IMAGE.read_bytes(),
    )
    executor = DeterministicNodeExecutor(artifacts, clock=lambda: 12.5)

    result = await execute(
        executor,
        "initialize_run",
        {
            "project_id": "project-stage-c",
            "source_artifact_id": source.artifact_id,
            "quality_preset": "balanced",
        },
    )

    assert set(SUPPORTED_NODE_IDS) == {
        "initialize_run",
        "prepare_context",
        "measure_target",
        "persist_visual_analysis",
        "prepare_measurement_seed",
        "materialize_candidate",
        "render_and_evaluate",
        "decide_after_render",
        "prepare_compile_repair",
        "select_current_best",
        "decide_after_selection",
        "load_current_best",
        "persist_visual_review",
        "finalize",
        "promote_validated_strategy",
    }
    assert result.output_patch["started_at"] == 12.5
    assert result.output_patch["reference_artifact_id"] in artifacts.values
    assert result.output_patch["run_config_artifact_id"] in artifacts.values
    assert "image" not in result.output_patch
    assert result.provenance["implementation"] == (
        "src/agent/app/nodes/png_to_shader_v1/deterministic/preparation.py"
        "#initialize_run"
    )


@pytest.mark.anyio
async def test_prepare_context_is_project_scoped_private_and_degrades_safely() -> None:
    artifacts = FakeArtifactAccess()
    now = datetime(2026, 7, 14, tzinfo=UTC)
    item = MemoryItem(
        schema_version=MEMORY_SCHEMA_VERSION,
        memory_id="constraint-1",
        kind="constraint",
        summary="不得公开的 Memory 正文",
        importance=0.8,
        source_run_id="older-run",
        glsl_sha256=None,
        iteration=None,
        created_at=now,
        updated_at=now,
    )
    reader = ScriptedMemoryReader([item])
    executor = DeterministicNodeExecutor(artifacts, memory_reader=reader)

    result = await execute(
        executor,
        "prepare_context",
        {"project_id": "project-stage-c"},
    )

    assert reader.project_ids == ["project-stage-c"]
    assert result.output_patch["selected_memory_ids"] == ["constraint-1"]
    assert "context_pack" not in result.output_patch
    context_id = result.output_patch["context_pack_artifact_id"]
    assert isinstance(context_id, str)
    assert "不得公开" in artifacts.values[context_id][1].decode()
    assert "不得公开" not in json.dumps(result.output_patch, ensure_ascii=False)

    failing = DeterministicNodeExecutor(
        artifacts,
        memory_reader=ScriptedMemoryReader(RuntimeError("secret-store-error")),
    )
    degraded = await execute(
        failing,
        "prepare_context",
        {"project_id": "project-stage-c"},
    )
    assert degraded.output_patch["memory_status"] == "degraded"
    assert "secret-store-error" not in json.dumps(degraded.to_dict())
    with pytest.raises(NodeLabError) as caught:
        await execute(
            failing,
            "prepare_context",
            {"project_id": "project-stage-c", "memory_strict": True},
        )
    assert caught.value.code == "memory_unavailable"


@pytest.mark.anyio
async def test_persist_materialize_and_prepare_repair_preserve_bindings() -> None:
    artifacts = FakeArtifactAccess()
    executor = DeterministicNodeExecutor(artifacts)
    analysis = artifacts.put_json("visual_analysis", analysis_payload())

    persisted = await execute(
        executor,
        "persist_visual_analysis",
        {"visual_analysis_artifact_id": analysis.artifact_id},
    )
    assert persisted.output_patch["phase"] == "analyzed"

    author, provenance, glsl = model_artifacts(artifacts)
    materialized = await execute(
        executor,
        "materialize_candidate",
        {
            "author_artifact_id": author.artifact_id,
            "candidate_provenance_artifact_id": provenance.artifact_id,
            "glsl_artifact_id": glsl.artifact_id,
            "candidate_sequence": 0,
            "candidate_records": [],
        },
    )
    record = materialized.output_patch["candidate_record"]
    assert record["glsl_ref"] != glsl.artifact_id
    copied_glsl = artifacts.values[record["glsl_ref"]][1]
    assert copied_glsl.decode() == GOLDEN_GLSL
    assert record["glsl_sha256"] == glsl.sha256
    assert materialized.output_patch["current_candidate_id"] == "candidate-0001"

    wrong_version = author_payload()
    wrong_version["author_version"] = "shader_author_compile_repair_v1_1"
    wrong_author = artifacts.put_json("author", wrong_version)
    with pytest.raises(NodeLabError) as mismatched_author:
        await execute(
            executor,
            "materialize_candidate",
            {
                "author_artifact_id": wrong_author.artifact_id,
                "candidate_provenance_artifact_id": provenance.artifact_id,
                "glsl_artifact_id": glsl.artifact_id,
            },
        )
    assert mismatched_author.value.code == "artifact_integrity_failed"

    with pytest.raises(NodeLabError) as model_with_generator:
        await execute(
            executor,
            "materialize_candidate",
            {
                "author_artifact_id": author.artifact_id,
                "candidate_provenance_artifact_id": provenance.artifact_id,
                "glsl_artifact_id": glsl.artifact_id,
                "candidate_generator_version": "unexpected-generator",
            },
        )
    assert model_with_generator.value.code == "artifact_integrity_failed"

    prepared = await execute(
        executor,
        "prepare_compile_repair",
        {
            "author_artifact_id": author.artifact_id,
            "compile_repair_count": 1,
            "budget_policy": BudgetPolicy(
                max_visual_refinements=1,
                max_compile_repairs=2,
                max_model_calls=6,
                max_wall_time_seconds=30,
            ).__dict__,
        },
    )
    assert prepared.output_patch["repair_budget"] == {
        "used": 1,
        "remaining": 1,
        "maximum": 2,
    }


@pytest.mark.anyio
async def test_render_node_uses_production_semantics_and_closes_step_renderer() -> None:
    artifacts = FakeArtifactAccess()
    reference_bytes = normalize_target_png(REFERENCE_IMAGE.read_bytes())
    reference = artifacts.upload_artifact(
        lab_run_id=artifacts.run.lab_run_id,
        kind="reference_png",
        content_type="image/png",
        data=reference_bytes,
    )
    renderers: list[TrackingRenderer] = []

    def factory() -> TrackingRenderer:
        renderer = TrackingRenderer(reference_bytes)
        renderers.append(renderer)
        return renderer

    executor = DeterministicNodeExecutor(artifacts, renderer_factory=factory)
    author, provenance, glsl = model_artifacts(artifacts)
    materialized = await execute(
        executor,
        "materialize_candidate",
        {
            "author_artifact_id": author.artifact_id,
            "candidate_provenance_artifact_id": provenance.artifact_id,
            "glsl_artifact_id": glsl.artifact_id,
        },
    )
    record = materialized.output_patch["candidate_record"]
    rendered = await execute(
        executor,
        "render_and_evaluate",
        {
            "candidate_record": record,
            "candidate_records": [record],
            "shader_artifact_id": record["glsl_ref"],
            "reference_artifact_id": reference.artifact_id,
            "target_measurements": measure_target(reference_bytes).to_dict(),
        },
    )

    assert rendered.output_patch["render_status"] == "success"
    assert rendered.usage["browser_launch_count"] == 1
    assert rendered.provenance["renderer_lifecycle"] == "cold_per_node_step"
    assert len(renderers) == 1
    assert renderers[0].closed is True


@pytest.mark.anyio
async def test_prepare_measurement_seed_materializes_independent_root() -> None:
    artifacts = FakeArtifactAccess()
    reference_bytes = normalize_target_png(REFERENCE_IMAGE.read_bytes())
    reference = artifacts.upload_artifact(
        lab_run_id=artifacts.run.lab_run_id,
        kind="reference_png",
        content_type="image/png",
        data=reference_bytes,
    )
    executor = DeterministicNodeExecutor(artifacts)
    measurements = json.loads(json.dumps(measure_target(reference_bytes).to_dict()))
    prepared = await execute(
        executor,
        "prepare_measurement_seed",
        {
            "reference_artifact_id": reference.artifact_id,
            "target_measurements": measurements,
            "measurement_seed_attempted": False,
        },
    )

    assert prepared.output_patch["phase"] == "measurement_seed_prepared"
    assert prepared.output_patch["measurement_seed_attempted"] is True
    assert prepared.output_patch["candidate_origin"] == "deterministic"
    assert (
        prepared.output_patch["candidate_generator_version"]
        == MEASUREMENT_AFFINE_SEED_VERSION
    )
    assert prepared.usage == {"model_call_count": 0, "browser_launch_count": 0}
    assert "glsl" not in prepared.output_patch

    materialized = await execute(
        executor,
        "materialize_candidate",
        {
            **prepared.output_patch,
            "candidate_sequence": 4,
            "current_candidate_id": "candidate-model-best",
            "candidate_records": [],
        },
    )
    record = materialized.output_patch["candidate_record"]
    assert record["candidate_id"] == "candidate-0005"
    assert record["parent_candidate_id"] is None
    assert record["origin"] == "deterministic"
    assert record["generator_version"] == MEASUREMENT_AFFINE_SEED_VERSION
    assert record["prompt_version"] == MEASUREMENT_AFFINE_SEED_VERSION
    assert record["model_ref"] == f"deterministic:{MEASUREMENT_AFFINE_SEED_VERSION}"

    with pytest.raises(NodeLabError) as tampered:
        await execute(
            executor,
            "materialize_candidate",
            {
                **prepared.output_patch,
                "candidate_generator_version": "tampered-seed-version",
                "candidate_sequence": 4,
                "candidate_records": [],
            },
        )
    assert tampered.value.code == "artifact_integrity_failed"

    with pytest.raises(NodeLabError) as repeated:
        await execute(
            executor,
            "prepare_measurement_seed",
            {
                "reference_artifact_id": reference.artifact_id,
                "target_measurements": measurements,
                "measurement_seed_attempted": True,
            },
        )
    assert repeated.value.code == "input_contract_invalid"


@pytest.mark.anyio
async def test_load_review_finalize_and_promote_preview_keep_hash_chain() -> None:
    artifacts = FakeArtifactAccess()
    executor = DeterministicNodeExecutor(artifacts, clock=lambda: 10.0)
    best = scored_candidate(artifacts)
    best_value = best.to_dict()

    loaded = await execute(
        executor,
        "load_current_best",
        {"current_best_record": best_value},
    )
    assert loaded.output_patch["render_evidence_binding"] == {
        "candidate_id": best.candidate_id,
        "glsl_sha256": best.glsl_sha256,
        "image_sha256": best.render_sha256,
    }
    assert "glsl" not in loaded.output_patch

    review = artifacts.put_json("visual_review", review_payload(best.candidate_id))
    persisted = await execute(
        executor,
        "persist_visual_review",
        {
            "current_best_record": best_value,
            "candidate_records": [best_value],
            "visual_review_artifact_id": review.artifact_id,
        },
    )
    updated_best = persisted.output_patch["current_best_record"]
    assert updated_best["review_ref"] == persisted.output_patch["review_artifact_id"]

    finalized = await execute(
        executor,
        "finalize",
        {
            "project_id": "project-stage-c",
            "run_id": "run-stage-c",
            "current_best_record": updated_best,
            "candidate_records": [updated_best],
            "target_measurements": {"analysis_width": 64, "analysis_height": 64},
            "started_at": 5.0,
        },
    )
    final = finalized.output_patch["final_result"]
    assert final["success"] is True
    assert final["glsl_sha256"] == best.glsl_sha256
    assert final["render_sha256"] == best.render_sha256
    assert "glsl" not in final

    preview = await execute(
        executor,
        "promote_validated_strategy",
        {
            "project_id": "project-stage-c",
            "run_id": "run-stage-c",
            "current_best_record": updated_best,
            "final_result": final,
        },
        effect_mode="preview",
    )
    assert preview.output_patch["memory_preview"]["glsl_sha256"] == best.glsl_sha256
    assert preview.provenance["memory_write"] is False
    assert preview.usage["memory_write_count"] == 0

    with pytest.raises(NodeLabError) as mismatched_final:
        await execute(
            executor,
            "promote_validated_strategy",
            {
                "project_id": "project-stage-c",
                "run_id": "run-stage-c",
                "current_best_record": updated_best,
                "final_result": {**final, "candidate_id": "candidate-tampered"},
            },
            effect_mode="preview",
        )
    assert mismatched_final.value.code == "input_contract_invalid"

    with pytest.raises(NodeLabError) as caught:
        await execute(
            executor,
            "promote_validated_strategy",
            {
                "project_id": "project-stage-c",
                "current_best_record": updated_best,
                "final_result": final,
            },
            effect_mode="project_commit",
        )
    assert caught.value.code == "effect_not_allowed"


@pytest.mark.anyio
async def test_project_scope_and_unknown_dispatch_fail_closed() -> None:
    artifacts = FakeArtifactAccess()
    executor = DeterministicNodeExecutor(artifacts)
    with pytest.raises(NodeLabError) as caught:
        await execute(
            executor,
            "prepare_context",
            {"project_id": "other-project"},
        )
    assert caught.value.code == "project_scope_mismatch"

    descriptor = build_png_to_shader_v1_registry().get("visual_analysis")
    with pytest.raises(NodeLabError) as unsupported:
        await executor.execute(
            descriptor,
            request("visual_analysis"),
            {},
        )
    assert unsupported.value.code == "node_adapter_not_implemented"
