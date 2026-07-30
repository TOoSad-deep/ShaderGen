from importlib.resources import files
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

from agent.app.config.direct_quality_presets import (
    DIRECT_QUALITY_PRESETS,
    load_direct_quality_presets,
)
from agent.app.contracts.layerplan_glsl_direct import (
    DirectOptimizationPolicy,
    LayerPlanGlslDirectConfig,
)


def _write_modified_config(
    path: Path,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    resource = files("agent.app.config").joinpath("direct_quality_presets.yaml")
    data = yaml.safe_load(resource.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_packaged_direct_quality_preset_yaml_is_loaded() -> None:
    manual = DIRECT_QUALITY_PRESETS.for_quality_preset("manual")

    assert DIRECT_QUALITY_PRESETS.version == "direct_quality_presets_v1"
    assert set(DIRECT_QUALITY_PRESETS.presets) == {
        "fast",
        "balanced",
        "high",
        "manual",
    }
    assert 0 <= manual.optimization_policy.target_mae <= 1
    assert 0 <= manual.optimization_policy.target_loss <= 1
    structural_candidates = 1 + manual.budgets.refine_budget
    assert manual.budgets.direct_author_llm_budget >= structural_candidates
    assert manual.budgets.compile_budget >= structural_candidates
    assert manual.budgets.draw_budget >= (
        structural_candidates + manual.budgets.uniform_tuning_draw_budget
    )


def test_python_defaults_follow_yaml_balanced_profile() -> None:
    balanced = DIRECT_QUALITY_PRESETS.for_quality_preset("balanced")
    policy = DirectOptimizationPolicy()
    budgets = LayerPlanGlslDirectConfig(implementation_identity_sha256="a" * 64)

    assert policy.target_mae == balanced.optimization_policy.target_mae
    assert policy.target_loss == balanced.optimization_policy.target_loss
    assert (
        policy.refinement_patience == balanced.optimization_policy.refinement_patience
    )
    assert budgets.refine_budget == balanced.budgets.refine_budget
    assert budgets.draw_budget == balanced.budgets.draw_budget


def test_direct_quality_preset_yaml_accepts_manual_adjustment(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "direct-quality-presets.yaml"

    def adjust(data: dict[str, Any]) -> None:
        data["presets"]["manual"]["budgets"]["refine_budget"] = 6

    _write_modified_config(config_path, adjust)

    loaded = load_direct_quality_presets(config_path)

    assert loaded.for_quality_preset("manual").budgets.refine_budget == 6


def test_direct_quality_preset_yaml_rejects_missing_public_preset(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "direct-quality-presets.yaml"

    def remove_high(data: dict[str, Any]) -> None:
        del data["presets"]["high"]

    _write_modified_config(config_path, remove_high)

    with pytest.raises(ValueError, match="missing=.*high"):
        load_direct_quality_presets(config_path)


def test_direct_quality_preset_yaml_rejects_incoherent_draw_budget(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "direct-quality-presets.yaml"

    def lower_draw_budget(data: dict[str, Any]) -> None:
        data["presets"]["manual"]["budgets"]["draw_budget"] = 9

    _write_modified_config(config_path, lower_draw_budget)

    with pytest.raises(ValueError, match="draw_budget must cover"):
        load_direct_quality_presets(config_path)


def test_direct_quality_preset_yaml_rejects_unknown_fields(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "direct-quality-presets.yaml"

    def add_unknown_field(data: dict[str, Any]) -> None:
        data["presets"]["manual"]["budgets"]["unknown_budget"] = 1

    _write_modified_config(config_path, add_unknown_field)

    with pytest.raises(ValueError, match="extra_forbidden"):
        load_direct_quality_presets(config_path)


def test_direct_quality_preset_yaml_rejects_unknown_schema_version(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "direct-quality-presets.yaml"

    def change_version(data: dict[str, Any]) -> None:
        data["version"] = "direct_quality_presets_v2"

    _write_modified_config(config_path, change_version)

    with pytest.raises(ValueError, match="literal_error"):
        load_direct_quality_presets(config_path)


@pytest.mark.parametrize("invalid", [True, "5", -1])
def test_direct_quality_preset_yaml_rejects_invalid_discrete_budget(
    tmp_path: Path,
    invalid: object,
) -> None:
    config_path = tmp_path / "direct-quality-presets.yaml"

    def change_budget(data: dict[str, Any]) -> None:
        data["presets"]["manual"]["budgets"]["refine_budget"] = invalid

    _write_modified_config(config_path, change_budget)

    with pytest.raises(ValueError, match="Direct quality preset config is invalid"):
        load_direct_quality_presets(config_path)
