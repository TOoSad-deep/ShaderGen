"""LayerPlan promotion evidence 私有 bundle 离线测试."""

from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import agent.app.services.layerplan_glsl_promotion_evidence as service
from agent.app.services.layerplan_glsl_promotion_evidence import (
    DURABILITY_STATUS,
    PromotionEvidenceError,
    build_promotion_evidence_bundle,
    verify_promotion_evidence_bundle,
)
from agent.app.services.layerplan_glsl_shadow_review import (
    HUMAN_EVALUATION_SCHEMA_VERSION,
)
from shaderforge.program_spec import canonical_json


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (canonical_json(value) + "\n").encode()


@pytest.fixture
def promotion_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> SimpleNamespace:
    source_root = tmp_path / "source"
    suite_dir = source_root / f"shadow-suite-{'a' * 12}"
    suite_dir.mkdir(parents=True)
    (suite_dir / "suite_report.json").write_bytes(b'{"fixture":"suite"}\n')
    os.chmod(suite_dir, 0o700)
    os.chmod(suite_dir / "suite_report.json", 0o600)
    run_ids = [f"shadow-fixture-{index:02d}" for index in range(1, 9)]
    runs = []
    for index, run_id in enumerate(run_ids, start=1):
        run_dir = source_root / run_id
        nested = run_dir / "arms/A/candidates/001"
        nested.mkdir(parents=True)
        (run_dir / "report.json").write_text(
            json.dumps({"run_id": run_id}), encoding="utf-8"
        )
        (nested / "render.png").write_bytes(f"render-{index}".encode())
        for path in [run_dir, *run_dir.rglob("*")]:
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        runs.append({"run_id": run_id})

    package_dir = tmp_path / "review" / f"shadow-review-{'b' * 12}"
    reviewer = package_dir / "reviewer/items/item-001"
    reviewer.mkdir(parents=True)
    (package_dir / "package-manifest.json").write_bytes(b'{"fixture":"package"}\n')
    (package_dir / "mapping.private.json").write_bytes(b'{"items":[]}\n')
    (reviewer / "a.png").write_bytes(b"a")
    for path in [package_dir, *package_dir.rglob("*")]:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)

    protocol = tmp_path / "protocol"
    protocol.mkdir()
    manifest_path = protocol / "manifest-v2.yaml"
    gate_path = protocol / "gate-v2.yaml"
    manifest_path.write_bytes(b"manifest fixture\n")
    gate_path.write_bytes(b"gate fixture\n")
    identity = "c" * 64
    manifest = SimpleNamespace(
        path=manifest_path,
        manifest_sha256=sha256(manifest_path.read_bytes()).hexdigest(),
        schema_version="layerplan_glsl_shadow_manifest_v2",
        implementation_identity_sha256=identity,
    )
    gate = SimpleNamespace(
        path=gate_path,
        gate_sha256=sha256(gate_path.read_bytes()).hexdigest(),
        schema_version="layerplan_glsl_shadow_gate_v2",
        implementation_identity_sha256=identity,
    )
    suite_payload = {
        "suite_report_sha256": "d" * 64,
        "runs": runs,
        "aggregate": {"automatic_gate": {"passed": True, "outcome": "supported"}},
    }
    package_payload = {
        "package_id": package_dir.name,
        "package_manifest_sha256": "e" * 64,
    }
    review_path = tmp_path / "human-review.json"
    review_path.write_bytes(
        _json_bytes(
            {
                "schema_version": "layerplan_glsl_shadow_human_review_v1",
                "package_id": package_dir.name,
                "reviewer": "fixture-reviewer",
                "items": [],
            }
        )
    )

    state = {"human_supported": True}

    def fake_evaluate(
        _package_dir: Path,
        *,
        suite_dir: Path,
        human_review_path: Path,
        manifest: Any,
        gate: Any,
    ) -> dict[str, Any]:
        del suite_dir, manifest, gate
        review_bytes = human_review_path.read_bytes()
        supported = state["human_supported"]
        return {
            "schema_version": HUMAN_EVALUATION_SCHEMA_VERSION,
            "package_id": package_payload["package_id"],
            "suite_report_sha256": suite_payload["suite_report_sha256"],
            "human_review": {
                "review_sha256": sha256(review_bytes).hexdigest(),
                "review_size_bytes": len(review_bytes),
            },
            "gate": {
                "passed": supported,
                "outcome": "supported" if supported else "not_supported",
            },
            "promotion_decision": (
                "no_go_pending_durable" if supported else "no_go_human_gate_failed"
            ),
        }

    monkeypatch.setattr(
        service,
        "verify_shadow_suite_report",
        lambda *_args, **_kwargs: suite_payload,
    )
    monkeypatch.setattr(
        service,
        "verify_blind_review_package",
        lambda *_args, **_kwargs: package_payload,
    )
    monkeypatch.setattr(service, "evaluate_blind_review", fake_evaluate)
    monkeypatch.setattr(service, "load_shadow_suite_manifest", lambda _path: manifest)
    monkeypatch.setattr(
        service,
        "load_shadow_suite_gate",
        lambda _path, *, manifest: gate,
    )
    evaluation_path = tmp_path / "human-evaluation.json"
    evaluation_path.write_bytes(
        _json_bytes(
            fake_evaluate(
                package_dir,
                suite_dir=suite_dir,
                human_review_path=review_path,
                manifest=manifest,
                gate=gate,
            )
        )
    )
    return SimpleNamespace(
        suite_dir=suite_dir,
        package_dir=package_dir,
        manifest=manifest,
        gate=gate,
        review_path=review_path,
        evaluation_path=evaluation_path,
        output_root=tmp_path / "bundles",
        state=state,
        run_ids=run_ids,
    )


def _build(fixture: SimpleNamespace) -> Path:
    return build_promotion_evidence_bundle(
        fixture.suite_dir,
        package_dir=fixture.package_dir,
        human_review_path=fixture.review_path,
        human_evaluation_path=fixture.evaluation_path,
        manifest=fixture.manifest,
        gate=fixture.gate,
        output_root=fixture.output_root,
    )


def test_complete_happy_path_is_private_content_addressed_and_offline(
    promotion_fixture: SimpleNamespace,
) -> None:
    bundle_dir = _build(promotion_fixture)
    payload = verify_promotion_evidence_bundle(bundle_dir)

    assert bundle_dir.name == (
        f"promotion-evidence-{payload['bundle_manifest_sha256'][:12]}"
    )
    assert payload["durability_status"] == DURABILITY_STATUS
    assert payload["registry_status"] == "not_registered"
    assert payload["source"]["run_ids"] == promotion_fixture.run_ids
    assert len(list((bundle_dir / "evidence").glob("shadow-fixture-*"))) == 8
    assert (bundle_dir / "review" / promotion_fixture.package_dir.name).is_dir()
    for path in [bundle_dir, *bundle_dir.rglob("*")]:
        assert (path.stat().st_mode & 0o777) == (0o700 if path.is_dir() else 0o600)


def test_human_fail_and_evaluation_tamper_are_rejected(
    promotion_fixture: SimpleNamespace,
) -> None:
    promotion_fixture.state["human_supported"] = False
    with pytest.raises(PromotionEvidenceError, match="human gate"):
        _build(promotion_fixture)

    promotion_fixture.state["human_supported"] = True
    payload = json.loads(promotion_fixture.evaluation_path.read_text())
    payload["gate"]["outcome"] = "not_supported"
    promotion_fixture.evaluation_path.write_bytes(_json_bytes(payload))
    with pytest.raises(PromotionEvidenceError, match="canonical JSON 字节"):
        _build(promotion_fixture)


@pytest.mark.parametrize(
    "relative",
    [
        "evidence/shadow-fixture-01/report.json",
        f"review/shadow-review-{'b' * 12}/mapping.private.json",
    ],
)
def test_offline_verifier_rejects_run_and_package_tamper(
    promotion_fixture: SimpleNamespace, relative: str
) -> None:
    bundle_dir = _build(promotion_fixture)
    target = bundle_dir / relative
    target.write_bytes(target.read_bytes() + b"tampered")
    with pytest.raises(PromotionEvidenceError, match="hash/size"):
        verify_promotion_evidence_bundle(bundle_dir)


def test_offline_verifier_rejects_symlink_extra_and_rename(
    promotion_fixture: SimpleNamespace,
) -> None:
    bundle_dir = _build(promotion_fixture)
    target = bundle_dir / "human/human-review.json"
    renamed = target.with_name("renamed.json")
    target.rename(renamed)
    with pytest.raises(PromotionEvidenceError, match="文件集合漂移"):
        verify_promotion_evidence_bundle(bundle_dir)
    renamed.rename(target)

    extra = bundle_dir / "human/extra.json"
    extra.write_text("{}", encoding="utf-8")
    os.chmod(extra, 0o600)
    with pytest.raises(PromotionEvidenceError, match="文件集合漂移"):
        verify_promotion_evidence_bundle(bundle_dir)
    extra.unlink()

    link = bundle_dir / "human/link.json"
    link.symlink_to(target)
    with pytest.raises(PromotionEvidenceError, match="symlink"):
        verify_promotion_evidence_bundle(bundle_dir)


def test_bundle_is_write_once_without_deleting_existing_bundle(
    promotion_fixture: SimpleNamespace,
) -> None:
    bundle_dir = _build(promotion_fixture)
    manifest_before = (bundle_dir / "promotion-evidence-manifest.json").read_bytes()
    with pytest.raises(FileExistsError, match="拒绝覆盖"):
        _build(promotion_fixture)
    assert bundle_dir.is_dir()
    assert (
        bundle_dir / "promotion-evidence-manifest.json"
    ).read_bytes() == manifest_before


def test_cli_only_prints_path_hash_and_outcome(
    monkeypatch: pytest.MonkeyPatch, capsys: Any, tmp_path: Path
) -> None:
    import scripts.run_layerplan_glsl_promotion_evidence as cli

    payload = {
        "bundle_manifest_sha256": "f" * 64,
        "human": {"gate_outcome": "supported"},
    }
    monkeypatch.setattr(cli, "verify_promotion_evidence_bundle", lambda _path: payload)
    bundle = tmp_path / "bundle"
    assert cli.main(["verify", "--bundle-dir", str(bundle)]) == 0
    output = capsys.readouterr().out
    assert output == f"path={bundle} sha256={'f' * 64} outcome=supported\n"
    assert "reviewer" not in output
