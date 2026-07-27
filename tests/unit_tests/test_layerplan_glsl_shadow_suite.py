"""LayerPlan shadow suite 冻结 manifest/gate 契约测试（不调用真实模型）."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent.app.services.layerplan_glsl_shadow_suite import (
    ShadowSuiteContractError,
    VerifiedSuiteRun,
    aggregate_shadow_suite,
    build_shadow_suite_report,
    current_direct_glsl_implementation_identity,
    load_shadow_suite_gate,
    load_shadow_suite_manifest,
    require_current_protocol_for_live,
    resolve_verified_sample_images,
)
from shaderforge.program_spec import canonical_json

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "benchmarks/layerplan_glsl_shadow/manifest_v1.yaml"
GATE = ROOT / "benchmarks/layerplan_glsl_shadow/gate_v1.yaml"
MANIFEST_V2 = ROOT / "benchmarks/layerplan_glsl_shadow/manifest_v2.yaml"
GATE_V2 = ROOT / "benchmarks/layerplan_glsl_shadow/gate_v2.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _copy_protocol(tmp_path: Path) -> tuple[Path, Path]:
    manifest_path = tmp_path / "manifest_v1.yaml"
    gate_path = tmp_path / "gate_v1.yaml"
    _write_yaml(manifest_path, _load_yaml(MANIFEST))
    images = tmp_path / "images"
    images.mkdir()
    for source in (MANIFEST.parent / "images").iterdir():
        (images / source.name).write_bytes(source.read_bytes())

    manifest = load_shadow_suite_manifest(manifest_path)
    gate = _load_yaml(GATE)
    gate["manifest_sha256"] = manifest.manifest_sha256
    gate["config_fingerprints"] = {
        "AB": manifest.config_fingerprint_for_order("AB"),
        "BA": manifest.config_fingerprint_for_order("BA"),
    }
    _write_yaml(gate_path, gate)
    return manifest_path, gate_path


def test_repository_protocol_loads_and_cross_balances() -> None:
    manifest = load_shadow_suite_manifest(MANIFEST)
    gate = load_shadow_suite_gate(GATE, manifest=manifest)
    images = resolve_verified_sample_images(manifest)

    assert manifest.rounds == 2
    assert manifest.arm_order(1) == ("A", "B")
    assert manifest.arm_order(2) == ("B", "A")
    assert manifest.arm_config(1).arm_order == ("A", "B")
    assert manifest.arm_config(2).arm_order == ("B", "A")
    assert set(images) == {
        "solid_circle",
        "ellipse_gradient",
        "rimmed_disk",
        "pink_gel",
    }
    assert gate.manifest_sha256 == manifest.manifest_sha256
    assert gate.gate_sha256 == sha256(GATE.read_bytes()).hexdigest()


def test_repository_v2_protocol_binds_current_implementation() -> None:
    manifest = load_shadow_suite_manifest(MANIFEST_V2)
    gate = load_shadow_suite_gate(GATE_V2, manifest=manifest)
    identity = current_direct_glsl_implementation_identity()

    assert manifest.schema_version == "layerplan_glsl_shadow_manifest_v2"
    assert dict(manifest.implementation_identity or {}) == identity
    assert manifest.implementation_identity_sha256 == identity["identity_sha256"]
    assert gate.schema_version == "layerplan_glsl_shadow_gate_v2"
    assert gate.implementation_identity_sha256 == identity["identity_sha256"]
    legacy = load_shadow_suite_manifest(MANIFEST)
    assert manifest.config_fingerprint_for_order("AB") != (
        legacy.config_fingerprint_for_order("AB")
    )
    assert manifest.arm_config(1).implementation_identity_sha256 == (
        identity["identity_sha256"]
    )


def test_legacy_protocol_remains_loadable_without_v2_identity() -> None:
    manifest = load_shadow_suite_manifest(MANIFEST)
    gate = load_shadow_suite_gate(GATE, manifest=manifest)

    assert manifest.implementation_identity is None
    assert manifest.implementation_identity_sha256 is None
    assert gate.implementation_identity_sha256 is None


def test_v2_manifest_rejects_implementation_drift(tmp_path: Path) -> None:
    payload = _load_yaml(MANIFEST_V2)
    payload["implementation_identity"]["prompts"]["repair"]["version"] = "drifted"
    path = tmp_path / "manifest_v2.yaml"
    _write_yaml(path, payload)

    with pytest.raises(
        ShadowSuiteContractError, match="implementation identity 自哈希不匹配"
    ):
        load_shadow_suite_manifest(path)


def test_v2_historical_self_consistent_identity_remains_loadable(
    tmp_path: Path,
) -> None:
    payload = _load_yaml(MANIFEST_V2)
    identity = payload["implementation_identity"]
    identity["prompts"]["repair"]["version"] = "historical-version"
    identity_without_hash = dict(identity)
    identity_without_hash.pop("identity_sha256")
    identity["identity_sha256"] = sha256(
        canonical_json(identity_without_hash).encode("utf-8")
    ).hexdigest()
    path = tmp_path / "historical_manifest_v2.yaml"
    _write_yaml(path, payload)

    loaded = load_shadow_suite_manifest(path)
    assert loaded.implementation_identity_sha256 == identity["identity_sha256"]
    assert dict(loaded.implementation_identity or {}) != (
        current_direct_glsl_implementation_identity()
    )


def test_v2_gate_rejects_identity_or_generation_mismatch(tmp_path: Path) -> None:
    manifest = load_shadow_suite_manifest(MANIFEST_V2)
    payload = _load_yaml(GATE_V2)
    payload["implementation_identity_sha256"] = "0" * 64
    path = tmp_path / "gate_v2.yaml"
    _write_yaml(path, payload)
    with pytest.raises(
        ShadowSuiteContractError, match="implementation identity 绑定已漂移"
    ):
        load_shadow_suite_gate(path, manifest=manifest)

    with pytest.raises(ShadowSuiteContractError, match="世代不一致"):
        load_shadow_suite_gate(GATE, manifest=manifest)


def test_legacy_protocol_is_verify_only() -> None:
    legacy_manifest = load_shadow_suite_manifest(MANIFEST)
    legacy_gate = load_shadow_suite_gate(GATE, manifest=legacy_manifest)
    with pytest.raises(ShadowSuiteContractError, match="只允许 --verify"):
        require_current_protocol_for_live(legacy_manifest, legacy_gate)

    current_manifest = load_shadow_suite_manifest(MANIFEST_V2)
    current_gate = load_shadow_suite_gate(GATE_V2, manifest=current_manifest)
    require_current_protocol_for_live(current_manifest, current_gate)

    forged = replace(current_manifest, rounds=4)
    with pytest.raises(ShadowSuiteContractError, match="内存对象或文件已漂移"):
        require_current_protocol_for_live(forged, current_gate)


def test_instruction_hash_drift_is_rejected(tmp_path: Path) -> None:
    manifest_path, _ = _copy_protocol(tmp_path)
    payload = _load_yaml(manifest_path)
    payload["samples"][0]["instruction"] = "已漂移的 instruction"
    _write_yaml(manifest_path, payload)

    with pytest.raises(ShadowSuiteContractError, match="instruction hash 漂移"):
        load_shadow_suite_manifest(manifest_path)


def test_reference_image_hash_drift_is_rejected(tmp_path: Path) -> None:
    manifest_path, _ = _copy_protocol(tmp_path)
    manifest = load_shadow_suite_manifest(manifest_path)
    (tmp_path / "images/solid_circle.png").write_bytes(b"tampered")

    with pytest.raises(ShadowSuiteContractError, match="参考图 hash 漂移"):
        resolve_verified_sample_images(manifest)


def test_manifest_file_hash_drift_breaks_gate_binding(tmp_path: Path) -> None:
    manifest_path, gate_path = _copy_protocol(tmp_path)
    payload = _load_yaml(manifest_path)
    payload["frozen_at"] = date(2026, 7, 28)
    _write_yaml(manifest_path, payload)
    manifest = load_shadow_suite_manifest(manifest_path)

    with pytest.raises(ShadowSuiteContractError, match="manifest hash 已漂移"):
        load_shadow_suite_gate(gate_path, manifest=manifest)


def test_config_drift_breaks_fingerprint_binding(tmp_path: Path) -> None:
    manifest_path, gate_path = _copy_protocol(tmp_path)
    payload = _load_yaml(manifest_path)
    payload["config"]["draw_budget_per_arm"] += 1
    _write_yaml(manifest_path, payload)
    manifest = load_shadow_suite_manifest(manifest_path)
    gate = _load_yaml(gate_path)
    gate["manifest_sha256"] = manifest.manifest_sha256
    _write_yaml(gate_path, gate)

    with pytest.raises(ShadowSuiteContractError, match="配置指纹已漂移"):
        load_shadow_suite_gate(gate_path, manifest=manifest)


def test_gate_hash_changes_after_gate_drift(tmp_path: Path) -> None:
    manifest_path, gate_path = _copy_protocol(tmp_path)
    manifest = load_shadow_suite_manifest(manifest_path)
    first = load_shadow_suite_gate(gate_path, manifest=manifest)
    payload = _load_yaml(gate_path)
    payload["human_review"]["min_arm_b_preference_rate"] = 0.6
    _write_yaml(gate_path, payload)
    second = load_shadow_suite_gate(gate_path, manifest=manifest)

    assert first.gate_sha256 != second.gate_sha256


@pytest.mark.parametrize(
    "schedule,rounds",
    [
        (["AB"], 2),
        (["AB", "AB"], 2),
        (["AB", "AA"], 2),
    ],
)
def test_invalid_or_unbalanced_schedule_is_rejected(
    tmp_path: Path, schedule: list[str], rounds: int
) -> None:
    payload = _load_yaml(MANIFEST)
    payload["arm_order_schedule"] = schedule
    payload["rounds"] = rounds
    path = tmp_path / "manifest.yaml"
    _write_yaml(path, payload)

    with pytest.raises(ShadowSuiteContractError):
        load_shadow_suite_manifest(path)


def test_reference_path_must_match_sample_id(tmp_path: Path) -> None:
    payload = _load_yaml(MANIFEST)
    payload["samples"][0]["reference_path"] = "../solid_circle.png"
    path = tmp_path / "manifest.yaml"
    _write_yaml(path, payload)

    with pytest.raises(ShadowSuiteContractError, match="reference_path 必须为"):
        load_shadow_suite_manifest(path)


def test_unknown_manifest_field_is_rejected(tmp_path: Path) -> None:
    payload = _load_yaml(MANIFEST)
    payload["unexpected"] = True
    path = tmp_path / "manifest.yaml"
    _write_yaml(path, payload)

    with pytest.raises(ShadowSuiteContractError, match="manifest 违反冻结契约"):
        load_shadow_suite_manifest(path)


def test_gate_ratio_out_of_range_is_rejected(tmp_path: Path) -> None:
    manifest_path, gate_path = _copy_protocol(tmp_path)
    manifest = load_shadow_suite_manifest(manifest_path)
    payload = _load_yaml(gate_path)
    payload["primary_endpoint"]["min_improved_sample_ratio"] = 1.1
    _write_yaml(gate_path, payload)

    with pytest.raises(ShadowSuiteContractError, match="gate 违反冻结契约"):
        load_shadow_suite_gate(gate_path, manifest=manifest)


def test_manifest_models_are_independent_between_copies() -> None:
    """防止测试 helper 或调用方意外共享可变 YAML 对象."""
    first = _load_yaml(MANIFEST)
    second = deepcopy(first)
    second["samples"][0]["sample_id"] = "changed"

    assert first["samples"][0]["sample_id"] == "solid_circle"


def test_round_index_is_one_based_and_bounded() -> None:
    manifest = load_shadow_suite_manifest(MANIFEST)

    with pytest.raises(ShadowSuiteContractError, match="round_index"):
        manifest.arm_order(0)
    with pytest.raises(ShadowSuiteContractError, match="round_index"):
        manifest.arm_order(3)


def _records(
    *,
    deltas: dict[str, tuple[float, float]],
    inconclusive: set[tuple[str, int]] | None = None,
) -> tuple[VerifiedSuiteRun, ...]:
    manifest = load_shadow_suite_manifest(MANIFEST)
    blocked = inconclusive or set()
    records: list[VerifiedSuiteRun] = []
    for sample in manifest.samples:
        for round_index, delta in enumerate(deltas[sample.sample_id], start=1):
            failed = (sample.sample_id, round_index) in blocked
            records.append(
                VerifiedSuiteRun(
                    sample_id=sample.sample_id,
                    round_index=round_index,
                    order_label="AB" if round_index == 1 else "BA",
                    run_id=f"shadow-{sample.sample_id}-{round_index}",
                    report_sha256="a" * 64,
                    status="inconclusive" if failed else "ok",
                    arm_a_status="ok",
                    arm_b_status="inconclusive" if failed else "ok",
                    arm_a_loss=0.1,
                    arm_b_loss=None if failed else 0.1 + delta,
                )
            )
    return tuple(records)


def test_v2_suite_report_explicitly_binds_implementation_identity() -> None:
    manifest = load_shadow_suite_manifest(MANIFEST_V2)
    gate = load_shadow_suite_gate(GATE_V2, manifest=manifest)
    report = build_shadow_suite_report(
        _records(
            deltas={
                sample.sample_id: (-0.01, -0.01)
                for sample in manifest.samples
            }
        ),
        manifest=manifest,
        gate=gate,
    )

    assert report["implementation_identity"] == {
        "schema_version": "direct_glsl_shadow_implementation_v2",
        "sha256": manifest.implementation_identity_sha256,
    }


def test_aggregate_passes_only_with_margin_and_both_order_directions() -> None:
    manifest = load_shadow_suite_manifest(MANIFEST)
    gate = load_shadow_suite_gate(GATE, manifest=manifest)
    records = _records(
        deltas={
            "solid_circle": (-0.010, -0.008),
            "ellipse_gradient": (-0.007, -0.006),
            "rimmed_disk": (-0.012, -0.009),
            "pink_gel": (-0.001, -0.002),
        }
    )

    result = aggregate_shadow_suite(records, manifest=manifest, gate=gate)

    assert result["automatic_gate"]["passed"] is True
    assert result["primary_endpoint"]["improved_sample_count"] == 3
    assert result["order_effect"]["consistent_direction"] is True
    assert result["promotion_decision"] == "no_go_pending_human_and_durable"


def test_aggregate_counts_inconclusive_against_arm_b() -> None:
    manifest = load_shadow_suite_manifest(MANIFEST)
    gate = load_shadow_suite_gate(GATE, manifest=manifest)
    records = _records(
        deltas={
            "solid_circle": (-0.010, -0.008),
            "ellipse_gradient": (-0.007, -0.006),
            "rimmed_disk": (-0.012, -0.009),
            "pink_gel": (-0.010, -0.008),
        },
        inconclusive={("solid_circle", 1), ("ellipse_gradient", 2)},
    )

    result = aggregate_shadow_suite(records, manifest=manifest, gate=gate)

    assert result["automatic_gate"]["passed"] is False
    assert result["inconclusive"]["sample_count"] == 2
    assert result["inconclusive"]["sample_ratio"] == 0.5
    assert result["promotion_decision"] == "no_go_automatic_gate_failed"


def test_aggregate_rejects_missing_or_duplicate_records() -> None:
    manifest = load_shadow_suite_manifest(MANIFEST)
    gate = load_shadow_suite_gate(GATE, manifest=manifest)
    records = _records(
        deltas={
            sample.sample_id: (-0.01, -0.01) for sample in manifest.samples
        }
    )

    with pytest.raises(ShadowSuiteContractError, match="缺失、重复"):
        aggregate_shadow_suite(records[:-1], manifest=manifest, gate=gate)
    with pytest.raises(ShadowSuiteContractError, match="缺失、重复"):
        aggregate_shadow_suite(
            records + (records[0],), manifest=manifest, gate=gate
        )
