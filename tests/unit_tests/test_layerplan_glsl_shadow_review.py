"""LayerPlan shadow suite v2 匿名人工盲评包离线测试."""

from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import agent.app.services.layerplan_glsl_shadow_review as review_service
from agent.app.services.layerplan_glsl_shadow_review import (
    HUMAN_REVIEW_SCHEMA_VERSION,
    ShadowReviewError,
    evaluate_blind_review,
    verify_blind_review_package,
    write_blind_review_package,
)


def _protocol() -> tuple[Any, Any]:
    manifest = SimpleNamespace(
        schema_version="layerplan_glsl_shadow_manifest_v2",
        samples=(object(), object(), object(), object()),
        rounds=2,
    )
    gate = SimpleNamespace(
        schema_version="layerplan_glsl_shadow_gate_v2",
        min_arm_b_preference_rate=0.5,
    )
    return manifest, gate


def _install_frozen_suite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Any, Any]:
    suite_dir = tmp_path / "evidence" / "shadow-suite-123456789abc"
    suite_dir.mkdir(parents=True)
    suite_payload = {
        "suite_report_sha256": "1" * 64,
        "runs": [{"run_id": f"run-{index}"} for index in range(1, 9)],
        "aggregate": {"automatic_gate": {"passed": True}},
    }
    sources = tmp_path / "fixture-images"
    sources.mkdir()
    items: list[dict[str, Any]] = []
    for index in range(1, 8):
        reference = sources / f"{index}-reference.png"
        arm_a = sources / f"{index}-arm-a.png"
        arm_b = sources / f"{index}-arm-b.png"
        reference.write_bytes(f"reference-{index}".encode())
        arm_a.write_bytes(f"arm-a-{index}".encode())
        arm_b.write_bytes(f"arm-b-{index}".encode())
        a_arm = "A" if index % 2 else "B"
        items.append(
            {
                "item_id": f"item-{index:03d}",
                "sample_id": f"sample_{index}",
                "round_index": 1 if index % 2 else 2,
                "run_id": f"run-{index}",
                "reference": reference,
                "A": arm_a if a_arm == "A" else arm_b,
                "B": arm_b if a_arm == "A" else arm_a,
                "a_arm": a_arm,
                "b_arm": "B" if a_arm == "A" else "A",
            }
        )
    unreviewable = [
        {
            "schedule_index": 8,
            "status": "unreviewable",
            "reason_code": "missing_paired_current_best",
        }
    ]
    monkeypatch.setattr(
        review_service,
        "verify_shadow_suite_report",
        lambda *_args, **_kwargs: suite_payload,
    )
    monkeypatch.setattr(
        review_service,
        "_source_items",
        lambda *_args, **_kwargs: (items, unreviewable),
    )
    manifest, gate = _protocol()
    return suite_dir, manifest, gate


def _build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path, Any, Any]:
    suite_dir, manifest, gate = _install_frozen_suite(monkeypatch, tmp_path)
    package_dir = write_blind_review_package(
        suite_dir,
        manifest=manifest,
        gate=gate,
        output_root=tmp_path / "review-output",
    )
    return package_dir, suite_dir, manifest, gate


def _write_minimal_run_tree(
    evidence_root: Path,
    *,
    run_id: str,
    report_sha256: str,
    missing_arm_b: bool,
) -> dict[str, Any]:
    run_dir = evidence_root / run_id
    (run_dir / "input").mkdir(parents=True)
    (run_dir / "input/reference").write_bytes(f"reference-{run_id}".encode())
    files: dict[str, str] = {}
    arms: list[dict[str, Any]] = []
    for arm_id, fill in (("A", b"arm-a"), ("B", b"arm-b")):
        if arm_id == "B" and missing_arm_b:
            arms.append(
                {
                    "arm_id": arm_id,
                    "current_best": None,
                    "candidates": [],
                }
            )
            continue
        spec_sha256 = (arm_id.lower() * 64)[:64]
        relative_root = (
            f"arms/{arm_id}/candidates/001-initial-{spec_sha256[:8]}"
        )
        candidate_dir = run_dir / relative_root
        candidate_dir.mkdir(parents=True)
        spec_path = candidate_dir / "spec.json"
        spec_path.write_text(
            json.dumps({"spec_sha256": spec_sha256}), encoding="utf-8"
        )
        render_path = candidate_dir / "render.png"
        render_path.write_bytes(fill + b"-" + run_id.encode())
        files[f"{relative_root}/spec.json"] = sha256(
            spec_path.read_bytes()
        ).hexdigest()
        files[f"{relative_root}/render.png"] = sha256(
            render_path.read_bytes()
        ).hexdigest()
        arms.append(
            {
                "arm_id": arm_id,
                "current_best": {"spec_sha256": spec_sha256},
                "candidates": [
                    {
                        "spec_sha256": spec_sha256,
                        "is_current_best": True,
                    }
                ],
            }
        )
    return {
        "report_sha256": report_sha256,
        "files": files,
        "arms": arms,
    }


def test_source_items_uses_fixed_parity_and_real_current_best_file_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    evidence_root = tmp_path / "evidence"
    suite_dir = evidence_root / "shadow-suite-fixed"
    suite_dir.mkdir(parents=True)
    first_report = "a" * 64
    second_report = "b" * 64
    payloads = {
        "run-reviewable": _write_minimal_run_tree(
            evidence_root,
            run_id="run-reviewable",
            report_sha256=first_report,
            missing_arm_b=False,
        ),
        "run-unreviewable": _write_minimal_run_tree(
            evidence_root,
            run_id="run-unreviewable",
            report_sha256=second_report,
            missing_arm_b=True,
        ),
    }
    monkeypatch.setattr(
        review_service,
        "verify_shadow_run",
        lambda run_dir: payloads[run_dir.name],
    )
    suite_hash = "1" * 64
    suite_payload = {
        "suite_report_sha256": suite_hash,
        "runs": [
            {
                "sample_id": "sample_alpha",
                "round_index": 1,
                "run_id": "run-reviewable",
                "report_sha256": first_report,
            },
            {
                "sample_id": "sample_beta",
                "round_index": 2,
                "run_id": "run-unreviewable",
                "report_sha256": second_report,
            },
        ],
    }

    items, unreviewable = review_service._source_items(suite_dir, suite_payload)
    repeated, repeated_unreviewable = review_service._source_items(
        suite_dir, suite_payload
    )

    assert items == repeated
    assert unreviewable == repeated_unreviewable
    assert len(items) == 1
    expected_a_arm = (
        "A"
        if sha256(f"{suite_hash}:sample_alpha:1".encode()).digest()[0] % 2 == 0
        else "B"
    )
    assert items[0]["a_arm"] == expected_a_arm
    assert items[0]["b_arm"] == ("B" if expected_a_arm == "A" else "A")
    assert items[0]["A"].read_bytes().startswith(
        b"arm-a" if expected_a_arm == "A" else b"arm-b"
    )
    assert items[0]["B"].read_bytes().startswith(
        b"arm-b" if expected_a_arm == "A" else b"arm-a"
    )
    assert unreviewable == [
        {
            "schedule_index": 2,
            "status": "unreviewable",
            "reason_code": "missing_paired_current_best",
        }
    ]


def test_package_has_seven_public_items_and_safe_unreviewable_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package_dir, suite_dir, manifest, gate = _build(monkeypatch, tmp_path)
    package = json.loads((package_dir / "package-manifest.json").read_text())
    mapping = json.loads((package_dir / "mapping.private.json").read_text())
    reviewer = package_dir / "reviewer"

    assert package["scheduled_count"] == 8
    assert package["item_count"] == 7
    assert package["unreviewable"] == [
        {
            "reason_code": "missing_paired_current_best",
            "schedule_index": 8,
            "status": "unreviewable",
        }
    ]
    assert set(package["unreviewable"][0]).isdisjoint({"arm", "run_id", "loss"})
    assert len(mapping["items"]) == 7
    assert not (reviewer / "mapping.private.json").exists()
    assert len(list(reviewer.glob("items/*/reference.png"))) == 7
    assert len(list(reviewer.glob("items/*/a.png"))) == 7
    assert len(list(reviewer.glob("items/*/b.png"))) == 7
    assert {path.name for path in reviewer.iterdir()} == {
        "index.html",
        "review-template.json",
        "items",
    }
    verify_blind_review_package(
        package_dir, suite_dir=suite_dir, manifest=manifest, gate=gate
    )


def test_package_is_write_once_and_rejects_tamper_extra_rename_and_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package_dir, suite_dir, manifest, gate = _build(monkeypatch, tmp_path)
    with pytest.raises(FileExistsError, match="拒绝覆盖"):
        write_blind_review_package(
            suite_dir,
            manifest=manifest,
            gate=gate,
            output_root=package_dir.parent,
        )

    asset = package_dir / "reviewer/items/item-001/a.png"
    original = asset.read_bytes()
    asset.write_bytes(original + b"tampered")
    with pytest.raises(ShadowReviewError, match="hash/size"):
        verify_blind_review_package(
            package_dir, suite_dir=suite_dir, manifest=manifest, gate=gate
        )
    asset.write_bytes(original)

    extra = package_dir / "reviewer/extra.txt"
    extra.write_text("extra", encoding="utf-8")
    os.chmod(extra, 0o600)
    with pytest.raises(ShadowReviewError, match="文件集合漂移"):
        verify_blind_review_package(
            package_dir, suite_dir=suite_dir, manifest=manifest, gate=gate
        )
    extra.unlink()

    renamed = asset.with_name("renamed.png")
    asset.rename(renamed)
    with pytest.raises(ShadowReviewError, match="文件集合漂移"):
        verify_blind_review_package(
            package_dir, suite_dir=suite_dir, manifest=manifest, gate=gate
        )
    renamed.rename(asset)

    link = package_dir / "reviewer/link"
    link.symlink_to(asset)
    with pytest.raises(ShadowReviewError, match="symlink"):
        verify_blind_review_package(
            package_dir, suite_dir=suite_dir, manifest=manifest, gate=gate
        )


@pytest.mark.parametrize("invalid_item_count", [True, -1, "7"])
def test_package_rejects_non_strict_item_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid_item_count: Any,
) -> None:
    package_dir, suite_dir, manifest, gate = _build(monkeypatch, tmp_path)
    manifest_path = package_dir / "package-manifest.json"
    payload = json.loads(manifest_path.read_text())
    payload.pop("package_manifest_sha256")
    payload["item_count"] = invalid_item_count
    payload["package_manifest_size_bytes"] = 0
    while True:
        encoded_payload = dict(payload)
        encoded_payload["package_manifest_sha256"] = sha256(
            review_service.canonical_json(payload).encode()
        ).hexdigest()
        data = (
            review_service.canonical_json(encoded_payload) + "\n"
        ).encode()
        if payload["package_manifest_size_bytes"] == len(data):
            break
        payload["package_manifest_size_bytes"] = len(data)
    manifest_path.write_bytes(data)

    with pytest.raises(ShadowReviewError, match="item_count"):
        verify_blind_review_package(
            package_dir, suite_dir=suite_dir, manifest=manifest, gate=gate
        )


def test_evaluation_uses_all_eight_scheduled_rounds_as_denominator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package_dir, suite_dir, manifest, gate = _build(monkeypatch, tmp_path)
    package = json.loads((package_dir / "package-manifest.json").read_text())
    mapping = json.loads((package_dir / "mapping.private.json").read_text())
    choices = []
    for index, item in enumerate(mapping["items"]):
        if index < 4:
            choice = "A" if item["a_arm"] == "B" else "B"
        else:
            choice = "tie"
        choices.append({"item_id": item["item_id"], "choice": choice})
    review_path = tmp_path / "human-review.json"
    review_path.write_text(
        json.dumps(
            {
                "schema_version": HUMAN_REVIEW_SCHEMA_VERSION,
                "package_id": package["package_id"],
                "reviewer": "reviewer-01",
                "items": choices,
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_blind_review(
        package_dir,
        suite_dir=suite_dir,
        human_review_path=review_path,
        manifest=manifest,
        gate=gate,
    )

    assert result["human_review"]["review_count"] == 7
    assert result["human_review"]["scheduled_count"] == 8
    assert result["human_review"]["unreviewable_count"] == 1
    assert result["human_review"]["arm_b_preference_count"] == 4
    assert result["human_review"]["tie_count"] == 3
    assert result["human_review"]["arm_b_preference_rate"] == 0.5
    assert result["gate"]["passed"] is True
    assert "reviewer" not in result["human_review"]
    assert len(result["human_review"]["reviewer_alias_sha256"]) == 64


def test_evaluation_verifies_package_before_reading_human_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package_dir, suite_dir, manifest, gate = _build(monkeypatch, tmp_path)
    (package_dir / "reviewer/index.html").write_text("tampered", encoding="utf-8")

    with pytest.raises(ShadowReviewError, match="hash/size"):
        evaluate_blind_review(
            package_dir,
            suite_dir=suite_dir,
            human_review_path=tmp_path / "does-not-exist.json",
            manifest=manifest,
            gate=gate,
        )


def test_human_review_must_cover_all_reviewable_items(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package_dir, suite_dir, manifest, gate = _build(monkeypatch, tmp_path)
    package = json.loads((package_dir / "package-manifest.json").read_text())
    review_path = tmp_path / "incomplete.json"
    review_path.write_text(
        json.dumps(
            {
                "schema_version": HUMAN_REVIEW_SCHEMA_VERSION,
                "package_id": package["package_id"],
                "reviewer": "r",
                "items": [
                    {"item_id": f"item-{index:03d}", "choice": "A"}
                    for index in range(1, 7)
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ShadowReviewError, match="完整覆盖"):
        evaluate_blind_review(
            package_dir,
            suite_dir=suite_dir,
            human_review_path=review_path,
            manifest=manifest,
            gate=gate,
        )


def test_cli_returns_two_on_fail_closed_verification(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    import scripts.run_layerplan_glsl_shadow_review as cli

    manifest, gate = _protocol()
    monkeypatch.setattr(cli, "load_shadow_suite_manifest", lambda _path: manifest)
    monkeypatch.setattr(
        cli, "load_shadow_suite_gate", lambda _path, *, manifest: gate
    )
    monkeypatch.setattr(
        cli,
        "verify_blind_review_package",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ShadowReviewError("tampered package")
        ),
    )

    assert (
        cli.main(
            [
                "verify",
                "--suite-dir",
                "suite",
                "--package-dir",
                "package",
            ]
        )
        == 2
    )
    assert "tampered package" in capsys.readouterr().err
