"""PromotionAuthorizationV1 可信 registry 运行时绑定测试."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent.app.services.layerplan_glsl_shadow_suite import (
    current_direct_glsl_implementation_identity,
)
from backend.app.core.engine_policy import (
    EnginePolicyConfigurationError,
    PromotionAuthorizationV1,
    ShaderEnginePolicyV1,
)
from backend.app.core.promotion_authorization import (
    PromotionAuthorizationError,
    require_verification_matches_policy,
    verify_runtime_promotion_authorization,
)
from backend.app.core.settings import BackendSettings

_SUITE_HASH = "a" * 64
_REVIEW_MANIFEST_HASH = "b" * 64
_REVIEW_RESULT_HASH = "c" * 64
_EVIDENCE_HASH = "d" * 64
_IDENTITY = "e" * 64
_EVIDENCE_ID = "direct-glsl-promotion-canary-001"
_EVIDENCE_URI = "s3://shadergen-immutable-evidence/direct-glsl/promotion-bundle.tar.zst"


def _authorization(
    *,
    target_stage: str = "canary",
    identity: str = _IDENTITY,
) -> PromotionAuthorizationV1:
    return PromotionAuthorizationV1.model_validate(
        {
            "authorization_id": "promote-canary-001",
            "target_stage": target_stage,
            "d090_suite_report_sha256": _SUITE_HASH,
            "automatic_gate_outcome": "supported",
            "recursive_verifier_version": "promotion-evidence-verifier-v1",
            "recursive_verification_result": "verified",
            "human_blind_review_manifest_sha256": _REVIEW_MANIFEST_HASH,
            "human_blind_review_result_sha256": _REVIEW_RESULT_HASH,
            "human_blind_review_b_preference": 0.625,
            "human_gate_outcome": "supported",
            "durable_registry_entry_id": _EVIDENCE_ID,
            "durable_evidence_uri": _EVIDENCE_URI,
            "durable_evidence_sha256": _EVIDENCE_HASH,
            "durability_status": "durable",
            "direct_implementation_identity": identity,
            "max_canary_percent": 20 if target_stage == "canary" else 100,
            "approved_at": "2026-07-27T10:00:00+08:00",
            "adr_id": "ADR-093",
        }
    )


def _policy(
    *,
    target_stage: str = "canary",
    identity: str = _IDENTITY,
) -> ShaderEnginePolicyV1:
    return ShaderEnginePolicyV1(
        policy_id=f"rollout-{target_stage}-001",
        stage=target_stage,
        shadow_percent=0,
        canary_percent=10 if target_stage == "canary" else 100,
        bucket_basis="project_id_v1",
        direct_engine="direct_glsl_layerplan_v1",
        fallback_engine="shader_graph_v1",
        promotion_authorization=_authorization(
            target_stage=target_stage,
            identity=identity,
        ),
    )


def _registry_entry(
    *,
    target_stage: str = "canary",
    identity: str = _IDENTITY,
) -> dict[str, Any]:
    return {
        "evidence_id": _EVIDENCE_ID,
        "kind": "layerplan_glsl_promotion_evidence",
        "suite_run_id": "shadow-suite-d03e2224684b",
        "durability_status": "durable",
        "gate_status": "passed",
        "summary": {
            "target_stage": target_stage,
            "d090_suite_report_sha256": _SUITE_HASH,
            "automatic_gate_outcome": "supported",
            "recursive_verifier_version": "promotion-evidence-verifier-v1",
            "recursive_verification_result": "verified",
            "human_blind_review_manifest_sha256": _REVIEW_MANIFEST_HASH,
            "human_blind_review_result_sha256": _REVIEW_RESULT_HASH,
            "human_blind_review_b_preference": 0.625,
            "human_gate_outcome": "supported",
            "direct_implementation_identity": identity,
        },
        "artifacts": [
            {
                "role": "promotion_evidence_bundle",
                "path": _EVIDENCE_URI,
                "availability": "object_store",
                "size_bytes": 1_700_000,
                "sha256": _EVIDENCE_HASH,
                "immutability_status": "immutable",
            }
        ],
        "limitations": [],
    }


def _write_registry(path: Path, entry: dict[str, Any] | None = None) -> Path:
    payload = {
        "schema_version": 1,
        "updated_at": "2026-07-27",
        "entries": [_registry_entry() if entry is None else entry],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def test_exact_durable_registry_binding_returns_frozen_receipt(
    tmp_path: Path,
) -> None:
    policy = _policy()
    registry = _write_registry(tmp_path / "registry.json")

    receipt = verify_runtime_promotion_authorization(
        policy,
        evidence_registry_path=registry,
        current_direct_implementation_identity=_IDENTITY,
    )

    assert receipt is not None
    assert receipt.registry_entry_id == _EVIDENCE_ID
    assert receipt.target_stage == "canary"
    assert receipt.durable_evidence_sha256 == _EVIDENCE_HASH
    require_verification_matches_policy(policy, receipt)
    settings = BackendSettings(
        engine_policy=policy,
        promotion_authorization_verification=receipt,
    )
    assert settings.promotion_authorization_verification is receipt


def test_direct_default_requires_matching_direct_default_entry(
    tmp_path: Path,
) -> None:
    policy = _policy(target_stage="direct_default")
    registry = _write_registry(
        tmp_path / "registry.json",
        _registry_entry(target_stage="direct_default"),
    )
    receipt = verify_runtime_promotion_authorization(
        policy,
        evidence_registry_path=registry,
        current_direct_implementation_identity=_IDENTITY,
    )
    assert receipt is not None
    assert receipt.target_stage == "direct_default"


def test_missing_or_non_durable_registry_entry_fails_closed(
    tmp_path: Path,
) -> None:
    policy = _policy()
    with pytest.raises(PromotionAuthorizationError, match="缺少"):
        verify_runtime_promotion_authorization(
            policy,
            evidence_registry_path=None,
            current_direct_implementation_identity=_IDENTITY,
        )

    missing = _write_registry(
        tmp_path / "missing.json",
        {**_registry_entry(), "evidence_id": "different-entry"},
    )
    with pytest.raises(PromotionAuthorizationError, match="不存在"):
        verify_runtime_promotion_authorization(
            policy,
            evidence_registry_path=missing,
            current_direct_implementation_identity=_IDENTITY,
        )

    partial_entry = {**_registry_entry(), "durability_status": "partial"}
    partial = _write_registry(tmp_path / "partial.json", partial_entry)
    with pytest.raises(PromotionAuthorizationError, match="durable"):
        verify_runtime_promotion_authorization(
            policy,
            evidence_registry_path=partial,
            current_direct_implementation_identity=_IDENTITY,
        )

    local_entry = deepcopy(_registry_entry())
    local_entry["artifacts"][0]["availability"] = "local_ignored"
    local = _write_registry(tmp_path / "local.json", local_entry)
    with pytest.raises(PromotionAuthorizationError, match="durable"):
        verify_runtime_promotion_authorization(
            policy,
            evidence_registry_path=local,
            current_direct_implementation_identity=_IDENTITY,
        )


@pytest.mark.parametrize(
    ("path", "replacement", "match"),
    [
        (
            ("summary", "d090_suite_report_sha256"),
            "f" * 64,
            "D094 suite hash",
        ),
        (
            ("summary", "human_blind_review_manifest_sha256"),
            "f" * 64,
            "human review manifest hash",
        ),
        (
            ("summary", "human_blind_review_result_sha256"),
            "f" * 64,
            "human review result hash",
        ),
        (
            ("artifacts", 0, "path"),
            "s3://other/bundle.tar.zst",
            "durable evidence URI",
        ),
        (
            ("artifacts", 0, "sha256"),
            "f" * 64,
            "durable evidence hash",
        ),
        (
            ("summary", "target_stage"),
            "direct_default",
            "target_stage",
        ),
        (
            ("summary", "direct_implementation_identity"),
            "f" * 64,
            "registry implementation identity",
        ),
    ],
)
def test_every_authorized_registry_binding_is_exact(
    tmp_path: Path,
    path: tuple[str | int, ...],
    replacement: Any,
    match: str,
) -> None:
    entry = deepcopy(_registry_entry())
    target: Any = entry
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = replacement
    registry = _write_registry(tmp_path / "drift.json", entry)

    with pytest.raises(PromotionAuthorizationError, match=match):
        verify_runtime_promotion_authorization(
            _policy(),
            evidence_registry_path=registry,
            current_direct_implementation_identity=_IDENTITY,
        )


def test_current_implementation_identity_must_match_exactly(
    tmp_path: Path,
) -> None:
    registry = _write_registry(tmp_path / "registry.json")
    with pytest.raises(
        PromotionAuthorizationError, match="current implementation identity"
    ):
        verify_runtime_promotion_authorization(
            _policy(),
            evidence_registry_path=registry,
            current_direct_implementation_identity="f" * 64,
        )


def test_registry_rejects_symlink_duplicate_json_key_and_ambiguous_id(
    tmp_path: Path,
) -> None:
    registry = _write_registry(tmp_path / "registry.json")
    link = tmp_path / "registry-link.json"
    link.symlink_to(registry)
    with pytest.raises(PromotionAuthorizationError, match="symlink"):
        verify_runtime_promotion_authorization(
            _policy(),
            evidence_registry_path=link,
            current_direct_implementation_identity=_IDENTITY,
        )

    duplicate_key = tmp_path / "duplicate-key.json"
    duplicate_key.write_text(
        '{"schema_version":1,"schema_version":1,'
        '"updated_at":"2026-07-27","entries":[]}',
        encoding="utf-8",
    )
    with pytest.raises(PromotionAuthorizationError, match="重复 JSON key"):
        verify_runtime_promotion_authorization(
            _policy(),
            evidence_registry_path=duplicate_key,
            current_direct_implementation_identity=_IDENTITY,
        )

    duplicate_id = tmp_path / "duplicate-id.json"
    duplicate_id.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-07-27",
                "entries": [_registry_entry(), _registry_entry()],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PromotionAuthorizationError, match="entry id 重复"):
        verify_runtime_promotion_authorization(
            _policy(),
            evidence_registry_path=duplicate_id,
            current_direct_implementation_identity=_IDENTITY,
        )


def test_settings_rejects_unverified_promotion_policy() -> None:
    with pytest.raises(PromotionAuthorizationError, match="缺少可信"):
        BackendSettings(engine_policy=_policy())


@pytest.mark.parametrize("target_stage", ["canary", "direct_default"])
def test_kill_switch_allows_direct_construction_without_registry_receipt(
    target_stage: str,
) -> None:
    settings = BackendSettings(
        engine_policy=_policy(target_stage=target_stage),
        promotion_authorization_verification=None,
        direct_glsl_kill_switch=True,
    )
    assert settings.engine_policy.stage == target_stage
    assert settings.engine_policy_resolution.effective_stage == "disabled"
    assert settings.promotion_authorization_verification is None


@pytest.mark.parametrize("target_stage", ["canary", "direct_default"])
def test_kill_switch_env_skips_registry_but_still_parses_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_stage: str,
) -> None:
    policy_path = tmp_path / f"{target_stage}.yaml"
    policy_path.write_text(
        yaml.safe_dump(
            _policy(target_stage=target_stage).model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHADERGEN_ENGINE_POLICY_PATH", str(policy_path))
    monkeypatch.setenv("SHADERGEN_DIRECT_GLSL_KILL_SWITCH", "1")
    monkeypatch.delenv("SHADERGEN_EVIDENCE_REGISTRY_PATH", raising=False)

    settings = BackendSettings.from_env(load_environment=False)

    assert settings.engine_policy.stage == target_stage
    assert settings.engine_policy_resolution.effective_stage == "disabled"
    assert settings.promotion_authorization_verification is None


def test_kill_switch_does_not_bypass_policy_yaml_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_path = tmp_path / "malformed-policy.yaml"
    payload = _policy().model_dump(mode="json")
    payload["unknown_field"] = "must-still-fail"
    policy_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("SHADERGEN_ENGINE_POLICY_PATH", str(policy_path))
    monkeypatch.setenv("SHADERGEN_DIRECT_GLSL_KILL_SWITCH", "1")
    monkeypatch.delenv("SHADERGEN_EVIDENCE_REGISTRY_PATH", raising=False)

    with pytest.raises(EnginePolicyConfigurationError):
        BackendSettings.from_env(load_environment=False)


def test_settings_from_env_verifies_current_identity_and_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    current = current_direct_glsl_implementation_identity()["identity_sha256"]
    assert isinstance(current, str)
    policy = _policy(identity=current)
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        yaml.safe_dump(
            policy.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    registry = _write_registry(
        tmp_path / "registry.json",
        _registry_entry(identity=current),
    )
    monkeypatch.setenv("SHADERGEN_ENGINE_POLICY_PATH", str(policy_path))
    monkeypatch.setenv("SHADERGEN_EVIDENCE_REGISTRY_PATH", str(registry))

    settings = BackendSettings.from_env(load_environment=False)

    assert settings.engine_policy.stage == "canary"
    assert settings.promotion_authorization_verification is not None
    assert (
        settings.promotion_authorization_verification.direct_implementation_identity
        == current
    )


def test_current_repository_registry_cannot_authorize_canary() -> None:
    root = Path(__file__).resolve().parents[2]
    current = current_direct_glsl_implementation_identity()["identity_sha256"]
    assert isinstance(current, str)
    with pytest.raises(PromotionAuthorizationError, match="不存在"):
        verify_runtime_promotion_authorization(
            _policy(identity=current),
            evidence_registry_path=root / "docs/evidence/registry.json",
            current_direct_implementation_identity=current,
        )
