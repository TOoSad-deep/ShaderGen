from pathlib import Path

import pytest

from shaderforge.config import RUNTIME_TIMEOUTS, load_runtime_timeouts


def _write_config(path: Path, *, fast_seconds: str = "18000") -> None:
    path.write_text(
        f"""
version: test_runtime_timeouts_v1
llm:
  request_seconds: 3600
renderer:
  prepare_seconds: 300
  draw_seconds: 120
  resource_close_seconds: 10
engine:
  attempt_seconds: 7200
  close_seconds: 60
production_shadow:
  attempt_seconds: 7200
  close_seconds: 60
  resource_close_seconds: 30
frontend:
  generation_request_seconds:
    fast: {fast_seconds}
    balanced: 21600
    high: 28800
    manual: 43200
  progress_request_seconds: 60
  progress_observation_grace_seconds: 7200
""".strip(),
        encoding="utf-8",
    )


def test_packaged_runtime_timeout_yaml_is_loaded() -> None:
    assert RUNTIME_TIMEOUTS.version == "runtime_timeouts_v1"
    assert RUNTIME_TIMEOUTS.llm.request_seconds == 3600
    assert RUNTIME_TIMEOUTS.renderer.prepare_seconds == 300
    assert RUNTIME_TIMEOUTS.renderer.draw_seconds == 120
    assert RUNTIME_TIMEOUTS.engine.attempt_seconds == 7200
    assert RUNTIME_TIMEOUTS.frontend.generation_request_seconds["manual"] == 43200


def test_runtime_timeout_yaml_accepts_user_values(tmp_path: Path) -> None:
    config_path = tmp_path / "runtime-timeouts.yaml"
    _write_config(config_path, fast_seconds="19000")

    config = load_runtime_timeouts(config_path)

    assert config.version == "test_runtime_timeouts_v1"
    assert config.frontend.generation_request_seconds["fast"] == 19000


@pytest.mark.parametrize("invalid", ["0", "-1", ".inf", "slow"])
def test_runtime_timeout_yaml_rejects_non_positive_or_non_finite_values(
    tmp_path: Path,
    invalid: str,
) -> None:
    config_path = tmp_path / "runtime-timeouts.yaml"
    _write_config(config_path, fast_seconds=invalid)

    with pytest.raises(ValueError, match="runtime timeout 配置无效"):
        load_runtime_timeouts(config_path)


def test_runtime_timeout_yaml_rejects_frontend_shorter_than_two_attempts(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "runtime-timeouts.yaml"
    _write_config(config_path, fast_seconds="14520")

    with pytest.raises(ValueError, match="两个串行"):
        load_runtime_timeouts(config_path)
