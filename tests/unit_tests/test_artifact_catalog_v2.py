from __future__ import annotations

import json
import re
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

import pytest

from shaderforge.store.artifact_catalog import (
    ArtifactCatalogError,
    ArtifactIntegrityError,
    LocalArtifactCatalog,
)
from shaderforge.store.artifacts_v2 import ArtifactRefV2
from shaderforge.store.legacy_artifact_adapter import LegacyArtifactRefAdapter
from shaderforge.store.local_artifacts import ArtifactRef, LocalArtifactStore


def _catalog(tmp_path: Path) -> tuple[LocalArtifactCatalog, object]:
    run = LocalArtifactStore(tmp_path).start_run("project-v2", "run-v2")
    return LocalArtifactCatalog(run, run_id="run-v2"), run


def test_catalog_round_trips_opaque_path_free_ref_with_stable_id(
    tmp_path: Path,
) -> None:
    catalog, _ = _catalog(tmp_path)

    first = catalog.put(
        run_id="run-v2",
        kind="intent",
        schema_version="intent_ir_v2",
        content_type="application/json",
        data=b'{"intent":1}',
    )
    second = catalog.put(
        run_id="run-v2",
        kind="intent",
        schema_version="intent_ir_v2",
        content_type="application/json",
        data=b'{"intent":1}',
    )

    assert first == second == catalog.resolve(first.artifact_id)
    assert catalog.read_bytes(first.artifact_id) == b'{"intent":1}'
    assert re.fullmatch(r"art_[0-9a-f]{64}", first.artifact_id)
    assert "intent" not in first.artifact_id
    assert "path" not in asdict(first)
    assert "uri" not in asdict(first)
    assert not hasattr(first, "relative_path")


def test_catalog_rejects_a_put_for_another_run_before_writing(tmp_path: Path) -> None:
    catalog, run = _catalog(tmp_path)

    with pytest.raises(ValueError, match="run_id.*不一致"):
        catalog.put(
            run_id="other-run",
            kind="render",
            schema_version="render_v2",
            content_type="image/png",
            data=b"png",
        )

    assert list(run.root.rglob("*.blob")) == []


def test_catalog_persists_atomic_run_manifest_and_recovers(tmp_path: Path) -> None:
    catalog, run = _catalog(tmp_path)
    ref = catalog.put(
        run_id="run-v2",
        kind="genome",
        schema_version="effect_genome_v0",
        content_type="application/json",
        data=b"{}",
    )

    manifest_path = run.path_for(".artifact-catalog-v2/manifest.json")
    manifest = json.loads(manifest_path.read_bytes())
    recovered = LocalArtifactCatalog(run, run_id="run-v2")

    assert manifest["run_id"] == "run-v2"
    assert manifest["revision"] == 1
    assert manifest["artifacts"][ref.artifact_id]["relative_path"].startswith(
        ".artifact-catalog-v2/blobs/"
    )
    assert recovered.resolve(ref.artifact_id) == ref
    assert list(manifest_path.parent.glob(".*.tmp")) == []


@pytest.mark.parametrize("tampered", (b"x", b"abc"))
def test_catalog_read_detects_size_or_sha_tampering(
    tmp_path: Path, tampered: bytes
) -> None:
    catalog, run = _catalog(tmp_path)
    ref = catalog.put(
        run_id="run-v2",
        kind="glsl",
        schema_version="glsl_v2",
        content_type="text/plain",
        data=b"ab",
    )
    manifest = json.loads(run.read_bytes(".artifact-catalog-v2/manifest.json"))
    relative_path = manifest["artifacts"][ref.artifact_id]["relative_path"]
    run.write_bytes(relative_path, tampered)

    with pytest.raises(ArtifactIntegrityError, match="size|SHA-256"):
        catalog.read_bytes(ref.artifact_id)


def test_catalog_rejects_manifest_bound_to_another_run(tmp_path: Path) -> None:
    catalog, run = _catalog(tmp_path)
    run.write_json(
        ".artifact-catalog-v2/manifest.json",
        {
            "schema_version": "artifact_catalog_manifest_v2",
            "run_id": "other-run",
            "revision": 0,
            "artifacts": {},
        },
    )

    with pytest.raises(ArtifactCatalogError, match="不属于当前 run"):
        catalog.resolve("art_" + "0" * 64)


@pytest.mark.parametrize(
    "raw_manifest",
    (
        (
            '{"schema_version":"artifact_catalog_manifest_v2",'
            '"schema_version":"artifact_catalog_manifest_v2",'
            '"run_id":"run-v2","revision":0,"artifacts":{}}'
        ),
        (
            '{"schema_version":"artifact_catalog_manifest_v2",'
            '"run_id":"run-v2","revision":0,"artifacts":{"art_'
            + "0" * 64
            + '":{"artifact_id":"art_'
            + "0" * 64
            + '","artifact_id":"art_'
            + "0" * 64
            + '","sha256":"'
            + "0" * 64
            + '","kind":"intent","schema_version":"intent_v2",'
            '"content_type":"application/json","size_bytes":0,'
            '"relative_path":".artifact-catalog-v2/blobs/art_' + "0" * 64 + '.blob"}}}'
        ),
    ),
)
def test_catalog_rejects_duplicate_json_keys(tmp_path: Path, raw_manifest: str) -> None:
    catalog, run = _catalog(tmp_path)
    run.write_text(
        ".artifact-catalog-v2/manifest.json",
        raw_manifest,
        content_type="application/json",
    )

    with pytest.raises(ArtifactCatalogError, match="重复 key"):
        catalog.resolve("art_" + "0" * 64)


def test_catalog_rejects_unknown_top_level_manifest_field(tmp_path: Path) -> None:
    catalog, run = _catalog(tmp_path)
    run.write_json(
        ".artifact-catalog-v2/manifest.json",
        {
            "schema_version": "artifact_catalog_manifest_v2",
            "run_id": "run-v2",
            "revision": 0,
            "artifacts": {},
            "storage_uri": "file:///should-not-be-accepted",
        },
    )

    with pytest.raises(ArtifactCatalogError, match="未知字段.*storage_uri"):
        catalog.resolve("art_" + "0" * 64)


def test_catalog_rejects_unknown_artifact_entry_field(tmp_path: Path) -> None:
    catalog, run = _catalog(tmp_path)
    ref = catalog.put(
        run_id="run-v2",
        kind="intent",
        schema_version="intent_v2",
        content_type="application/json",
        data=b"{}",
    )
    manifest = json.loads(run.read_bytes(".artifact-catalog-v2/manifest.json"))
    manifest["artifacts"][ref.artifact_id]["storage_uri"] = "file:///leak"
    run.write_json(".artifact-catalog-v2/manifest.json", manifest)

    with pytest.raises(ArtifactCatalogError, match="条目包含未知字段.*storage_uri"):
        catalog.resolve(ref.artifact_id)


@pytest.mark.parametrize(
    ("field_name", "tampered_value"),
    (
        ("kind", "tampered_kind"),
        ("schema_version", "tampered_schema"),
        ("content_type", "text/plain"),
        ("sha256", "0" * 64),
        ("size_bytes", 999),
    ),
)
def test_catalog_rejects_metadata_tampering_that_reuses_an_artifact_id(
    tmp_path: Path,
    field_name: str,
    tampered_value: str | int,
) -> None:
    catalog, run = _catalog(tmp_path)
    ref = catalog.put(
        run_id="run-v2",
        kind="intent",
        schema_version="intent_v2",
        content_type="application/json",
        data=b"{}",
    )
    manifest = json.loads(run.read_bytes(".artifact-catalog-v2/manifest.json"))
    manifest["artifacts"][ref.artifact_id][field_name] = tampered_value
    run.write_json(".artifact-catalog-v2/manifest.json", manifest)

    with pytest.raises(ArtifactCatalogError, match="元数据与 artifact_id 不一致"):
        catalog.resolve(ref.artifact_id)


def test_artifact_ref_v2_rejects_path_like_id_and_bad_integrity_metadata() -> None:
    with pytest.raises(ValueError, match="opaque"):
        ArtifactRefV2(
            artifact_id="../artifact",
            sha256="0" * 64,
            kind="intent",
            schema_version="intent_v2",
            content_type="application/json",
            size_bytes=1,
        )
    with pytest.raises(ValueError, match="sha256"):
        ArtifactRefV2(
            artifact_id="artifact",
            sha256="bad",
            kind="intent",
            schema_version="intent_v2",
            content_type="application/json",
            size_bytes=1,
        )


def test_legacy_adapter_reads_original_bytes_without_copying(tmp_path: Path) -> None:
    run = LocalArtifactStore(tmp_path).start_run("project-v1", "run-v1")
    legacy_ref = run.write_bytes(
        "final/render.png", b"legacy-png", content_type="image/png"
    )
    before = sorted(path.relative_to(run.root) for path in run.root.rglob("*"))
    adapter = LegacyArtifactRefAdapter(run, run_id="run-v1")

    v2_ref = adapter.adapt(
        legacy_ref,
        kind="final_render",
        schema_version="legacy_artifact_v1",
    )
    after = sorted(path.relative_to(run.root) for path in run.root.rglob("*"))

    assert adapter.resolve(v2_ref.artifact_id) == v2_ref
    assert adapter.read_bytes(v2_ref.artifact_id) == b"legacy-png"
    assert re.fullmatch(r"legacy_[0-9a-f]{64}", v2_ref.artifact_id)
    assert not hasattr(adapter, "put")
    assert after == before


@pytest.mark.parametrize(
    "legacy_ref",
    (
        ArtifactRef(
            relative_path="final/render.png",
            sha256=sha256(b"legacy-png").hexdigest(),
            size_bytes=999,
            content_type="image/png",
        ),
        ArtifactRef(
            relative_path="final/render.png",
            sha256="0" * 64,
            size_bytes=len(b"legacy-png"),
            content_type="image/png",
        ),
    ),
)
def test_legacy_adapter_validates_v1_ref_against_bytes(
    tmp_path: Path, legacy_ref: ArtifactRef
) -> None:
    run = LocalArtifactStore(tmp_path).start_run("project-v1", "run-v1")
    run.write_bytes("final/render.png", b"legacy-png", content_type="image/png")
    adapter = LegacyArtifactRefAdapter(run, run_id="run-v1")

    with pytest.raises(ArtifactIntegrityError, match="size|SHA-256"):
        adapter.adapt(
            legacy_ref,
            kind="final_render",
            schema_version="legacy_artifact_v1",
        )


def test_legacy_adapter_revalidates_bytes_on_each_read(tmp_path: Path) -> None:
    run = LocalArtifactStore(tmp_path).start_run("project-v1", "run-v1")
    legacy_ref = run.write_text("final/shader.frag", "old")
    adapter = LegacyArtifactRefAdapter(run, run_id="run-v1")
    v2_ref = adapter.adapt(
        legacy_ref,
        kind="glsl",
        schema_version="legacy_artifact_v1",
    )
    run.write_text("final/shader.frag", "new")

    with pytest.raises(ArtifactIntegrityError, match="SHA-256"):
        adapter.read_bytes(v2_ref.artifact_id)
