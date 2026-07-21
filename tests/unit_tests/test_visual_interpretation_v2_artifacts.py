from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from shaderforge.contracts.taxonomy import REQUIRED_LAYER_ORDER
from shaderforge.intent import (
    LayerHypothesis,
    PrimitiveCandidate,
    RequiredLayerAssessment,
    StrategyHypothesis,
    VisualInterpretationArtifactBundle,
    VisualInterpretationV2,
    load_visual_interpretation_call,
    materialize_visual_interpretation_call,
)
from shaderforge.store import (
    ArtifactIntegrityError,
    ArtifactRefV2,
    LocalArtifactCatalog,
    LocalArtifactStore,
    RunArtifactStore,
)


def _catalog(
    tmp_path: Path,
    run_id: str,
) -> tuple[RunArtifactStore, LocalArtifactCatalog]:
    run = LocalArtifactStore(tmp_path).start_run("project-v2", run_id)
    return run, LocalArtifactCatalog(run, run_id=run_id)


def _input_ref(catalog: LocalArtifactCatalog) -> ArtifactRefV2:
    return catalog.put(
        run_id=catalog.run_id,
        kind="normalized_reference",
        schema_version="normalized_target_png_v1",
        content_type="image/png",
        data=b"frozen-input",
    )


def _interpretation(evidence_ref: ArtifactRefV2) -> VisualInterpretationV2:
    return VisualInterpretationV2(
        summary="主体由基础填色构成。",
        layer_hypotheses=(
            LayerHypothesis(
                layer_id="layer-base",
                role="base_fill",
                order=0,
                confidence=0.9,
                region_description="主体内部",
                primitive_candidates=("solid_fill",),
                evidence_refs=(evidence_ref,),
            ),
        ),
        required_layer_assessments=tuple(
            RequiredLayerAssessment(
                layer=layer,
                status="required" if layer == "base_fill" else "not_required",
                confidence=0.9,
                rationale="测试闭集判断。",
                evidence_refs=(evidence_ref,),
            )
            for layer in REQUIRED_LAYER_ORDER
        ),
        primitive_candidates=(
            PrimitiveCandidate(
                candidate_id="primitive-base",
                primitive_id="solid_fill",
                layer_id="layer-base",
                confidence=0.9,
                evidence_refs=(evidence_ref,),
            ),
        ),
        strategy_hypotheses=(
            StrategyHypothesis(
                strategy_id="strategy-solid",
                template_ids=("solid-shape",),
                required_layer_ids=("layer-base",),
                complexity="low",
                confidence=0.9,
                evidence_refs=(evidence_ref,),
            ),
        ),
        evidence_refs=(evidence_ref,),
    )


def _manifest_kinds(run: RunArtifactStore) -> tuple[str, ...]:
    manifest = json.loads(
        run.read_bytes(".artifact-catalog-v2/manifest.json").decode("utf-8")
    )
    return tuple(item["kind"] for item in manifest["artifacts"].values())


def test_success_call_freezes_prompt_model_inputs_raw_and_interpretation(
    tmp_path: Path,
) -> None:
    run, catalog = _catalog(tmp_path, "run-success")
    input_ref = _input_ref(catalog)
    interpretation = _interpretation(input_ref)
    raw_response = interpretation.model_dump_json()

    bundle = materialize_visual_interpretation_call(
        catalog=catalog,
        run_id="run-success",
        prompt_name="analyze_visual_layers_v2",
        prompt_version="2.1.0",
        prompt_text="只返回 VisualInterpretationV2 JSON。",
        model_id="provider/model-version",
        input_artifact_refs=(input_ref,),
        raw_response=raw_response,
        attempt_count=2,
        repair_count=1,
        parser_status="succeeded",
        interpretation=interpretation,
    )

    audit = bundle.audit
    assert bundle.interpretation == interpretation
    assert audit.prompt.name == "analyze_visual_layers_v2"
    assert audit.prompt.version == "2.1.0"
    assert (
        audit.prompt.sha256
        == sha256("只返回 VisualInterpretationV2 JSON。".encode()).hexdigest()
    )
    assert audit.model_id == "provider/model-version"
    assert audit.input_artifact_refs == (input_ref,)
    assert audit.raw_response_sha256 == sha256(raw_response.encode()).hexdigest()
    assert audit.visual_interpretation_ref is not None
    assert audit.visual_interpretation_sha256 == audit.visual_interpretation_ref.sha256
    assert audit.attempt_count == 2
    assert audit.repair_count == 1
    assert audit.parser_status == "succeeded"
    assert {
        "visual_interpretation_prompt",
        "visual_interpretation_raw_response",
        "visual_interpretation",
        "visual_interpretation_call_audit",
    }.issubset(_manifest_kinds(run))


def test_call_reloads_strictly_after_catalog_restart_and_is_deterministic(
    tmp_path: Path,
) -> None:
    run, catalog = _catalog(tmp_path, "run-reload")
    input_ref = _input_ref(catalog)
    interpretation = _interpretation(input_ref)

    def materialize() -> VisualInterpretationArtifactBundle:
        return materialize_visual_interpretation_call(
            catalog=catalog,
            run_id="run-reload",
            prompt_name="analyze_visual_layers_v2",
            prompt_version="2.1.0",
            prompt_text="frozen prompt",
            model_id="provider/model-version",
            input_artifact_refs=(input_ref,),
            raw_response=interpretation.model_dump_json(),
            attempt_count=1,
            repair_count=0,
            parser_status="succeeded",
            interpretation=interpretation,
        )

    first = materialize()
    second = materialize()
    replay = LocalArtifactCatalog(run, run_id="run-reload")
    restored = load_visual_interpretation_call(first.audit_ref, resolver=replay)

    assert second == first
    assert restored == first


def test_failed_parser_attempt_has_raw_audit_but_no_success_artifact(
    tmp_path: Path,
) -> None:
    run, catalog = _catalog(tmp_path, "run-failed")
    input_ref = _input_ref(catalog)

    bundle = materialize_visual_interpretation_call(
        catalog=catalog,
        run_id="run-failed",
        prompt_name="analyze_visual_layers_v2",
        prompt_version="2.1.0",
        prompt_text="frozen prompt",
        model_id="provider/model-version",
        input_artifact_refs=(input_ref,),
        raw_response="not-json",
        attempt_count=3,
        repair_count=2,
        parser_status="failed",
        parser_error_code="strict_json_invalid",
    )

    assert bundle.interpretation is None
    assert bundle.audit.parser_status == "failed"
    assert bundle.audit.parser_error_code == "strict_json_invalid"
    assert bundle.audit.visual_interpretation_ref is None
    assert bundle.audit.visual_interpretation_sha256 is None
    assert "visual_interpretation" not in _manifest_kinds(run)
    assert (
        load_visual_interpretation_call(
            bundle.audit_ref,
            resolver=catalog,
        )
        == bundle
    )


def test_success_status_rejects_raw_and_interpretation_mismatch_before_output(
    tmp_path: Path,
) -> None:
    run, catalog = _catalog(tmp_path, "run-mismatch")
    input_ref = _input_ref(catalog)
    interpretation = _interpretation(input_ref)
    different = interpretation.model_copy(update={"summary": "不同解释。"})

    with pytest.raises(ValueError, match="解析结果不一致"):
        materialize_visual_interpretation_call(
            catalog=catalog,
            run_id="run-mismatch",
            prompt_name="analyze_visual_layers_v2",
            prompt_version="2.1.0",
            prompt_text="frozen prompt",
            model_id="provider/model-version",
            input_artifact_refs=(input_ref,),
            raw_response=interpretation.model_dump_json(),
            attempt_count=1,
            repair_count=0,
            parser_status="succeeded",
            interpretation=different,
        )

    assert "visual_interpretation" not in _manifest_kinds(run)
    assert "visual_interpretation_call_audit" not in _manifest_kinds(run)


def test_reload_rejects_tampered_raw_response(tmp_path: Path) -> None:
    run, catalog = _catalog(tmp_path, "run-tamper")
    input_ref = _input_ref(catalog)
    interpretation = _interpretation(input_ref)
    bundle = materialize_visual_interpretation_call(
        catalog=catalog,
        run_id="run-tamper",
        prompt_name="analyze_visual_layers_v2",
        prompt_version="2.1.0",
        prompt_text="frozen prompt",
        model_id="provider/model-version",
        input_artifact_refs=(input_ref,),
        raw_response=interpretation.model_dump_json(),
        attempt_count=1,
        repair_count=0,
        parser_status="succeeded",
        interpretation=interpretation,
    )
    raw_ref = bundle.audit.raw_response_ref
    blob = run.path_for(f".artifact-catalog-v2/blobs/{raw_ref.artifact_id}.blob")
    blob.write_bytes(b"tampered")

    with pytest.raises(ArtifactIntegrityError):
        load_visual_interpretation_call(bundle.audit_ref, resolver=catalog)
