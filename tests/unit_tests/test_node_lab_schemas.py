from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from agent.app.lab.fixtures import FixtureDefinition
from agent.app.lab.models import (
    LabRunCreateRequest,
    StepExecutionRequest,
    ensure_json_object,
)


@pytest.mark.parametrize(
    "value",
    (
        {"image": b"png"},
        {"score": math.nan},
        {"nested": {1: "non-string-key"}},
        {"tuple": (1, 2)},
    ),
)
def test_request_state_rejects_non_json_safe_values(value: dict) -> None:
    with pytest.raises(ValidationError):
        LabRunCreateRequest(initial_state=value)


def test_step_request_is_strict_and_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        StepExecutionRequest.model_validate(
            {
                "lab_run_id": "lab-1",
                "node_id": "decide_after_render",
                "execution_mode": "fixture",
                "unexpected": True,
            }
        )


def test_step_request_new_controls_have_safe_backward_compatible_defaults() -> None:
    request = StepExecutionRequest(
        lab_run_id="lab-1",
        node_id="decide_after_render",
    )

    assert request.execution_mode == "fixture"
    assert request.effect_mode == "lab_commit"
    assert request.preview_only is False
    assert request.allow_model_call is False
    assert request.mock_response_artifact_id is None


def test_step_request_accepts_explicit_preview_and_mock_controls() -> None:
    request = StepExecutionRequest(
        lab_run_id="lab-1",
        node_id="visual_analysis",
        execution_mode="mock",
        effect_mode="preview",
        preview_only=True,
        allow_model_call=False,
        mock_response_artifact_id="artifact-mock-1",
        inputs={"target_measurements": {"image_width": 192}},
    )

    assert request.to_dict()["effect_mode"] == "preview"
    assert request.preview_only is True
    assert request.mock_response_artifact_id == "artifact-mock-1"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("effect_mode", "memory_write"),
        ("preview_only", "true"),
        ("allow_model_call", 1),
        ("mock_response_artifact_id", "../secret"),
    ),
)
def test_step_request_new_controls_are_strict(field: str, value: object) -> None:
    payload = {
        "lab_run_id": "lab-1",
        "node_id": "visual_analysis",
        field: value,
    }

    with pytest.raises(ValidationError):
        StepExecutionRequest.model_validate(payload)


def test_step_request_inputs_remain_json_safe_with_new_controls() -> None:
    with pytest.raises(ValidationError):
        StepExecutionRequest(
            lab_run_id="lab-1",
            node_id="visual_analysis",
            preview_only=True,
            inputs={"mock": b"raw-model-output"},
        )


def test_fixture_hash_is_stable_and_changes_with_content() -> None:
    first = FixtureDefinition(
        fixture_id="fixture-1",
        node_id="decide_after_render",
        fixture_version="v1",
        output_patch={"next_action": "select"},
    )
    same = FixtureDefinition.model_validate(first.to_dict())
    changed = FixtureDefinition(
        fixture_id="fixture-1",
        node_id="decide_after_render",
        fixture_version="v1",
        output_patch={"next_action": "finalize"},
    )

    assert first.content_sha256 == same.content_sha256
    assert first.content_sha256 != changed.content_sha256


def test_json_depth_has_hard_limit() -> None:
    value: dict = {}
    cursor = value
    for _ in range(34):
        cursor["nested"] = {}
        cursor = cursor["nested"]

    with pytest.raises(ValueError, match="超过 32 层"):
        ensure_json_object(value)
