from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from PIL import Image

from scripts.run_ultra_dataset_benchmark import (
    EXPECTED_PRESET,
    EXPECTED_SCHEMA,
    _load_cases,
    _multipart_body,
    _summary,
)

ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = ROOT / "dataset"
MANIFEST = ROOT / "benchmarks/ultra_dataset/manifest.yaml"


def test_repository_manifest_covers_dataset_png_files_once() -> None:
    document, cases = _load_cases(MANIFEST)

    assert document["schema_version"] == EXPECTED_SCHEMA
    assert document["quality_preset"] == EXPECTED_PRESET
    assert document["suite_id"] == "shadergen-ultra-dataset-v1"
    assert len(cases) == 16
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert {Path(case["image_path"]) for case in cases} == set(
        DATASET_ROOT.glob("*.png")
    )

    for case in cases:
        image_path = Path(case["image_path"])
        image_bytes = image_path.read_bytes()
        assert case["image_bytes"] == len(image_bytes)
        assert case["image_sha256"] == sha256(image_bytes).hexdigest()
        with Image.open(image_path) as image:
            image.load()
            assert image.format == "PNG"
            assert image.mode == "RGB"
            assert image.size == (1280, 720)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda value: value.update(schema_version="unknown"), "schema_version"),
        (lambda value: value.update(quality_preset="high"), "quality_preset=ultra"),
        (
            lambda value: value["cases"].append(dict(value["cases"][0])),
            "case_id 不能为空或重复",
        ),
        (
            lambda value: value["cases"].append(
                {"case_id": "missing", "image": "missing.png"}
            ),
            "图片不存在或不是 PNG",
        ),
    ),
)
def test_load_cases_rejects_invalid_manifest(
    tmp_path: Path,
    mutation: object,
    message: str,
) -> None:
    document = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    for case in document["cases"]:
        case["image"] = str((MANIFEST.parent / case["image"]).resolve())
    assert callable(mutation)
    mutation(document)
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        _load_cases(manifest)


def test_multipart_body_freezes_online_ultra_contract() -> None:
    body, boundary = _multipart_body(
        image=b"\x89PNG\r\n\x1a\nfixture",
        filename="fixture.png",
        project_id="00000000-0000-0000-0000-000000000001",
        instruction="保留中心高光",
    )

    assert boundary.startswith("shadergen-")
    assert f"--{boundary}\r\n".encode() in body
    assert b'name="generation_mode"\r\n\r\nprocedural_v1\r\n' in body
    assert b'name="quality_preset"\r\n\r\nultra\r\n' in body
    assert b'filename="fixture.png"\r\n' in body
    assert "保留中心高光".encode() in body
    assert b"\x89PNG\r\n\x1a\nfixture" in body
    assert body.endswith(f"--{boundary}--\r\n".encode())


def test_summary_distinguishes_collection_completion_from_case_success() -> None:
    results = [
        {
            "case_id": "z-pass",
            "status": "succeeded",
            "total_loss": 0.08,
            "threshold_passed": True,
            "charged_model_calls": 3,
            "duration_seconds": 4.5,
        },
        {
            "case_id": "a-fail",
            "status": "failed",
            "total_loss": None,
            "threshold_passed": False,
            "charged_model_calls": 40,
            "duration_seconds": 6.25,
        },
    ]

    report = _summary(results, expected_cases=2)

    assert report["status"] == "completed"
    assert report["completed_cases"] == 2
    assert report["succeeded_cases"] == 1
    assert report["failed_cases"] == 1
    assert report["threshold_met_cases"] == 1
    assert report["mean_total_loss"] == 0.08
    assert report["charged_model_calls"] == 43
    assert report["duration_seconds_sum"] == 10.75
    assert [case["case_id"] for case in report["cases"]] == ["a-fail", "z-pass"]


def test_summary_marks_partial_result_collection_incomplete() -> None:
    report = _summary([], expected_cases=16)

    assert report["status"] == "incomplete"
    assert report["completed_cases"] == 0
    assert report["mean_total_loss"] is None
