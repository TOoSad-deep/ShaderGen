from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import MappingProxyType

import pytest

from backend.app.core.runtime_policy import (
    DEFAULT_RUNTIME_POLICY_PATH,
    RUNTIME_POLICY_SCHEMA_VERSION,
    RuntimePolicyConfigurationError,
    load_runtime_policy,
)
from shaderforge.contracts import AcceptancePolicy, BudgetPolicy


def _default_text() -> str:
    return Path(DEFAULT_RUNTIME_POLICY_PATH).read_text(encoding="utf-8")


def test_default_runtime_policy_is_packaged_frozen_and_fingerprinted() -> None:
    source = Path(DEFAULT_RUNTIME_POLICY_PATH)
    registry = load_runtime_policy(source)

    assert source.is_file()
    assert registry.schema_version == RUNTIME_POLICY_SCHEMA_VERSION
    assert registry.config_sha256 == sha256(source.read_bytes()).hexdigest()
    assert isinstance(registry.profiles, MappingProxyType)
    assert set(registry.profiles) == {"fast", "balanced", "high", "ultra"}
    assert registry.resolve("fast").budget.max_model_calls == 5
    assert registry.resolve("balanced").budget.max_visual_refinements == 2
    assert registry.resolve("high").budget.max_wall_time_seconds == 600
    assert registry.resolve("ultra").budget == BudgetPolicy(
        max_visual_refinements=10,
        max_compile_repairs=5,
        max_model_calls=40,
        max_wall_time_seconds=2400,
        max_shader_chars=30000,
        renderer_replay_on_crash=2,
    )
    assert registry.resolve("ultra").acceptance == AcceptancePolicy(
        min_total_improvement=0.002,
        max_protected_regression=0.02,
        quality_threshold=0.12,
        stagnation_rounds=6,
    )
    assert registry.resolve("high").evidence() == {
        "schema_version": RUNTIME_POLICY_SCHEMA_VERSION,
        "config_sha256": registry.config_sha256,
        "profile": "high",
        "budget": {
            "max_visual_refinements": 4,
            "max_compile_repairs": 2,
            "max_model_calls": 12,
            "max_wall_time_seconds": 600,
            "max_shader_chars": 30000,
            "renderer_replay_on_crash": 1,
        },
        "acceptance": {
            "min_total_improvement": 0.005,
            "max_protected_regression": 0.02,
            "quality_threshold": 0.12,
            "stagnation_rounds": 2,
        },
    }


def test_runtime_policy_allows_custom_values_within_code_ceiling(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime-policy.yaml"
    path.write_text(
        _default_text()
        .replace("max_model_calls: 8", "max_model_calls: 7")
        .replace("quality_threshold: 0.12", "quality_threshold: 0.10", 2),
        encoding="utf-8",
    )

    registry = load_runtime_policy(path)

    assert registry.resolve("balanced").budget.max_model_calls == 7
    assert registry.resolve("balanced").acceptance.quality_threshold == 0.10
    assert registry.config_sha256 == sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda text: text.replace(
                "max_model_calls: 5",
                "max_model_calls: 5\n      max_model_calls: 4",
                1,
            ),
            "不得重复",
        ),
        (
            lambda text: text.replace(
                "max_model_calls: 5",
                "max_model_calls: 5\n      unknown_budget: 1",
                1,
            ),
            "Schema 校验失败",
        ),
        (
            lambda text: text.replace("  high:\n", "  review:\n", 1),
            "Schema 校验失败",
        ),
        (
            lambda text: text.split("\n  ultra:\n", 1)[0] + "\n",
            "Schema 校验失败",
        ),
        (
            lambda text: text.replace(
                "png_to_shader_runtime_policy_v2",
                "png_to_shader_runtime_policy_v1",
                1,
            ),
            "Schema 校验失败",
        ),
        (
            lambda text: text.replace(
                "max_model_calls: 40", "max_model_calls: 41", 1
            ),
            "超过代码硬上限",
        ),
    ],
)
def test_runtime_policy_rejects_ambiguous_or_unsafe_configuration(
    tmp_path: Path,
    mutate,
    message: str,
) -> None:
    path = tmp_path / "invalid-runtime-policy.yaml"
    path.write_text(mutate(_default_text()), encoding="utf-8")

    with pytest.raises(RuntimePolicyConfigurationError, match=message):
        load_runtime_policy(path)
