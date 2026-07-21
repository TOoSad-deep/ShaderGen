from __future__ import annotations

import json
import shutil
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from shaderforge.benchmark import (
    LoadedV2Dataset,
    V2DatasetReadiness,
)
from shaderforge.benchmark import (
    evaluate_v2_dataset_readiness as public_evaluate_v2_dataset_readiness,
)
from shaderforge.benchmark import (
    load_v2_dataset_manifest as public_load_v2_dataset_manifest,
)
from shaderforge.benchmark.v2_dataset import (
    CRITICAL_CLASS_IDS,
    INITIAL_GENOME_NODE_KINDS,
    V2DatasetManifest,
    evaluate_v2_dataset_readiness,
    evaluate_v2_dataset_stage_gate,
    load_v2_dataset_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_ROOT = ROOT / "benchmarks"
MANIFEST = BENCHMARK_ROOT / "png_to_shader_v2/dataset_manifest.v1.json"


def _copy_benchmark(tmp_path: Path) -> tuple[Path, Path]:
    benchmark_root = tmp_path / "benchmarks"
    shutil.copytree(
        BENCHMARK_ROOT / "png_to_shader_v2",
        benchmark_root / "png_to_shader_v2",
    )
    shutil.copytree(
        BENCHMARK_ROOT / "png_to_shader_v1/images",
        benchmark_root / "png_to_shader_v1/images",
    )
    return benchmark_root, benchmark_root / "png_to_shader_v2/dataset_manifest.v1.json"


def _read_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_manifest(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_v2_dataset_loads_regression_and_visible_validation_without_release_leakage() -> None:
    dataset = load_v2_dataset_manifest(MANIFEST, benchmark_root=BENCHMARK_ROOT)

    development = dataset.manifest.split("development")
    validation = dataset.manifest.split("validation")
    release = dataset.manifest.split("release-held-out")
    assert len(development.samples) == 10
    assert {item.dataset_role for item in development.samples} == {"regression"}
    assert {item.source_suite_id for item in development.samples} == {
        "png_to_shader_v1_m0"
    }
    assert validation.status == "available"
    assert len(validation.samples) == 41
    assert {item.dataset_role for item in validation.samples} == {"evaluation"}
    assert {item.source_suite_id for item in validation.samples} == {
        "freegameui_cc0_validation_v1",
        "freegameui_cc0_complex_validation_v1",
        "oga_cc0_complex_validation_v1",
    }
    assert validation.access_policy == "visible_validation"
    assert release.status == "not_populated" and not release.samples
    assert release.access_policy == "sealed_release_test"
    assert {item.source_suite_id for item in dataset.manifest.source_records} == {
        "png_to_shader_v1_m0",
        "freegameui_cc0_validation_v1",
        "freegameui_cc0_complex_validation_v1",
        "oga_cc0_complex_validation_v1",
    }
    assert all(
        dataset.resolve_image(sample).is_relative_to(BENCHMARK_ROOT.resolve())
        for sample in (*development.samples, *validation.samples)
    )

    covered_nodes = {item.node_kind for item in dataset.taxonomy.primitives}
    assert INITIAL_GENOME_NODE_KINDS <= covered_nodes
    assert {
        primitive
        for sample in development.samples
        for primitive in sample.expected_primitives.items
    } <= dataset.taxonomy.primitive_ids


def test_v2_dataset_readiness_reports_actual_insufficient_denominators() -> None:
    dataset = load_v2_dataset_manifest(MANIFEST, benchmark_root=BENCHMARK_ROOT)

    readiness = evaluate_v2_dataset_readiness(dataset)

    assert not readiness.ready
    assert readiness.validation_ready
    assert not readiness.release_held_out_ready
    development = readiness.split("development")
    assert {
        item.class_id: item.actual_denominator for item in development.critical_classes
    } == {
        "multi_instance": 1,
        "ring": 1,
        "hollow": 0,
        "required_highlight": 2,
        "required_rim": 3,
        "required_outline": 1,
    }
    validation = readiness.split("validation")
    assert validation.ready_for_gate
    assert validation.sample_count == 41
    assert {
        item.class_id: item.actual_denominator for item in validation.critical_classes
    } == {
        "multi_instance": 11,
        "ring": 20,
        "hollow": 10,
        "required_highlight": 16,
        "required_rim": 26,
        "required_outline": 36,
    }
    assert all(item.minimum_denominator == 10 for item in validation.critical_classes)
    assert all(item.sufficient for item in validation.critical_classes)

    release = readiness.split("release-held-out")
    assert not release.ready_for_gate
    assert release.sample_count == 0
    assert tuple(item.class_id for item in release.critical_classes) == CRITICAL_CLASS_IDS
    assert all(item.actual_denominator == 0 for item in release.critical_classes)
    assert all(item.minimum_denominator == 10 for item in release.critical_classes)
    assert all(not item.sufficient for item in release.critical_classes)


def test_v2_dataset_stage_gate_keeps_release_sealed_until_v2_3() -> None:
    intent_dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=BENCHMARK_ROOT,
        gate_stage="v2_1_intent",
    )
    compiler_dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=BENCHMARK_ROOT,
        gate_stage="v2_2_genome_compiler",
    )
    graph_dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=BENCHMARK_ROOT,
        gate_stage="v2_3_graph_conformance",
    )
    release_dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=BENCHMARK_ROOT,
        gate_stage="v2_3_release_candidate",
    )

    intent_gate = evaluate_v2_dataset_stage_gate(
        intent_dataset,
        stage="v2_1_intent",
    )
    compiler_gate = evaluate_v2_dataset_stage_gate(
        compiler_dataset,
        stage="v2_2_genome_compiler",
    )
    graph_gate = evaluate_v2_dataset_stage_gate(
        graph_dataset,
        stage="v2_3_graph_conformance",
    )
    release_gate = evaluate_v2_dataset_stage_gate(
        release_dataset,
        stage="v2_3_release_candidate",
    )

    assert intent_gate.ready
    assert intent_gate.manifest_id == intent_dataset.manifest.manifest_id
    assert intent_gate.dataset_version == intent_dataset.manifest.dataset_version
    assert intent_gate.manifest_sha256 == sha256(MANIFEST.read_bytes()).hexdigest()
    assert (
        intent_gate.taxonomy_sha256
        == intent_dataset.manifest.expected_primitives_taxonomy.sha256
    )
    assert intent_gate.required_splits == ("validation",)
    assert intent_gate.blockers == ()
    assert compiler_gate.ready
    assert compiler_gate.required_splits == ("validation",)
    assert graph_gate.ready
    assert graph_gate.required_splits == ("validation",)
    assert graph_gate.blockers == ()
    assert not release_gate.ready
    assert release_gate.required_splits == ("validation", "release-held-out")
    assert "release-held-out:split_status:not_populated" in release_gate.blockers
    assert all(blocker.startswith("release-held-out:") for blocker in release_gate.blockers)


def test_v2_dataset_stage_gate_rejects_unverified_or_stale_inputs(
    tmp_path: Path,
) -> None:
    dataset = load_v2_dataset_manifest(MANIFEST, benchmark_root=BENCHMARK_ROOT)
    readiness = evaluate_v2_dataset_readiness(dataset)

    with pytest.raises(TypeError, match="只接受 load_v2_dataset_manifest"):
        evaluate_v2_dataset_stage_gate(  # type: ignore[arg-type]
            readiness,
            stage="v2_1_intent",
        )

    benchmark_root, manifest_path = _copy_benchmark(tmp_path)
    copied = load_v2_dataset_manifest(
        manifest_path,
        benchmark_root=benchmark_root,
        gate_stage="v2_1_intent",
    )
    manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Manifest/taxonomy"):
        evaluate_v2_dataset_stage_gate(copied, stage="v2_1_intent")


def test_v2_dataset_stage_gate_rejects_unknown_stage() -> None:
    dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=BENCHMARK_ROOT,
        gate_stage="v2_1_intent",
    )

    with pytest.raises(ValueError, match="不支持的 V2 dataset gate stage"):
        evaluate_v2_dataset_stage_gate(
            dataset,
            stage="v2_4_unknown",  # type: ignore[arg-type]
        )


def test_v2_dataset_rejects_unknown_fields_and_duplicate_json_keys(
    tmp_path: Path,
) -> None:
    benchmark_root, manifest_path = _copy_benchmark(tmp_path)
    manifest = _read_manifest(manifest_path)
    manifest["unexpected"] = True
    _write_manifest(manifest_path, manifest)

    with pytest.raises(ValidationError, match="unexpected"):
        load_v2_dataset_manifest(manifest_path, benchmark_root=benchmark_root)

    benchmark_root, manifest_path = _copy_benchmark(tmp_path / "duplicate")
    raw = manifest_path.read_text(encoding="utf-8")
    raw = raw.replace(
        '  "manifest_id":',
        '  "manifest_id": "duplicate",\n  "manifest_id":',
        1,
    )
    manifest_path.write_text(raw, encoding="utf-8")

    with pytest.raises(ValueError, match="JSON 字段不得重复：manifest_id"):
        load_v2_dataset_manifest(manifest_path, benchmark_root=benchmark_root)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("image", "../outside.png", "越过 benchmark 根目录"),
        ("sha256", "0" * 64, "SHA-256"),
        ("resolution", [1, 1], "尺寸"),
    ],
)
def test_v2_dataset_validates_safe_image_identity_and_size(
    tmp_path: Path,
    field: str,
    replacement: object,
    message: str,
) -> None:
    benchmark_root, manifest_path = _copy_benchmark(tmp_path)
    manifest = _read_manifest(manifest_path)
    manifest["splits"][0]["samples"][0][field] = replacement
    _write_manifest(manifest_path, manifest)

    with pytest.raises(ValueError, match=message):
        load_v2_dataset_manifest(manifest_path, benchmark_root=benchmark_root)


def test_v2_dataset_rejects_cross_split_hash_group_near_duplicate(
    tmp_path: Path,
) -> None:
    benchmark_root, manifest_path = _copy_benchmark(tmp_path)
    manifest = _read_manifest(manifest_path)
    duplicate = dict(manifest["splits"][0]["samples"][0])
    duplicate.update(
        {
            "case_id": "curated_solid_circle",
            "dataset_role": "evaluation",
            "source_suite_id": "curated_v2",
            "image": "png_to_shader_v2/curated_solid_circle.png",
        }
    )
    shutil.copyfile(
        benchmark_root / "png_to_shader_v1/images/solid_circle.png",
        benchmark_root / "png_to_shader_v2/curated_solid_circle.png",
    )
    manifest["splits"][1].update(status="available", samples=[duplicate])
    _write_manifest(manifest_path, manifest)

    with pytest.raises(ValidationError, match="hash_group 不得跨 split"):
        load_v2_dataset_manifest(manifest_path, benchmark_root=benchmark_root)


def test_v2_dataset_rejects_visual_family_cross_split_even_with_new_content() -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sample = dict(raw["splits"][0]["samples"][0])
    sample.update(
        case_id="validation_same_visual_family",
        dataset_role="evaluation",
        source_suite_id="curated_v2",
        image="png_to_shader_v2/validation_same_visual_family.png",
        sha256="f" * 64,
        hash_group="validation.unique-content",
    )
    raw["splits"][1].update(status="available", samples=[sample])

    with pytest.raises(ValidationError, match="visual_family 不得跨 split"):
        V2DatasetManifest.model_validate_json(json.dumps(raw), strict=True)


def test_v2_dataset_rejects_v1_sample_in_release_held_out(
    tmp_path: Path,
) -> None:
    benchmark_root, manifest_path = _copy_benchmark(tmp_path)
    manifest = _read_manifest(manifest_path)
    v1_sample = dict(manifest["splits"][0]["samples"][0])
    v1_sample.update(
        {
            "case_id": "release_solid_circle",
            "dataset_role": "evaluation",
            "source_suite_id": "curated_v2",
            "image": "png_to_shader_v2/release_solid_circle.png",
            "hash_group": "release.solid_circle",
            "visual_family": "release.solid_circle",
        }
    )
    shutil.copyfile(
        benchmark_root / "png_to_shader_v1/images/solid_circle.png",
        benchmark_root / "png_to_shader_v2/release_solid_circle.png",
    )
    manifest["splits"][2].update(status="available", samples=[v1_sample])
    _write_manifest(manifest_path, manifest)

    with pytest.raises(ValidationError, match="V1 样本只能登记"):
        load_v2_dataset_manifest(manifest_path, benchmark_root=benchmark_root)


def test_v2_dataset_rejects_unknown_primitive_and_taxonomy_hash_drift(
    tmp_path: Path,
) -> None:
    benchmark_root, manifest_path = _copy_benchmark(tmp_path)
    manifest = _read_manifest(manifest_path)
    manifest["splits"][0]["samples"][0]["expected_primitives"]["items"].append(
        "unregistered_primitive"
    )
    _write_manifest(manifest_path, manifest)

    with pytest.raises(ValueError, match="taxonomy 未登记 primitive"):
        load_v2_dataset_manifest(manifest_path, benchmark_root=benchmark_root)

    benchmark_root, manifest_path = _copy_benchmark(tmp_path / "taxonomy")
    taxonomy_path = (
        benchmark_root / "png_to_shader_v2/expected_primitives_taxonomy.v1.json"
    )
    taxonomy_path.write_text(
        taxonomy_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="taxonomy SHA-256"):
        load_v2_dataset_manifest(manifest_path, benchmark_root=benchmark_root)


def test_v2_dataset_rejects_source_or_license_record_drift(tmp_path: Path) -> None:
    benchmark_root, manifest_path = _copy_benchmark(tmp_path)
    source_path = benchmark_root / "png_to_shader_v2/sources/complex_validation.v1.md"
    source_path.write_bytes(source_path.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="来源/许可记录 SHA-256"):
        load_v2_dataset_manifest(manifest_path, benchmark_root=benchmark_root)


def test_v2_dataset_requires_all_three_splits_in_frozen_order() -> None:
    raw = json.loads(MANIFEST.read_text(encoding="utf-8"))
    raw["splits"] = list(reversed(raw["splits"]))

    with pytest.raises(ValidationError, match="splits 必须按"):
        V2DatasetManifest.model_validate_json(json.dumps(raw), strict=True)


def test_v2_dataset_taxonomy_reference_is_content_addressed() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    taxonomy_path = BENCHMARK_ROOT / manifest["expected_primitives_taxonomy"]["path"]
    assert (
        sha256(taxonomy_path.read_bytes()).hexdigest()
        == manifest["expected_primitives_taxonomy"]["sha256"]
    )


@pytest.mark.parametrize(
    ("field_name", "replacement", "message"),
    (
        ("node_kind", "unknown_node", "node_kind 未登记"),
        ("node_version", "999", "node_version.*不一致"),
    ),
)
def test_v2_dataset_taxonomy_matches_registry_kind_and_version_exactly(
    tmp_path: Path,
    field_name: str,
    replacement: str,
    message: str,
) -> None:
    benchmark_root, manifest_path = _copy_benchmark(tmp_path)
    manifest = _read_manifest(manifest_path)
    taxonomy_path = (
        benchmark_root / "png_to_shader_v2/expected_primitives_taxonomy.v1.json"
    )
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    taxonomy["primitives"][0][field_name] = replacement
    taxonomy_path.write_text(
        json.dumps(taxonomy, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest["expected_primitives_taxonomy"]["sha256"] = sha256(
        taxonomy_path.read_bytes()
    ).hexdigest()
    _write_manifest(manifest_path, manifest)

    with pytest.raises(ValueError, match=message):
        load_v2_dataset_manifest(manifest_path, benchmark_root=benchmark_root)


def test_v2_dataset_loader_and_readiness_are_exported_from_public_package() -> None:
    dataset = public_load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=BENCHMARK_ROOT,
    )
    readiness = public_evaluate_v2_dataset_readiness(dataset)

    assert isinstance(dataset, LoadedV2Dataset)
    assert isinstance(readiness, V2DatasetReadiness)
