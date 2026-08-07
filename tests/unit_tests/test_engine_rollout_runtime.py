from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.app.services import engine_rollout_runtime as runtime_module
from backend.app.services.engine_rollout import (
    EngineAttemptContext,
    EngineAttemptFailure,
    ParentRunRequest,
)
from backend.app.services.engine_rollout_runtime import (
    DirectEngineAttemptExecutor,
    _claim_private_attempt,
    _direct_response_payload,
    _publish_node_progress,
    _safe_node_progress_update,
    build_engine_rollout_runtime,
)
from shaderforge.store import LocalArtifactStore


def test_runtime_is_direct_only_and_uses_isolated_stores(tmp_path: Path) -> None:
    public = LocalArtifactStore(tmp_path / "public")
    runtime = build_engine_rollout_runtime(
        public_store=public,
        private_attempt_root=tmp_path / "private",
    )
    assert runtime.artifacts.public_store.base_root == public.base_root
    assert runtime.artifacts.private_attempt_store.restrictive_permissions is True
    assert runtime.coordinator._direct_attempt_limit == 3


def test_private_attempt_claim_uses_parent_human_readable_layout(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "private", restrictive_permissions=True)
    parent_run_id = uuid4()
    context = EngineAttemptContext(
        parent_run_id=parent_run_id,
        attempt_id=uuid4(),
        attempt_index=0,
    )
    request = ParentRunRequest(
        parent_run_id=parent_run_id,
        project_id="project",
        image=b"image",
        content_type="image/png",
        instruction="",
        quality_preset="balanced",
        publication_date="2026-08-07",
        filename="粉色 气泡.png",
    )

    _claim_private_attempt(store, request, context)

    expected = (
        tmp_path
        / "private"
        / "粉色-气泡"
        / "2026-08-07"
        / str(parent_run_id)
        / str(context.attempt_id)
    )
    assert store.resolve_run(str(context.attempt_id)).root == expected
    with pytest.raises(EngineAttemptFailure, match="engine_attempt_duplicate"):
        _claim_private_attempt(store, request, context)


def _policy_result(
    *, mae: float = 0.06, loss: float = 0.08, quality_preset: str = "balanced"
):
    return SimpleNamespace(
        status="ok",
        failure_code=None,
        current_best=SimpleNamespace(
            mae=mae,
            loss=loss,
            spec=SimpleNamespace(fragment_source="void main(){}"),
            png_bytes=b"\x89PNG\r\n\x1a\nfake",
            metrics={"global_mae": mae},
            residual_summary={},
        ),
        optimization_policy=SimpleNamespace(
            quality_preset=quality_preset,
            target_mae=0.06,
            target_loss=0.08,
            fingerprint=lambda: "policy-fingerprint",
        ),
        optimization_policy_fingerprint="policy-fingerprint",
        refinement_stop_reason="target_reached",
        non_improving_count=0,
        duplicate_patch_count=0,
        uniform_optimization_summary=None,
        plan_ledger=SimpleNamespace(llm_call_count=1),
        direct_ledger=SimpleNamespace(
            llm_call_count=2,
            draw_count=3,
            compile_count=1,
        ),
        config=SimpleNamespace(
            draw_budget=8,
            plan_llm_budget=2,
            direct_author_llm_budget=8,
            refine_budget=2,
        ),
        config_fingerprint="config-fingerprint",
        identity=SimpleNamespace(implementation_identity_sha256="a" * 64),
        canvas_width=64,
        canvas_height=64,
        to_safe_summary=lambda: {"status": "ok"},
    )


def test_response_uses_agent_policy_and_requires_both_targets() -> None:
    payload = _direct_response_payload(
        _policy_result(mae=0.07, loss=0.07), quality_preset="balanced"
    )

    pipeline = payload["pipeline"]
    assert pipeline["target_mae"] == 0.06
    assert pipeline["target_loss"] == 0.08
    # Loss meets its target, but MAE does not: this must not be reported as reached.
    assert pipeline["target_reached"] is False
    assert pipeline["optimization_policy_fingerprint"] == "policy-fingerprint"
    assert pipeline["refinement_stop_reason"] == "target_reached"


def test_response_rejects_policy_for_another_preset() -> None:
    result = _policy_result()
    result.optimization_policy.quality_preset = "high"

    with pytest.raises(EngineAttemptFailure, match="engine_response_contract_failed"):
        _direct_response_payload(result, quality_preset="balanced")


def test_node_progress_projects_only_safe_uniform_decision_fields() -> None:
    events: list[dict[str, object]] = []
    request = ParentRunRequest(
        parent_run_id=uuid4(),
        project_id="project",
        image=b"image",
        content_type="image/png",
        instruction="",
        quality_preset="balanced",
        publication_date="2026-08-07",
        progress_callback=lambda event, _render: events.append(event),
    )
    _publish_node_progress(
        request,
        node_name="record_uniform_outcome",
        status="completed",
        attempt_index=0,
        duration_ms=1.0,
        update={
            "reason_code": "uniform_candidate_accepted",
            "uniform_optimization": {
                "draw_count": 2,
                "draw_budget": 4,
                "evaluated_count": 2,
                "accepted_count": 1,
                "candidate_outcome": "accepted",
            },
        },
    )

    assert events[0]["reason_code"] == "uniform_candidate_accepted"
    assert events[0]["uniform_optimization"] == {
        "draw_count": 2,
        "draw_budget": 4,
        "evaluated_count": 2,
        "accepted_count": 1,
        "candidate_outcome": "accepted",
    }
    assert _safe_node_progress_update(
        {
            "reason_code": "uniform_candidate_accepted",
            "uniform_optimization": {
                "draw_count": 2,
                "draw_budget": 4,
                "evaluated_count": 2,
                "accepted_count": 1,
                "path": "u_gain",
            },
        }
    ) == {"reason_code": "uniform_candidate_accepted"}
    assert _safe_node_progress_update(
        {
            "reason_code": "candidate_failures_exhausted",
            "uniform_optimization": {
                "draw_count": 1,
                "draw_budget": 4,
                "evaluated_count": 0,
                "accepted_count": 0,
                "stop_reason": "candidate_failures_exhausted",
            },
        }
    ) == {
        "reason_code": "candidate_failures_exhausted",
        "uniform_optimization": {
            "draw_count": 1,
            "draw_budget": 4,
            "evaluated_count": 0,
            "accepted_count": 0,
            "stop_reason": "candidate_failures_exhausted",
        },
    }
    assert _safe_node_progress_update(
        {"reason_code": "global_compile_budget_exhausted"}
    ) == {"reason_code": "global_compile_budget_exhausted"}


@pytest.mark.anyio
async def test_attempt_forwards_quality_preset_to_agent_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class Runner:
        async def run(self, _image: bytes, **kwargs):
            captured.update(kwargs)
            return _policy_result(quality_preset="high")

        async def close(self) -> None:
            return None

    monkeypatch.setattr(runtime_module, "_claim_private_attempt", lambda *_args: None)
    monkeypatch.setattr(runtime_module, "_write_private_success", lambda *_args: None)
    monkeypatch.setattr(runtime_module, "_write_private_failure", lambda *_args: None)
    progress_events: list[dict[str, object]] = []
    parent_run_id = uuid4()
    context = EngineAttemptContext(
        parent_run_id=parent_run_id,
        attempt_id=uuid4(),
        attempt_index=0,
    )
    executor = DirectEngineAttemptExecutor(
        context,
        config=SimpleNamespace(),
        private_attempt_store=LocalArtifactStore(tmp_path / "private"),
        runner_factory=lambda _config: Runner(),
    )

    result = await executor.execute(
        ParentRunRequest(
            parent_run_id=parent_run_id,
            project_id="project",
            image=b"image",
            content_type="image/png",
            instruction="match",
            quality_preset="high",
            publication_date="2026-08-07",
            progress_callback=lambda event, _render: progress_events.append(event),
        ),
        context,
    )

    assert captured["quality_preset"] == "high"
    assert result.response_payload["quality_preset"] == "high"
    completed = progress_events[-1]
    assert completed["phase"] == "direct_completed"
    assert completed["optimization_policy_fingerprint"] == "policy-fingerprint"
    assert completed["refinement_stop_reason"] == "target_reached"
    assert completed["best"] == {
        "mae": 0.06,
        "loss": 0.08,
        "target_mae": 0.06,
        "target_loss": 0.08,
    }
    assert "glsl" not in completed
    assert "uniform_values" not in completed
