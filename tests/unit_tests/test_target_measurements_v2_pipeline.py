from __future__ import annotations

from collections.abc import Callable
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from shaderforge.analysis import (
    RadialSegmentStructureEvidenceV1,
    TargetMeasurementsV2,
    classify_instance_mask_topology_v2,
    compute_target_hypothesis_hash,
    measure_target_v2,
    verify_radial_segment_structure_evidence_v1,
)
from shaderforge.store import (
    ArtifactIntegrityError,
    LocalArtifactCatalog,
    LocalArtifactStore,
    RunArtifactStore,
)


def _png(draw_image: Callable[[ImageDraw.ImageDraw], None]) -> bytes:
    image = Image.new("RGB", (32, 32), (255, 255, 255))
    draw_image(ImageDraw.Draw(image))
    output = BytesIO()
    image.save(output, format="PNG", compress_level=9)
    return output.getvalue()


def _rgba_png(
    draw_image: Callable[[ImageDraw.ImageDraw], None],
    *,
    size: int = 64,
) -> bytes:
    image = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw_image(ImageDraw.Draw(image))
    output = BytesIO()
    image.save(output, format="PNG", compress_level=9)
    return output.getvalue()


def _catalog(
    tmp_path: Path,
    run_id: str,
) -> tuple[RunArtifactStore, LocalArtifactCatalog]:
    run = LocalArtifactStore(tmp_path).start_run("project-v2", run_id)
    return run, LocalArtifactCatalog(run, run_id=run_id)


def test_instance_topology_classifier_distinguishes_ring_hollow_open_solid() -> None:
    size = 64
    center = (size - 1) / 2

    def classify(active: set[tuple[int, int]]) -> str:
        mask = tuple((x, y) in active for y in range(size) for x in range(size))
        return classify_instance_mask_topology_v2(mask, width=size, height=size)

    disk = {
        (x, y)
        for y in range(size)
        for x in range(size)
        if (x - center) ** 2 + (y - center) ** 2 <= 24**2
    }
    ring = {(x, y) for x, y in disk if (x - center) ** 2 + (y - center) ** 2 >= 12**2}
    hollow = disk - {(x, y) for y in range(30, 34) for x in range(30, 34)}
    opened = {
        (x, y) for x, y in ring if not (x >= int(center) and abs(y - center) <= 3)
    }

    assert classify(disk) == "solid"
    assert classify(ring) == "ring"
    assert classify(hollow) == "hollow"
    assert classify(opened) == "open"


def _mask_pixels(data: bytes) -> set[tuple[int, int]]:
    with Image.open(BytesIO(data)) as image:
        width, _height = image.size
        return {
            (index % width, index // width)
            for index, value in enumerate(image.convert("L").tobytes())
            if value == 255
        }


def test_measure_target_v2_materializes_solid_measurements_and_evidence(
    tmp_path: Path,
) -> None:
    source = _png(lambda draw: draw.rectangle((8, 8, 23, 23), fill=(20, 80, 210)))
    _run, catalog = _catalog(tmp_path, "run-solid")

    bundle = measure_target_v2(source, catalog=catalog, run_id="run-solid")

    target = bundle.measurements
    primary = target.target_hypotheses[0]
    assert primary.fill_topology == "solid"
    assert primary.component_count == 1
    assert primary.instance_count == 1
    assert primary.hole_count == 0
    assert tuple(item.fill_topology for item in primary.instance_geometries) == (
        "solid",
    )
    assert primary.area_ratio == pytest.approx(0.25)
    assert primary.center_uv == pytest.approx((0.5, 0.5))
    assert target.image_size == (32, 32)
    assert target.palette_lab
    assert target.region_statistics[0].region_id == "full_normalized_image"
    assert target.symmetry.horizontal == pytest.approx(1.0)
    assert target.symmetry.vertical == pytest.approx(1.0)
    assert target.gradient_evidence
    assert bundle.uncertainty.hard_constraint_policy == "verification_required"

    persisted = TargetMeasurementsV2.model_validate_json(
        catalog.read_bytes(bundle.measurements_ref.artifact_id),
        strict=True,
    )
    assert persisted == target
    assert catalog.read_bytes(bundle.target_source_ref.artifact_id) == source
    assert catalog.read_bytes(bundle.normalized_reference_ref.artifact_id)
    assert catalog.read_bytes(bundle.evidence_index_ref.artifact_id)
    for artifact_set in bundle.hypothesis_artifacts:
        assert catalog.read_bytes(artifact_set.subject_mask_ref.artifact_id)
        assert catalog.read_bytes(artifact_set.edge_ref.artifact_id)
        for instance_ref in artifact_set.instance_mask_refs:
            assert catalog.read_bytes(instance_ref.artifact_id)


def test_measure_target_v2_recomputes_ring_hole(tmp_path: Path) -> None:
    def draw_ring(draw: ImageDraw.ImageDraw) -> None:
        draw.ellipse((5, 5, 26, 26), fill=(30, 30, 30))
        draw.ellipse((11, 11, 20, 20), fill=(255, 255, 255))

    _run, catalog = _catalog(tmp_path, "run-ring")
    bundle = measure_target_v2(
        _png(draw_ring),
        catalog=catalog,
        run_id="run-ring",
    )

    primary = bundle.measurements.target_hypotheses[0]
    assert primary.fill_topology == "ring"
    assert primary.component_count == 1
    assert primary.instance_count == 1
    assert primary.hole_count == 1
    assert primary.instance_geometries[0].fill_topology == "ring"
    filled = next(
        item
        for item in bundle.measurements.target_hypotheses
        if item.hypothesis_id == "hypothesis_rgb_holes_filled_solid"
    )
    assert filled.fill_topology == "solid"
    assert filled.component_count == 1
    assert filled.instance_count == 1
    assert filled.hole_count == 0
    assert filled.instance_geometries[0].fill_topology == "solid"


def test_measure_target_v2_retains_connected_dominant_color_instance_partition(
    tmp_path: Path,
) -> None:
    def draw_overlapping_disks(draw: ImageDraw.ImageDraw) -> None:
        draw.ellipse((3, 7, 19, 24), fill=(245, 70, 105))
        draw.ellipse((13, 7, 29, 24), fill=(55, 135, 245))

    source = _png(draw_overlapping_disks)
    _run, catalog = _catalog(tmp_path, "run-color-instances")
    bundle = measure_target_v2(
        source,
        catalog=catalog,
        run_id="run-color-instances",
    )

    primary = bundle.measurements.target_hypotheses[0]
    assert primary.component_count == 1
    assert primary.instance_count == 1
    partition = next(
        item
        for item in bundle.measurements.target_hypotheses
        if item.hypothesis_id == "hypothesis_dominant_color_instances"
    )
    assert partition.fill_topology == "solid"
    assert partition.component_count == 1
    assert partition.instance_count == 2
    assert partition.hole_count == 0
    assert len(partition.relations) == 1
    assert partition.relations[0].kind == "touches"


def test_measure_target_v2_uses_source_alpha_and_retains_segmented_ring_hypotheses(
    tmp_path: Path,
) -> None:
    def draw_segments(draw: ImageDraw.ImageDraw) -> None:
        for index in range(12):
            start = index * 30 + 4
            draw.arc(
                (6, 6, 57, 57),
                start=start,
                end=start + 22,
                fill=(255, 255, 255, 255),
                width=8,
            )

    source = _rgba_png(draw_segments)
    _run, catalog = _catalog(tmp_path, "run-alpha-segmented-ring")
    bundle = measure_target_v2(
        source,
        catalog=catalog,
        run_id="run-alpha-segmented-ring",
    )

    literal, semantic = bundle.measurements.target_hypotheses
    assert literal.fill_topology == "open"
    assert literal.component_count == 12
    assert literal.instance_count == 12
    assert literal.hole_count == 0
    assert semantic.fill_topology == "ring"
    assert semantic.component_count == 1
    assert semantic.instance_count == 12
    assert semantic.hole_count == 1
    assert {item.fill_topology for item in literal.instance_geometries} == {"solid"}
    assert {item.fill_topology for item in semantic.instance_geometries} == {"solid"}
    assert {item.kind for item in semantic.relations} == {"touches", "disjoint"}
    assert bundle.uncertainty.low_confidence
    assert bundle.uncertainty.alternate_hypothesis_ids == (
        "hypothesis_semantic_radial_ring",
    )
    assert bundle.measurements.region_statistics[0].region_id == (
        "source_visible_alpha"
    )
    assert semantic.radial_segment_evidence_ref is not None
    evidence = verify_radial_segment_structure_evidence_v1(
        semantic.radial_segment_evidence_ref,
        resolver=catalog,
    )
    assert len(evidence.segments) == 12
    assert len(evidence.raw_relations) == 66
    assert tuple(item.ownership_mask_ref for item in evidence.segments) == (
        semantic.instance_mask_refs
    )
    assert all(item.raw_fill_topology == "solid" for item in evidence.segments)
    assert all(0.0 < item.angular_span_rad < 30.0 / 180.0 * 3.1416 for item in evidence.segments)


@pytest.mark.parametrize("segment_count", [12, 18])
def test_radial_segment_evidence_replays_12_and_18_segments(
    tmp_path: Path,
    segment_count: int,
) -> None:
    step = 360.0 / segment_count

    def draw_segments(draw: ImageDraw.ImageDraw) -> None:
        for index in range(segment_count):
            start = index * step + step * 0.14
            draw.arc(
                (6, 6, 57, 57),
                start=start,
                end=start + step * 0.70,
                fill=(80, 210, 255, 255),
                width=8,
            )

    run_id = f"run-segments-{segment_count}"
    _run, catalog = _catalog(tmp_path, run_id)
    bundle = measure_target_v2(
        _rgba_png(draw_segments),
        catalog=catalog,
        run_id=run_id,
    )
    semantic = next(
        item
        for item in bundle.measurements.target_hypotheses
        if item.radial_segment_evidence_ref is not None
    )
    assert semantic.instance_count == segment_count
    assert semantic.radial_segment_evidence_ref is not None
    evidence = verify_radial_segment_structure_evidence_v1(
        semantic.radial_segment_evidence_ref,
        resolver=catalog,
    )
    assert len(evidence.segments) == segment_count
    assert len(evidence.raw_relations) == segment_count * (segment_count - 1) // 2
    assert all(item.raw_is_subset_of_ownership for item in evidence.segments)


def test_radial_segment_evidence_rejects_rehashed_geometry_tampering(
    tmp_path: Path,
) -> None:
    def draw_segments(draw: ImageDraw.ImageDraw) -> None:
        for index in range(12):
            draw.arc(
                (6, 6, 57, 57),
                start=index * 30 + 4,
                end=index * 30 + 26,
                fill=(255, 255, 255, 255),
                width=8,
            )

    _run, catalog = _catalog(tmp_path, "run-segment-tamper")
    bundle = measure_target_v2(
        _rgba_png(draw_segments),
        catalog=catalog,
        run_id="run-segment-tamper",
    )
    semantic = next(
        item
        for item in bundle.measurements.target_hypotheses
        if item.radial_segment_evidence_ref is not None
    )
    assert semantic.radial_segment_evidence_ref is not None
    evidence = RadialSegmentStructureEvidenceV1.model_validate_json(
        catalog.read_bytes(semantic.radial_segment_evidence_ref.artifact_id),
        strict=True,
    )
    first = evidence.segments[0]
    changed_center = (first.angular_center_rad + 0.05) % (2.0 * 3.141592653589793)
    changed = evidence.model_copy(
        update={
            "segments": (
                first.model_copy(update={"angular_center_rad": changed_center}),
                *evidence.segments[1:],
            )
        }
    )
    changed_ref = catalog.put(
        run_id="run-segment-tamper",
        kind="radial_segment_structure_evidence",
        schema_version="radial_segment_structure_evidence_v1",
        content_type="application/json",
        data=changed.model_dump_json().encode("utf-8"),
    )
    rebound_draft = semantic.model_copy(
        update={
            "hypothesis_hash": "0" * 64,
            "radial_segment_evidence_ref": changed_ref,
            "evidence_refs": tuple(
                changed_ref
                if item == semantic.radial_segment_evidence_ref
                else item
                for item in semantic.evidence_refs
            ),
        }
    )
    assert compute_target_hypothesis_hash(
        bundle.measurements.target_sha256,
        rebound_draft,
    ) != semantic.hypothesis_hash
    with pytest.raises(ValueError, match="重建结果不一致"):
        verify_radial_segment_structure_evidence_v1(
            changed_ref,
            resolver=catalog,
        )


def test_measure_target_v2_filters_tiny_alpha_fragments_and_noise_holes(
    tmp_path: Path,
) -> None:
    def draw_noisy_subject(draw: ImageDraw.ImageDraw) -> None:
        draw.ellipse((10, 10, 53, 53), fill=(255, 90, 10, 255))
        draw.point((31, 31), fill=(255, 255, 255, 0))
        draw.point((1, 1), fill=(255, 90, 10, 255))

    _run, catalog = _catalog(tmp_path, "run-alpha-noise")
    bundle = measure_target_v2(
        _rgba_png(draw_noisy_subject),
        catalog=catalog,
        run_id="run-alpha-noise",
    )

    primary = bundle.measurements.target_hypotheses[0]
    assert primary.fill_topology == "solid"
    assert primary.component_count == 1
    assert primary.instance_count == 1
    assert primary.hole_count == 0


def test_measure_target_v2_keeps_all_components_as_disjoint_instances(
    tmp_path: Path,
) -> None:
    def draw_pair(draw: ImageDraw.ImageDraw) -> None:
        draw.rectangle((3, 7, 11, 20), fill=(220, 40, 60))
        draw.rectangle((20, 10, 28, 23), fill=(30, 120, 210))

    _run, catalog = _catalog(tmp_path, "run-multi")
    bundle = measure_target_v2(
        _png(draw_pair),
        catalog=catalog,
        run_id="run-multi",
    )

    primary = bundle.measurements.target_hypotheses[0]
    artifacts = bundle.hypothesis_artifacts[0]
    assert primary.component_count == 2
    assert primary.instance_count == 2
    assert len(primary.relations) == 1
    assert primary.relations[0].kind == "disjoint"
    subject = _mask_pixels(catalog.read_bytes(artifacts.subject_mask_ref.artifact_id))
    instances = [
        _mask_pixels(catalog.read_bytes(ref.artifact_id))
        for ref in artifacts.instance_mask_refs
    ]
    assert instances[0].isdisjoint(instances[1])
    assert instances[0] | instances[1] == subject


def test_measure_target_v2_is_deterministic_and_low_confidence_is_soft_only(
    tmp_path: Path,
) -> None:
    source = _png(lambda draw: draw.rectangle((8, 8, 23, 23), fill=(235, 235, 235)))
    _run, catalog = _catalog(tmp_path, "run-deterministic")

    first = measure_target_v2(
        source,
        catalog=catalog,
        run_id="run-deterministic",
    )
    second = measure_target_v2(
        source,
        catalog=catalog,
        run_id="run-deterministic",
    )

    assert second == first
    assert first.uncertainty.low_confidence
    assert first.uncertainty.hard_constraint_policy == "soft_only"
    assert first.uncertainty.strategy in {
        "alternate_hypothesis_retained",
        "soft_only_manual_review",
    }
    if first.uncertainty.strategy == "alternate_hypothesis_retained":
        assert len(first.measurements.target_hypotheses) >= 2
        assert first.uncertainty.alternate_hypothesis_ids
    else:
        assert "manual_or_model_segmentation_required" in first.uncertainty.reason_codes


def test_measurements_artifacts_replay_after_catalog_restart_and_detect_tampering(
    tmp_path: Path,
) -> None:
    source = _png(lambda draw: draw.rectangle((6, 6, 25, 25), fill=(0, 140, 90)))
    run, catalog = _catalog(tmp_path, "run-replay")
    bundle = measure_target_v2(source, catalog=catalog, run_id="run-replay")

    replay = LocalArtifactCatalog(run, run_id="run-replay")
    restored = TargetMeasurementsV2.model_validate_json(
        replay.read_bytes(bundle.measurements_ref.artifact_id),
        strict=True,
    )
    assert restored == bundle.measurements

    blob_path = run.path_for(
        f".artifact-catalog-v2/blobs/{bundle.measurements_ref.artifact_id}.blob"
    )
    blob_path.write_bytes(b"tampered")
    with pytest.raises(ArtifactIntegrityError):
        replay.read_bytes(bundle.measurements_ref.artifact_id)
