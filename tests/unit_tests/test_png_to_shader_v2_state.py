from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agent.app.states.png_to_shader_v2_state import (
    BudgetVectorV2,
    build_checkpoint_namespace_v2,
    commit_budget_v2,
    evolve_state_v2,
    reserve_budget_v2,
    restore_state_v2,
    serialize_state_v2,
)
from shaderforge.contracts.canonical import canonical_sha256
from tests.fixtures.png_to_shader_v2_contracts import make_state


def _delta(*, render_calls: int = 0, artifact_bytes: int = 0) -> BudgetVectorV2:
    return BudgetVectorV2(
        wall_time_ms=0,
        model_calls=0,
        model_tokens=0,
        render_calls=render_calls,
        candidate_attempts=0,
        artifact_bytes=artifact_bytes,
        cost_usd_micros=0,
    )


def test_state_v4_serialization_and_last_confirmed_checkpoint_recovery_smoke() -> None:
    state = make_state()
    payload = serialize_state_v2(state)
    restored = restore_state_v2(payload)

    assert restored == state
    assert restored.evaluation_revision == 0
    assert restored.checkpoint_namespace == build_checkpoint_namespace_v2(state.run_id)
    assert canonical_sha256(restored) == canonical_sha256(state)


def test_state_restore_rejects_v1_and_wrong_namespace_without_upgrade() -> None:
    raw = make_state().model_dump(mode="json")
    raw["state_schema_version"] = "state_v1"
    with pytest.raises(ValidationError, match="state_v4"):
        restore_state_v2(json.dumps(raw))

    raw = make_state().model_dump(mode="json")
    raw["state_schema_version"] = "state_v2"
    raw["graph_version"] = "2.0"
    raw["checkpoint_schema_version"] = "checkpoint_v2"
    raw["checkpoint_namespace"] = f"png-to-shader-v2:{raw['run_id']}"
    for field in (
        "active_render_call_ordinal",
        "promotion_operation_ref",
        "promotion_receipt_ref",
    ):
        raw.pop(field)
    with pytest.raises(ValidationError, match="state_v4"):
        restore_state_v2(json.dumps(raw))

    raw = make_state().model_dump(mode="json")
    raw["state_schema_version"] = "state_v3"
    raw["graph_version"] = "2.3"
    raw["checkpoint_schema_version"] = "checkpoint_v3"
    raw["checkpoint_namespace"] = f"png-to-shader-v2.3:{raw['run_id']}"
    with pytest.raises(ValidationError, match="state_v4"):
        restore_state_v2(json.dumps(raw))

    raw = make_state().model_dump(mode="json")
    raw["checkpoint_namespace"] = "png-to-shader-v1:project-v2"
    with pytest.raises(ValidationError, match="checkpoint_namespace"):
        restore_state_v2(json.dumps(raw))


def test_state_restore_rejects_duplicate_json_keys_and_non_object() -> None:
    payload = serialize_state_v2(make_state()).decode("utf-8")
    duplicate = payload.replace(
        '"run_revision":0',
        '"run_revision":0,"run_revision":1',
        1,
    )

    with pytest.raises(ValueError, match="重复 JSON key"):
        restore_state_v2(duplicate)
    with pytest.raises(ValueError, match="JSON object"):
        restore_state_v2("[]")


def test_budget_revision_cas_reserve_commit_and_hard_limit() -> None:
    initial = make_state().budget_state
    reserved = reserve_budget_v2(
        initial,
        _delta(render_calls=2, artifact_bytes=100),
        expected_revision=0,
    )
    assert reserved.revision == 1
    assert reserved.reserved.render_calls == 2

    committed = commit_budget_v2(
        reserved,
        reservation=_delta(render_calls=2, artifact_bytes=100),
        used=_delta(render_calls=1, artifact_bytes=80),
        expected_revision=1,
    )
    assert committed.revision == 2
    assert committed.used.render_calls == 1
    assert committed.reserved.render_calls == 0

    with pytest.raises(RuntimeError, match="revision"):
        reserve_budget_v2(committed, _delta(render_calls=1), expected_revision=1)
    with pytest.raises(ValueError, match="超限"):
        reserve_budget_v2(
            committed,
            _delta(render_calls=committed.limits.render_calls),
            expected_revision=2,
        )


def test_run_revision_uses_an_independent_cas_domain() -> None:
    state = make_state()
    evolved = evolve_state_v2(
        state,
        expected_run_revision=0,
        phase="measured",
    )

    assert evolved.run_revision == 1
    assert evolved.budget_state.revision == state.budget_state.revision
    with pytest.raises(RuntimeError, match="revision"):
        evolve_state_v2(evolved, expected_run_revision=0, phase="interpreted")


@pytest.mark.parametrize(
    ("field_name", "changed_value"),
    (
        ("state_schema_version", "state_v3"),
        ("graph_id", "another_graph"),
        ("graph_version", "2.1"),
        ("checkpoint_schema_version", "checkpoint_v3"),
        ("checkpoint_namespace", "png-to-shader-v2.3:other-run"),
        ("project_id", "other-project"),
        ("run_id", "other-run"),
        ("budget_state", None),
    ),
)
def test_state_transition_rejects_identity_version_namespace_and_budget_changes(
    field_name: str,
    changed_value: object,
) -> None:
    state = make_state()
    with pytest.raises(ValueError, match="不得通过 transition 修改"):
        evolve_state_v2(
            state,
            expected_run_revision=state.run_revision,
            **{field_name: changed_value},
        )


def test_state_transition_strictly_revalidates_updates_and_rejects_unknown_fields() -> (
    None
):
    state = make_state()
    with pytest.raises(ValidationError, match="evaluation_revision"):
        evolve_state_v2(
            state,
            expected_run_revision=state.run_revision,
            evaluation_revision="1",
        )
    with pytest.raises(ValueError, match="未知字段"):
        evolve_state_v2(
            state,
            expected_run_revision=state.run_revision,
            inline_genome={},
        )
