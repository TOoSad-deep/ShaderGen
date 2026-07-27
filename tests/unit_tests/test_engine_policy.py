from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256

import pytest
from pydantic import ValidationError

from backend.app.core.engine_policy import (
    EnginePolicyConfigurationError,
    ShaderEnginePolicyV1,
    bucket_matches_percent,
    disabled_shader_engine_policy,
    load_shader_engine_policy,
    parse_direct_glsl_kill_switch,
    resolve_engine_policy,
    shader_engine_policy_sha256,
    stable_project_bucket,
)
from backend.app.core.settings import BackendSettings

_HASH = "a" * 64


def _authorization_yaml(
    *,
    target_stage: str = "canary",
    max_canary_percent: int = 20,
) -> str:
    return f"""\
  schema_version: promotion_authorization_v1
  authorization_id: promote-001
  target_stage: {target_stage}
  d090_suite_report_sha256: {_HASH}
  automatic_gate_outcome: supported
  recursive_verifier_version: recursive-verifier-v1
  recursive_verification_result: verified
  human_blind_review_manifest_sha256: {"b" * 64}
  human_blind_review_result_sha256: {"c" * 64}
  human_blind_review_b_preference: 0.5
  human_gate_outcome: supported
  durable_registry_entry_id: d090-direct-glsl
  durable_evidence_uri: s3://shadergen-evidence/d090/report.json
  durable_evidence_sha256: {"d" * 64}
  durability_status: durable
  direct_implementation_identity: git:0123456789abcdef
  max_canary_percent: {max_canary_percent}
  approved_at: 2026-07-27T10:00:00+08:00
  adr_id: ADR-091
"""


def _policy_yaml(
    *,
    stage: str = "canary",
    shadow_percent: int = 0,
    canary_percent: int = 10,
    authorization: str | None = None,
) -> str:
    auth = (
        _authorization_yaml(
            target_stage=stage,
            max_canary_percent=100 if stage == "direct_default" else 20,
        )
        if authorization is None
        else authorization
    )
    promotion = (
        "promotion_authorization: null"
        if auth.strip() == "null"
        else f"promotion_authorization:\n{auth.rstrip()}"
    )
    return f"""\
schema_version: shader_engine_policy_v1
policy_id: rollout-001
stage: {stage}
shadow_percent: {shadow_percent}
canary_percent: {canary_percent}
bucket_basis: project_id_v1
direct_engine: direct_glsl_layerplan_v1
fallback_engine: shader_graph_v1
{promotion}
"""


def test_missing_policy_is_frozen_disabled_old() -> None:
    policy = load_shader_engine_policy(None)
    assert policy == disabled_shader_engine_policy()
    assert policy.stage == "disabled"
    assert policy.fallback_engine == "shader_graph_v1"
    with pytest.raises(ValidationError):
        policy.stage = "canary"


def test_loads_canonical_policy_and_hash_is_yaml_order_independent(tmp_path) -> None:
    first_path = tmp_path / "first.yaml"
    first_path.write_text(_policy_yaml(), encoding="utf-8")
    first = load_shader_engine_policy(first_path)
    payload = first.model_dump(mode="json")
    second = ShaderEnginePolicyV1.model_validate(
        dict(reversed(list(payload.items())))
    )
    assert shader_engine_policy_sha256(first) == shader_engine_policy_sha256(second)
    assert len(shader_engine_policy_sha256(first)) == 64


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda text: text + "\nunknown: true\n", "无法加载"),
        (
            lambda text: text.replace("canary_percent: 10", "canary_percent: 21"),
            "无法加载",
        ),
        (
            lambda text: text.replace(
                "direct_engine: direct_glsl_layerplan_v1",
                "direct_engine: client_selected",
            ),
            "无法加载",
        ),
        (
            lambda text: text.replace("policy_id: rollout-001", "policy_id: bad id"),
            "无法加载",
        ),
    ],
)
def test_invalid_policy_fails_closed(tmp_path, mutate, match) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(mutate(_policy_yaml()), encoding="utf-8")
    with pytest.raises(EnginePolicyConfigurationError, match=match):
        load_shader_engine_policy(path)


def test_duplicate_yaml_key_fails_closed(tmp_path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text(
        _policy_yaml().replace(
            "policy_id: rollout-001",
            "policy_id: rollout-001\npolicy_id: rollout-002",
        ),
        encoding="utf-8",
    )
    with pytest.raises(EnginePolicyConfigurationError):
        load_shader_engine_policy(path)


def test_stage_combinations_fail_closed(tmp_path) -> None:
    production_shadow = tmp_path / "shadow.yaml"
    production_shadow.write_text(
        _policy_yaml(
            stage="production_shadow",
            shadow_percent=25,
            canary_percent=0,
            authorization="null\n",
        ),
        encoding="utf-8",
    )
    assert load_shader_engine_policy(production_shadow).shadow_percent == 25

    bad_disabled = tmp_path / "bad-disabled.yaml"
    bad_disabled.write_text(
        _policy_yaml(
            stage="disabled",
            canary_percent=0,
            authorization="null\n",
        ).replace("shadow_percent: 0", "shadow_percent: 1"),
        encoding="utf-8",
    )
    with pytest.raises(EnginePolicyConfigurationError):
        load_shader_engine_policy(bad_disabled)


@pytest.mark.parametrize(
    ("canary_percent", "max_canary_percent"),
    [(0, 100), (99, 100), (100, 99)],
)
def test_direct_default_requires_full_100_percent_authorization(
    tmp_path,
    canary_percent,
    max_canary_percent,
) -> None:
    path = tmp_path / "direct-default.yaml"
    path.write_text(
        _policy_yaml(
            stage="direct_default",
            canary_percent=canary_percent,
            authorization=_authorization_yaml(
                target_stage="direct_default",
                max_canary_percent=max_canary_percent,
            ),
        ),
        encoding="utf-8",
    )
    with pytest.raises(EnginePolicyConfigurationError):
        load_shader_engine_policy(path)


def test_direct_default_accepts_only_full_100_percent_authorization(tmp_path) -> None:
    path = tmp_path / "direct-default.yaml"
    path.write_text(
        _policy_yaml(stage="direct_default", canary_percent=100),
        encoding="utf-8",
    )
    policy = load_shader_engine_policy(path)
    assert policy.canary_percent == 100
    assert policy.promotion_authorization is not None
    assert policy.promotion_authorization.max_canary_percent == 100


@pytest.mark.parametrize(
    ("field", "unsupported"),
    [
        ("automatic_gate_outcome", "pending"),
        ("human_gate_outcome", "pending"),
        ("durability_status", "partial"),
    ],
)
def test_promotion_authorization_requires_supported_durable_gates(
    tmp_path,
    field,
    unsupported,
) -> None:
    path = tmp_path / "unsupported-gate.yaml"
    path.write_text(
        _policy_yaml().replace(f"{field}: supported", f"{field}: {unsupported}")
        if field != "durability_status"
        else _policy_yaml().replace(
            "durability_status: durable",
            f"durability_status: {unsupported}",
        ),
        encoding="utf-8",
    )
    with pytest.raises(EnginePolicyConfigurationError):
        load_shader_engine_policy(path)


def test_stable_project_bucket_implements_project_id_v1_exactly() -> None:
    expected = (
        int.from_bytes(
            sha256(b"project_id_v1\0rollout-001\0project-42").digest()[:8],
            "big",
        )
        % 10_000
    )
    assert (
        stable_project_bucket(policy_id="rollout-001", project_id="project-42")
        == expected
    )
    assert bucket_matches_percent(expected, 100)
    assert not bucket_matches_percent(expected, 0)


def test_kill_switch_has_highest_priority_without_mutating_policy(tmp_path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(_policy_yaml(), encoding="utf-8")
    policy = load_shader_engine_policy(path)
    resolution = resolve_engine_policy(policy, kill_switch_active=True)
    assert resolution.configured_stage == "canary"
    assert resolution.effective_stage == "disabled"
    assert policy.stage == "canary"
    with pytest.raises(FrozenInstanceError):
        resolution.effective_stage = "canary"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, False), ("", False), ("0", False), ("1", True), (" 1 ", True)],
)
def test_kill_switch_parsing(raw, expected) -> None:
    assert parse_direct_glsl_kill_switch(raw) is expected


def test_kill_switch_rejects_ambiguous_value() -> None:
    with pytest.raises(EnginePolicyConfigurationError, match="0 或 1"):
        parse_direct_glsl_kill_switch("true")


def test_backend_settings_freezes_policy_and_kill_switch(monkeypatch, tmp_path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(_policy_yaml(), encoding="utf-8")
    monkeypatch.setenv("SHADERGEN_ENGINE_POLICY_PATH", str(path))
    monkeypatch.setenv("SHADERGEN_DIRECT_GLSL_KILL_SWITCH", "1")
    settings = BackendSettings.from_env(load_environment=False)
    assert settings.engine_policy.stage == "canary"
    assert settings.engine_policy_resolution.effective_stage == "disabled"
    assert len(settings.engine_policy_sha256) == 64


def test_backend_settings_does_not_read_client_engine(monkeypatch) -> None:
    monkeypatch.setenv("SHADERGEN_ENGINE", "direct_glsl_layerplan_v1")
    settings = BackendSettings.from_env(load_environment=False)
    assert settings.engine_policy.stage == "disabled"
